"""Support for ESP32 CC1101 USB RF selectable entities."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import Esp32Cc1101Client
from .const import (
    CONF_PORT,
    DOMAIN,
    FREQUENCY_PRESETS,
    FREQUENCY_PRESET_MAP,
    MODULATION_OPTIONS,
    PRESET_CUSTOM,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ESP32 CC1101 select entities."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client: Esp32Cc1101Client = entry_data["client"]

    entities = [
        Esp32FrequencySelect(entry, client),
        Esp32ModulationSelect(entry, client),
    ]
    async_add_entities(entities)


class Esp32FrequencySelect(SelectEntity):
    """Selector for radio frequency presets."""

    _attr_has_entity_name = True
    _attr_name = "Frequency Preset"
    _attr_options = FREQUENCY_PRESETS
    _attr_icon = "mdi:sine-wave"

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_freq_preset_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ESP32 CC1101 USB ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="CC1101 + ESP32-WROOM-32",
            sw_version=client.version,
        )

    async def async_added_to_hass(self) -> None:
        """Register client listener."""
        self.async_on_remove(
            self.client.register_listener(self._handle_client_update)
        )

    def _handle_client_update(self, data: dict[str, Any]) -> None:
        if data.get("type") in ("telemetry", "connection"):
            self.async_write_ha_state()

    @property
    def current_option(self) -> str:
        """Return the current selected frequency preset."""
        cur = self.client.frequency
        for name, val in FREQUENCY_PRESET_MAP.items():
            if abs(val - cur) < 0.05:
                return name
        return PRESET_CUSTOM

    async def async_select_option(self, option: str) -> None:
        """Change the radio frequency preset."""
        if option in FREQUENCY_PRESET_MAP:
            target_freq = FREQUENCY_PRESET_MAP[option]
            await self.client.async_set_config(freq=target_freq)
            self.async_write_ha_state()


class Esp32ModulationSelect(SelectEntity):
    """Selector for radio modulation modes."""

    _attr_has_entity_name = True
    _attr_name = "Modulation"
    _attr_options = MODULATION_OPTIONS
    _attr_icon = "mdi:waveform"

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_modulation_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ESP32 CC1101 USB ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="CC1101 + ESP32-WROOM-32",
            sw_version=client.version,
        )

    async def async_added_to_hass(self) -> None:
        """Register client listener."""
        self.async_on_remove(
            self.client.register_listener(self._handle_client_update)
        )

    def _handle_client_update(self, data: dict[str, Any]) -> None:
        if data.get("type") in ("telemetry", "connection"):
            self.async_write_ha_state()

    @property
    def current_option(self) -> str:
        """Return the current selected modulation mode."""
        return self.client.modulation

    async def async_select_option(self, option: str) -> None:
        """Change the radio modulation mode."""
        if option in MODULATION_OPTIONS:
            await self.client.async_set_config(mod=option)
            self.async_write_ha_state()
