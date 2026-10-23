"""Client communication layer for ESP32 CC1101 USB RF Transceiver."""

import asyncio
from collections.abc import Callable
import json
import logging
from typing import Any
import serialx

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CONF_BAUDRATE,
    CONF_PORT,
    CONF_FREQUENCY,
    CONF_MODULATION,
    CONF_BANDWIDTH,
    CONF_TX_POWER,
    CONF_REPEATS,
    CONF_MIN_RSSI,
    CONF_MIN_REPEATS,
    CONF_RX_MODE,
    DEFAULT_FREQUENCY,
    DEFAULT_MODULATION,
    DEFAULT_BANDWIDTH,
    DEFAULT_TX_POWER,
    DEFAULT_REPEATS,
    DEFAULT_MIN_RSSI,
    DEFAULT_MIN_REPEATS,
    EVENT_RF_RECEIVED,
    EVENT_CODE_RECEIVED,
    MODULATION_NAME_TO_CODE,
    MODULATION_CODE_TO_NAME,
    MODULATION_OOK,
    RX_MODE_DECODED,
    RX_MODE_MAP,
    RX_MODE_REV_MAP,
)

_LOGGER = logging.getLogger(__name__)


class Esp32Cc1101Protocol(asyncio.Protocol):
    """Protocol for handling asynchronous serial data from the ESP32."""

    def __init__(self, client: "Esp32Cc1101Client") -> None:
        self.client = client
        self.transport: asyncio.Transport | None = None
        self._buffer = b""

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        self.client.on_connected(self.transport)

    def data_received(self, data: bytes) -> None:
        self._buffer += data
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            clean_line = line.decode("utf-8", errors="ignore").strip()
            if clean_line:
                self.client.handle_incoming_line(clean_line)

    def connection_lost(self, exc: Exception | None) -> None:
        self.client.on_disconnected(exc)


class Esp32Cc1101Client:
    """Manages the serial connection, protocol commands, and telemetry for the ESP32 CC1101."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.port: str = entry.data[CONF_PORT]
        self.baudrate: int = entry.data[CONF_BAUDRATE]

        self.transport: asyncio.Transport | None = None
        self.protocol: Esp32Cc1101Protocol | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._shutdown: bool = False
        self._listeners: list[Callable[[dict[str, Any]], None]] = []

        # Current radio and hardware telemetry
        self.connected: bool = False
        self.chip_detected: bool = False
        self.version: str = "Unknown"
        self.frequency: float = entry.options.get(
            CONF_FREQUENCY, entry.data.get(CONF_FREQUENCY, DEFAULT_FREQUENCY)
        )
        self.modulation: str = entry.options.get(
            CONF_MODULATION, entry.data.get(CONF_MODULATION, DEFAULT_MODULATION)
        )
        self.bandwidth: float = entry.options.get(
            CONF_BANDWIDTH, entry.data.get(CONF_BANDWIDTH, DEFAULT_BANDWIDTH)
        )
        self.tx_power: int = entry.options.get(
            CONF_TX_POWER, entry.data.get(CONF_TX_POWER, DEFAULT_TX_POWER)
        )
        self.repeats: int = entry.options.get(
            CONF_REPEATS, entry.data.get(CONF_REPEATS, DEFAULT_REPEATS)
        )
        self.min_rssi: int = entry.options.get(
            CONF_MIN_RSSI, entry.data.get(CONF_MIN_RSSI, DEFAULT_MIN_RSSI)
        )
        self.min_repeats: int = entry.options.get(
            CONF_MIN_REPEATS, entry.data.get(CONF_MIN_REPEATS, DEFAULT_MIN_REPEATS)
        )
        self.rx_mode: str = entry.options.get(
            CONF_RX_MODE, entry.data.get(CONF_RX_MODE, RX_MODE_DECODED)
        )
        self.radio_state: int = 0
        self.rssi: int | None = None

        # Received data state
        self.last_code: str = ""
        self.last_decoded_code: int | None = None
        self.last_protocol: int | None = None
        self.last_bit_length: int | None = None
        self.last_pulse_length: int | None = None
        self.last_rssi: int | None = None

    def register_listener(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Register a callback for telemetry and incoming signals."""
        self._listeners.append(callback)

        def remove():
            if callback in self._listeners:
                self._listeners.remove(callback)

        return remove

    def _notify_listeners(self, data: dict[str, Any]) -> None:
        """Notify all registered listeners."""
        for callback in list(self._listeners):
            try:
                callback(data)
            except Exception as err:
                _LOGGER.error("Error in ESP32 CC1101 listener callback: %s", err)

    async def async_start(self) -> None:
        """Start the connection and watchdog loop."""
        self._shutdown = False
        await self._connect()
        if not self.connected:
            self._start_reconnect_task()

    async def async_stop(self) -> None:
        """Stop the client and close serial transport."""
        self._shutdown = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        if self.transport:
            self.transport.close()
            self.transport = None
        self.connected = False

    async def _connect(self) -> bool:
        """Attempt to establish serial connection."""
        try:
            loop = asyncio.get_running_loop()
            transport, protocol = await serialx.create_serial_connection(
                loop, lambda: Esp32Cc1101Protocol(self), self.port, baudrate=self.baudrate
            )
            self.transport = transport
            self.protocol = protocol
            self.connected = True
            _LOGGER.info("Connected to ESP32 CC1101 on %s (%d baud)", self.port, self.baudrate)
            # Sync configured frequency and parameters to radio
            await asyncio.sleep(0.3)
            await self.async_set_config(
                freq=self.frequency,
                mod=self.modulation,
                bw=self.bandwidth,
                pa=self.tx_power,
                min_rssi=self.min_rssi,
                min_repeats=self.min_repeats,
                rx_mode=self.rx_mode,
            )
            await self.async_query_status()
            return True
        except Exception as err:
            _LOGGER.warning("Could not connect to ESP32 CC1101 on %s: %s", self.port, err)
            self.connected = False
            return False

    def _start_reconnect_task(self) -> None:
        """Start background reconnection loop with exponential backoff."""
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = self.hass.async_create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Reconnection loop with backoff."""
        backoff = 2
        while not self._shutdown and not self.connected:
            _LOGGER.debug("Reconnecting to ESP32 CC1101 on %s in %ds...", self.port, backoff)
            await asyncio.sleep(backoff)
            if self._shutdown:
                break
            success = await self._connect()
            if success:
                _LOGGER.info("Reconnection to ESP32 CC1101 on %s succeeded!", self.port)
                break
            backoff = min(backoff * 2, 30)

    def on_connected(self, transport: asyncio.Transport) -> None:
        """Called when transport is connected."""
        self.transport = transport
        self.connected = True
        _LOGGER.info("Connected to ESP32 CC1101 on %s at %d baud", self.port, self.baudrate)
        self._notify_listeners({"type": "connection", "connected": True})

    def on_disconnected(self, exc: Exception | None) -> None:
        """Called when transport is disconnected."""
        self.connected = False
        self.transport = None
        _LOGGER.warning("Serial connection to ESP32 CC1101 lost (%s): %s", self.port, exc)
        self._notify_listeners({"type": "connection", "connected": False})
        if not self._shutdown:
            self._start_reconnect_task()

    def handle_incoming_line(self, line: str) -> None:
        """Parse incoming line from the ESP32."""
        if not line.startswith("{"):
            _LOGGER.debug("ESP32 raw serial line: %s", line)
            return

        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            _LOGGER.debug("Ignoring non-JSON serial line: %s", line)
            return

        event = payload.get("event")
        status = payload.get("status")

        # Telemetry / Status event
        if event in ("status", "ready", "config_updated") or (status in ("READY", "OK")):
            if "freq" in payload:
                self.frequency = float(payload["freq"])
            if "mod" in payload:
                mod_val = payload["mod"]
                self.modulation = (
                    MODULATION_CODE_TO_NAME.get(int(mod_val), str(mod_val))
                    if isinstance(mod_val, (int, str)) and str(mod_val).isdigit()
                    else str(mod_val)
                )
            if "bw" in payload:
                self.bandwidth = float(payload["bw"])
            if "pa" in payload:
                self.tx_power = int(payload["pa"])
            if "radio_state" in payload:
                self.radio_state = int(payload["radio_state"])
            if "version" in payload:
                self.version = str(payload["version"])
            if "chip_detected" in payload:
                self.chip_detected = bool(payload["chip_detected"])
            if "rssi" in payload:
                self.rssi = int(payload["rssi"])
            if "min_rssi" in payload:
                self.min_rssi = int(payload["min_rssi"])
            if "min_repeats" in payload:
                self.min_repeats = int(payload["min_repeats"])
            if "rx_mode" in payload:
                rxm = payload["rx_mode"]
                if isinstance(rxm, int) or (isinstance(rxm, str) and str(rxm).isdigit()):
                    self.rx_mode = RX_MODE_REV_MAP.get(int(rxm), RX_MODE_DECODED)
                elif isinstance(rxm, str) and rxm in RX_MODE_MAP:
                    self.rx_mode = rxm

            _LOGGER.info(
                "ESP32 CC1101 Online: FW=%s, Freq=%.2fMHz, Mod=%s, BW=%.1fkHz, Power=%ddBm, Squelch=%ddBm, MinRepeats=%d, Mode=%s, CC1101=%s",
                self.version,
                self.frequency,
                self.modulation,
                self.bandwidth,
                self.tx_power,
                self.min_rssi,
                self.min_repeats,
                self.rx_mode,
                "OK" if self.chip_detected else "FAILED",
            )
            self._notify_listeners({"type": "telemetry", "payload": payload})
            return

        # Standard RF code decoded
        if event == "rx_code":
            self.last_decoded_code = payload.get("code")
            self.last_protocol = payload.get("protocol")
            self.last_bit_length = payload.get("bits")
            self.last_pulse_length = payload.get("pulse_length")
            self.last_rssi = payload.get("rssi")
            raw_str = payload.get("raw", "")
            if raw_str:
                self.last_code = raw_str

            _LOGGER.info(
                "RF Code Received: code=%s (hex=0x%X), bits=%s, proto=%s, pulse_len=%dus, rssi=%ddBm, freq=%.2fMHz",
                self.last_decoded_code,
                self.last_decoded_code or 0,
                self.last_bit_length,
                self.last_protocol,
                self.last_pulse_length or 0,
                self.last_rssi or 0,
                payload.get("freq", self.frequency),
            )

            self.hass.bus.async_fire(
                EVENT_CODE_RECEIVED,
                {
                    "code": self.last_decoded_code,
                    "protocol": self.last_protocol,
                    "bits": self.last_bit_length,
                    "pulse_length": self.last_pulse_length,
                    "rssi": self.last_rssi,
                    "freq": payload.get("freq", self.frequency),
                    "raw": raw_str,
                },
            )
            self.hass.bus.async_fire(EVENT_RF_RECEIVED, {"raw": raw_str})
            self._notify_listeners({"type": "rx_code", "payload": payload})
            return

        # Raw RF pulse train captured
        if event == "rx_raw" or "raw" in payload:
            raw_str = payload.get("raw", "")
            self.last_code = raw_str
            self.last_rssi = payload.get("rssi")
            self.last_decoded_code = None
            self.last_protocol = None
            self.last_bit_length = None
            self.last_pulse_length = None

            _LOGGER.info(
                "RF Raw Pulses Received: count=%s pulses, rssi=%ddBm, freq=%.2fMHz",
                payload.get("count", len(raw_str.split(",")) if raw_str else 0),
                self.last_rssi or 0,
                payload.get("freq", self.frequency),
            )

            self.hass.bus.async_fire(
                EVENT_RF_RECEIVED,
                {
                    "raw": raw_str,
                    "count": payload.get("count"),
                    "rssi": self.last_rssi,
                    "freq": payload.get("freq", self.frequency),
                },
            )
            self._notify_listeners({"type": "rx_raw", "payload": payload})
            return

        # Transmission completed
        if event == "tx_complete" or status == "TX_OK":
            if "radio_state" in payload:
                self.radio_state = int(payload["radio_state"])
            _LOGGER.info("RF Transmit Complete: status=ok, radio_state=%s", self.radio_state)
            self._notify_listeners({"type": "tx_complete", "payload": payload})
            return

        if event == "error" or "error" in payload:
            _LOGGER.warning("ESP32 CC1101 Error: %s", payload.get("message", payload))
            return

        _LOGGER.debug("Unhandled payload from ESP32: %s", payload)

    def _write_line(self, line: str) -> None:
        """Write line to transport."""
        if not self.transport:
            raise HomeAssistantError("ESP32 CC1101 USB is not connected")
        data = (line.strip() + "\n").encode("utf-8")
        self.transport.write(data)

    async def async_send_raw(
        self, pulses: list[int] | str, repeats: int | None = None, gap: int = 8250
    ) -> None:
        """Transmit raw pulse train."""
        repeat_count = repeats if repeats is not None else self.repeats
        if isinstance(pulses, str):
            clean_str = pulses.replace(" ", "").strip()
            pulse_list = [int(p) for p in clean_str.split(",") if p.isdigit()]
        else:
            pulse_list = pulses

        if not pulse_list:
            raise HomeAssistantError("Pulse list cannot be empty")

        cmd = {
            "cmd": "tx_raw",
            "pulses": pulse_list,
            "repeats": repeat_count,
            "gap": gap,
        }
        self._write_line(json.dumps(cmd))
        _LOGGER.info("Transmitted raw RF signal (%d pulses, %d repeats)", len(pulse_list), repeat_count)

    async def async_send_code(
        self,
        code: int,
        bits: int = 24,
        protocol: int = 1,
        pulse_length: int = 350,
        repeats: int | None = None,
    ) -> None:
        """Transmit standard protocol RF code."""
        repeat_count = repeats if repeats is not None else self.repeats
        cmd = {
            "cmd": "tx_code",
            "code": code,
            "bits": bits,
            "protocol": protocol,
            "pulse_length": pulse_length,
            "repeats": repeat_count,
        }
        self._write_line(json.dumps(cmd))
        _LOGGER.info(
            "Transmitted standard RF code %d (proto=%d, bits=%d, repeats=%d)",
            code,
            protocol,
            bits,
            repeat_count,
        )

    async def async_set_config(
        self,
        freq: float | None = None,
        mod: str | int | None = None,
        bw: float | None = None,
        pa: int | None = None,
        min_rssi: int | None = None,
        min_repeats: int | None = None,
        rx_mode: str | int | None = None,
    ) -> None:
        """Update CC1101 radio frequency, modulation, bandwidth, power, squelch, and rx mode."""
        cmd: dict[str, Any] = {"cmd": "set_config"}
        if freq is not None:
            self.frequency = float(freq)
            cmd["freq"] = self.frequency
        if mod is not None:
            if isinstance(mod, str):
                self.modulation = mod
                cmd["mod"] = MODULATION_NAME_TO_CODE.get(mod, 2)
            else:
                cmd["mod"] = int(mod)
                self.modulation = MODULATION_CODE_TO_NAME.get(int(mod), str(mod))
        if bw is not None:
            self.bandwidth = float(bw)
            cmd["bw"] = self.bandwidth
        if pa is not None:
            self.tx_power = int(pa)
            cmd["pa"] = self.tx_power
        if min_rssi is not None:
            self.min_rssi = int(min_rssi)
            cmd["min_rssi"] = self.min_rssi
        if min_repeats is not None:
            self.min_repeats = max(1, int(min_repeats))
            cmd["min_repeats"] = self.min_repeats
        if rx_mode is not None:
            if isinstance(rx_mode, str):
                self.rx_mode = rx_mode
                cmd["rx_mode"] = RX_MODE_MAP.get(rx_mode, 0)
            else:
                cmd["rx_mode"] = int(rx_mode)
                self.rx_mode = RX_MODE_REV_MAP.get(int(rx_mode), RX_MODE_DECODED)

        self._write_line(json.dumps(cmd))
        _LOGGER.info("Sent radio configuration to ESP32: %s", cmd)

    async def async_ping(self) -> None:
        """Send ping to verify ESP32 communication."""
        self._write_line(json.dumps({"cmd": "ping"}))

    async def async_reset(self) -> None:
        """Send radio reset command to CC1101."""
        self._write_line(json.dumps({"cmd": "reset"}))

    async def async_query_status(self) -> None:
        """Request status from ESP32."""
        self._write_line(json.dumps({"cmd": "get_status"}))
