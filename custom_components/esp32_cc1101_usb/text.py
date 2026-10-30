"""Text entity for specifying command name to learn."""
from __future__ import annotations

from typing import Any

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_PORT, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ESP32 CC1101 text entities (now handled via Options Flow modal wizard)."""
    return


class Esp32TargetDeviceText(TextEntity):
    """Text entity to specify or select target device name."""

    _attr_has_entity_name = True
    _attr_name = "Target Device Name"
    _attr_icon = "mdi:devices"

    def __init__(self, entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        self.entry = entry
        self.entry_data = entry_data
        self._attr_unique_id = f"esp32_cc1101_target_device_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_learner")},
            name=f"ESP32 CC1101 RF Learner ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="RF Learning Studio",
            via_device=(DOMAIN, entry.entry_id),
        )
        if "target_device_input" not in entry_data:
            entry_data["target_device_input"] = "Ceiling Fan"
        self._attr_native_value = entry_data["target_device_input"]

        def _update_dev(new_dev: str) -> None:
            self._attr_native_value = new_dev
            self.entry_data["target_device_input"] = new_dev
            if getattr(self, "hass", None) is not None:
                self.async_write_ha_state()

        entry_data["update_target_device_text_callback"] = _update_dev

    @property
    def native_value(self) -> str:
        return self.entry_data.get("target_device_input", "Ceiling Fan")

    async def async_set_value(self, value: str) -> None:
        self.entry_data["target_device_input"] = value
        self._attr_native_value = value
        self.async_write_ha_state()
        if "update_target_device_select_callback" in self.entry_data:
            try:
                self.entry_data["update_target_device_select_callback"]()
            except Exception:
                pass


class Esp32LearnNameText(TextEntity):
    """Text entity to specify name of the next RF button to learn."""

    _attr_has_entity_name = True
    _attr_name = "Command Name to Learn"
    _attr_icon = "mdi:rename-box"

    def __init__(self, entry: ConfigEntry, entry_data: dict[str, Any]) -> None:
        self.entry = entry
        self.entry_data = entry_data
        self._attr_unique_id = f"esp32_cc1101_learn_name_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_learner")},
            name=f"ESP32 CC1101 RF Learner ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="RF Learning Studio",
            via_device=(DOMAIN, entry.entry_id),
        )
        if "learn_name_input" not in entry_data:
            entry_data["learn_name_input"] = "Speed 1"
        self._attr_native_value = entry_data["learn_name_input"]

        # Register callback so when learning finishes, name can auto-increment
        def _update_name(new_name: str) -> None:
            self._attr_native_value = new_name
            if getattr(self, "hass", None) is not None:
                self.async_write_ha_state()

        entry_data["update_learn_name_callback"] = _update_name

    @property
    def native_value(self) -> str:
        """Return the current text value."""
        return self.entry_data.get("learn_name_input", "")

    async def async_set_value(self, value: str) -> None:
        """Set a new command name."""
        self.entry_data["learn_name_input"] = value
        self._attr_native_value = value
        self.async_write_ha_state()
