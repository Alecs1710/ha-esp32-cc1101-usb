from datetime import datetime
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
        self._attr_native_value = "No signal"
        self._raw_code = ""
        self._pulses_count = 0

    @property
    def extra_state_attributes(self):
        return {
            "raw_code": self._raw_code,
            "pulses_count": self._pulses_count,
        }

    def update_code(self, code: str):
        self._raw_code = code
        self._pulses_count = len(code.split(",")) if code else 0
        if len(code) > 250:
            self._attr_native_value = f"Received ({self._pulses_count} pulses) at {datetime.now().strftime('%H:%M:%S')}"
        else:
            self._attr_native_value = code
        self.async_write_ha_state()
