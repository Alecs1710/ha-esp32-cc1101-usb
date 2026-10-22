import asyncio
from collections import Counter
import json
import logging
import serialx
import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store
from .const import (
    DOMAIN,
    CONF_PORT,
    CONF_BAUDRATE,
    EVENT_RF_RECEIVED,
    STORAGE_KEY,
    STORAGE_VERSION,
    LEARN_STEPS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "button"]

SERVICE_SEND_RAW_SCHEMA = vol.Schema({
    vol.Required("code"): cv.string,
})

SERVICE_LEARN_COMMAND_SCHEMA = vol.Schema({
    vol.Required("name"): cv.string,
})

SERVICE_SEND_COMMAND_SCHEMA = vol.Schema({
    vol.Required("name"): cv.string,
})

SERVICE_DELETE_COMMAND_SCHEMA = vol.Schema({
    vol.Required("name"): cv.string,
})


def _average_rf_samples(samples: list[str]) -> str:
    parsed = []
    for s in samples:
        try:
            pulses = [int(x.strip()) for x in s.split(",") if x.strip()]
            if pulses:
                parsed.append(pulses)
        except ValueError:
            continue

    if not parsed:
        return samples[-1]

    most_common_len = Counter(len(p) for p in parsed).most_common(1)[0][0]
    matching = [p for p in parsed if len(p) == most_common_len]

    averaged = [
        str(round(sum(p[i] for p in matching) / len(matching)))
        for i in range(most_common_len)
    ]
    return ",".join(averaged)


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
                    self.hass.async_create_task(self._handle_raw_received(raw_data))
                elif "status" in payload:
                    _LOGGER.info("ESP32 CC1101 hardware response: %s", payload)
            except json.JSONDecodeError:
                pass

    async def _handle_raw_received(self, raw_data: str) -> None:
        entry_data = self.hass.data[DOMAIN].get(self.entry.entry_id)
        if not entry_data:
            return

        entry_data["last_code"] = raw_data
        for listener in entry_data.get("listeners", []):
            listener(raw_data)

        self.hass.bus.async_fire(EVENT_RF_RECEIVED, {"raw": raw_data})

        learning = entry_data.get("learning")
        if learning and learning.get("active"):
            learning["samples"].append(raw_data)
            count = len(learning["samples"])
            cmd_name = learning["name"]

            for listener in entry_data.get("listeners", []):
                listener(raw_data)

            if count < LEARN_STEPS:
                persistent_notification.async_create(
                    self.hass,
                    f"Captured press **{count} of {LEARN_STEPS}** for `{cmd_name}`.\n\n"
                    f"Please press the physical remote button again ({LEARN_STEPS - count} remaining).",
                    title=f"Learning RF Code: {cmd_name} ({count}/{LEARN_STEPS})",
                    notification_id=f"{DOMAIN}_learning",
                )
            else:
                final_code = _average_rf_samples(learning["samples"])
                learning["active"] = False
                entry_data["saved_codes"][cmd_name] = final_code
                await entry_data["store"].async_save(entry_data["saved_codes"])

                if entry_data.get("add_button_callback"):
                    entry_data["add_button_callback"](cmd_name)

                for listener in entry_data.get("listeners", []):
                    listener(final_code)

                persistent_notification.async_create(
                    self.hass,
                    f"Successfully learned and saved **{cmd_name}** after {LEARN_STEPS} presses!\n\n"
                    f"**Averaged Code:** `{final_code}`\n\n"
                    f"A new button entity for **{cmd_name}** has been added to your device.",
                    title=f"RF Code Saved: {cmd_name}",
                    notification_id=f"{DOMAIN}_learning",
                )

    def connection_lost(self, exc):
        _LOGGER.warning("Lost USB connection to ESP32: %s", exc)


def send_rf_code(hass: HomeAssistant, code: str) -> None:
    clean_code = code.replace(" ", "").strip()
    if not clean_code:
        raise HomeAssistantError("RF code cannot be empty")

    sent = False
    for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
        if not isinstance(entry_data, dict):
            continue
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

    store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry.entry_id}")
    saved_codes = await store.async_load() or {}

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
        "listeners": [],
        "last_code": "",
        "store": store,
        "saved_codes": saved_codes,
        "learning": {"active": False, "name": "", "samples": []},
        "add_button_callback": None,
    }

    async def async_send_raw_service(call: ServiceCall):
        code = call.data.get("code", "")
        send_rf_code(hass, code)

    async def async_learn_command_service(call: ServiceCall):
        name = call.data.get("name", "").strip()
        if not name:
            raise HomeAssistantError("Command name cannot be empty")

        for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
            if isinstance(entry_data, dict) and "learning" in entry_data:
                entry_data["learning"] = {
                    "active": True,
                    "name": name,
                    "samples": [],
                }
                for listener in entry_data.get("listeners", []):
                    listener("")

        persistent_notification.async_create(
            hass,
            f"Ready to learn **{name}**.\n\n"
            f"Please press the button on your physical remote **{LEARN_STEPS} times** "
            f"(pause 1 second between presses).",
            title=f"Learning RF Code: {name} (0/{LEARN_STEPS})",
            notification_id=f"{DOMAIN}_learning",
        )

    async def async_send_command_service(call: ServiceCall):
        name = call.data.get("name", "").strip()
        for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
            if isinstance(entry_data, dict):
                saved = entry_data.get("saved_codes", {})
                if name in saved:
                    send_rf_code(hass, saved[name])
                    return
        raise HomeAssistantError(f"Saved RF command '{name}' not found")

    async def async_delete_command_service(call: ServiceCall):
        name = call.data.get("name", "").strip()
        deleted = False
        for entry_id, entry_data in hass.data.get(DOMAIN, {}).items():
            if isinstance(entry_data, dict):
                saved = entry_data.get("saved_codes", {})
                if name in saved:
                    del saved[name]
                    await entry_data["store"].async_save(saved)
                    deleted = True
                    for listener in entry_data.get("listeners", []):
                        listener("")
        if not deleted:
            raise HomeAssistantError(f"Saved RF command '{name}' not found")

    if not hass.services.has_service(DOMAIN, "send_raw"):
        hass.services.async_register(
            DOMAIN, "send_raw", async_send_raw_service, schema=SERVICE_SEND_RAW_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, "learn_command"):
        hass.services.async_register(
            DOMAIN, "learn_command", async_learn_command_service, schema=SERVICE_LEARN_COMMAND_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, "send_command"):
        hass.services.async_register(
            DOMAIN, "send_command", async_send_command_service, schema=SERVICE_SEND_COMMAND_SCHEMA
        )
    if not hass.services.has_service(DOMAIN, "delete_command"):
        hass.services.async_register(
            DOMAIN, "delete_command", async_delete_command_service, schema=SERVICE_DELETE_COMMAND_SCHEMA
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
