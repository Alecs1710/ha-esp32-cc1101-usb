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
        Esp32TxPowerNumber(entry, client),
        Esp32RepeatsNumber(entry, client),
        Esp32SensitivityNumber(entry, client),
        Esp32MinRepeatsNumber(entry, client),
    ]
    async_add_entities(entities)


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
        """Return the current configured TX power."""
        return self.client.tx_power

    async def async_set_native_value(self, value: float) -> None:
        """Set a new transmit power."""
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
        """Return current repeat count."""
        return self.client.repeats

    async def async_set_native_value(self, value: float) -> None:
        """Set default repeats."""
        self.client.repeats = int(value)
        self.async_write_ha_state()


class Esp32SensitivityNumber(NumberEntity):
    """Adjustable RSSI threshold squelch slider."""

    _attr_has_entity_name = True
    _attr_name = "Receiver Squelch"
    _attr_native_min_value = -100
    _attr_native_max_value = -10
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "dBm"
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:volume-vibrate"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_sensitivity_{entry.entry_id}"
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
        """Return the current configured squelch RSSI threshold."""
        return self.client.min_rssi

    async def async_set_native_value(self, value: float) -> None:
        """Set a new squelch threshold."""
        await self.client.async_set_config(min_rssi=int(value))
        self.async_write_ha_state()


class Esp32MinRepeatsNumber(NumberEntity):
    """Filter slider requiring N consecutive repeats before dispatch."""

    _attr_has_entity_name = True
    _attr_name = "Minimum Repeats Filter"
    _attr_native_min_value = 1
    _attr_native_max_value = 5
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:filter-outline"
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
        """Return the current configured minimum repeats."""
        return self.client.min_repeats

    async def async_set_native_value(self, value: float) -> None:
        """Set a new minimum repeats requirement."""
        await self.client.async_set_config(min_repeats=int(value))
        self.async_write_ha_state()
