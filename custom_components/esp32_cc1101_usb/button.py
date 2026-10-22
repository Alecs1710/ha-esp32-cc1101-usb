from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from . import send_rf_code
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([Esp32Cc1101RepeatButton(hass, entry.entry_id)])


class Esp32Cc1101RepeatButton(ButtonEntity):
    def __init__(self, hass: HomeAssistant, entry_id: str):
        self.hass = hass
        self._entry_id = entry_id
        self._attr_name = "ESP32 CC1101 Repeat Last Code"
        self._attr_unique_id = f"esp32_cc1101_repeat_last_code_{entry_id}"
        self._attr_icon = "mdi:access-point-network"

    async def async_press(self) -> None:
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        last_code = entry_data.get("last_code", "")
        if not last_code:
            raise HomeAssistantError("No RF code has been captured yet to repeat")
        send_rf_code(self.hass, last_code)
