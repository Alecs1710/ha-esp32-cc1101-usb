"""Support for ESP32 CC1101 USB RF as a Home Assistant Remote Entity."""

from __future__ import annotations

from collections.abc import Iterable
import json
import logging
from typing import Any

from homeassistant.components.remote import (
    ATTR_COMMAND,
    ATTR_NUM_REPEATS,
    RemoteEntity,
    RemoteEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import Esp32Cc1101Client
from .const import CONF_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ESP32 CC1101 remote entity."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client: Esp32Cc1101Client = entry_data["client"]
    async_add_entities([Esp32Cc1101Remote(hass, entry, client)])


class Esp32Cc1101Remote(RemoteEntity):
    """Representation of an ESP32 CC1101 RF Transceiver Remote."""

    _attr_has_entity_name = True
    _attr_name = "Transceiver"
    _attr_supported_features = (
        RemoteEntityFeature.LEARN_COMMAND | RemoteEntityFeature.DELETE_COMMAND
    )

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: Esp32Cc1101Client,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"esp32_cc1101_remote_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ESP32 CC1101 USB ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="CC1101 + ESP32-WROOM-32",
            sw_version=client.version,
        )

    @property
    def is_on(self) -> bool:
        """Return True if connected."""
        return self.client.connected

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return device state attributes."""
        return {
            "frequency_mhz": self.client.frequency,
            "modulation": self.client.modulation,
            "bandwidth_khz": self.client.bandwidth,
            "tx_power_dbm": self.client.tx_power,
            "radio_state": self.client.radio_state,
            "rssi": self.client.rssi,
            "chip_detected": self.client.chip_detected,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on radio."""
        await self.client.async_start()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off radio."""
        await self.client.async_stop()

    async def async_send_command(
        self, command: Iterable[str], **kwargs: Any
    ) -> None:
        """Send a list of RF commands."""
        num_repeats = kwargs.get(ATTR_NUM_REPEATS)
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        saved_codes = entry_data.get("saved_codes", {})

        from . import find_saved_command
        for cmd in command:
            cmd_str = str(cmd).strip()

            # Check if it's a saved command name (resilient to renaming)
            matched_key, saved = find_saved_command(self.hass, self._entry_id, cmd_str)
            if saved is not None:
                if isinstance(saved, dict) and "code" in saved:
                    code_val = saved["code"]
                    if saved.get("type") == "rolling_code":
                        delta = saved.get("delta", 15)
                        next_code = code_val + delta
                        counter = ((next_code & 0xFF) >> 4) & 0x0F
                        if counter > 7:
                            next_code = (saved.get("base_id", 0) << 8) | 0x07
                        saved["code"] = next_code
                        self.hass.async_create_task(
                            self._entry_data["store"].async_save(saved_codes)
                        )
                        code_val = next_code
                    await self.client.async_send_code(
                        code=code_val,
                        bits=saved.get("bits", 24),
                        protocol=saved.get("protocol", 1),
                        pulse_length=saved.get("pulse_length", 350),
                        repeats=num_repeats,
                    )
                elif isinstance(saved, dict) and "raw" in saved:
                    await self.client.async_send_raw(saved["raw"], repeats=num_repeats)
                elif isinstance(saved, str):
                    await self.client.async_send_raw(saved, repeats=num_repeats)
                continue

            # Check if it's a JSON command (e.g. {"code": 12345, "protocol": 1})
            if cmd_str.startswith("{") and cmd_str.endswith("}"):
                try:
                    payload = json.loads(cmd_str)
                    if "code" in payload:
                        await self.client.async_send_code(
                            code=int(payload["code"]),
                            bits=int(payload.get("bits", 24)),
                            protocol=int(payload.get("protocol", 1)),
                            pulse_length=int(payload.get("pulse_length", 350)),
                            repeats=num_repeats or payload.get("repeats"),
                        )
                        continue
                    if "pulses" in payload:
                        await self.client.async_send_raw(
                            payload["pulses"],
                            repeats=num_repeats or payload.get("repeats"),
                            gap=payload.get("gap", 8250),
                        )
                        continue
                except (json.JSONDecodeError, ValueError) as err:
                    _LOGGER.warning("Could not parse JSON RF command '%s': %s", cmd_str, err)

            # Check if it's a raw pulse string (e.g. "250,750,250...")
            if "," in cmd_str:
                await self.client.async_send_raw(cmd_str, repeats=num_repeats)
                continue

            # Check if it's a numeric code (standard 24-bit RCSwitch protocol 1)
            if cmd_str.isdigit():
                await self.client.async_send_code(
                    code=int(cmd_str),
                    bits=24,
                    protocol=1,
                    pulse_length=350,
                    repeats=num_repeats,
                )
                continue

            raise HomeAssistantError(
                f"Unknown RF command format or missing stored command: '{cmd_str}'"
            )

    async def async_learn_command(self, **kwargs: Any) -> None:
        """Put remote into learning mode for specified command names."""
        commands = kwargs.get(ATTR_COMMAND, [])
        if not commands:
            raise HomeAssistantError("Must specify at least one command name to learn")

        cmd_name = str(commands[0]).strip()
        from . import async_start_learning_flow
        await async_start_learning_flow(self.hass, self._entry_id, cmd_name)

    async def async_delete_command(self, **kwargs: Any) -> None:
        """Delete stored commands from persistent storage."""
        commands = kwargs.get(ATTR_COMMAND, [])
        from . import async_delete_command_flow
        for cmd in commands:
            cmd_name = str(cmd).strip()
            await async_delete_command_flow(self.hass, self._entry_id, cmd_name)
