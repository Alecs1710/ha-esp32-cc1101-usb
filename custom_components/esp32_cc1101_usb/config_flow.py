"""Config flow for ESP32 CC1101 USB RF integration."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import logging
import os
from typing import Any
import serial.tools.list_ports
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

from homeassistant import config_entries
from homeassistant.components import usb
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import selector
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .client import Esp32Cc1101Client

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
    RX_MODE_ALL,
    DEFAULT_RX_MODE,
    RX_MODE_OPTIONS,
    get_rolling_cycle_codes,
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
        self._learn_button_name: str | None = None
        self._learn_repeats: int = DEFAULT_REPEATS
        self._learn_min_rssi: int = -60
        self._candidate_code: Any = None
        self._candidate_desc: str = ""
        self._record_error_desc: str = ""
        self._record_sample_count: int = 0
        self._menu_message: str | None = None
        self._inspect_button_name: str | None = None
        self._inspect_message: str | None = None

    def _get_all_devices(self) -> list[str]:
        """Return sorted list of all existing device names."""
        entry_id = self.config_entry.entry_id
        entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
        saved = entry_data.get("saved_codes", {})
        devs = set(saved.get("__devices__", []))
        for k, v in saved.items():
            if k.startswith("__"):
                continue
            if isinstance(v, dict) and v.get("device"):
                devs.add(v["device"].strip())
            elif " - " in k:
                devs.add(k.split(" - ", 1)[0].strip())
            else:
                devs.add("Learned Remotes")
        return sorted([d for d in devs if d])

    def _get_all_buttons(self) -> dict[str, str]:
        """Return dict of {display_label: full_storage_key} for ALL saved buttons across all devices."""
        entry_id = self.config_entry.entry_id
        entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
        saved = entry_data.get("saved_codes", {})
        all_buttons: dict[str, str] = {}
        for full_key, val in saved.items():
            if full_key.startswith("__"):
                continue
            curr_dev = ""
            curr_cmd = full_key
            if isinstance(val, dict) and val.get("device"):
                curr_dev = val["device"].strip()
                curr_cmd = val.get("command", full_key).strip()
            elif " - " in full_key:
                curr_dev, curr_cmd = full_key.split(" - ", 1)
                curr_dev = curr_dev.strip()
                curr_cmd = curr_cmd.strip()

            label = f"[{curr_dev}] {curr_cmd}" if curr_dev else curr_cmd
            all_buttons[label] = full_key
        return dict(sorted(all_buttons.items()))

    def _get_device_buttons(self, dev_name: str) -> dict[str, str]:
        """Return dict of {short_button_name: full_storage_key} for a device."""
        entry_id = self.config_entry.entry_id
        entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
        saved = entry_data.get("saved_codes", {})
        buttons: dict[str, str] = {}
        d_lower = dev_name.lower().strip()
        for full_key, val in saved.items():
            if full_key.startswith("__"):
                continue
            curr_dev = ""
            curr_cmd = full_key
            if isinstance(val, dict) and val.get("device"):
                curr_dev = val["device"].strip()
                curr_cmd = val.get("command", full_key).strip()
            elif " - " in full_key:
                curr_dev, curr_cmd = full_key.split(" - ", 1)
                curr_dev = curr_dev.strip()
                curr_cmd = curr_cmd.strip()
            else:
                curr_dev = "Learned Remotes"

            if (
                curr_dev.lower() == d_lower
                or (d_lower in ("learned remotes", "dispositivo", "mandos rf") and curr_dev.lower() in ("learned remotes", "dispositivo", "mandos rf", ""))
            ):
                buttons[curr_cmd] = full_key
        return buttons

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage entry into options flow."""
        device_id = self.context.get("device_id") or self.context.get("source_device_id")
        if device_id:
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
                        return await self.async_step_device_menu()

        status_msg = f"{self._menu_message}\n\n" if self._menu_message else ""
        self._menu_message = None

        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")
        if is_es:
            menu = {
                "manage_devices": "Gestionar Dispositivos y Mandos",
                "backup_menu": "Copias de Seguridad (Exportar / Importar JSON)",
                "radio_settings": "Ajustes de Radio y Puerto USB",
            }
        else:
            menu = {
                "manage_devices": "Manage Devices & Remotes",
                "backup_menu": "Backup & Restore (Export / Import JSON)",
                "radio_settings": "Radio & USB Port Settings",
            }

        return self.async_show_menu(
            step_id="init",
            menu_options=menu,
            description_placeholders={
                "status_msg": status_msg,
            },
        )

    async def async_step_backup_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Unified menu for JSON Backup & Restore operations."""
        status_msg = f"{self._menu_message}\n\n" if self._menu_message else ""
        self._menu_message = None

        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")
        if is_es:
            menu = {
                "export_json": "Exportar a JSON (Copia de Seguridad)",
                "import_json": "Importar desde JSON",
                "init": "Volver al Menú Principal",
            }
        else:
            menu = {
                "export_json": "Export to JSON (Backup)",
                "import_json": "Import from JSON",
                "init": "Return to Main Menu",
            }

        return self.async_show_menu(
            step_id="backup_menu",
            menu_options=menu,
            description_placeholders={
                "status_msg": status_msg,
            },
        )

    async def async_step_manage_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select an existing device or enter a new device name."""
        errors: dict[str, str] = {}
        entry_id = self.config_entry.entry_id
        devs = self._get_all_devices()

        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")
        if user_input is not None:
            dev_name = user_input.get("device_name", "").strip()
            if dev_name in ("__back__", "« Volver al Menú Principal", "« Return to Main Menu") or not dev_name:
                return await self.async_step_init()
            else:
                self._target_device = dev_name
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

                if entry_data.get("add_device_sensor_callback"):
                    try:
                        entry_data["add_device_sensor_callback"](dev_name)
                    except Exception as err:
                        _LOGGER.debug("Could not add device sensor: %s", err)

                return await self.async_step_device_menu()

        if devs:
            dev_options = [SelectOptionDict(value=d, label=d) for d in devs]
            dev_options.append(SelectOptionDict(value="__back__", label="« Volver al Menú Principal" if is_es else "« Return to Main Menu"))
            dev_selector = SelectSelector(
                SelectSelectorConfig(
                    options=dev_options,
                    mode=SelectSelectorMode.DROPDOWN,
                    custom_value=True,
                )
            )
            schema = vol.Schema(
                {
                    vol.Required("device_name", default=devs[0]): dev_selector,
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Optional("device_name", default=""): str,
                }
            )

        return self.async_show_form(
            step_id="manage_devices",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_device_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show action menu for this specific device."""
        dev_name = self._target_device or "Device"
        buttons = self._get_device_buttons(dev_name)
        btn_count = len(buttons)
        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")
        if is_es:
            menu = {
                "device_learn_button": "Aprender Nuevo Botón",
            }
            if buttons:
                menu["device_manage_buttons"] = "Gestionar / Editar Botones"
            menu["device_edit_device"] = "Editar Dispositivo (Renombrar / Eliminar)"
            menu["manage_devices"] = "Cambiar de Dispositivo"
            menu["init"] = "Volver al Menú Principal"
            menu["finish"] = "Finalizar y Cerrar"
        else:
            menu = {
                "device_learn_button": "Learn New Button",
            }
            if buttons:
                menu["device_manage_buttons"] = "Manage / Edit Buttons"
            menu["device_edit_device"] = "Edit Device (Rename / Delete)"
            menu["manage_devices"] = "Change Device"
            menu["init"] = "Return to Main Menu"
            menu["finish"] = "Finish and Close"

        status_msg = f"{self._menu_message}\n\n" if self._menu_message else ""
        self._menu_message = None

        return self.async_show_menu(
            step_id="device_menu",
            menu_options=menu,
            description_placeholders={
                "device_name": dev_name,
                "button_count": str(btn_count),
                "status_msg": status_msg,
            },
        )

    async def async_step_device_manage_buttons(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select a button of the current device to inspect, test or edit."""
        dev_name = self._target_device or "Device"
        buttons = self._get_device_buttons(dev_name)
        if not buttons:
            return await self.async_step_device_menu()

        btn_list = sorted(list(buttons.keys()))
        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")
        if user_input is not None:
            chosen = user_input.get("button_name", "")
            if chosen in ("__back__", f"« Volver al Menú de {dev_name}", f"« Return to {dev_name} Menu"):
                return await self.async_step_device_menu()
            self._inspect_button_name = chosen
            return await self.async_step_button_edit_menu()

        options = [SelectOptionDict(value=b, label=b) for b in btn_list]
        back_label = f"« Volver al Menú de {dev_name}" if is_es else f"« Return to {dev_name} Menu"
        options.append(SelectOptionDict(value="__back__", label=back_label))

        default_btn = self._inspect_button_name if self._inspect_button_name in btn_list else btn_list[0]
        schema = vol.Schema(
            {
                vol.Required("button_name", default=default_btn): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        status_msg = f"{self._menu_message}\n\n" if self._menu_message else ""
        self._menu_message = None

        return self.async_show_form(
            step_id="device_manage_buttons",
            data_schema=schema,
            description_placeholders={
                "device_name": dev_name,
                "status_msg": status_msg,
            },
        )

    async def async_step_button_edit_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Display full technical details of chosen button and unified actions."""
        dev_name = self._target_device or "Device"
        btn_name = self._inspect_button_name or ""
        entry_id = self.config_entry.entry_id
        entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
        saved = entry_data.get("saved_codes", {})
        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")

        buttons = self._get_device_buttons(dev_name)
        full_key = buttons.get(btn_name) or f"{dev_name} - {btn_name}"
        code_data = saved.get(full_key)

        if not code_data:
            return await self.async_step_device_manage_buttons()

        details_lines = []
        has_code = False
        has_raw = False
        curr_mode = "code"

        if isinstance(code_data, dict):
            c_type = code_data.get("type", "code")
            curr_mode = code_data.get("send_mode", "code" if "code" in code_data and code_data["code"] is not None else "raw")
            repeats = code_data.get("repeats", DEFAULT_REPEATS)
            mode_lbl = ("Código Estándar" if curr_mode == "code" else "Pulsos Raw") if is_es else ("Standard Code" if curr_mode == "code" else "Raw Pulses")

            cycle = code_data.get("cycle") or (get_rolling_cycle_codes(code_data.get("code")) if "code" in code_data else None)
            if cycle:
                c_type = "rolling_code"
                mode_lbl = "Código Rotativo (8 Estados)" if is_es else "Rotating Code (8 States)"

            if is_es:
                details_lines.append("<details open>")
                details_lines.append(f"<summary><b>Información de Señal ({btn_name})</b> <i>(clic para plegar)</i></summary>\n")
                details_lines.append("> | Parámetro | Valor |")
                details_lines.append("> | :--- | :--- |")
                details_lines.append(f"> | **Tipo de Registro** | `{c_type}` |")
                details_lines.append(f"> | **Modo de Envío Activo** | `{mode_lbl}` |")
            else:
                details_lines.append("<details open>")
                details_lines.append(f"<summary><b>RF Signal Information ({btn_name})</b> <i>(click to collapse)</i></summary>\n")
                details_lines.append("> | Parameter | Value |")
                details_lines.append("> | :--- | :--- |")
                details_lines.append(f"> | **Record Type** | `{c_type}` |")
                details_lines.append(f"> | **Active Send Mode** | `{mode_lbl}` |")

            if "code" in code_data and code_data["code"] is not None:
                has_code = True
                c_val = code_data["code"]
                hex_str = f" (`0x{int(c_val):X}`)" if isinstance(c_val, (int, str)) and str(c_val).isdigit() else ""
                if is_es:
                    details_lines.append(f"> | **Código Decodificado** | `{c_val}`{hex_str} |")
                    if cycle:
                        try:
                            cur_step = cycle.index(int(c_val)) + 1
                        except (ValueError, TypeError):
                            cur_step = 1
                        details_lines.append(f"> | **Ciclo Rotativo** | Paso {cur_step} de {len(cycle)} códigos |")
                    details_lines.append(
                        f"> | **Parámetros RF** | Bits: `{code_data.get('bits', 24)}` · Protocolo: `{code_data.get('protocol', 1)}` · Pulso: `{code_data.get('pulse_length', 350)} µs` |"
                    )
                else:
                    details_lines.append(f"> | **Decoded Code** | `{c_val}`{hex_str} |")
                    if cycle:
                        try:
                            cur_step = cycle.index(int(c_val)) + 1
                        except (ValueError, TypeError):
                            cur_step = 1
                        details_lines.append(f"> | **Rotating Cycle** | Step {cur_step} of {len(cycle)} codes |")
                    details_lines.append(
                        f"> | **RF Parameters** | Bits: `{code_data.get('bits', 24)}` · Protocol: `{code_data.get('protocol', 1)}` · Pulse: `{code_data.get('pulse_length', 350)} µs` |"
                    )

            if is_es:
                details_lines.append(f"> | **Repeticiones de Envío** | `{repeats}` ráfagas |")
            else:
                details_lines.append(f"> | **Transmit Repeats** | `{repeats}` bursts |")

            if cycle:
                cycle_title = f"Ver Ciclo de 8 Códigos ({len(cycle)} estados) - Clic para desplegar" if is_es else f"View 8-Code Rolling Cycle ({len(cycle)} states) - Click to expand"
                details_lines.append("\n<details>")
                details_lines.append(f"<summary><b>{cycle_title}</b></summary>\n")
                cycle_text_lines = []
                for idx, c_item in enumerate(cycle):
                    marker = " -> Actual" if str(c_item) == str(code_data.get("code")) else ""
                    cycle_text_lines.append(f"[Paso {idx + 1}]: 0x{c_item:08X} ({c_item}){marker}")
                details_lines.append("```text\n" + "\n".join(cycle_text_lines) + "\n```\n")
                details_lines.append("</details>")

            raw_str = code_data.get("raw") or ""
            if raw_str:
                has_raw = True
                p_list = [p.strip() for p in raw_str.split(",") if p.strip()]
                raw_title = f"Ver Pulsos Raw ({len(p_list)} pulsos) - Clic para desplegar" if is_es else f"View Raw Pulses ({len(p_list)} pulses) - Click to expand"
                details_lines.append("\n<details>")
                details_lines.append(f"<summary><b>{raw_title}</b></summary>\n")
                details_lines.append(f"```text\n{raw_str}\n```\n")
                details_lines.append("</details>")

            details_lines.append("</details>")
        elif isinstance(code_data, str):
            has_raw = True
            curr_mode = "raw"
            p_list = [p.strip() for p in code_data.split(",") if p.strip()]
            if is_es:
                details_lines.append("<details open>")
                details_lines.append(f"<summary><b>Información de Señal Raw ({btn_name})</b> <i>(clic para plegar)</i></summary>\n")
                details_lines.append("> | Parámetro | Valor |")
                details_lines.append("> | :--- | :--- |")
                details_lines.append("> | **Tipo de Registro** | `raw` |")
                details_lines.append("> | **Modo de Envío Activo** | `Pulsos Raw` |")
                details_lines.append(f"> | **Pulsos Registrados** | `{len(p_list)} pulsos` |")
                details_lines.append(f"> | **Repeticiones de Envío** | `{DEFAULT_REPEATS}` ráfagas |")
                raw_title = f"Ver Pulsos Raw ({len(p_list)} pulsos) - Clic para desplegar"
                details_lines.append("\n<details>")
                details_lines.append(f"<summary><b>{raw_title}</b></summary>\n")
                details_lines.append(f"```text\n{code_data}\n```\n")
                details_lines.append("</details>")
                details_lines.append("</details>")
            else:
                details_lines.append("<details open>")
                details_lines.append(f"<summary><b>Raw Signal Information ({btn_name})</b> <i>(click to collapse)</i></summary>\n")
                details_lines.append("> | Parameter | Value |")
                details_lines.append("> | :--- | :--- |")
                details_lines.append("> | **Record Type** | `raw` |")
                details_lines.append("> | **Active Send Mode** | `Raw Pulses` |")
                details_lines.append(f"> | **Recorded Pulses** | `{len(p_list)} pulses` |")
                details_lines.append(f"> | **Transmit Repeats** | `{DEFAULT_REPEATS}` bursts |")
                raw_title = f"View Raw Pulses ({len(p_list)} pulses) - Click to expand"
                details_lines.append("\n<details>")
                details_lines.append(f"<summary><b>{raw_title}</b></summary>\n")
                details_lines.append(f"```text\n{code_data}\n```\n")
                details_lines.append("</details>")
                details_lines.append("</details>")

        msg = f"{self._inspect_message}\n\n" if self._inspect_message else ""
        self._inspect_message = None

        menu: dict[str, str] = {}
        if is_es:
            if has_code:
                menu["button_test_code"] = "Probar Envío RF (Código)"
            if has_raw:
                menu["button_test_raw"] = "Probar Envío RF (Pulsos Raw)"
            menu["button_relearn"] = "Sobrescribir / Re-grabar Código (5 segundos)"
            menu["button_rename"] = "Renombrar Botón"
            menu["button_move"] = "Mover a Otro Dispositivo"
            menu["button_repeats"] = "Ajustar Repeticiones de Envío"
            if has_code and has_raw:
                target_label = "Pulsos Raw" if curr_mode == "code" else "Código Estándar"
                menu["button_toggle_mode"] = f"Alternar Modo Preferido a: {target_label}"
            menu["button_delete"] = "Eliminar este Botón"
            menu["device_manage_buttons"] = "Volver a Selección de Botones"
            menu["device_menu"] = f"Volver al Menú de {dev_name}"
            menu["init"] = "Volver al Menú Principal"
        else:
            if has_code:
                menu["button_test_code"] = "Test RF Transmit (Code)"
            if has_raw:
                menu["button_test_raw"] = "Test RF Transmit (Raw Pulses)"
            menu["button_relearn"] = "Overwrite / Re-record Code (5 seconds)"
            menu["button_rename"] = "Rename Button"
            menu["button_move"] = "Move to Another Device"
            menu["button_repeats"] = "Adjust Transmit Repeats"
            if has_code and has_raw:
                target_label = "Raw Pulses" if curr_mode == "code" else "Standard Code"
                menu["button_toggle_mode"] = f"Switch Preferred Mode to: {target_label}"
            menu["button_delete"] = "Delete this Button"
            menu["device_manage_buttons"] = "Return to Button Selection"
            menu["device_menu"] = f"Return to {dev_name} Menu"
            menu["init"] = "Return to Main Menu"

        return self.async_show_menu(
            step_id="button_edit_menu",
            menu_options=menu,
            description_placeholders={
                "button_name": btn_name,
                "device_name": dev_name,
                "inspect_message": msg,
                "button_details": "\n".join(details_lines),
            },
        )

    async def async_step_button_test_code(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Transmit current button using standard protocol code."""
        dev_name = self._target_device or "Device"
        btn_name = self._inspect_button_name or ""
        entry_id = self.config_entry.entry_id
        entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
        client: Esp32Cc1101Client | None = entry_data.get("client")
        saved = entry_data.get("saved_codes", {})
        buttons = self._get_device_buttons(dev_name)
        full_key = buttons.get(btn_name)
        code_data = saved.get(full_key) if full_key else None

        if client and isinstance(code_data, dict) and "code" in code_data and code_data["code"] is not None:
            repeats = code_data.get("repeats", DEFAULT_REPEATS)
            try:
                await client.async_send_code(
                    code=code_data["code"],
                    bits=code_data.get("bits", 24),
                    protocol=code_data.get("protocol", 1),
                    pulse_length=code_data.get("pulse_length", 350),
                    repeats=repeats,
                )
                self._inspect_message = "Transmitido código estándar correctamente. Comprueba si el aparato ha reaccionado."
            except Exception as err:
                self._inspect_message = f"Error transmitiendo código: {err}"
        return await self.async_step_button_edit_menu()

    async def async_step_button_test_raw(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Transmit current button using raw pulse train."""
        dev_name = self._target_device or "Device"
        btn_name = self._inspect_button_name or ""
        entry_id = self.config_entry.entry_id
        entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
        client: Esp32Cc1101Client | None = entry_data.get("client")
        saved = entry_data.get("saved_codes", {})
        buttons = self._get_device_buttons(dev_name)
        full_key = buttons.get(btn_name)
        code_data = saved.get(full_key) if full_key else None

        raw_val = None
        repeats = DEFAULT_REPEATS
        if isinstance(code_data, dict):
            raw_val = code_data.get("raw")
            repeats = code_data.get("repeats", DEFAULT_REPEATS)
        elif isinstance(code_data, str):
            raw_val = code_data

        if client and raw_val:
            try:
                await client.async_send_raw(raw_val, repeats=repeats)
                self._inspect_message = "Transmitidos pulsos raw correctamente. Comprueba si el aparato ha reaccionado."
            except Exception as err:
                self._inspect_message = f"Error transmitiendo raw: {err}"
        return await self.async_step_button_edit_menu()

    async def async_step_button_toggle_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Toggle preferred send mode between code and raw."""
        dev_name = self._target_device or "Device"
        btn_name = self._inspect_button_name or ""
        entry_id = self.config_entry.entry_id
        entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
        saved = entry_data.get("saved_codes", {})
        buttons = self._get_device_buttons(dev_name)
        full_key = buttons.get(btn_name)
        code_data = saved.get(full_key) if full_key else None

        if isinstance(code_data, dict):
            cur = code_data.get("send_mode", "code")
            new_mode = "raw" if cur == "code" else "code"
            code_data["send_mode"] = new_mode
            await entry_data["store"].async_save(saved)
            mode_lbl = "Pulsos Raw" if new_mode == "raw" else "Código Estándar"
            self._inspect_message = f"Modo de envío cambiado a: {mode_lbl}."

        return await self.async_step_button_edit_menu()

    async def async_step_button_relearn(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Overwrite/re-record code for current button without re-typing name."""
        self._learn_button_name = self._inspect_button_name
        dev_name = self._target_device or "Device"
        buttons = self._get_device_buttons(dev_name)
        full_key = buttons.get(self._inspect_button_name)
        entry_data = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
        saved = entry_data.get("saved_codes", {})
        val = saved.get(full_key, {})
        if isinstance(val, dict):
            self._learn_repeats = val.get("repeats", DEFAULT_REPEATS)
        return await self.async_step_device_learn_record()

    async def async_step_button_rename(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Rename button while preserving all signal data and parameters."""
        dev_name = self._target_device or "Device"
        old_btn = self._inspect_button_name or ""
        errors: dict[str, str] = {}
        if user_input is not None:
            new_btn = user_input.get("new_button_name", "").strip()
            if not new_btn:
                errors["new_button_name"] = "button_name_required"
            elif new_btn == old_btn:
                return await self.async_step_button_edit_menu()
            else:
                entry_data = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
                saved = entry_data.get("saved_codes", {})
                buttons = self._get_device_buttons(dev_name)
                old_key = buttons.get(old_btn) or f"{dev_name} - {old_btn}"
                if old_key in saved:
                    code_data = saved.pop(old_key)
                    if isinstance(code_data, dict):
                        code_data["command"] = new_btn
                    else:
                        code_data = {
                            "type": "raw",
                            "raw": code_data,
                            "device": dev_name,
                            "command": new_btn,
                        }
                    new_key = f"{dev_name} - {new_btn}" if dev_name else new_btn
                    saved[new_key] = code_data
                    await entry_data["store"].async_save(saved)

                    if entry_data.get("remove_button_callback"):
                        try:
                            await entry_data["remove_button_callback"](old_key)
                        except Exception:
                            pass
                    if entry_data.get("add_button_callback"):
                        try:
                            entry_data["add_button_callback"](new_key)
                        except Exception:
                            pass

                    self._inspect_button_name = new_btn
                    self._inspect_message = f"Botón renombrado con éxito a '{new_btn}'."
                    return await self.async_step_button_edit_menu()

        schema = vol.Schema(
            {
                vol.Required("new_button_name", default=old_btn): str,
            }
        )
        return self.async_show_form(
            step_id="button_rename",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "button_name": old_btn,
                "device_name": dev_name,
            },
        )

    async def async_step_button_move(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Move current button to another device."""
        dev_name = self._target_device or "Device"
        btn_name = self._inspect_button_name or ""
        errors: dict[str, str] = {}
        all_devs = self._get_all_devices()
        dest_options = [d for d in all_devs if d != dev_name]

        if user_input is not None:
            dest_dev = user_input.get("destination_device", "").strip()
            if not dest_dev:
                errors["destination_device"] = "device_name_required"
            elif dest_dev == dev_name:
                errors["destination_device"] = "same_device"
            else:
                entry_data = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
                saved = entry_data.get("saved_codes", {})
                buttons = self._get_device_buttons(dev_name)
                old_key = buttons.get(btn_name) or f"{dev_name} - {btn_name}"
                if old_key in saved:
                    code_data = saved.pop(old_key)
                    if isinstance(code_data, dict):
                        code_data["device"] = dest_dev
                        code_data["command"] = btn_name
                    else:
                        code_data = {
                            "type": "raw",
                            "raw": code_data,
                            "device": dest_dev,
                            "command": btn_name,
                        }
                    new_key = f"{dest_dev} - {btn_name}"
                    saved[new_key] = code_data
                    devices_list = saved.setdefault("__devices__", [])
                    if dest_dev not in devices_list:
                        devices_list.append(dest_dev)

                    await entry_data["store"].async_save(saved)

                    if entry_data.get("remove_button_callback"):
                        try:
                            await entry_data["remove_button_callback"](old_key)
                        except Exception:
                            pass
                    if entry_data.get("add_button_callback"):
                        try:
                            entry_data["add_button_callback"](new_key)
                        except Exception:
                            pass
                    if entry_data.get("add_device_sensor_callback"):
                        try:
                            entry_data["add_device_sensor_callback"](dest_dev)
                        except Exception:
                            pass

                    self._menu_message = f"Botón '{btn_name}' movido con éxito a '{dest_dev}'."
                    return await self.async_step_device_manage_buttons()

        schema_dict: dict[Any, Any] = {}
        if dest_options:
            schema_dict[vol.Required("destination_device", default=dest_options[0])] = SelectSelector(
                SelectSelectorConfig(
                    options=[SelectOptionDict(value=d, label=d) for d in dest_options],
                    mode=SelectSelectorMode.DROPDOWN,
                    custom_value=True,
                )
            )
        else:
            schema_dict[vol.Required("destination_device")] = str

        return self.async_show_form(
            step_id="button_move",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={
                "button_name": btn_name,
                "current_device": dev_name,
            },
        )

    async def async_step_button_repeats(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Adjust transmit repeats for current button."""
        dev_name = self._target_device or "Device"
        btn_name = self._inspect_button_name or ""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
        saved = entry_data.get("saved_codes", {})
        buttons = self._get_device_buttons(dev_name)
        full_key = buttons.get(btn_name) or f"{dev_name} - {btn_name}"

        if user_input is not None:
            new_repeats = int(user_input.get("repeats", DEFAULT_REPEATS))
            if full_key in saved:
                val = saved[full_key]
                if isinstance(val, dict):
                    val["repeats"] = new_repeats
                else:
                    saved[full_key] = {
                        "type": "raw",
                        "raw": val,
                        "device": dev_name,
                        "command": btn_name,
                        "repeats": new_repeats,
                    }
                await entry_data["store"].async_save(saved)
                self._inspect_message = f"Repeticiones de '{btn_name}' actualizadas a {new_repeats}."
                return await self.async_step_button_edit_menu()

        cur_val = saved.get(full_key, {})
        cur_reps = cur_val.get("repeats", DEFAULT_REPEATS) if isinstance(cur_val, dict) else DEFAULT_REPEATS

        schema = vol.Schema(
            {
                vol.Required("repeats", default=cur_reps): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=25,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="button_repeats",
            data_schema=schema,
            description_placeholders={
                "button_name": btn_name,
                "device_name": dev_name,
            },
        )

    async def async_step_button_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Delete current button with confirmation."""
        dev_name = self._target_device or "Device"
        btn_name = self._inspect_button_name or ""
        entry_id = self.config_entry.entry_id
        buttons = self._get_device_buttons(dev_name)
        full_key = buttons.get(btn_name) or f"{dev_name} - {btn_name}"

        if user_input is not None:
            if user_input.get("confirm", True):
                from . import async_delete_command_flow
                await async_delete_command_flow(self.hass, entry_id, full_key)
                self._menu_message = f"Botón '{btn_name}' eliminado correctamente de {dev_name}."
                rem_buttons = self._get_device_buttons(dev_name)
                if rem_buttons:
                    return await self.async_step_device_manage_buttons()
                return await self.async_step_device_menu()
            return await self.async_step_button_edit_menu()

        schema = vol.Schema({vol.Required("confirm", default=True): bool})
        return self.async_show_form(
            step_id="button_delete",
            data_schema=schema,
            description_placeholders={
                "button_name": btn_name,
                "device_name": dev_name,
            },
        )

    async def async_step_device_edit_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage or edit the current device (rename / delete)."""
        dev_name = self._target_device or "Device"
        buttons = self._get_device_buttons(dev_name)
        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")
        if is_es:
            menu = {
                "device_rename": "Renombrar Dispositivo",
                "device_delete": "Eliminar Dispositivo Completo",
                "device_menu": f"Volver al Menú de {dev_name}",
                "init": "Volver al Menú Principal",
            }
        else:
            menu = {
                "device_rename": "Rename Device",
                "device_delete": "Delete Entire Device",
                "device_menu": f"Return to {dev_name} Menu",
                "init": "Return to Main Menu",
            }

        return self.async_show_menu(
            step_id="device_edit_device",
            menu_options=menu,
            description_placeholders={
                "device_name": dev_name,
                "button_count": str(len(buttons)),
            },
        )

    async def async_step_device_rename(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Rename device and update all its associated buttons, sensors, and registry entry."""
        old_dev = self._target_device or "Device"
        errors: dict[str, str] = {}
        if user_input is not None:
            new_dev = user_input.get("new_device_name", "").strip()
            if not new_dev:
                errors["new_device_name"] = "device_name_required"
            elif new_dev == old_dev:
                return await self.async_step_device_menu()
            else:
                entry_id = self.config_entry.entry_id
                entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
                saved = entry_data.get("saved_codes", {})

                devs_list = saved.setdefault("__devices__", [])
                if old_dev in devs_list:
                    devs_list[devs_list.index(old_dev)] = new_dev
                elif new_dev not in devs_list:
                    devs_list.append(new_dev)

                keys_to_update = []
                for k, v in list(saved.items()):
                    if k.startswith("__"):
                        continue
                    code_dev = v.get("device", "") if isinstance(v, dict) else (k.split(" - ", 1)[0] if " - " in k else "")
                    if code_dev.lower() == old_dev.lower():
                        keys_to_update.append((k, v))

                for old_key, code_data in keys_to_update:
                    saved.pop(old_key, None)
                    short_name = code_data.get("command") if isinstance(code_data, dict) else (old_key.split(" - ", 1)[1] if " - " in old_key else old_key)
                    if isinstance(code_data, dict):
                        code_data["device"] = new_dev
                    else:
                        code_data = {
                            "type": "raw",
                            "raw": code_data,
                            "device": new_dev,
                            "command": short_name,
                        }
                    new_key = f"{new_dev} - {short_name}"
                    saved[new_key] = code_data

                    if entry_data.get("remove_button_callback"):
                        try:
                            await entry_data["remove_button_callback"](old_key)
                        except Exception:
                            pass
                    if entry_data.get("add_button_callback"):
                        try:
                            entry_data["add_button_callback"](new_key)
                        except Exception:
                            pass

                await entry_data["store"].async_save(saved)

                try:
                    dev_reg = dr.async_get(self.hass)
                    old_slug = old_dev.lower().replace(" ", "_")
                    dev_entry = dev_reg.async_get_device(identifiers={(DOMAIN, f"{entry_id}_dev_{old_slug}")})
                    if dev_entry:
                        new_slug = new_dev.lower().replace(" ", "_")
                        dev_reg.async_update_device(
                            dev_entry.id,
                            name=new_dev,
                            new_identifiers={(DOMAIN, f"{entry_id}_dev_{new_slug}")},
                        )
                except Exception as err:
                    _LOGGER.debug("Device registry rename notice: %s", err)

                if entry_data.get("add_device_sensor_callback"):
                    try:
                        entry_data["add_device_sensor_callback"](new_dev)
                    except Exception:
                        pass
                if entry_data.get("remove_device_sensor_callback"):
                    try:
                        await entry_data["remove_device_sensor_callback"](old_dev)
                    except Exception:
                        pass

                self._target_device = new_dev
                self._menu_message = f"Dispositivo renombrado correctamente a '{new_dev}'."
                return await self.async_step_device_menu()

        schema = vol.Schema(
            {
                vol.Required("new_device_name", default=old_dev): str,
            }
        )
        return self.async_show_form(
            step_id="device_rename",
            data_schema=schema,
            errors=errors,
            description_placeholders={"device_name": old_dev},
        )

    async def async_step_device_delete(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Delete this device and all its buttons with confirmation."""
        dev_name = self._target_device or "Device"
        entry_id = self.config_entry.entry_id
        if user_input is not None:
            if user_input.get("confirm", True):
                from . import async_delete_device_flow
                await async_delete_device_flow(self.hass, entry_id, dev_name)
                self._menu_message = f"Dispositivo '{dev_name}' y todos sus botones han sido eliminados."
                self._target_device = None
                return await self.async_step_manage_devices()
            return await self.async_step_device_menu()

        schema = vol.Schema({vol.Required("confirm", default=True): bool})
        return self.async_show_form(
            step_id="device_delete",
            data_schema=schema,
            description_placeholders={"device_name": dev_name},
        )

    async def async_step_device_learn_button(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1: Enter button name, transmit repeats, and initial power filter."""
        errors: dict[str, str] = {}
        entry_id = self.config_entry.entry_id
        dev_name = self._target_device or "Device"

        if user_input is not None:
            btn_name = user_input.get("button_name", "").strip()
            repeats = user_input.get("repeats", DEFAULT_REPEATS)
            min_rssi = user_input.get("min_rssi", self._learn_min_rssi)
            if not btn_name:
                errors["button_name"] = "button_name_required"
            else:
                self._learn_button_name = btn_name
                self._learn_repeats = int(repeats)
                self._learn_min_rssi = int(min_rssi)
                return await self.async_step_device_learn_record()

        schema = vol.Schema(
            {
                vol.Required("button_name"): str,
                vol.Optional("repeats", default=DEFAULT_REPEATS): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=25,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Optional("min_rssi", default=self._learn_min_rssi): NumberSelector(
                    NumberSelectorConfig(
                        min=-90,
                        max=-30,
                        step=5,
                        unit_of_measurement="dBm",
                        mode=NumberSelectorMode.SLIDER,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="device_learn_button",
            data_schema=schema,
            description_placeholders={"device_name": dev_name},
            errors=errors,
        )

    async def async_step_device_learn_record(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2: Start manual 5-second recording when the user is ready."""
        entry_id = self.config_entry.entry_id
        dev_name = self._target_device or "Device"
        btn_name = self._learn_button_name or "Boton"
        entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
        client: Esp32Cc1101Client | None = entry_data.get("client")

        if user_input is not None and client:
            samples: list[str] = []
            decoded_samples: list[dict[str, Any]] = []
            learn_rssi = self._learn_min_rssi

            def on_capture(event_data: dict[str, Any]) -> None:
                event_type = event_data.get("type")
                if event_type in ("rx_code", "rx_raw"):
                    payload = event_data.get("payload", {})
                    pkt_rssi = payload.get("rssi") if payload else None
                    if pkt_rssi is None:
                        pkt_rssi = client.last_rssi
                    if pkt_rssi is not None and pkt_rssi < learn_rssi:
                        _LOGGER.debug(
                            "Dropped weak learning sample: RSSI %s < %s dBm",
                            pkt_rssi,
                            learn_rssi,
                        )
                        return

                    raw = client.last_code or ""
                    if raw:
                        pulse_parts = [p.strip() for p in raw.split(",") if p.strip()]
                        if len(pulse_parts) >= 16:
                            samples.append(raw)
                    if payload and "code" in payload and "bits" in payload:
                        decoded_samples.append({
                            "code": payload["code"],
                            "bits": payload["bits"],
                            "protocol": payload.get("protocol", 1),
                            "pulse_length": payload.get("pulse_length", 350),
                        })

            prev_mode = client.rx_mode
            prev_rssi = client.min_rssi
            await client.async_set_config(min_rssi=learn_rssi, rx_mode="Learn Mode")
            remove_listener = client.register_listener(on_capture)
            try:
                await asyncio.sleep(5.0)
            finally:
                remove_listener()
                await client.async_set_config(min_rssi=prev_rssi, rx_mode=prev_mode or RX_MODE_ALL)

            from . import _analyze_learning_consensus
            result_code, desc = _analyze_learning_consensus(samples, decoded_samples)

            if not result_code:
                self._record_error_desc = desc
                self._record_sample_count = len(samples)
                return await self.async_step_device_learn_failed()

            full_cmd_name = f"{dev_name} - {btn_name}" if dev_name else btn_name
            save_code = dict(result_code) if isinstance(result_code, dict) else result_code
            if isinstance(save_code, dict):
                save_code["device"] = dev_name
                save_code["command"] = btn_name
                save_code["repeats"] = int(self._learn_repeats)
            else:
                save_code = {
                    "type": "raw",
                    "raw": save_code,
                    "device": dev_name,
                    "command": btn_name,
                    "repeats": int(self._learn_repeats),
                }

            saved = entry_data["saved_codes"]
            saved[full_cmd_name] = save_code
            devices_list = saved.setdefault("__devices__", [])
            if dev_name not in devices_list:
                devices_list.append(dev_name)

            await entry_data["store"].async_save(saved)

            if entry_data.get("add_button_callback"):
                entry_data["add_button_callback"](full_cmd_name)

            if dev_name:
                if entry_data.get("add_device_sensor_callback"):
                    try:
                        entry_data["add_device_sensor_callback"](dev_name)
                    except Exception:
                        pass
                device_button_listeners = entry_data.get("device_button_listeners", {})
                listener = device_button_listeners.get(dev_name.lower())
                if listener:
                    try:
                        listener(btn_name, client.last_rssi, save_code.get("code") or save_code.get("raw"))
                    except Exception:
                        pass

            self._candidate_code = save_code
            self._candidate_desc = desc
            return await self.async_step_device_learn_success()

        schema = vol.Schema({})

        return self.async_show_form(
            step_id="device_learn_record",
            data_schema=schema,
            description_placeholders={
                "device_name": dev_name,
                "button_name": btn_name,
                "learn_rssi": str(self._learn_min_rssi),
            },
        )

    async def async_step_device_learn_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Screen shown if 5-second continuous capture did not find consensus."""
        dev_name = self._target_device or "Device"
        btn_name = self._learn_button_name or "Boton"
        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")

        menu: dict[str, str] = {}
        if is_es:
            menu["device_learn_record"] = "Reintentar Grabación (5 segundos)"
            menu["device_learn_tune_rssi"] = f"Ajustar Filtro de Potencia (Actual: {self._learn_min_rssi} dBm)"
            menu["device_learn_button"] = "Cambiar Nombre del Botón"
            menu["device_menu"] = f"Volver al Menú de {dev_name}"
        else:
            menu["device_learn_record"] = "Retry Recording (5 seconds)"
            menu["device_learn_tune_rssi"] = f"Tune Signal Power Filter (Current: {self._learn_min_rssi} dBm)"
            menu["device_learn_button"] = "Change Button Name"
            menu["device_menu"] = f"Return to {dev_name} Menu"

        return self.async_show_menu(
            step_id="device_learn_failed",
            menu_options=menu,
            description_placeholders={
                "button_name": btn_name,
                "error_reason": self._record_error_desc or "Interferencias o señal débil",
                "sample_count": str(self._record_sample_count),
                "device_name": dev_name,
                "learn_rssi": str(self._learn_min_rssi),
            },
        )

    async def async_step_device_learn_tune_rssi(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step to adjust learning squelch/power filter exclusively for recording."""
        dev_name = self._target_device or "Device"
        btn_name = self._learn_button_name or "Boton"

        if user_input is not None:
            self._learn_min_rssi = int(user_input.get("learn_min_rssi", self._learn_min_rssi))
            return await self.async_step_device_learn_record()

        schema = vol.Schema(
            {
                vol.Required(
                    "learn_min_rssi",
                    default=self._learn_min_rssi,
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=-90,
                        max=-30,
                        step=5,
                        unit_of_measurement="dBm",
                        mode=NumberSelectorMode.SLIDER,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="device_learn_tune_rssi",
            data_schema=schema,
            description_placeholders={
                "device_name": dev_name,
                "button_name": btn_name,
                "current_rssi": str(self._learn_min_rssi),
            },
        )

    async def async_step_device_learn_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step: Optional remote detection verification check."""
        entry_id = self.config_entry.entry_id
        dev_name = self._target_device or "Device"
        btn_name = self._learn_button_name or "Boton"
        entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
        client: Esp32Cc1101Client | None = entry_data.get("client")

        if user_input is not None and client:
            from . import _codes_match, _compare_raw_pulses
            matched = False
            matched_signal: Any = None
            loop = asyncio.get_running_loop()
            event_future: asyncio.Future[bool] = loop.create_future()

            def on_verify(event_data: dict[str, Any]) -> None:
                nonlocal matched, matched_signal
                if event_future.done():
                    return
                event_type = event_data.get("type")
                if event_type in ("rx_code", "rx_raw"):
                    payload = event_data.get("payload", {})
                    rx_code = payload.get("code")
                    rx_raw = payload.get("raw") or ""

                    cand = self._candidate_code
                    is_match = False
                    if isinstance(cand, dict):
                        cand_code = cand.get("code")
                        if cand_code is not None and rx_code is not None and _codes_match(cand_code, rx_code):
                            is_match = True
                        elif rx_code is not None:
                            try:
                                rx_int = int(rx_code)
                                cand_int = int(cand_code) if cand_code is not None else None
                                base_id = cand.get("base_id")
                                target_base = int(base_id) if base_id is not None else (cand_int >> 8 if cand_int else None)

                                if target_base:
                                    if (rx_int >> 8) == target_base:
                                        is_match = True
                                        cand["code"] = rx_int
                                        cand["type"] = "rolling_code"
                                    elif rx_int == target_base or (rx_int >> 8) == (target_base >> 8):
                                        is_match = True
                                elif cand_int:
                                    if (rx_int >> 8) == (cand_int >> 8) or (cand_int >> 8) == rx_int or (rx_int >> 8) == cand_int:
                                        is_match = True
                            except (ValueError, TypeError):
                                pass

                        if not is_match and rx_raw and cand.get("raw"):
                            if _compare_raw_pulses(rx_raw, cand["raw"]) >= 0.70:
                                is_match = True
                    elif isinstance(cand, str) and rx_raw:
                        if _compare_raw_pulses(rx_raw, cand) >= 0.70:
                            is_match = True

                    if is_match:
                        matched = True
                        matched_signal = rx_code or rx_raw
                        if not event_future.done():
                            event_future.set_result(True)

            remove_listener = client.register_listener(on_verify)
            try:
                await asyncio.wait_for(event_future, timeout=12.0)
            except asyncio.TimeoutError:
                pass
            finally:
                remove_listener()

            if not matched:
                return await self.async_step_device_learn_verify_failed()

            if dev_name:
                device_button_listeners = entry_data.get("device_button_listeners", {})
                listener = device_button_listeners.get(dev_name.lower())
                if listener:
                    try:
                        listener(btn_name, client.last_rssi, matched_signal)
                    except Exception:
                        pass

            return await self.async_step_device_learn_success()

        schema = vol.Schema({})

        return self.async_show_form(
            step_id="device_learn_verify",
            data_schema=schema,
            description_placeholders={
                "candidate_desc": self._candidate_desc or "Señal capturada",
                "button_name": btn_name,
                "device_name": dev_name,
            },
        )

    async def async_step_device_learn_verify_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Screen shown when verification single-click did not match or timed out."""
        dev_name = self._target_device or "Device"
        btn_name = self._learn_button_name or "Boton"
        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")

        menu: dict[str, str] = {}
        if is_es:
            menu["device_learn_verify"] = "Reintentar Comprobación (pulsar 1 vez)"
            menu["device_test_send"] = "Probar Envío RF desde Home Assistant"
            menu["device_learn_button"] = f"Aprender otro botón para {dev_name}"
            menu["device_learn_record"] = "Volver a Grabar (5 segundos continuos)"
            menu["device_menu"] = f"Finalizar y volver al menú de {dev_name}"
        else:
            menu["device_learn_verify"] = "Retry Verification (Press 1 time)"
            menu["device_test_send"] = "Test RF Transmit from Home Assistant"
            menu["device_learn_button"] = f"Learn another button for {dev_name}"
            menu["device_learn_record"] = "Re-record (5 seconds hold)"
            menu["device_menu"] = f"Finish and return to {dev_name} menu"

        return self.async_show_menu(
            step_id="device_learn_verify_failed",
            menu_options=menu,
            description_placeholders={
                "button_name": btn_name,
                "device_name": dev_name,
            },
        )

    async def async_step_device_test_send(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Test transmit the learned button code to check if appliance responds."""
        dev_name = self._target_device or "Device"
        btn_name = self._learn_button_name or "Boton"
        entry_id = self.config_entry.entry_id
        entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
        client: Esp32Cc1101Client | None = entry_data.get("client")
        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")

        has_raw = False
        if client and self._candidate_code:
            code_obj = self._candidate_code
            repeats = int(self._learn_repeats)
            has_raw = isinstance(code_obj, dict) and bool(code_obj.get("raw"))
            try:
                if isinstance(code_obj, dict) and "code" in code_obj and code_obj["code"] is not None:
                    await client.async_send_code(
                        code=code_obj["code"],
                        bits=code_obj.get("bits", 24),
                        protocol=code_obj.get("protocol", 1),
                        pulse_length=code_obj.get("pulse_length", 350),
                        repeats=repeats,
                    )
                elif isinstance(code_obj, dict) and "raw" in code_obj:
                    await client.async_send_raw(code_obj["raw"], repeats=repeats)
                elif isinstance(code_obj, str):
                    await client.async_send_raw(code_obj, repeats=repeats)
            except Exception as err:
                _LOGGER.error("Failed to test send RF code: %s", err)

        menu: dict[str, str] = {}
        if is_es:
            menu["device_test_send"] = "Enviar Señal Otra Vez (Código)"
            if has_raw:
                menu["device_test_send_raw"] = "Probar Envío con Pulsos Raw"
            menu["device_learn_verify"] = "Probar Detección del Mando Físico"
            menu["device_learn_button"] = f"Aprender otro botón para {dev_name}"
            menu["device_menu"] = f"Volver al menú de {dev_name}"
            menu["finish"] = "Finalizar y Cerrar"
        else:
            menu["device_test_send"] = "Send Signal Again (Code)"
            if has_raw:
                menu["device_test_send_raw"] = "Test Transmit using Raw Pulses"
            menu["device_learn_verify"] = "Test Physical Remote Detection"
            menu["device_learn_button"] = f"Learn another button for {dev_name}"
            menu["device_menu"] = f"Return to {dev_name} menu"
            menu["finish"] = "Finish and Close"

        return self.async_show_menu(
            step_id="device_test_send",
            menu_options=menu,
            description_placeholders={
                "button_name": btn_name,
                "device_name": dev_name,
            },
        )

    async def async_step_device_test_send_raw(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Test transmit raw pulses directly for this learned button."""
        entry_id = self.config_entry.entry_id
        entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
        client: Esp32Cc1101Client | None = entry_data.get("client")
        dev_name = self._target_device or "Device"
        btn_name = self._learn_button_name or "Boton"
        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")

        raw_str = ""
        if isinstance(self._candidate_code, dict):
            raw_str = self._candidate_code.get("raw") or ""
        elif isinstance(self._candidate_code, str):
            raw_str = self._candidate_code

        if client and raw_str:
            try:
                await client.async_send_raw(raw_str, repeats=int(self._learn_repeats))
            except Exception as err:
                _LOGGER.error("Failed to test send raw RF pulses: %s", err)

        menu: dict[str, str] = {}
        if is_es:
            menu["device_test_send_raw"] = "Enviar Pulsos Raw Otra Vez"
            menu["device_test_send"] = "Probar Envío con Código Estándar"
            menu["device_learn_verify"] = "Probar Detección del Mando Físico"
            menu["device_learn_button"] = f"Aprender otro botón para {dev_name}"
            menu["device_menu"] = f"Volver al menú de {dev_name}"
            menu["finish"] = "Finalizar y Cerrar"
        else:
            menu["device_test_send_raw"] = "Send Raw Pulses Again"
            menu["device_test_send"] = "Test Send Standard Code"
            menu["device_learn_verify"] = "Test Physical Remote Detection"
            menu["device_learn_button"] = f"Learn another button for {dev_name}"
            menu["device_menu"] = f"Return to {dev_name} menu"
            menu["finish"] = "Finish and Close"

        return self.async_show_menu(
            step_id="device_test_send",
            menu_options=menu,
            description_placeholders={
                "button_name": btn_name,
                "device_name": dev_name,
            },
        )

    async def async_step_device_learn_success(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Screen shown after button is learned and saved."""
        dev_name = self._target_device or "Device"
        btn_name = self._learn_button_name or "Boton"
        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")

        menu: dict[str, str] = {}
        if is_es:
            menu["device_test_send"] = "Probar Envío RF (Comprobar aparato)"
            menu["device_learn_verify"] = "Probar Detección del Mando Físico"
            menu["device_learn_button"] = f"Aprender otro botón para {dev_name}"
            menu["device_menu"] = f"Volver al menú de {dev_name}"
            menu["finish"] = "Finalizar y Cerrar"
        else:
            menu["device_test_send"] = "Test RF Transmit (Check device)"
            menu["device_learn_verify"] = "Test Physical Remote Detection"
            menu["device_learn_button"] = f"Learn another button for {dev_name}"
            menu["device_menu"] = f"Return to {dev_name} menu"
            menu["finish"] = "Finish and Close"

        return self.async_show_menu(
            step_id="device_learn_success",
            menu_options=menu,
            description_placeholders={
                "button_name": btn_name,
                "candidate_desc": self._candidate_desc or "Señal confirmada",
                "device_name": dev_name,
            },
        )

    async def async_step_radio_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage live CC1101 radio tuning and USB serial port configuration."""
        if user_input is not None:
            new_port = user_input.pop(CONF_PORT, None)
            port_changed = False
            cur_port = self.config_entry.data.get(CONF_PORT)
            if new_port and new_port != cur_port:
                new_data = dict(self.config_entry.data)
                new_data[CONF_PORT] = new_port
                self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)
                port_changed = True

            freq_choice = user_input.get(CONF_FREQUENCY)
            if freq_choice in FREQUENCY_PRESET_MAP:
                user_input[CONF_FREQUENCY] = FREQUENCY_PRESET_MAP[freq_choice]

            result = self.async_create_entry(title="", data=user_input)
            if port_changed:
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self.config_entry.entry_id)
                )
            return result

        port_options = await self.hass.async_add_executor_job(_get_serial_ports)
        cur_port = self.config_entry.data.get(CONF_PORT, DEFAULT_PORT)
        if not any(opt["value"] == cur_port for opt in port_options):
            port_options.insert(0, SelectOptionDict(value=cur_port, label=f"{cur_port} (Actual)"))

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
                vol.Required(CONF_PORT, default=cur_port): SelectSelector(
                    SelectSelectorConfig(
                        options=port_options,
                        mode=SelectSelectorMode.DROPDOWN,
                        custom_value=True,
                    )
                ),
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
                vol.Required(CONF_TX_POWER, default=cur_pa): NumberSelector(
                    NumberSelectorConfig(
                        min=-30,
                        max=10,
                        step=1,
                        unit_of_measurement="dBm",
                        mode=NumberSelectorMode.SLIDER,
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
                vol.Required(CONF_REPEATS, default=cur_repeats): NumberSelector(
                    NumberSelectorConfig(
                        min=1,
                        max=25,
                        step=1,
                        mode=NumberSelectorMode.BOX,
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

    async def async_step_export_json(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Export all learned buttons and devices as formatted JSON."""
        entry_data = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
        saved_codes = entry_data.get("saved_codes", {})

        devices = list(saved_codes.get("__devices__", []))
        buttons = {k: v for k, v in saved_codes.items() if not k.startswith("__")}

        export_payload = {
            "version": 1,
            "exported_at": datetime.now().isoformat(),
            "devices": devices,
            "buttons": buttons,
        }
        json_str = json.dumps(export_payload, indent=2, ensure_ascii=False)

        backup_path = self.hass.config.path("esp32_cc1101_backup.json")
        backup_note = ""
        try:
            with open(backup_path, "w", encoding="utf-8") as f:
                f.write(json_str)
            backup_note = f"\n\n*Se ha guardado automáticamente una copia en `{backup_path}`*"
        except Exception:
            pass

        if user_input is not None:
            return await self.async_step_backup_menu()

        schema = vol.Schema(
            {
                vol.Optional("json_data", default=json_str): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        )

        return self.async_show_form(
            step_id="export_json",
            data_schema=schema,
            description_placeholders={
                "json_text": json_str,
                "button_count": str(len(buttons)),
                "device_count": str(len(devices)),
                "backup_note": backup_note,
            },
        )

    async def async_step_import_json(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Import learned buttons and devices from a JSON string."""
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_text = user_input.get("json_data", "").strip()
            mode = user_input.get("mode", "merge")
            if not raw_text:
                return await self.async_step_backup_menu()
            else:
                try:
                    parsed = json.loads(raw_text)
                    imported_buttons: dict[str, Any] = {}
                    imported_devices: list[str] = []

                    if isinstance(parsed, dict) and "buttons" in parsed:
                        imported_buttons = parsed.get("buttons", {})
                        imported_devices = parsed.get("devices", [])
                    elif isinstance(parsed, dict):
                        imported_buttons = {k: v for k, v in parsed.items() if not k.startswith("__")}
                        imported_devices = parsed.get("__devices__", [])

                    if not imported_buttons:
                        errors["json_data"] = "no_buttons_found"
                    else:
                        entry_data = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
                        saved_codes = entry_data.get("saved_codes", {})

                        if mode == "replace":
                            saved_codes.clear()
                            saved_codes["__devices__"] = []

                        devices_set = set(saved_codes.setdefault("__devices__", []))
                        for d in imported_devices:
                            if d and d not in devices_set:
                                devices_set.add(d)

                        count_imported = 0
                        for k, v in imported_buttons.items():
                            if k.startswith("__"):
                                continue
                            saved_codes[k] = v
                            count_imported += 1
                            dev = v.get("device") if isinstance(v, dict) else None
                            if not dev and " - " in k:
                                dev = k.split(" - ", 1)[0].strip()
                            if dev:
                                devices_set.add(dev)
                            if entry_data.get("add_button_callback"):
                                try:
                                    entry_data["add_button_callback"](k)
                                except Exception:
                                    pass

                        saved_codes["__devices__"] = sorted(list(devices_set))
                        for d in devices_set:
                            if entry_data.get("add_device_sensor_callback"):
                                try:
                                    entry_data["add_device_sensor_callback"](d)
                                except Exception:
                                    pass

                        await entry_data["store"].async_save(saved_codes)
                        self._menu_message = (
                            f"Importación completada con éxito: {count_imported} botones importados "
                            f"en {len(devices_set)} dispositivos (Modo: {'Fusión' if mode == 'merge' else 'Reemplazo total'})."
                        )
                        return await self.async_step_backup_menu()
                except json.JSONDecodeError as err:
                    _LOGGER.error("Invalid JSON provided for import: %s", err)
                    errors["json_data"] = "invalid_json"

        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")
        options = [
            SelectOptionDict(
                value="merge",
                label="Fusionar (Mantener existentes y añadir nuevos)" if is_es else "Merge (Keep existing and add new)",
            ),
            SelectOptionDict(
                value="replace",
                label="Reemplazar todo (Borrar existentes y sustituir)" if is_es else "Replace All (Wipe existing and replace)",
            ),
        ]

        return self.async_show_form(
            step_id="import_json",
            data_schema=vol.Schema(
                {
                    vol.Required("json_data"): selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    ),
                    vol.Required("mode", default="merge"): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Finish and close the options flow."""
        return self.async_create_entry(title="", data={})

    # Aliases for backwards-compatibility
    async def async_step_device_inspect_buttons(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Alias for device_manage_buttons."""
        return await self.async_step_device_manage_buttons(user_input)

    async def async_step_device_inspect_button_detail(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Alias for button_edit_menu."""
        return await self.async_step_button_edit_menu(user_input)

    async def async_step_device_configure_button(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Alias for button_repeats."""
        return await self.async_step_button_repeats(user_input)

    async def async_step_device_delete_button(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Alias for button_delete."""
        return await self.async_step_button_delete(user_input)

    async def async_step_device_delete_this_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Alias for device_delete."""
        return await self.async_step_device_delete(user_input)

    async def async_step_device_move_button(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Alias for button_move."""
        return await self.async_step_button_move(user_input)

    async def async_step_inspect_test_code(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Alias for button_test_code."""
        return await self.async_step_button_test_code(user_input)

    async def async_step_inspect_test_raw(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Alias for button_test_raw."""
        return await self.async_step_button_test_raw(user_input)

    async def async_step_inspect_toggle_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Alias for button_toggle_mode."""
        return await self.async_step_button_toggle_mode(user_input)

    async def async_step_inspect_move_button(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Alias for button_move."""
        return await self.async_step_button_move(user_input)

    async def async_step_delete_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Alias for manage_devices."""
        return await self.async_step_manage_devices(user_input)

    async def async_step_select_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Alias for manage_devices."""
        return await self.async_step_manage_devices(user_input)

    async def async_step_learn_button(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Alias for manage_devices."""
        return await self.async_step_manage_devices(user_input)
