from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from . import send_rf_code
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    saved_codes = entry_data.get("saved_codes", {})

    entities: list[ButtonEntity] = [Esp32Cc1101RepeatButton(hass, entry.entry_id)]
    created_commands: set[str] = set()

    for cmd_name in saved_codes:
        entities.append(Esp32Cc1101SavedCommandButton(hass, entry.entry_id, cmd_name))
        created_commands.add(cmd_name)

    async_add_entities(entities)

    def add_new_command_button(cmd_name: str) -> None:
        if cmd_name not in created_commands:
            created_commands.add(cmd_name)
            async_add_entities(
                [Esp32Cc1101SavedCommandButton(hass, entry.entry_id, cmd_name)]
            )

    entry_data["add_button_callback"] = add_new_command_button


class Esp32Cc1101RepeatButton(ButtonEntity):
    def __init__(self, hass: HomeAssistant, entry_id: str):
        self.hass = hass
        self._entry_id = entry_id
        self._attr_name = "ESP32 CC1101 Repeat Last Code"
        self._attr_unique_id = f"esp32_cc1101_repeat_last_code_{entry_id}"
        self._attr_icon = "mdi:access-point-network"

    async def async_press(self) -> None:
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        last_code = entry_data.get("last_code", "")
        if not last_code:
            raise HomeAssistantError("No RF code has been captured yet to repeat")
        send_rf_code(self.hass, last_code)


class Esp32Cc1101SavedCommandButton(ButtonEntity):
    def __init__(self, hass: HomeAssistant, entry_id: str, command_name: str):
        self.hass = hass
        self._entry_id = entry_id
        self._command_name = command_name
        slug = command_name.lower().replace(" ", "_")
        self._attr_name = f"ESP32 CC1101 {command_name}"
        self._attr_unique_id = f"esp32_cc1101_cmd_{entry_id}_{slug}"
        self._attr_icon = "mdi:remote-tv"

    async def async_press(self) -> None:
        entry_data = self.hass.data[DOMAIN].get(self._entry_id, {})
        saved_codes = entry_data.get("saved_codes", {})
        code = saved_codes.get(self._command_name)
        if not code:
            raise HomeAssistantError(
                f"Command '{self._command_name}' no longer exists in storage"
            )
        send_rf_code(self.hass, code)
