"""Config flow for ESP32 CC1101 USB RF integration."""

from __future__ import annotations

import os
from typing import Any
import serial.tools.list_ports
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import usb
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    DOMAIN,
    CONF_PORT,
    CONF_BAUDRATE,
    CONF_FREQUENCY,
    CONF_MODULATION,
    CONF_BANDWIDTH,
    CONF_TX_POWER,
    CONF_REPEATS,
    CONF_MIN_RSSI,
    CONF_MIN_REPEATS,
    CONF_RX_MODE,
    DEFAULT_PORT,
    DEFAULT_BAUDRATE,
    DEFAULT_FREQUENCY,
    DEFAULT_MODULATION,
    DEFAULT_BANDWIDTH,
    DEFAULT_TX_POWER,
    DEFAULT_REPEATS,
    DEFAULT_MIN_RSSI,
    DEFAULT_MIN_REPEATS,
    FREQUENCY_PRESETS,
    FREQUENCY_PRESET_MAP,
    get_preset_for_freq,
    MODULATION_OPTIONS,
    RX_MODE_DECODED,
    RX_MODE_OPTIONS,
)


def _get_serial_ports() -> list[SelectOptionDict]:
    """Retrieve available serial ports on host system."""
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
            desc = (
                f" - {port.description}"
                if port.description and port.description != "n/a"
                else ""
            )
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
                label=f"{DEFAULT_PORT} (Manual / Default)",
            )
        )

    return options


class Esp32Cc1101ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ESP32 CC1101 USB RF."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._discovered_device: str | None = None
        self._discovered_description: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            port = user_input[CONF_PORT]
            await self.async_set_unique_id(port)
            self._abort_if_unique_id_configured()

            freq_choice = user_input.get(CONF_FREQUENCY, FREQUENCY_PRESETS[0])
            user_input[CONF_FREQUENCY] = FREQUENCY_PRESET_MAP.get(freq_choice, DEFAULT_FREQUENCY)

            user_input[CONF_MODULATION] = user_input.get(
                CONF_MODULATION, DEFAULT_MODULATION
            )
            user_input[CONF_BANDWIDTH] = DEFAULT_BANDWIDTH
            user_input[CONF_TX_POWER] = DEFAULT_TX_POWER
            user_input[CONF_REPEATS] = DEFAULT_REPEATS

            return self.async_create_entry(
                title=f"ESP32 CC1101 ({port})",
                data=user_input,
            )

        port_options = await self.hass.async_add_executor_job(_get_serial_ports)
        default_port = port_options[0]["value"]

        freq_options = [
            SelectOptionDict(value=p, label=p) for p in FREQUENCY_PRESETS
        ]

        schema = vol.Schema(
            {
                vol.Required(CONF_PORT, default=default_port): SelectSelector(
                    SelectSelectorConfig(
                        options=port_options,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
                vol.Required(CONF_BAUDRATE, default=DEFAULT_BAUDRATE): vol.In(
                    [9600, 57600, 115200, 230400, 460800, 921600]
                ),
                vol.Required(CONF_FREQUENCY, default=FREQUENCY_PRESETS[0]): SelectSelector(
                    SelectSelectorConfig(
                        options=freq_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_MODULATION, default=DEFAULT_MODULATION
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=m, label=m)
                            for m in MODULATION_OPTIONS
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_usb(self, discovery_info: usb.UsbServiceInfo) -> FlowResult:
        """Handle USB plug-and-play discovery."""
        dev_path = discovery_info.device
        await self.async_set_unique_id(dev_path)
        self._abort_if_unique_id_configured()

        self._discovered_device = dev_path
        self._discovered_description = discovery_info.description or "ESP32 CC1101 USB RF"

        self.context["title_placeholders"] = {
            "name": f"{self._discovered_description} ({dev_path})"
        }
        return await self.async_step_discovery_confirm()

    async def async_step_discovery_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm USB discovered device setup."""
        if user_input is not None:
            freq_choice = user_input.get(CONF_FREQUENCY, FREQUENCY_PRESETS[0])
            user_input[CONF_FREQUENCY] = FREQUENCY_PRESET_MAP.get(freq_choice, DEFAULT_FREQUENCY)
            user_input[CONF_PORT] = self._discovered_device
            user_input[CONF_BAUDRATE] = DEFAULT_BAUDRATE
            user_input[CONF_BANDWIDTH] = DEFAULT_BANDWIDTH
            user_input[CONF_TX_POWER] = DEFAULT_TX_POWER
            user_input[CONF_REPEATS] = DEFAULT_REPEATS

            return self.async_create_entry(
                title=f"ESP32 CC1101 ({self._discovered_device})",
                data=user_input,
            )

        freq_options = [
            SelectOptionDict(value=p, label=p) for p in FREQUENCY_PRESETS
        ]

        schema = vol.Schema(
            {
                vol.Required(CONF_FREQUENCY, default=FREQUENCY_PRESETS[0]): SelectSelector(
                    SelectSelectorConfig(
                        options=freq_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_MODULATION, default=DEFAULT_MODULATION
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=m, label=m)
                            for m in MODULATION_OPTIONS
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="discovery_confirm",
            data_schema=schema,
            description_placeholders={
                "device": self._discovered_device or "Unknown",
                "description": self._discovered_description or "ESP32 CC1101",
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return Esp32Cc1101OptionsFlow()


class Esp32Cc1101OptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for creating devices, learning buttons, and tuning radio parameters."""

    def __init__(self) -> None:
        """Initialize options flow."""
        self._target_device: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage entry into options flow."""
        # Detect if opened directly from a specific device card in Home Assistant
        device_id = self.context.get("device_id") or self.context.get("source_device_id")
        if device_id:
            from homeassistant.helpers import device_registry as dr
            dev_reg = dr.async_get(self.hass)
            dev_entry = dev_reg.async_get(device_id)
            if dev_entry:
                prefix = f"{self.config_entry.entry_id}_dev_"
                for dom, ident in dev_entry.identifiers:
                    if dom == DOMAIN and prefix in ident:
                        orig_dev_slug = ident.split(prefix, 1)[1]
                        dev_name = dev_entry.name_by_user or dev_entry.name
                        saved = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {}).get("saved_codes", {})
                        for d in saved.get("__devices__", []):
                            if d.lower().replace(" ", "_") == orig_dev_slug:
                                dev_name = d
                                break
                        self._target_device = dev_name
                        return await self.async_step_device_learn_button()

        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")
        if is_es:
            menu = {
                "create_device": "➕ Crear Nuevo Dispositivo RF",
                "learn_button": "🎯 Aprender Botón para Dispositivo",
                "radio_settings": "⚙️ Parámetros de Radio CC1101",
            }
        else:
            menu = {
                "create_device": "➕ Create New RF Device",
                "learn_button": "🎯 Learn Button for Existing Device",
                "radio_settings": "⚙️ CC1101 Radio Parameters & Tuning",
            }

        return self.async_show_menu(
            step_id="init",
            menu_options=menu,
        )

    async def async_step_device_learn_button(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Learn a button specifically for the currently open device."""
        errors: dict[str, str] = {}
        entry_id = self.config_entry.entry_id
        dev_name = self._target_device or "Device"

        if user_input is not None:
            btn_name = user_input.get("button_name", "").strip()
            if not btn_name:
                errors["button_name"] = "button_name_required"
            else:
                from . import async_start_learning_flow
                await async_start_learning_flow(
                    self.hass, entry_id, btn_name, dev_name
                )
                return self.async_create_entry(title="", data={})

        schema = vol.Schema(
            {
                vol.Required("button_name"): str,
            }
        )

        return self.async_show_form(
            step_id="device_learn_button",
            data_schema=schema,
            description_placeholders={"device_name": dev_name},
            errors=errors,
        )

    async def async_step_create_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create a new logical device without forcing a button name."""
        errors: dict[str, str] = {}
        entry_id = self.config_entry.entry_id

        if user_input is not None:
            dev_name = user_input.get("device_name", "").strip()
            if not dev_name:
                errors["device_name"] = "device_name_required"
            else:
                from homeassistant.helpers import device_registry as dr
                dev_reg = dr.async_get(self.hass)
                slug_dev = dev_name.lower().replace(" ", "_")
                dev_reg.async_get_or_create(
                    config_entry_id=entry_id,
                    identifiers={(DOMAIN, f"{entry_id}_dev_{slug_dev}")},
                    name=dev_name,
                    manufacturer="ESP32 CC1101 RF",
                    model="Learned RF Device",
                    via_device=(DOMAIN, entry_id),
                )

                entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
                saved = entry_data.get("saved_codes", {})
                devices_list = saved.setdefault("__devices__", [])
                if dev_name not in devices_list:
                    devices_list.append(dev_name)
                    await entry_data["store"].async_save(saved)

                persistent_notification = self.hass.components.persistent_notification
                persistent_notification.async_create(
                    f"Device **{dev_name}** has been created!\n\n"
                    f"Open **{dev_name}** in Settings -> Devices and click **Configure** to learn remote buttons for it.",
                    title=f"Device Created: {dev_name}",
                    notification_id=f"{DOMAIN}_dev_created_{slug_dev}",
                )
                return self.async_create_entry(title="", data={})

        schema = vol.Schema(
            {
                vol.Required("device_name"): str,
            }
        )

        return self.async_show_form(
            step_id="create_device",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_learn_button(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Learn a button for an existing device."""
        errors: dict[str, str] = {}
        entry_id = self.config_entry.entry_id
        entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
        saved = entry_data.get("saved_codes", {})

        existing_devices = set(saved.get("__devices__", []))
        for k, v in saved.items():
            if k == "__devices__":
                continue
            if isinstance(v, dict) and v.get("device"):
                existing_devices.add(v["device"].strip())
            elif " - " in k:
                existing_devices.add(k.split(" - ", 1)[0].strip())

        dev_list = sorted(list(existing_devices))
        if not dev_list:
            return await self.async_step_create_device()

        if user_input is not None:
            chosen_dev = user_input.get("target_device")
            btn_name = user_input.get("button_name", "").strip()
            if not btn_name:
                errors["button_name"] = "button_name_required"
            else:
                from . import async_start_learning_flow
                await async_start_learning_flow(
                    self.hass, entry_id, btn_name, chosen_dev
                )
                return self.async_create_entry(title="", data={})

        schema = vol.Schema(
            {
                vol.Required("target_device", default=dev_list[0]): SelectSelector(
                    SelectSelectorConfig(
                        options=[SelectOptionDict(value=d, label=d) for d in dev_list],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required("button_name"): str,
            }
        )

        return self.async_show_form(
            step_id="learn_button",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_radio_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage CC1101 live radio tuning."""
        if user_input is not None:
            freq_choice = user_input.get(CONF_FREQUENCY)
            if freq_choice in FREQUENCY_PRESET_MAP:
                user_input[CONF_FREQUENCY] = FREQUENCY_PRESET_MAP[freq_choice]

            return self.async_create_entry(title="", data=user_input)

        cur_freq = self.config_entry.options.get(
            CONF_FREQUENCY,
            self.config_entry.data.get(CONF_FREQUENCY, DEFAULT_FREQUENCY),
        )
        cur_preset = get_preset_for_freq(float(cur_freq))

        cur_mod = self.config_entry.options.get(
            CONF_MODULATION,
            self.config_entry.data.get(CONF_MODULATION, DEFAULT_MODULATION),
        )
        cur_bw = self.config_entry.options.get(
            CONF_BANDWIDTH,
            self.config_entry.data.get(CONF_BANDWIDTH, DEFAULT_BANDWIDTH),
        )
        cur_pa = self.config_entry.options.get(
            CONF_TX_POWER,
            self.config_entry.data.get(CONF_TX_POWER, DEFAULT_TX_POWER),
        )
        cur_repeats = self.config_entry.options.get(
            CONF_REPEATS,
            self.config_entry.data.get(CONF_REPEATS, DEFAULT_REPEATS),
        )
        cur_min_rssi = self.config_entry.options.get(
            CONF_MIN_RSSI,
            self.config_entry.data.get(CONF_MIN_RSSI, DEFAULT_MIN_RSSI),
        )
        cur_min_repeats = self.config_entry.options.get(
            CONF_MIN_REPEATS,
            self.config_entry.data.get(CONF_MIN_REPEATS, DEFAULT_MIN_REPEATS),
        )
        cur_rx_mode = self.config_entry.options.get(
            CONF_RX_MODE,
            self.config_entry.data.get(CONF_RX_MODE, RX_MODE_DECODED),
        )

        freq_options = [
            SelectOptionDict(value=p, label=p) for p in FREQUENCY_PRESETS
        ]
        mod_options = [
            SelectOptionDict(value=m, label=m) for m in MODULATION_OPTIONS
        ]
        rx_mode_options = [
            SelectOptionDict(value=r, label=r) for r in RX_MODE_OPTIONS
        ]

        schema = vol.Schema(
            {
                vol.Required(CONF_FREQUENCY, default=cur_preset): SelectSelector(
                    SelectSelectorConfig(
                        options=freq_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_MODULATION, default=cur_mod): SelectSelector(
                    SelectSelectorConfig(
                        options=mod_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_BANDWIDTH, default=cur_bw): NumberSelector(
                    NumberSelectorConfig(
                        min=58.0,
                        max=812.0,
                        step=1.0,
                        unit_of_measurement="kHz",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_TX_POWER, default=cur_pa): NumberSelector(
                    NumberSelectorConfig(
                        min=-30,
                        max=10,
                        step=1,
                        unit_of_measurement="dBm",
                        mode=NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Required(CONF_REPEATS, default=cur_repeats): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=25,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_MIN_RSSI, default=cur_min_rssi): NumberSelector(
                    NumberSelectorConfig(
                        min=-100,
                        max=-10,
                        step=1,
                        unit_of_measurement="dBm",
                        mode=NumberSelectorMode.SLIDER,
                    )
                ),
                vol.Required(CONF_MIN_REPEATS, default=cur_min_repeats): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=5,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(CONF_RX_MODE, default=cur_rx_mode): SelectSelector(
                    SelectSelectorConfig(
                        options=rx_mode_options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(step_id="radio_settings", data_schema=schema)
