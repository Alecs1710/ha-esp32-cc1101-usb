"""Support for ESP32 CC1101 USB RF action buttons."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
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
    """Set up the ESP32 CC1101 button entities."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client: Esp32Cc1101Client = entry_data["client"]
    saved_codes: dict[str, str] = entry_data.get("saved_codes", {})

    entities: list[ButtonEntity] = [
        Esp32RepeatButton(entry, client),
        Esp32PingButton(entry, client),
        Esp32ResetButton(entry, client),
    ]
    created_commands: set[str] = set()

    for cmd_name in saved_codes:
        entities.append(
            Esp32SavedCommandButton(entry, client, cmd_name, entry_data)
        )
        created_commands.add(cmd_name)

    async_add_entities(entities)

    def add_new_command_button(cmd_name: str) -> None:
        if cmd_name not in created_commands:
            created_commands.add(cmd_name)
            async_add_entities(
                [Esp32SavedCommandButton(entry, client, cmd_name, entry_data)]
            )

    entry_data["add_button_callback"] = add_new_command_button


class Esp32RepeatButton(ButtonEntity):
    """Button to repeat the last captured RF code."""

    _attr_has_entity_name = True
    _attr_name = "Repeat Last Code"
    _attr_icon = "mdi:replay"

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_repeat_last_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ESP32 CC1101 USB ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="CC1101 + ESP32-WROOM-32",
            sw_version=client.version,
        )

    async def async_press(self) -> None:
        """Transmit last captured code."""
        if not self.client.last_code:
            raise HomeAssistantError("No RF code has been captured yet to repeat")

        if self.client.last_decoded_code is not None:
            await self.client.async_send_code(
                code=self.client.last_decoded_code,
                bits=self.client.last_bit_length or 24,
                protocol=self.client.last_protocol or 1,
                pulse_length=self.client.last_pulse_length or 350,
            )
        else:
            await self.client.async_send_raw(self.client.last_code)


class Esp32PingButton(ButtonEntity):
    """Diagnostic button to test USB connection."""

    _attr_has_entity_name = True
    _attr_name = "Ping Radio"
    _attr_icon = "mdi:swap-horizontal"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_ping_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ESP32 CC1101 USB ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="CC1101 + ESP32-WROOM-32",
            sw_version=client.version,
        )

    async def async_press(self) -> None:
        """Send ping."""
        await self.client.async_ping()
        await self.client.async_query_status()


class Esp32ResetButton(ButtonEntity):
    """Diagnostic button to reset the CC1101 transceiver."""

    _attr_has_entity_name = True
    _attr_name = "Reset Radio"
    _attr_icon = "mdi:restart"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_reset_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ESP32 CC1101 USB ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="CC1101 + ESP32-WROOM-32",
            sw_version=client.version,
        )

    async def async_press(self) -> None:
        """Send radio reset."""
        await self.client.async_reset()


class Esp32SavedCommandButton(ButtonEntity):
    """Button for a user-learned and named RF command."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:remote-tv"

    def __init__(
        self,
        entry: ConfigEntry,
        client: Esp32Cc1101Client,
        command_name: str,
        entry_data: dict[str, Any],
    ) -> None:
        self.entry = entry
        self.client = client
        self._command_name = command_name
        self._entry_data = entry_data
        slug = command_name.lower().replace(" ", "_")
        self._attr_name = f"Command {command_name}"
        self._attr_unique_id = f"esp32_cc1101_cmd_{entry.entry_id}_{slug}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ESP32 CC1101 USB ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="CC1101 + ESP32-WROOM-32",
            sw_version=client.version,
        )

    async def async_press(self) -> None:
        """Transmit saved RF command."""
        saved_codes = self._entry_data.get("saved_codes", {})
        code = saved_codes.get(self._command_name)
        if not code:
            raise HomeAssistantError(
                f"Command '{self._command_name}' no longer exists in storage"
            )
        await self.client.async_send_raw(code)
