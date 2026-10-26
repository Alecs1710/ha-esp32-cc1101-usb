"""Support for ESP32 CC1101 USB RF sensors."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import Esp32Cc1101Client
from .const import CONF_PORT, DOMAIN, LEARN_STEPS

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ESP32 CC1101 sensor entities."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client: Esp32Cc1101Client = entry_data["client"]

    entities: list[SensorEntity] = [
        Esp32LastCodeSensor(entry, client),
        Esp32RssiSensor(entry, client),
        Esp32ActiveFrequencySensor(entry, client),
        Esp32RadioStatusSensor(entry, client),
        Esp32LearningSensor(hass, entry, client),
    ]

    saved_codes: dict[str, Any] = entry_data.get("saved_codes", {})
    device_names: set[str] = set(saved_codes.get("__devices__", []))
    for k, v in saved_codes.items():
        if k.startswith("__"):
            continue
        if isinstance(v, dict) and v.get("device"):
            device_names.add(v["device"].strip())
        elif " - " in k:
            device_names.add(k.split(" - ", 1)[0].strip())

    created_device_sensors: dict[str, Esp32DeviceLastButtonSensor] = {}
    for dev_name in sorted(list(device_names)):
        if not dev_name:
            continue
        dev_sensor = Esp32DeviceLastButtonSensor(entry, dev_name, entry_data)
        entities.append(dev_sensor)
        created_device_sensors[dev_name.lower()] = dev_sensor

    async_add_entities(entities)

    def add_device_sensor(new_dev: str) -> None:
        if not new_dev:
            return
        dev_lower = new_dev.lower()
        if dev_lower not in created_device_sensors:
            dev_sensor = Esp32DeviceLastButtonSensor(entry, new_dev, entry_data)
            created_device_sensors[dev_lower] = dev_sensor
            async_add_entities([dev_sensor])

    async def remove_device_sensor(dev_to_remove: str) -> None:
        if not dev_to_remove:
            return
        dev_lower = dev_to_remove.lower()
        sensor = created_device_sensors.pop(dev_lower, None)
        if sensor and getattr(sensor, "hass", None) is not None:
            try:
                await sensor.async_remove()
            except Exception as err:
                _LOGGER.debug("Could not remove device sensor for '%s': %s", dev_to_remove, err)

    entry_data["add_device_sensor_callback"] = add_device_sensor
    entry_data["remove_device_sensor_callback"] = remove_device_sensor


class Esp32LastCodeSensor(SensorEntity):
    """Sensor displaying the last captured RF signal."""

    _attr_has_entity_name = True
    _attr_name = "Last Code"
    _attr_icon = "mdi:radio-tower"
    _attr_force_update = True

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_last_code_{entry.entry_id}"
        self._attr_native_value = "No signal"
        self._timestamp = ""
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
        event_type = data.get("type")
        if event_type in ("rx_code", "rx_raw"):
            self._timestamp = datetime.now().strftime("%H:%M:%S")
            if self.client.last_decoded_code is not None:
                self._attr_native_value = (
                    f"Code: {self.client.last_decoded_code} "
                    f"(P{self.client.last_protocol}, {self.client.last_bit_length}b)"
                )
            elif self.client.last_code:
                pulses = len(self.client.last_code.split(","))
                self._attr_native_value = f"Captured ({pulses} pulses) at {self._timestamp}"
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return rich signal telemetry attributes."""
        raw = self.client.last_code
        pulses_count = len(raw.split(",")) if raw else 0
        return {
            "raw_code": raw,
            "decoded_code": self.client.last_decoded_code,
            "protocol": self.client.last_protocol,
            "bits": self.client.last_bit_length,
            "pulse_length_us": self.client.last_pulse_length,
            "rssi_dbm": self.client.last_rssi,
            "frequency_mhz": self.client.frequency,
            "pulses_count": pulses_count,
            "timestamp": self._timestamp,
        }


class Esp32RssiSensor(SensorEntity):
    """Sensor reporting the received signal strength (RSSI)."""

    _attr_has_entity_name = True
    _attr_name = "Signal RSSI"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "dBm"

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_rssi_{entry.entry_id}"
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
        if self.client.last_rssi is not None or self.client.rssi is not None:
            self.async_write_ha_state()

    @property
    def native_value(self) -> int | None:
        """Return the last measured RSSI."""
        return self.client.last_rssi if self.client.last_rssi is not None else self.client.rssi


class Esp32ActiveFrequencySensor(SensorEntity):
    """Sensor displaying the currently tuned radio frequency."""

    _attr_has_entity_name = True
    _attr_name = "Active Frequency"
    _attr_native_unit_of_measurement = "MHz"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:sine-wave"

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_active_freq_{entry.entry_id}"
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
    def native_value(self) -> float:
        """Return the active tuned frequency."""
        return round(self.client.frequency, 2)


class Esp32RadioStatusSensor(SensorEntity):
    """Diagnostic sensor reporting hardware radio state."""

    _attr_has_entity_name = True
    _attr_name = "Radio State"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chip"

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_radio_status_{entry.entry_id}"
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
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        """Return human-readable status string."""
        if not self.client.connected:
            return "Disconnected"
        if self.client.radio_state == 13:
            return "Listening (RX)"
        if self.client.radio_state == 19:
            return "Transmitting (TX)"
        if self.client.radio_state == 1:
            return "Idle"
        return f"State {self.client.radio_state}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes."""
        return {
            "usb_connected": self.client.connected,
            "chip_detected": self.client.chip_detected,
            "firmware_version": self.client.version,
            "radio_state_code": self.client.radio_state,
            "bandwidth_khz": self.client.bandwidth,
            "tx_power_dbm": self.client.tx_power,
            "modulation": self.client.modulation,
        }


class Esp32LearningSensor(SensorEntity):
    """Sensor reporting the status of multi-step code learning."""

    _attr_has_entity_name = True
    _attr_name = "Learning Status"
    _attr_icon = "mdi:remote"

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: Esp32Cc1101Client
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_learning_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ESP32 CC1101 USB ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="CC1101 + ESP32-WROOM-32",
            sw_version=client.version,
        )

    async def async_added_to_hass(self) -> None:
        """Register client listener and learning update callback."""
        self.async_on_remove(
            self.client.register_listener(self._handle_client_update)
        )
        entry_data = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
        entry_data["update_learning_sensor_callback"] = self.async_write_ha_state

    def _handle_client_update(self, data: dict[str, Any]) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        """Return learning state."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
        learning = entry_data.get("learning", {})
        if learning.get("status"):
            return str(learning["status"])

        name = learning.get("name", "")
        dev = learning.get("device", "")
        label = f"{dev} - {name}" if dev else name
        phase = learning.get("phase", "idle")

        if learning.get("active"):
            count = len(learning.get("samples", []))
            if phase == "recording":
                return f"Grabando '{label}' (Mantén 5s pulsado - {count} ráfagas)"
            if phase == "verifying":
                return f"Verificando '{label}' (Pulsa 1 vez para confirmar)"
            return f"Aprendiendo '{label}'"

        if phase == "done":
            return f"Verificado y guardado '{label}'"
        if phase == "failed":
            return f"Fallo al capturar '{label}'"
        if phase == "timeout":
            return f"Tiempo agotado en verificación '{label}'"

        saved = entry_data.get("saved_codes", {})
        saved_commands = [k for k in saved if not k.startswith("__")]
        return f"Inactivo ({len(saved_commands)} guardados)"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return learning metadata."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
        saved = entry_data.get("saved_codes", {})
        learning = entry_data.get("learning", {})
        saved_commands = [k for k in saved if not k.startswith("__")]
        return {
            "learning_active": learning.get("active", False),
            "learning_phase": learning.get("phase", "idle"),
            "target_command": learning.get("name", ""),
            "target_device": learning.get("device", ""),
            "bursts_captured": len(learning.get("samples", [])),
            "candidate_desc": learning.get("candidate_desc", ""),
            "saved_commands_count": len(saved_commands),
            "saved_commands": saved_commands,
        }


class Esp32DeviceLastButtonSensor(SensorEntity):
    """Sensor displaying which button of this device was last pressed over the air."""

    _attr_has_entity_name = True
    _attr_name = "Last Button Pressed"
    _attr_icon = "mdi:remote"
    _attr_force_update = True

    def __init__(
        self,
        entry: ConfigEntry,
        device_name: str,
        entry_data: dict[str, Any],
    ) -> None:
        self.entry = entry
        self._device_name = device_name
        self._entry_data = entry_data
        self._last_button = "None"
        self._last_rssi: int | None = None
        self._last_code: Any = None
        self._reception_count = 0
        self._last_received_time = "Never"

        slug_dev = device_name.lower().replace(" ", "_")
        self._attr_unique_id = f"esp32_cc1101_dev_last_btn_{entry.entry_id}_{slug_dev}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_dev_{slug_dev}")},
            name=device_name,
            manufacturer="ESP32 CC1101 RF",
            model="Learned RF Device",
            via_device=(DOMAIN, entry.entry_id),
        )
        self._attr_native_value = "None"

    async def async_added_to_hass(self) -> None:
        """Register listener for passive over-the-air RF button presses."""
        listeners = self._entry_data.setdefault("device_button_listeners", {})
        listeners[self._device_name.lower()] = self.on_button_received

    def on_button_received(self, cmd_name: str, rssi: int | None, code: Any) -> None:
        """Handle signal received over the air for this device."""
        self._last_button = cmd_name
        self._last_rssi = rssi
        self._last_code = code
        self._reception_count += 1
        self._last_received_time = datetime.now().strftime("%H:%M:%S")
        self._attr_native_value = cmd_name
        if getattr(self, "hass", None) is not None:
            self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        """Return the name of the last button pressed."""
        return self._attr_native_value

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return rich metadata about the last over-the-air button capture."""
        return {
            "device": self._device_name,
            "last_button": self._last_button,
            "rssi_dbm": self._last_rssi,
            "code": self._last_code,
            "reception_count": self._reception_count,
            "last_received": self._last_received_time,
        }

