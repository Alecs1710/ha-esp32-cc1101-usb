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
    get_preset_for_freq,
    MODULATION_OPTIONS,
    PRESET_CUSTOM,
    RX_MODE_OPTIONS,
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
        Esp32RxModeSelect(entry, client),
        Esp32DeleteSelect(hass, entry, entry_data),
    ]
    async_add_entities(entities)


class Esp32FrequencySelect(SelectEntity):
    """Selector for radio frequency."""

    _attr_has_entity_name = True
    _attr_name = "Frequency"
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
        return get_preset_for_freq(self.client.frequency)

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


class Esp32RxModeSelect(SelectEntity):
    """Selector for RF reception filter mode (Decoded Only, All, Learn)."""

    _attr_has_entity_name = True
    _attr_name = "RF Reception Mode"
    _attr_options = RX_MODE_OPTIONS
    _attr_icon = "mdi:filter-check"

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_rx_mode_{entry.entry_id}"
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
        """Return the current reception mode."""
        if self.client.rx_mode in RX_MODE_OPTIONS:
            return self.client.rx_mode
        return RX_MODE_DECODED

    async def async_select_option(self, option: str) -> None:
        """Change the reception mode."""
        if option in RX_MODE_OPTIONS:
            await self.client.async_set_config(rx_mode=option)
            self.async_write_ha_state()


class Esp32DeleteSelect(SelectEntity):
    """Selector to choose which learned RF command to delete."""

    _attr_has_entity_name = True
    _attr_name = "Delete RF Command"
    _attr_icon = "mdi:trash-can-outline"

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        self.hass = hass
        self.entry = entry
        self.entry_data = entry_data
        self._attr_unique_id = f"esp32_cc1101_delete_select_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_learned_commands")},
            name=f"ESP32 CC1101 Learned Remotes ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="RF Stored Commands",
            via_device=(DOMAIN, entry.entry_id),
        )
        self._selected = "None"
        self._update_options()

        def _refresh() -> None:
            self._update_options()
            if self._selected not in self._attr_options:
                self._selected = "None"
            self.entry_data["delete_target_name"] = self._selected
            self.async_write_ha_state()

        entry_data["update_delete_select_callback"] = _refresh
        entry_data["delete_target_name"] = self._selected

    def _update_options(self) -> None:
        saved = self.entry_data.get("saved_codes", {})
        names = set(saved.keys())

        try:
            from homeassistant.helpers import entity_registry as er
            ent_reg = er.async_get(self.hass)
            prefix = f"esp32_cc1101_cmd_{self.entry.entry_id}_"
            for ent_id, ent_entry in ent_reg.entities.items():
                if ent_entry.config_entry_id == self.entry.entry_id and ent_entry.unique_id.startswith(prefix):
                    if ent_entry.original_name:
                        names.add(ent_entry.original_name)
                    elif ent_entry.name:
                        names.add(ent_entry.name)
                    else:
                        names.add(ent_entry.unique_id[len(prefix):].replace("_", " ").title())
        except Exception as err:
            _LOGGER.debug("Could not scan entity registry in delete select: %s", err)

        self._attr_options = ["None"] + sorted(list(names))

    @property
    def current_option(self) -> str:
        return self._selected

    async def async_select_option(self, option: str) -> None:
        self._selected = option
        self.entry_data["delete_target_name"] = option
        self.async_write_ha_state()


