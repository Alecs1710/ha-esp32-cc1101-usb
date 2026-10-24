"""Support for ESP32 CC1101 USB RF action buttons."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
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
        Esp32StartLearnButton(hass, entry, client, entry_data),
        Esp32CancelLearnButton(hass, entry, client, entry_data),
        Esp32ConfirmDeleteButton(hass, entry, client, entry_data),
    ]
    created_command_entities: dict[str, Esp32SavedCommandButton] = {}

    for cmd_name in saved_codes:
        btn = Esp32SavedCommandButton(entry, client, cmd_name, entry_data)
        entities.append(btn)
        created_command_entities[cmd_name] = btn

    async_add_entities(entities)

    def add_new_command_button(cmd_name: str) -> None:
        if cmd_name not in created_command_entities:
            btn = Esp32SavedCommandButton(entry, client, cmd_name, entry_data)
            created_command_entities[cmd_name] = btn
            async_add_entities([btn])

    async def remove_command_button(cmd_name: str) -> None:
        btn = created_command_entities.pop(cmd_name, None)
        if not btn:
            for k in list(created_command_entities.keys()):
                if k.lower() == cmd_name.lower() or k.lower().replace(" ", "_") == cmd_name.lower().replace(" ", "_"):
                    btn = created_command_entities.pop(k)
                    break
        if btn and getattr(btn, "hass", None) is not None:
            try:
                await btn.async_remove()
            except Exception as err:
                _LOGGER.debug("Could not dynamically remove entity '%s': %s", cmd_name, err)

    entry_data["add_button_callback"] = add_new_command_button
    entry_data["remove_button_callback"] = remove_command_button


class Esp32RepeatButton(ButtonEntity):
    """Button to repeat the last captured RF code."""

    _attr_has_entity_name = True
    _attr_name = "Repeat Last Code"
    _attr_icon = "mdi:replay"

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        self.entry = entry
        self.client = client
        self._attr_unique_id = f"esp32_cc1101_repeat_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ESP32 CC1101 USB ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="CC1101 + ESP32-WROOM-32",
            sw_version=client.version,
        )

    async def async_press(self) -> None:
        """Transmit the last decoded or raw code captured."""
        if not self.client.last_code and self.client.last_decoded_code is None:
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


class Esp32StartLearnButton(ButtonEntity):
    """Button to start RF learning mode for a new remote button."""

    _attr_has_entity_name = True
    _attr_name = "Start Learning RF Code"
    _attr_icon = "mdi:record-rec"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: Esp32Cc1101Client,
        entry_data: dict[str, Any],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.entry_data = entry_data
        self._attr_unique_id = f"esp32_cc1101_learn_start_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_learner")},
            name=f"ESP32 CC1101 RF Learner ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="RF Learning Studio",
            via_device=(DOMAIN, entry.entry_id),
        )

    async def async_press(self) -> None:
        """Start the learning sequence."""
        from . import async_start_learning_flow
        target_name = self.entry_data.get("learn_name_input")
        if not target_name or not target_name.strip():
            count = len(self.entry_data.get("saved_codes", {})) + 1
            target_name = f"Remote Button {count}"

        await async_start_learning_flow(self.hass, self.entry.entry_id, target_name.strip())


class Esp32CancelLearnButton(ButtonEntity):
    """Button to cancel ongoing RF learning."""

    _attr_has_entity_name = True
    _attr_name = "Cancel Learning"
    _attr_icon = "mdi:close-circle-outline"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: Esp32Cc1101Client,
        entry_data: dict[str, Any],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.entry_data = entry_data
        self._attr_unique_id = f"esp32_cc1101_learn_cancel_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_learner")},
            name=f"ESP32 CC1101 RF Learner ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="RF Learning Studio",
            via_device=(DOMAIN, entry.entry_id),
        )

    async def async_press(self) -> None:
        """Cancel learning."""
        from . import async_cancel_learning_flow
        await async_cancel_learning_flow(self.hass, self.entry.entry_id)


class Esp32ConfirmDeleteButton(ButtonEntity):
    """Button to confirm deletion of the selected learned RF command."""

    _attr_has_entity_name = True
    _attr_name = "Confirm Delete Command"
    _attr_icon = "mdi:delete-alert"

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: Esp32Cc1101Client,
        entry_data: dict[str, Any],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.entry_data = entry_data
        self._attr_unique_id = f"esp32_cc1101_confirm_delete_{entry.entry_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_learned_commands")},
            name=f"ESP32 CC1101 Learned Remotes ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="RF Stored Commands",
            via_device=(DOMAIN, entry.entry_id),
        )

    async def async_press(self) -> None:
        """Confirm and delete the selected command."""
        target = self.entry_data.get("delete_target_name", "None")
        if not target or target == "None":
            raise HomeAssistantError(
                "Please choose an RF command from the 'Delete RF Command' dropdown before clicking Confirm Delete."
            )

        from . import async_delete_command_flow
        await async_delete_command_flow(self.hass, self.entry.entry_id, target)
        self.entry_data["delete_target_name"] = "None"


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
        self._attr_name = f"{command_name}"
        self._attr_unique_id = f"esp32_cc1101_cmd_{entry.entry_id}_{slug}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_learned_commands")},
            name=f"ESP32 CC1101 Learned Remotes ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / ESP32",
            model="RF Stored Commands",
            via_device=(DOMAIN, entry.entry_id),
        )

    async def async_press(self) -> None:
        """Transmit saved RF command."""
        saved_codes = self._entry_data.get("saved_codes", {})
        code = saved_codes.get(self._command_name)
        if not code:
            raise HomeAssistantError(
                f"Command '{self._command_name}' no longer exists in storage"
            )
        if isinstance(code, dict) and "code" in code:
            code_val = code["code"]
            if code.get("type") == "rolling_code":
                delta = code.get("delta", 15)
                next_code = code_val + delta
                # Counter wrap handling for 4-bit counter (0..7) with inverted checksum nibble
                counter = ((next_code & 0xFF) >> 4) & 0x0F
                if counter > 7:
                    next_code = (code.get("base_id", 0) << 8) | 0x07
                code["code"] = next_code
                self.hass.async_create_task(
                    self._entry_data["store"].async_save(self._entry_data["saved_codes"])
                )
                code_val = next_code

            await self.client.async_send_code(
                code=code_val,
                bits=code.get("bits", 24),
                protocol=code.get("protocol", 1),
                pulse_length=code.get("pulse_length", 350),
            )
        elif isinstance(code, str):
            await self.client.async_send_raw(code)
        elif isinstance(code, dict) and "raw" in code:
            await self.client.async_send_raw(code["raw"])
