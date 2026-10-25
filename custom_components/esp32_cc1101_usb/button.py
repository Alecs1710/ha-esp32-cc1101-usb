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
    ]
    created_command_entities: dict[str, Esp32SavedCommandButton] = {}

    for cmd_name in saved_codes:
        if cmd_name.startswith("__"):
            continue
        btn = Esp32SavedCommandButton(entry, client, cmd_name, entry_data)
        entities.append(btn)
        created_command_entities[cmd_name] = btn

    async_add_entities(entities)

    def add_new_command_button(cmd_name: str) -> None:
        if cmd_name.startswith("__"):
            return
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

        saved_codes = entry_data.get("saved_codes", {})
        code_data = saved_codes.get(command_name)

        dev_name = ""
        short_cmd = command_name
        if isinstance(code_data, dict) and code_data.get("device"):
            dev_name = code_data["device"].strip()
            short_cmd = code_data.get("command", command_name).strip()
        elif " - " in command_name:
            dev_name, short_cmd = command_name.split(" - ", 1)
            dev_name = dev_name.strip()
            short_cmd = short_cmd.strip()

        if not dev_name:
            dev_name = "Learned Remotes"

        slug_dev = dev_name.lower().replace(" ", "_")
        slug_full = command_name.lower().replace(" ", "_")

        self._attr_name = short_cmd
        self._attr_unique_id = f"esp32_cc1101_cmd_{entry.entry_id}_{slug_full}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_dev_{slug_dev}")},
            name=dev_name,
            manufacturer="ESP32 CC1101 RF",
            model="Learned RF Device",
            via_device=(DOMAIN, entry.entry_id),
        )

    async def async_press(self) -> None:
        """Transmit saved RF command."""
        saved_codes = self._entry_data.get("saved_codes", {})
        code = saved_codes.get(self._command_name)
        if not code:
            from . import find_saved_command
            _, code = find_saved_command(self.hass, self.entry.entry_id, self._command_name)
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
