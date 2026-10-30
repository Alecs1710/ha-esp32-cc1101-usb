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
from .const import CONF_PORT, DEFAULT_REPEATS, DOMAIN

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
            c_low = cmd_name.lower().strip()
            c_slug = c_low.replace(" ", "_")
            for k, entity in list(created_command_entities.items()):
                k_low = k.lower().strip()
                k_slug = k_low.replace(" ", "_")
                short_k = k.split(" - ", 1)[1].lower().strip() if " - " in k else k_low
                short_slug = short_k.replace(" ", "_")
                if (
                    k_low == c_low
                    or k_slug == c_slug
                    or short_k == c_low
                    or short_slug == c_slug
                    or entity.entity_id == cmd_name
                    or entity.unique_id.endswith(f"_{c_slug}")
                    or entity.unique_id.endswith(f"_{short_slug}")
                ):
                    btn = created_command_entities.pop(k, None)
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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return rich stored RF command attributes for easy inspection."""
        saved_codes = self._entry_data.get("saved_codes", {})
        code_data = saved_codes.get(self._command_name)
        if not code_data:
            from . import find_saved_command
            _, code_data = find_saved_command(self.hass, self.entry.entry_id, self._command_name)

        if not code_data:
            return {"status": "not_found"}

        attrs: dict[str, Any] = {
            "command_name": self._command_name,
        }
        if isinstance(code_data, dict):
            attrs["type"] = code_data.get("type", "code")
            attrs["send_mode"] = code_data.get("send_mode", "code" if "code" in code_data else "raw")
            if "code" in code_data and code_data["code"] is not None:
                c = code_data["code"]
                attrs["code"] = c
                try:
                    attrs["code_hex"] = f"0x{int(c):X}"
                except (ValueError, TypeError):
                    pass
            attrs["bits"] = code_data.get("bits")
            attrs["protocol"] = code_data.get("protocol")
            attrs["pulse_length_us"] = code_data.get("pulse_length")
            attrs["repeats"] = code_data.get("repeats", DEFAULT_REPEATS)
            raw = code_data.get("raw") or ""
            if raw:
                pulses = [p.strip() for p in raw.split(",") if p.strip()]
                attrs["raw_pulse_count"] = len(pulses)
                attrs["raw_preview"] = ",".join(pulses[:10]) + ("..." if len(pulses) > 10 else "")
            attrs["device"] = code_data.get("device")
        elif isinstance(code_data, str):
            attrs["type"] = "raw"
            attrs["send_mode"] = "raw"
            pulses = [p.strip() for p in code_data.split(",") if p.strip()]
            attrs["raw_pulse_count"] = len(pulses)
            attrs["raw_preview"] = ",".join(pulses[:10]) + ("..." if len(pulses) > 10 else "")

        return attrs

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
        custom_repeats = code.get("repeats") if isinstance(code, dict) else None
        send_mode = code.get("send_mode") if isinstance(code, dict) else None

        if isinstance(code, dict) and send_mode == "raw" and "raw" in code and code["raw"]:
            await self.client.async_send_raw(code["raw"], repeats=custom_repeats)
        elif isinstance(code, dict) and "code" in code and code["code"] is not None:
            await self.client.async_send_code(
                code=code["code"],
                bits=code.get("bits", 24),
                protocol=code.get("protocol", 1),
                pulse_length=code.get("pulse_length", 350),
                repeats=custom_repeats,
            )
        elif isinstance(code, str):
            await self.client.async_send_raw(code, repeats=custom_repeats)
        elif isinstance(code, dict) and "raw" in code:
            await self.client.async_send_raw(code["raw"], repeats=custom_repeats)
