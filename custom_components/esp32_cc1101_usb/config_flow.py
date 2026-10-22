import os
import serial.tools.list_ports
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from .const import DOMAIN, CONF_PORT, CONF_BAUDRATE, DEFAULT_PORT, DEFAULT_BAUDRATE


def _get_serial_ports() -> list[SelectOptionDict]:
    options: list[SelectOptionDict] = []
    seen_paths: set[str] = set()

    by_id_dir = "/dev/serial/by-id"
    if os.path.isdir(by_id_dir):
        for entry in sorted(os.listdir(by_id_dir)):
            full_path = os.path.join(by_id_dir, entry)
            real_path = os.path.realpath(full_path)
            seen_paths.add(real_path)
            options.append(
                SelectOptionDict(
                    value=full_path,
                    label=f"{entry} ({real_path})",
                )
            )

    for port in serial.tools.list_ports.comports():
        if port.device not in seen_paths:
            desc = f" - {port.description}" if port.description and port.description != "n/a" else ""
            options.append(
                SelectOptionDict(
                    value=port.device,
                    label=f"{port.device}{desc}",
                )
            )

    if not options:
        options.append(
            SelectOptionDict(
                value=DEFAULT_PORT,
                label=f"{DEFAULT_PORT} (Manual/Default)",
            )
        )

    return options


class Esp32Cc1101ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_PORT])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"ESP32 CC1101 ({user_input[CONF_PORT]})",
                data=user_input,
            )

        port_options = await self.hass.async_add_executor_job(_get_serial_ports)
        default_port = port_options[0]["value"]

        schema = vol.Schema({
            vol.Required(CONF_PORT, default=default_port): SelectSelector(
                SelectSelectorConfig(
                    options=port_options,
                    mode=SelectSelectorMode.DROPDOWN,
                    custom_value=True,
                )
            ),
            vol.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): vol.In(
                [9600, 57600, 115200, 230400, 460800]
            ),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
