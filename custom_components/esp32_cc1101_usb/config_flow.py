import voluptuous as vol
from homeassistant import config_entries
from .const import DOMAIN, CONF_PORT, CONF_BAUDRATE, DEFAULT_PORT, DEFAULT_BAUDRATE

class Esp32Cc1101ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_PORT])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"ESP32 CC1101 ({user_input[CONF_PORT]})",
                data=user_input
            )

        schema = vol.Schema({
            vol.Required(CONF_PORT, default=DEFAULT_PORT): str,
            vol.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): int,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)
