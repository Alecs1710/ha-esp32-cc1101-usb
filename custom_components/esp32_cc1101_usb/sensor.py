from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    sensor = Esp32Cc1101Sensor(entry.entry_id)

    def update_sensor(code: str):
        sensor.update_code(code)

    data["listener"] = update_sensor
    async_add_entities([sensor])

class Esp32Cc1101Sensor(SensorEntity):
    def __init__(self, entry_id: str):
        self._entry_id = entry_id
        self._attr_name = "ESP32 CC1101 Last Code"
        self._attr_unique_id = f"esp32_cc1101_last_code_{entry_id}"
        self._attr_icon = "mdi:radio-tower"
        self._state = "No signal"

    @property
    def state(self):
        return self._state

    def update_code(self, code: str):
        self._state = code
        self.async_write_ha_state()
