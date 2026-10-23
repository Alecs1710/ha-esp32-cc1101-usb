"""Support for ESP32 CC1101 USB RF adjustable numeric entities."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import Esp32Cc1101Client
from .const import CONF_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ESP32 CC1101 number entities."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client: Esp32Cc1101Client = entry_data["client"]

    entities = [
        Esp32FrequencyNumber(entry, client),
        Esp32TxPowerNumber(entry, client),
        Esp32RepeatsNumber(entry, client),
        Esp32SensitivityNumber(entry, client),
        Esp32MinRepeatsNumber(entry, client),
    ]
    async_add_entities(entities)


class Esp32FrequencyNumber(NumberEntity):
    """Numeric input for fine-tuning frequency in MHz."""

    _attr_has_entity_name = True
    _attr_name = "Frequency"
    _attr_native_min_value = 300.0
    _attr_native_max_value = 928.0
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = "MHz"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:radio-tower"

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_frequency_{entry.entry_id}"
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
        """Return the current configured frequency."""
        return round(self.client.frequency, 2)

    async def async_set_native_value(self, value: float) -> None:
        """Set a new radio frequency."""
        await self.client.async_set_config(freq=value)
        self.async_write_ha_state()


class Esp32TxPowerNumber(NumberEntity):
    """Numeric slider for transmit power in dBm."""

    _attr_has_entity_name = True
    _attr_name = "Transmit Power"
    _attr_native_min_value = -30
    _attr_native_max_value = 10
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "dBm"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:signal-variant"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_tx_power_{entry.entry_id}"
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
    def native_value(self) -> int:
        """Return the current transmit power."""
        return self.client.tx_power

    async def async_set_native_value(self, value: float) -> None:
        """Set a new transmit power level."""
        await self.client.async_set_config(pa=int(value))
        self.async_write_ha_state()


class Esp32RepeatsNumber(NumberEntity):
    """Numeric input for default transmission repeats."""

    _attr_has_entity_name = True
    _attr_name = "Transmit Repeats"
    _attr_native_min_value = 1
    _attr_native_max_value = 25
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:repeat"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_repeats_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ESP32 CC1101 USB ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="CC1101 + ESP32-WROOM-32",
            sw_version=client.version,
        )

    @property
    def native_value(self) -> int:
        """Return the current repeat count."""
        return self.client.repeats

    async def async_set_native_value(self, value: float) -> None:
        """Set a new default repetition count."""
        self.client.repeats = int(value)
        self.async_write_ha_state()


class Esp32SensitivityNumber(NumberEntity):
    """Numeric slider for receiver squelch sensitivity in dBm."""

    _attr_has_entity_name = True
    _attr_name = "Receiver Sensitivity (Squelch)"
    _attr_native_min_value = -95
    _attr_native_max_value = -40
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "dBm"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:filter-variant"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_squelch_{entry.entry_id}"
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
    def native_value(self) -> int:
        """Return the current squelch threshold."""
        return self.client.min_rssi

    async def async_set_native_value(self, value: float) -> None:
        """Set a new squelch threshold."""
        await self.client.async_set_config(min_rssi=int(value))
        self.async_write_ha_state()


class Esp32MinRepeatsNumber(NumberEntity):
    """Numeric input for minimum consecutive repeats required for raw pulses."""

    _attr_has_entity_name = True
    _attr_name = "Minimum Repeats Filter"
    _attr_native_min_value = 1
    _attr_native_max_value = 5
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:repeat-once"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_min_repeats_{entry.entry_id}"
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
    def native_value(self) -> int:
        """Return the current minimum repeat requirement."""
        return self.client.min_repeats

    async def async_set_native_value(self, value: float) -> None:
        """Set new minimum repeat requirement."""
        await self.client.async_set_config(min_repeats=int(value))
        self.async_write_ha_state()

