import asyncio
import json
import logging
import serialx

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
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
        _LOGGER.info("Conectado por USB al ESP32-WROOM-32 en %s", self.entry.data[CONF_PORT])

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
                    raw_data = str(payload["raw"])
                    entry_data = self.hass.data[DOMAIN].get(self.entry.entry_id)
                    if entry_data and entry_data.get("listener"):
                        entry_data["listener"](raw_data)
                    self.hass.bus.async_fire(EVENT_RF_RECEIVED, {"raw": raw_data})
                elif "status" in payload:
                    _LOGGER.debug("Estado del ESP32 CC1101: %s", payload)
            except json.JSONDecodeError:
                pass

    def connection_lost(self, exc):
        _LOGGER.warning("Conexion USB perdida con ESP32: %s", exc)

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
        raise ConfigEntryNotReady(f"No se pudo abrir el puerto {port}: {err}") from err

    hass.data[DOMAIN][entry.entry_id] = {
        "transport": transport,
        "protocol": protocol,
        "listener": None,
    }

    async def async_send_raw_service(call: ServiceCall):
        code = call.data.get("code")
        if code and transport:
            transport.write(f"TX:{code}\n".encode())
            _LOGGER.debug("Enviado al ESP32 via USB: TX:%s", code)

    if not hass.services.has_service(DOMAIN, "send_raw"):
        hass.services.async_register(DOMAIN, "send_raw", async_send_raw_service)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data and data.get("transport"):
            data["transport"].close()
    return unload_ok
