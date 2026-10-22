from datetime import datetime
from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN, LEARN_STEPS


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    last_code_sensor = Esp32Cc1101Sensor(hass, entry.entry_id)
    learning_sensor = Esp32Cc1101LearningSensor(hass, entry.entry_id)

    def update_sensors(code: str):
        if code:
            last_code_sensor.update_code(code)
        learning_sensor.update_status()

    data["listeners"].append(update_sensors)
    async_add_entities([last_code_sensor, learning_sensor])


class Esp32Cc1101Sensor(SensorEntity):
    def __init__(self, hass: HomeAssistant, entry_id: str):
        self.hass = hass
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


class Esp32Cc1101LearningSensor(SensorEntity):
    def __init__(self, hass: HomeAssistant, entry_id: str):
        self.hass = hass
        self._entry_id = entry_id
        self._attr_name = "ESP32 CC1101 Learning Status"
        self._attr_unique_id = f"esp32_cc1101_learning_status_{entry_id}"
        self._attr_icon = "mdi:remote"
        self._attr_native_value = "Idle"

    @property
    def extra_state_attributes(self):
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        saved = entry_data.get("saved_codes", {})
        learning = entry_data.get("learning", {})
        return {
            "learning_active": learning.get("active", False),
            "target_command": learning.get("name", ""),
            "presses_captured": len(learning.get("samples", [])),
            "presses_required": LEARN_STEPS,
            "saved_commands_count": len(saved),
            "saved_commands": list(saved.keys()),
        }

    def update_status(self):
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        learning = entry_data.get("learning", {})
        if learning.get("active"):
            count = len(learning.get("samples", []))
            name = learning.get("name", "")
            self._attr_native_value = f"Learning '{name}' ({count}/{LEARN_STEPS} presses)"
        else:
            saved_count = len(entry_data.get("saved_codes", {}))
            self._attr_native_value = f"Idle ({saved_count} saved)"
        self.async_write_ha_state()
