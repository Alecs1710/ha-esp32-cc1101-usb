"""Support for ESP32 CC1101 RF Ceiling Fan entities with assumed state and speeds."""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.fan import (
    FanEntity,
    FanEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import Esp32Cc1101Client
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

FAN_KEYWORDS = ("fan", "vent", "ventilador")
SPEED_KEYWORDS = ("speed", "velocidad", "vel")
OFF_KEYWORDS = ("off", "apagar", "apagado", "stop", "parar")


def _is_speed_command(cmd_name: str) -> bool:
    """Check if command name indicates a fan speed."""
    c = cmd_name.strip().lower()
    if any(k in c for k in SPEED_KEYWORDS):
        return True
    if re.match(r"^v?\d+$", c):
        return True
    return False


def _is_off_command(cmd_name: str) -> bool:
    """Check if command name indicates fan off."""
    c = cmd_name.strip().lower()
    return any(k in c for k in OFF_KEYWORDS)


def _get_device_fan_commands(
    saved_codes: dict[str, Any], dev_name: str
) -> tuple[dict[str, str], str | None]:
    """Extract speed commands and off command for a specific device."""
    speed_cmds: dict[str, str] = {}
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

        if _is_off_command(curr_cmd):
            off_cmd = full_key
        elif _is_speed_command(curr_cmd):
            speed_cmds[curr_cmd] = full_key

    # Sort speed commands naturally (e.g. Speed 1, Speed 2, Speed 3)
    def natural_sort_key(s: str) -> int:
        match = re.search(r"\d+", s)
        return int(match.group()) if match else 999

    sorted_speeds = dict(sorted(speed_cmds.items(), key=lambda item: natural_sort_key(item[0])))
    return sorted_speeds, off_cmd


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up ESP32 CC1101 Fan entities."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client: Esp32Cc1101Client = entry_data["client"]
    saved_codes: dict[str, Any] = entry_data.get("saved_codes", {})

    # Discover all devices that qualify as a fan
    all_devs: set[str] = set(saved_codes.get("__devices__", []))
    for k, v in saved_codes.items():
        if k.startswith("__"):
            continue
        if isinstance(v, dict) and v.get("device"):
            all_devs.add(v["device"].strip())
        elif " - " in k:
            all_devs.add(k.split(" - ", 1)[0].strip())

    fan_entities: dict[str, Esp32Cc1101Fan] = {}

    for dev in all_devs:
        speeds, off_cmd = _get_device_fan_commands(saved_codes, dev)
        # Device is a fan if it has speeds or has fan in device name
        if speeds or any(k in dev.lower() for k in FAN_KEYWORDS):
            fan = Esp32Cc1101Fan(entry, client, dev, entry_data, speeds, off_cmd)
            fan_entities[dev.lower()] = fan

    if fan_entities:
        async_add_entities(list(fan_entities.values()))

    # Register dynamic update callback when new commands are learned
    def add_or_update_fan(full_cmd: str) -> None:
        saved = entry_data.get("saved_codes", {})
        code_data = saved.get(full_cmd)
        dev = ""
        if isinstance(code_data, dict) and code_data.get("device"):
            dev = code_data["device"].strip()
        elif " - " in full_cmd:
            dev = full_cmd.split(" - ", 1)[0].strip()

        if not dev:
            return

        speeds, off_cmd = _get_device_fan_commands(saved, dev)
        dev_lower = dev.lower()

        if dev_lower in fan_entities:
            fan_entities[dev_lower].update_commands(speeds, off_cmd)
        elif speeds or any(k in dev.lower() for k in FAN_KEYWORDS):
            fan = Esp32Cc1101Fan(entry, client, dev, entry_data, speeds, off_cmd)
            fan_entities[dev_lower] = fan
            async_add_entities([fan])

    entry_data["update_fan_commands_callback"] = add_or_update_fan

    # Register state listener for passive RF remote synchronisation
    state_listeners = entry_data.setdefault("state_listeners", {})

    def on_rf_received(dev_name: str, cmd_name: str) -> None:
        fan = fan_entities.get(dev_name.lower())
        if fan:
            fan.handle_passive_rf(cmd_name)

    state_listeners["fan"] = on_rf_received


class Esp32Cc1101Fan(FanEntity):
    """Ceiling fan representation with speed presets and optimistic state."""

    _attr_has_entity_name = True
    _attr_name = None  # Inherits device name (e.g. 'Ventilador Techo')
    _attr_icon = "mdi:fan"
    _attr_assumed_state = True

    def __init__(
        self,
        entry: ConfigEntry,
        client: Esp32Cc1101Client,
        device_name: str,
        entry_data: dict[str, Any],
        speed_commands: dict[str, str],
        off_command: str | None,
    ) -> None:
        self.entry = entry
        self.client = client
        self.device_name = device_name
        self.entry_data = entry_data
        self._speed_commands = speed_commands
        self._off_command = off_command

        slug_dev = device_name.lower().replace(" ", "_")
        self._attr_unique_id = f"esp32_cc1101_fan_{entry.entry_id}_{slug_dev}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_dev_{slug_dev}")},
            name=device_name,
            manufacturer="ESP32 CC1101 RF",
            model="Learned RF Device",
            via_device=(DOMAIN, entry.entry_id),
        )

        self._attr_is_on: bool = False
        self._attr_preset_mode: str | None = None
        self._attr_percentage: int | None = 0
        self._attr_supported_features = (
            FanEntityFeature.TURN_ON
            | FanEntityFeature.TURN_OFF
            | FanEntityFeature.SET_SPEED
            | FanEntityFeature.PRESET_MODE
        )
        self._attr_preset_modes = list(self._speed_commands.keys())

    def update_commands(
        self, speed_commands: dict[str, str], off_command: str | None
    ) -> None:
        """Update speed presets when new buttons are learned."""
        self._speed_commands = speed_commands
        self._off_command = off_command
        self._attr_preset_modes = list(self._speed_commands.keys())
        if getattr(self, "hass", None) is not None:
            self.async_write_ha_state()

    @property
    def speed_count(self) -> int:
        """Return the number of speeds the fan supports."""
        return max(len(self._attr_preset_modes), 1)

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

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan at the desired speed or last known speed."""
        if preset_mode and preset_mode in self._speed_commands:
            await self.async_set_preset_mode(preset_mode)
            return

        if percentage is not None and self._attr_preset_modes:
            await self.async_set_percentage(percentage)
            return

        # Default: turn on to last known preset or first available speed
        target_preset = self._attr_preset_mode
        if not target_preset or target_preset not in self._speed_commands:
            if self._attr_preset_modes:
                target_preset = self._attr_preset_modes[0]

        if target_preset and target_preset in self._speed_commands:
            await self.async_set_preset_mode(target_preset)
        else:
            self._attr_is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        if self._off_command:
            await self._async_transmit_command(self._off_command)

        self._attr_is_on = False
        self._attr_percentage = 0
        self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set the fan speed using preset mode."""
        if preset_mode not in self._speed_commands:
            raise HomeAssistantError(f"Unknown preset mode '{preset_mode}'")

        full_key = self._speed_commands[preset_mode]
        await self._async_transmit_command(full_key)

        self._attr_is_on = True
        self._attr_preset_mode = preset_mode
        if self._attr_preset_modes:
            idx = self._attr_preset_modes.index(preset_mode)
            self._attr_percentage = round((idx + 1) * 100 / len(self._attr_preset_modes))

        self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set fan speed via percentage slider."""
        if percentage == 0:
            await self.async_turn_off()
            return

        if not self._attr_preset_modes:
            self._attr_is_on = True
            self._attr_percentage = percentage
            self.async_write_ha_state()
            return

        # Map 1..100% to discrete preset modes
        num_presets = len(self._attr_preset_modes)
        idx = min(num_presets - 1, max(0, int((percentage - 1) / (100 / num_presets))))
        preset = self._attr_preset_modes[idx]
        await self.async_set_preset_mode(preset)

    @callback
    def handle_passive_rf(self, command: str) -> None:
        """Update state when physical remote is pressed in the room."""
        if _is_off_command(command):
            self._attr_is_on = False
            self._attr_percentage = 0
            self.async_write_ha_state()
            _LOGGER.debug("Passive RF: Fan '%s' turned OFF by physical remote", self.device_name)
            return

        for preset in self._attr_preset_modes:
            if (
                preset.lower() == command.lower()
                or preset.lower().replace(" ", "") == command.lower().replace(" ", "")
            ):
                self._attr_is_on = True
                self._attr_preset_mode = preset
                idx = self._attr_preset_modes.index(preset)
                self._attr_percentage = round((idx + 1) * 100 / len(self._attr_preset_modes))
                self.async_write_ha_state()
                _LOGGER.debug(
                    "Passive RF: Fan '%s' set to '%s' by physical remote",
                    self.device_name,
                    preset,
                )
                break
