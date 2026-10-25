"""Support for ESP32 CC1101 RF Light entities with assumed state."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import Esp32Cc1101Client
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

LIGHT_KEYWORDS = ("light", "luz", "lamp", "iluminacion")
ON_KEYWORDS = ("on", "encender", "activar")
OFF_KEYWORDS = ("off", "apagar", "desactivar")


def _get_device_light_commands(
    saved_codes: dict[str, Any], dev_name: str
) -> tuple[str | None, str | None, str | None]:
    """Find toggle, on, and off commands for a device light."""
    toggle_cmd: str | None = None
    on_cmd: str | None = None
    off_cmd: str | None = None

    for full_key, val in saved_codes.items():
        if full_key.startswith("__"):
            continue
        curr_dev = ""
        curr_cmd = full_key
        if isinstance(val, dict) and val.get("device"):
            curr_dev = val["device"].strip()
            curr_cmd = val.get("command", full_key).strip()
        elif " - " in full_key:
            curr_dev, curr_cmd = full_key.split(" - ", 1)
            curr_dev = curr_dev.strip()
            curr_cmd = curr_cmd.strip()

        if curr_dev.lower() != dev_name.lower():
            continue

        c = curr_cmd.strip().lower()
        if not any(k in c for k in LIGHT_KEYWORDS):
            continue

        if any(k in c for k in OFF_KEYWORDS):
            off_cmd = full_key
        elif any(k in c for k in ON_KEYWORDS):
            on_cmd = full_key
        else:
            toggle_cmd = full_key

    return toggle_cmd, on_cmd, off_cmd


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ESP32 CC1101 Light entities."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client: Esp32Cc1101Client = entry_data["client"]
    saved_codes: dict[str, Any] = entry_data.get("saved_codes", {})

    all_devs: set[str] = set(saved_codes.get("__devices__", []))
    for k, v in saved_codes.items():
        if k.startswith("__"):
            continue
        if isinstance(v, dict) and v.get("device"):
            all_devs.add(v["device"].strip())
        elif " - " in k:
            all_devs.add(k.split(" - ", 1)[0].strip())

    light_entities: dict[str, Esp32Cc1101Light] = {}

    for dev in all_devs:
        toggle, on_cmd, off_cmd = _get_device_light_commands(saved_codes, dev)
        if toggle or on_cmd or off_cmd:
            light = Esp32Cc1101Light(entry, client, dev, entry_data, toggle, on_cmd, off_cmd)
            light_entities[dev.lower()] = light

    if light_entities:
        async_add_entities(list(light_entities.values()))

    # Register dynamic update callback when new commands are learned
    def add_or_update_light(full_cmd: str) -> None:
        saved = entry_data.get("saved_codes", {})
        code_data = saved.get(full_cmd)
        dev = ""
        if isinstance(code_data, dict) and code_data.get("device"):
            dev = code_data["device"].strip()
        elif " - " in full_cmd:
            dev = full_cmd.split(" - ", 1)[0].strip()

        if not dev:
            return

        toggle, on_c, off_c = _get_device_light_commands(saved, dev)
        dev_lower = dev.lower()

        if dev_lower in light_entities:
            light_entities[dev_lower].update_commands(toggle, on_c, off_c)
        elif toggle or on_c or off_c:
            light = Esp32Cc1101Light(entry, client, dev, entry_data, toggle, on_c, off_c)
            light_entities[dev_lower] = light
            async_add_entities([light])

    entry_data["update_light_commands_callback"] = add_or_update_light

    # Register state listener for passive RF remote synchronisation
    state_listeners = entry_data.setdefault("state_listeners", {})

    def on_rf_received(dev_name: str, cmd_name: str) -> None:
        light = light_entities.get(dev_name.lower())
        if light:
            light.handle_passive_rf(cmd_name)

    state_listeners["light"] = on_rf_received


class Esp32Cc1101Light(LightEntity):
    """Ceiling fan light or RF light switch representation with assumed state."""

    _attr_has_entity_name = True
    _attr_name = "Light"
    _attr_icon = "mdi:lightbulb"
    _attr_assumed_state = True
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(
        self,
        entry: ConfigEntry,
        client: Esp32Cc1101Client,
        device_name: str,
        entry_data: dict[str, Any],
        toggle_command: str | None,
        on_command: str | None,
        off_command: str | None,
    ) -> None:
        self.entry = entry
        self.client = client
        self.device_name = device_name
        self.entry_data = entry_data
        self._toggle_cmd = toggle_command
        self._on_cmd = on_command
        self._off_cmd = off_command

        slug_dev = device_name.lower().replace(" ", "_")
        self._attr_unique_id = f"esp32_cc1101_light_{entry.entry_id}_{slug_dev}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_dev_{slug_dev}")},
            name=device_name,
            manufacturer="ESP32 CC1101 RF",
            model="Learned RF Device",
            via_device=(DOMAIN, entry.entry_id),
        )
        self._attr_is_on: bool = False

    def update_commands(
        self,
        toggle_command: str | None,
        on_command: str | None,
        off_command: str | None,
    ) -> None:
        """Update commands when new buttons are learned."""
        self._toggle_cmd = toggle_command
        self._on_cmd = on_command
        self._off_cmd = off_command
        if getattr(self, "hass", None) is not None:
            self.async_write_ha_state()

    async def _async_transmit_command(self, full_key: str) -> None:
        """Transmit the RF command associated with a given key."""
        saved_codes = self.entry_data.get("saved_codes", {})
        code = saved_codes.get(full_key)
        if not code:
            from . import find_saved_command
            _, code = find_saved_command(self.hass, self.entry.entry_id, full_key)

        if not code:
            raise HomeAssistantError(f"RF command '{full_key}' not found in storage")

        if isinstance(code, dict) and "code" in code:
            code_val = code["code"]
            if code.get("type") == "rolling_code":
                delta = code.get("delta", 15)
                next_code = code_val + delta
                counter = ((next_code & 0xFF) >> 4) & 0x0F
                if counter > 7:
                    next_code = (code.get("base_id", 0) << 8) | 0x07
                code["code"] = next_code
                self.hass.async_create_task(
                    self.entry_data["store"].async_save(self.entry_data["saved_codes"])
                )
                code_val = next_code

            await self.client.async_send_code(
                code=code_val,
                bits=code.get("bits", 24),
                protocol=code.get("protocol", 1),
                pulse_length=code.get("pulse_length", 350),
            )
        elif isinstance(code, str):
            await self.client.async_send_raw(code)
        elif isinstance(code, dict) and "raw" in code:
            await self.client.async_send_raw(code["raw"])

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the light."""
        target_cmd = self._on_cmd or self._toggle_cmd
        if not target_cmd:
            raise HomeAssistantError(f"No light command configured for '{self.device_name}'")

        await self._async_transmit_command(target_cmd)
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        target_cmd = self._off_cmd or self._toggle_cmd
        if not target_cmd:
            raise HomeAssistantError(f"No light command configured for '{self.device_name}'")

        await self._async_transmit_command(target_cmd)
        self._attr_is_on = False
        self.async_write_ha_state()

    @callback
    def handle_passive_rf(self, command: str) -> None:
        """Update state when physical remote is pressed in the room."""
        c = command.strip().lower()
        if not any(k in c for k in LIGHT_KEYWORDS):
            return

        if any(k in c for k in OFF_KEYWORDS):
            self._attr_is_on = False
        elif any(k in c for k in ON_KEYWORDS):
            self._attr_is_on = True
        else:
            self._attr_is_on = not self._attr_is_on

        self.async_write_ha_state()
        _LOGGER.debug(
            "Passive RF: Light for '%s' set to %s by physical remote",
            self.device_name,
            self._attr_is_on,
        )
