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
    """Set up the ESP32 CC1101 text entities."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([Esp32LearnNameText(entry, entry_data)])


class Esp32LearnNameText(TextEntity):
    """Text entity to specify name of the next RF button to learn."""

    _attr_has_entity_name = True
    _attr_name = "New RF Button Name"
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
            count = len(entry_data.get("saved_codes", {})) + 1
            entry_data["learn_name_input"] = f"Remote Button {count}"
        self._attr_native_value = entry_data["learn_name_input"]

        # Register callback so when learning finishes, name can auto-increment
        def _update_name(new_name: str) -> None:
            self._attr_native_value = new_name
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
