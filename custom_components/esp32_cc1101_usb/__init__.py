import asyncio
import json
import logging
import serialx
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from .const import DOMAIN, CONF_PORT, CONF_BAUDRATE, EVENT_RF_RECEIVED

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "button"]

SERVICE_SEND_RAW_SCHEMA = vol.Schema({
    vol.Required("code"): cv.string,
})


class Esp32SerialProtocol(asyncio.Protocol):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self.transport = None
        self._buffer = b""

    def connection_made(self, transport):
        self.transport = transport
        _LOGGER.info("Connected via USB to ESP32-WROOM-32 on %s", self.entry.data[CONF_PORT])

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
                    raw_data = str(payload["raw"]).strip()
                    entry_data = self.hass.data[DOMAIN].get(self.entry.entry_id)
                    if entry_data:
                        entry_data["last_code"] = raw_data
                        if entry_data.get("listener"):
                            entry_data["listener"](raw_data)
                    self.hass.bus.async_fire(EVENT_RF_RECEIVED, {"raw": raw_data})
                elif "status" in payload:
                    _LOGGER.info("ESP32 CC1101 hardware response: %s", payload)
            except json.JSONDecodeError:
                pass

    def connection_lost(self, exc):
        _LOGGER.warning("Lost USB connection to ESP32: %s", exc)


def send_rf_code(hass: HomeAssistant, code: str) -> None:
    clean_code = code.replace(" ", "").strip()
    if not clean_code:
        raise HomeAssistantError("RF code cannot be empty")

    sent = False
    for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
        transport = entry_data.get("transport")
        if transport:
            transport.write(f"TX:{clean_code}\n".encode("utf-8"))
            _LOGGER.info("Sent RF code to ESP32 (%s): TX:%s", entry_id, clean_code)
            sent = True

    if not sent:
        raise HomeAssistantError("No active ESP32 CC1101 USB connection found")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    port = entry.data[CONF_PORT]
    baudrate = entry.data[CONF_BAUDRATE]

    def factory():
        return Esp32SerialProtocol(hass, entry)

    loop = asyncio.get_running_loop()
    try:
        transport, protocol = await serialx.create_serial_connection(
            loop, factory, port, baudrate=baudrate
        )
    except Exception as err:
        raise ConfigEntryNotReady(f"Could not open serial port {port}: {err}") from err

    hass.data[DOMAIN][entry.entry_id] = {
        "transport": transport,
        "protocol": protocol,
        "listener": None,
        "last_code": "",
    }

    async def async_send_raw_service(call: ServiceCall):
        code = call.data.get("code", "")
        send_rf_code(hass, code)

    if not hass.services.has_service(DOMAIN, "send_raw"):
        hass.services.async_register(
            DOMAIN,
            "send_raw",
            async_send_raw_service,
            schema=SERVICE_SEND_RAW_SCHEMA,
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data and data.get("transport"):
            data["transport"].close()
    return unload_ok
