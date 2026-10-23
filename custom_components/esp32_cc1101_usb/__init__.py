"""The ESP32 CC1101 USB RF integration."""

from __future__ import annotations

import asyncio
from collections import Counter
import logging
from typing import Any
import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store

from .client import Esp32Cc1101Client
from .const import (
    DOMAIN,
    CONF_FREQUENCY,
    CONF_MODULATION,
    CONF_BANDWIDTH,
    CONF_TX_POWER,
    CONF_REPEATS,
    DEFAULT_FREQUENCY,
    DEFAULT_MODULATION,
    DEFAULT_BANDWIDTH,
    DEFAULT_TX_POWER,
    DEFAULT_REPEATS,
    STORAGE_KEY,
    STORAGE_VERSION,
    LEARN_STEPS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["remote", "sensor", "select", "number", "button"]

SERVICE_SEND_RAW_SCHEMA = vol.Schema(
    {
        vol.Required("code"): cv.string,
        vol.Optional("repeats"): vol.Coerce(int),
        vol.Optional("gap"): vol.Coerce(int),
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_SEND_CODE_SCHEMA = vol.Schema(
    {
        vol.Required("code"): vol.Coerce(int),
        vol.Optional("bits", default=24): vol.Coerce(int),
        vol.Optional("protocol", default=1): vol.Coerce(int),
        vol.Optional("pulse_length", default=350): vol.Coerce(int),
        vol.Optional("repeats"): vol.Coerce(int),
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_SET_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional("frequency"): vol.Coerce(float),
        vol.Optional("modulation"): cv.string,
        vol.Optional("bandwidth"): vol.Coerce(float),
        vol.Optional("tx_power"): vol.Coerce(int),
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_LEARN_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("repeats"): vol.Coerce(int),
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_DELETE_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_PING_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
    }
)


def _average_rf_samples(samples: list[str]) -> str:
    """Average raw pulse trains across multiple captures."""
    parsed = []
    for s in samples:
        try:
            pulses = [int(x.strip()) for x in s.split(",") if x.strip()]
            if pulses:
                parsed.append(pulses)
        except ValueError:
            continue

    if not parsed:
        return samples[-1] if samples else ""

    most_common_len = Counter(len(p) for p in parsed).most_common(1)[0][0]
    matching = [p for p in parsed if len(p) == most_common_len]

    averaged = [
        str(round(sum(p[i] for p in matching) / len(matching)))
        for i in range(most_common_len)
    ]
    return ",".join(averaged)


def _get_entry_data(
    hass: HomeAssistant, config_entry_id: str | None = None
) -> tuple[str, dict[str, Any]]:
    """Retrieve entry data dictionary, targeting entry_id or using the first registered device."""
    domain_data = hass.data.get(DOMAIN, {})
    if not domain_data:
        raise HomeAssistantError("No ESP32 CC1101 USB device configured")

    if config_entry_id and config_entry_id in domain_data:
        return config_entry_id, domain_data[config_entry_id]

    # Default to first active entry
    for entry_id, data in domain_data.items():
        if isinstance(data, dict) and "client" in data:
            return entry_id, data

    raise HomeAssistantError("No active ESP32 CC1101 USB connection available")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ESP32 CC1101 USB from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    client = Esp32Cc1101Client(hass, entry)
    await client.async_start()

    store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry.entry_id}")
    saved_codes = await store.async_load() or {}

    entry_data: dict[str, Any] = {
        "client": client,
        "store": store,
        "saved_codes": saved_codes,
        "learning": {"active": False, "name": "", "samples": []},
        "add_button_callback": None,
    }
    hass.data[DOMAIN][entry.entry_id] = entry_data

    # Setup listener for learning mode captures
    def on_client_event(data: dict[str, Any]) -> None:
        event_type = data.get("type")
        if event_type in ("rx_code", "rx_raw"):
            learning = entry_data.get("learning")
            if learning and learning.get("active"):
                raw_code = client.last_code
                if raw_code:
                    hass.async_create_task(_handle_learning_sample(hass, entry.entry_id, raw_code))

    client.register_listener(on_client_event)

    # Listen for options flow changes
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    # Register services once
    if not hass.services.has_service(DOMAIN, "send_raw"):

        async def async_send_raw_service(call: ServiceCall) -> None:
            _, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            code = call.data["code"]
            repeats = call.data.get("repeats")
            gap = call.data.get("gap", 8250)
            await data["client"].async_send_raw(code, repeats=repeats, gap=gap)

        async def async_send_code_service(call: ServiceCall) -> None:
            _, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            code = call.data["code"]
            bits = call.data.get("bits", 24)
            protocol = call.data.get("protocol", 1)
            pulse_len = call.data.get("pulse_length", 350)
            repeats = call.data.get("repeats")
            await data["client"].async_send_code(
                code=code,
                bits=bits,
                protocol=protocol,
                pulse_length=pulse_len,
                repeats=repeats,
            )

        async def async_set_config_service(call: ServiceCall) -> None:
            _, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            freq = call.data.get("frequency")
            mod = call.data.get("modulation")
            bw = call.data.get("bandwidth")
            pa = call.data.get("tx_power")
            await data["client"].async_set_config(freq=freq, mod=mod, bw=bw, pa=pa)

        async def async_learn_command_service(call: ServiceCall) -> None:
            entry_id, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            name = call.data["name"].strip()
            if not name:
                raise HomeAssistantError("Command name cannot be empty")

            data["learning"] = {
                "active": True,
                "name": name,
                "samples": [],
            }
            persistent_notification.async_create(
                hass,
                f"Ready to learn **{name}**.\n\n"
                f"Please press the button on your physical remote **{LEARN_STEPS} times** "
                f"(pause 1 second between presses).",
                title=f"Learning RF Code: {name} (0/{LEARN_STEPS})",
                notification_id=f"{DOMAIN}_learning_{entry_id}",
            )

        async def async_send_command_service(call: ServiceCall) -> None:
            _, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            name = call.data["name"].strip()
            saved = data.get("saved_codes", {})
            if name not in saved:
                raise HomeAssistantError(f"Saved RF command '{name}' not found")
            repeats = call.data.get("repeats")
            await data["client"].async_send_raw(saved[name], repeats=repeats)

        async def async_delete_command_service(call: ServiceCall) -> None:
            _, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            name = call.data["name"].strip()
            saved = data.get("saved_codes", {})
            if name in saved:
                del saved[name]
                await data["store"].async_save(saved)
            else:
                raise HomeAssistantError(f"Saved RF command '{name}' not found")

        async def async_ping_service(call: ServiceCall) -> None:
            _, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            await data["client"].async_ping()

        hass.services.async_register(
            DOMAIN, "send_raw", async_send_raw_service, schema=SERVICE_SEND_RAW_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, "send_code", async_send_code_service, schema=SERVICE_SEND_CODE_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, "set_config", async_set_config_service, schema=SERVICE_SET_CONFIG_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, "learn_command", async_learn_command_service, schema=SERVICE_LEARN_COMMAND_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, "send_command", async_send_command_service, schema=SERVICE_SEND_COMMAND_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, "delete_command", async_delete_command_service, schema=SERVICE_DELETE_COMMAND_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, "ping", async_ping_service, schema=SERVICE_PING_SCHEMA
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _handle_learning_sample(hass: HomeAssistant, entry_id: str, raw_code: str) -> None:
    """Handle individual capture during multi-step learning mode."""
    entry_data = hass.data[DOMAIN].get(entry_id)
    if not entry_data:
        return

    learning = entry_data.get("learning")
    if not learning or not learning.get("active"):
        return

    learning["samples"].append(raw_code)
    count = len(learning["samples"])
    cmd_name = learning["name"]

    if count < LEARN_STEPS:
        persistent_notification.async_create(
            hass,
            f"Captured press **{count} of {LEARN_STEPS}** for `{cmd_name}`.\n\n"
            f"Please press the physical remote button again ({LEARN_STEPS - count} remaining).",
            title=f"Learning RF Code: {cmd_name} ({count}/{LEARN_STEPS})",
            notification_id=f"{DOMAIN}_learning_{entry_id}",
        )
    else:
        final_code = _average_rf_samples(learning["samples"])
        learning["active"] = False
        entry_data["saved_codes"][cmd_name] = final_code
        await entry_data["store"].async_save(entry_data["saved_codes"])

        if entry_data.get("add_button_callback"):
            entry_data["add_button_callback"](cmd_name)

        persistent_notification.async_create(
            hass,
            f"Successfully learned and saved **{cmd_name}** after {LEARN_STEPS} presses!\n\n"
            f"**Averaged Code:** `{final_code}`\n\n"
            f"A button entity for **{cmd_name}** has been added to your dashboard.",
            title=f"RF Code Saved: {cmd_name}",
            notification_id=f"{DOMAIN}_learning_{entry_id}",
        )


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update and push settings directly to CC1101."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not entry_data:
        return

    client: Esp32Cc1101Client = entry_data["client"]
    new_freq = entry.options.get(CONF_FREQUENCY, entry.data.get(CONF_FREQUENCY, DEFAULT_FREQUENCY))
    new_mod = entry.options.get(CONF_MODULATION, entry.data.get(CONF_MODULATION, DEFAULT_MODULATION))
    new_bw = entry.options.get(CONF_BANDWIDTH, entry.data.get(CONF_BANDWIDTH, DEFAULT_BANDWIDTH))
    new_pa = entry.options.get(CONF_TX_POWER, entry.data.get(CONF_TX_POWER, DEFAULT_TX_POWER))
    new_repeats = entry.options.get(CONF_REPEATS, entry.data.get(CONF_REPEATS, DEFAULT_REPEATS))

    client.repeats = new_repeats
    await client.async_set_config(freq=new_freq, mod=new_mod, bw=new_bw, pa=new_pa)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if entry_data and "client" in entry_data:
            await entry_data["client"].async_stop()
    return unload_ok
