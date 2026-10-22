import asyncio
import json
import logging
import serial_asyncio

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from .const import DOMAIN, CONF_PORT, CONF_BAUDRATE, EVENT_RF_RECEIVED

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]

class Esp32SerialProtocol(asyncio.Protocol):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.transport = None
        self._buffer = b""

    def connection_made(self, transport):
        self.transport = transport
        _LOGGER.info("Connected to ESP32-WROOM-32 via USB at %s", self.entry.data[CONF_PORT])

    def data_received(self, data):
        self._buffer += data
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            clean_line = line.decode("utf-8", errors="ignore").strip()
            if not clean_line.startswith("{"):
                continue
            try:
                payload = json.loads(clean_line)
                if "raw" in payload:
                    raw_data = payload["raw"]
                    entry_data = self.hass.data[DOMAIN].get(self.entry.entry_id)
                    if entry_data and entry_data.get("listener"):
                        entry_data["listener"](raw_data)
                    self.hass.bus.async_fire(EVENT_RF_RECEIVED, {"raw": raw_data})
            except json.JSONDecodeError:
                pass

    def connection_lost(self, exc):
        _LOGGER.warning("Lost USB connection to ESP32: %s", exc)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    port = entry.data[CONF_PORT]
    baudrate = entry.data[CONF_BAUDRATE]

    protocol_ref = {}

    def factory():
        proto = Esp32SerialProtocol(hass, entry)
        protocol_ref["instance"] = proto
        return proto

    loop = asyncio.get_running_loop()
    transport, protocol = await serial_asyncio.create_serial_connection(
        loop, factory, port, baudrate=baudrate
    )

    hass.data[DOMAIN][entry.entry_id] = {
        "transport": transport,
        "protocol": protocol,
        "listener": None,
    }

    async def async_send_raw_service(call: ServiceCall):
        code = call.data.get("code")
        if code and transport:
            transport.write(f"TX:{code}\n".encode())
            _LOGGER.debug("Sent to ESP32 via USB: TX:%s", code)

    hass.services.async_register(DOMAIN, "send_raw", async_send_raw_service)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data and data.get("transport"):
        data["transport"].close()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
