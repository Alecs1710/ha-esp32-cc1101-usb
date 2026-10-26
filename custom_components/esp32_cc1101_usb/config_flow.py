"""Config flow for ESP32 CC1101 USB RF integration."""

from __future__ import annotations

import asyncio
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
    RX_MODE_ALL,
    DEFAULT_RX_MODE,
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
        self._learn_button_name: str | None = None
        self._learn_repeats: int = DEFAULT_REPEATS
        self._learn_min_rssi: int = -60
        self._candidate_code: Any = None
        self._candidate_desc: str = ""
        self._record_error_desc: str = ""
        self._record_sample_count: int = 0
        self._menu_message: str | None = None

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
                        return await self.async_step_device_menu()

        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")
        if is_es:
            menu = {
                "manage_devices": "Gestionar y Aprender Botones por Dispositivo",
                "delete_menu": "Eliminar Botones o Dispositivos RF",
                "radio_settings": "Parametros de Radio CC1101",
            }
        else:
            menu = {
                "manage_devices": "Manage & Learn Buttons by Device",
                "delete_menu": "Delete RF Buttons or Devices",
                "radio_settings": "CC1101 Radio Parameters & Tuning",
            }

        return self.async_show_menu(
            step_id="init",
            menu_options=menu,
        )

    async def async_step_device_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Show action menu for this specific device."""
        dev_name = self._target_device or "Device"
        buttons = self._get_device_buttons(dev_name)
        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")
        if is_es:
            menu = {
                "device_learn_button": f"Aprender Nuevo Boton ({dev_name})",
            }
            if buttons:
                menu["device_configure_button"] = "Configurar Repeticiones de Boton"
                menu["device_delete_button"] = "Eliminar Boton de este Dispositivo"
            menu["device_delete_this_device"] = f"Eliminar este Dispositivo Completo ({dev_name})"
            menu["manage_devices"] = "Cambiar de Dispositivo"
            menu["finish"] = "Finalizar y Cerrar"
        else:
            menu = {
                "device_learn_button": f"Learn New Button ({dev_name})",
            }
            if buttons:
                menu["device_configure_button"] = "Configure Button Repetitions"
                menu["device_delete_button"] = "Delete Button from Device"
            menu["device_delete_this_device"] = f"Delete this Device ({dev_name})"
            menu["manage_devices"] = "Change Device"
            menu["finish"] = "Finish and Close"

        status_msg = f"{self._menu_message}\n\n" if self._menu_message else ""
        self._menu_message = None

        return self.async_show_menu(
            step_id="device_menu",
            menu_options=menu,
            description_placeholders={
                "device_name": dev_name,
                "status_msg": status_msg,
            },
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Finish and close the options flow."""
        return self.async_create_entry(title="", data={})

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
            # Restrict receiver sensitivity strictly for learning to reject weak noise/distant remotes
            await client.async_set_config(min_rssi=learn_rssi, rx_mode="Learn Mode")
            remove_listener = client.register_listener(on_capture)
            try:
                # Capture continuous button press bursts for 5 seconds
                await asyncio.sleep(5.0)
            finally:
                remove_listener()
                # Immediately restore normal squelch and mode for standard household reception
                await client.async_set_config(min_rssi=prev_rssi, rx_mode=prev_mode or RX_MODE_ALL)

            from . import _analyze_learning_consensus
            result_code, desc = _analyze_learning_consensus(samples, decoded_samples)

            if not result_code:
                self._record_error_desc = desc
                self._record_sample_count = len(samples)
                return await self.async_step_device_learn_failed()

            self._candidate_code = result_code
            self._candidate_desc = desc
            return await self.async_step_device_learn_verify()

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
            menu["device_learn_record"] = "Reintentar Grabacion (5 segundos)"
            menu["device_learn_tune_rssi"] = f"Ajustar Filtro de Potencia (Actual: {self._learn_min_rssi} dBm)"
            menu["device_learn_button"] = "Cambiar Nombre del Boton"
            menu["device_menu"] = f"Volver al Menu de {dev_name}"
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
                "error_reason": self._record_error_desc or "Interferencias o senal debil",
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
        """Step 3: Verification single-click check in-dialog."""
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
                        elif cand.get("type") == "rolling_code" and rx_code is not None:
                            base_id = cand.get("base_id")
                            try:
                                rx_int = int(rx_code)
                                if rx_int > 0xFFFFFF:
                                    b_rx = rx_int & 0xFF
                                    if (((b_rx >> 4) & 0x0F) + (b_rx & 0x0F)) == 7 and base_id and (rx_int >> 8) == int(base_id):
                                        is_match = True
                                        cand["code"] = rx_int
                            except (ValueError, TypeError):
                                pass
                        elif cand_code is not None and rx_code is not None:
                            try:
                                rx_int = int(rx_code)
                                cand_int = int(cand_code)
                                # Only upgrade to rolling_code if 32-bit counter protocol is verified
                                if rx_int > 0xFFFFFF and cand_int > 0xFFFFFF:
                                    rx_base = rx_int >> 8
                                    cand_base = cand_int >> 8
                                    if rx_base == cand_base and rx_base > 0xFFFF:
                                        b_rx = rx_int & 0xFF
                                        b_cand = cand_int & 0xFF
                                        if (((b_rx >> 4) & 0x0F) + (b_rx & 0x0F)) == 7 and (((b_cand >> 4) & 0x0F) + (b_cand & 0x0F)) == 7:
                                            is_match = True
                                            cand["type"] = "rolling_code"
                                            cand["base_id"] = rx_base
                                            cand["delta"] = 15
                                            cand["code"] = rx_int
                            except (ValueError, TypeError):
                                pass

                        # Only use raw pulse correlation if rx_code was NOT decoded
                        if not is_match and rx_code is None and rx_raw and cand.get("raw"):
                            if _compare_raw_pulses(rx_raw, cand["raw"]) >= 0.85:
                                is_match = True
                    elif isinstance(cand, str) and rx_code is None and rx_raw:
                        if _compare_raw_pulses(rx_raw, cand) >= 0.85:
                            is_match = True

                    if is_match:
                        matched = True
                        matched_signal = rx_code or rx_raw
                        if not event_future.done():
                            event_future.set_result(True)

            remove_listener = client.register_listener(on_verify)
            try:
                await asyncio.wait_for(event_future, timeout=6.0)
            except asyncio.TimeoutError:
                pass
            finally:
                remove_listener()

            if not matched:
                return await self.async_step_device_learn_verify_failed()

            # Matched! Save code and create entity
            full_cmd_name = f"{dev_name} - {btn_name}" if dev_name else btn_name
            result_code = self._candidate_code
            if isinstance(result_code, dict):
                result_code["device"] = dev_name
                result_code["command"] = btn_name
                result_code["repeats"] = int(self._learn_repeats)
            else:
                result_code = {
                    "type": "raw",
                    "raw": result_code,
                    "device": dev_name,
                    "command": btn_name,
                    "repeats": int(self._learn_repeats),
                }

            saved = entry_data["saved_codes"]
            saved[full_cmd_name] = result_code
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
                        listener(btn_name, client.last_rssi, matched_signal)
                    except Exception:
                        pass

            return await self.async_step_device_learn_success()

        schema = vol.Schema({})

        return self.async_show_form(
            step_id="device_learn_verify",
            data_schema=schema,
            description_placeholders={
                "candidate_desc": self._candidate_desc or "Senal capturada",
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
            menu["device_learn_verify"] = "Reintentar Comprobacion (pulsar 1 vez)"
            menu["device_learn_record"] = "Volver a Grabar (5 segundos continuos)"
            menu["device_menu"] = f"Cancelar y volver al menu de {dev_name}"
        else:
            menu["device_learn_verify"] = "Retry Verification (Press 1 time)"
            menu["device_learn_record"] = "Re-record (5 seconds hold)"
            menu["device_menu"] = f"Cancel and return to {dev_name} menu"

        return self.async_show_menu(
            step_id="device_learn_verify_failed",
            menu_options=menu,
            description_placeholders={
                "button_name": btn_name,
                "device_name": dev_name,
            },
        )

    async def async_step_device_learn_success(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Screen shown after successful learning & verification."""
        dev_name = self._target_device or "Device"
        btn_name = self._learn_button_name or "Boton"
        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")

        menu: dict[str, str] = {}
        if is_es:
            menu["device_learn_button"] = f"Aprender otro boton para {dev_name}"
            menu["device_menu"] = f"Volver al menu de {dev_name}"
            menu["finish"] = "Finalizar y Cerrar"
        else:
            menu["device_learn_button"] = f"Learn another button for {dev_name}"
            menu["device_menu"] = f"Return to {dev_name} menu"
            menu["finish"] = "Finish and Close"

        return self.async_show_menu(
            step_id="device_learn_success",
            menu_options=menu,
            description_placeholders={
                "button_name": btn_name,
                "candidate_desc": self._candidate_desc or "Senal confirmada",
                "device_name": dev_name,
            },
        )

    async def async_step_device_configure_button(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure repetitions for a learned button on this device."""
        errors: dict[str, str] = {}
        entry_id = self.config_entry.entry_id
        dev_name = self._target_device or "Device"
        entry_data = self.hass.data.get(DOMAIN, {}).get(entry_id, {})
        saved = entry_data.get("saved_codes", {})
        buttons = self._get_device_buttons(dev_name)

        if not buttons:
            return await self.async_step_device_learn_button()

        btn_list = sorted(list(buttons.keys()))

        if user_input is not None:
            chosen_btn = user_input.get("button_name")
            new_repeats = user_input.get("repeats", DEFAULT_REPEATS)
            full_key = buttons.get(chosen_btn)
            if full_key and full_key in saved:
                val = saved[full_key]
                if isinstance(val, dict):
                    val["repeats"] = int(new_repeats)
                else:
                    saved[full_key] = {
                        "type": "raw",
                        "raw": val,
                        "device": dev_name,
                        "command": chosen_btn,
                        "repeats": int(new_repeats),
                    }
                await entry_data["store"].async_save(saved)
                self._menu_message = f"Repeticiones de '{chosen_btn}' actualizadas a {new_repeats}."
                return await self.async_step_device_menu()

        first_key = buttons[btn_list[0]]
        first_val = saved.get(first_key, {})
        cur_repeats = first_val.get("repeats", DEFAULT_REPEATS) if isinstance(first_val, dict) else DEFAULT_REPEATS

        schema = vol.Schema(
            {
                vol.Required("button_name", default=btn_list[0]): SelectSelector(
                    SelectSelectorConfig(
                        options=[SelectOptionDict(value=b, label=b) for b in btn_list],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required("repeats", default=cur_repeats): NumberSelector(
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
            step_id="device_configure_button",
            data_schema=schema,
            description_placeholders={"device_name": dev_name},
            errors=errors,
        )

    async def async_step_device_delete_button(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Delete an individual button from this device."""
        errors: dict[str, str] = {}
        entry_id = self.config_entry.entry_id
        dev_name = self._target_device or "Device"
        buttons = self._get_device_buttons(dev_name)

        if not buttons:
            self._menu_message = f"No quedan botones para eliminar en {dev_name}."
            return await self.async_step_device_menu()

        btn_list = sorted(list(buttons.keys()))

        if user_input is not None:
            chosen_btn = user_input.get("button_name")
            full_key = buttons.get(chosen_btn)
            if full_key:
                from . import async_delete_command_flow
                await async_delete_command_flow(self.hass, entry_id, full_key)
                self._menu_message = f"Boton '{chosen_btn}' eliminado correctamente de {dev_name}."
                return await self.async_step_device_menu()

        schema = vol.Schema(
            {
                vol.Required("button_name", default=btn_list[0]): SelectSelector(
                    SelectSelectorConfig(
                        options=[SelectOptionDict(value=b, label=b) for b in btn_list],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="device_delete_button",
            data_schema=schema,
            description_placeholders={"device_name": dev_name},
            errors=errors,
        )

    async def async_step_device_delete_this_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm deletion of this device and all its associated buttons."""
        entry_id = self.config_entry.entry_id
        dev_name = self._target_device or "Device"

        if user_input is not None:
            if user_input.get("confirm", True):
                from . import async_delete_device_flow
                await async_delete_device_flow(self.hass, entry_id, dev_name)
                self._menu_message = f"Dispositivo '{dev_name}' y todos sus botones han sido eliminados."
                self._target_device = None
                return await self.async_step_manage_devices()
            return await self.async_step_device_menu()

        schema = vol.Schema(
            {
                vol.Required("confirm", default=True): bool,
            }
        )

        return self.async_show_form(
            step_id="device_delete_this_device",
            data_schema=schema,
            description_placeholders={"device_name": dev_name},
        )

    async def async_step_delete_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Global menu to delete buttons or devices across the integration."""
        is_es = (getattr(self.hass.config, "language", "") or "").lower().startswith("es")
        all_buttons = self._get_all_buttons()
        all_devs = self._get_all_devices()

        if is_es:
            menu = {}
            if all_buttons:
                menu["delete_single_button"] = f"Eliminar un Boton ({len(all_buttons)} disponibles)"
            if all_devs:
                menu["delete_device"] = f"Eliminar un Dispositivo Completo ({len(all_devs)} disponibles)"
            menu["init"] = "Volver al Menu Principal"
            menu["finish"] = "Finalizar y Cerrar"
        else:
            menu = {}
            if all_buttons:
                menu["delete_single_button"] = f"Delete a Button ({len(all_buttons)} available)"
            if all_devs:
                menu["delete_device"] = f"Delete an Entire Device ({len(all_devs)} available)"
            menu["init"] = "Return to Main Menu"
            menu["finish"] = "Finish and Close"

        status_msg = f"{self._menu_message}\n\n" if self._menu_message else ""
        self._menu_message = None

        return self.async_show_menu(
            step_id="delete_menu",
            menu_options=menu,
            description_placeholders={"status_msg": status_msg},
        )

    async def async_step_delete_single_button(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Delete any button across all devices."""
        entry_id = self.config_entry.entry_id
        all_buttons = self._get_all_buttons()

        if not all_buttons:
            self._menu_message = "No quedan botones para eliminar."
            return await self.async_step_delete_menu()

        btn_labels = list(all_buttons.keys())

        if user_input is not None:
            chosen_label = user_input.get("button_name")
            full_key = all_buttons.get(chosen_label)
            if full_key:
                from . import async_delete_command_flow
                await async_delete_command_flow(self.hass, entry_id, full_key)
                self._menu_message = f"Boton '{chosen_label}' eliminado correctamente."
                return await self.async_step_delete_menu()

        schema = vol.Schema(
            {
                vol.Required("button_name", default=btn_labels[0]): SelectSelector(
                    SelectSelectorConfig(
                        options=[SelectOptionDict(value=b, label=b) for b in btn_labels],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="delete_single_button",
            data_schema=schema,
        )

    async def async_step_delete_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Delete an entire device and all its buttons."""
        entry_id = self.config_entry.entry_id
        all_devs = self._get_all_devices()

        if not all_devs:
            self._menu_message = "No quedan dispositivos para eliminar."
            return await self.async_step_delete_menu()

        if user_input is not None:
            chosen_dev = user_input.get("device_name")
            if chosen_dev:
                from . import async_delete_device_flow
                await async_delete_device_flow(self.hass, entry_id, chosen_dev)
                self._menu_message = f"Dispositivo '{chosen_dev}' y todos sus botones han sido eliminados correctamente."
                return await self.async_step_delete_menu()

        schema = vol.Schema(
            {
                vol.Required("device_name", default=all_devs[0]): SelectSelector(
                    SelectSelectorConfig(
                        options=[SelectOptionDict(value=d, label=d) for d in all_devs],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="delete_device",
            data_schema=schema,
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

                if entry_data.get("add_device_sensor_callback"):
                    try:
                        entry_data["add_device_sensor_callback"](dev_name)
                    except Exception as err:
                        _LOGGER.debug("Could not add device sensor: %s", err)

                try:
                    from homeassistant.components import persistent_notification
                    persistent_notification.async_create(
                        self.hass,
                        f"Device **{dev_name}** has been created!\n\n"
                        f"Open **{dev_name}** in Settings -> Devices and click **Configure** to learn remote buttons for it.",
                        title=f"Device Created: {dev_name}",
                        notification_id=f"{DOMAIN}_dev_created_{slug_dev}",
                    )
                except Exception as notif_err:
                    _LOGGER.debug("Notification notice: %s", notif_err)

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

    async def async_step_manage_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select an existing device or enter a new device name."""
        errors: dict[str, str] = {}
        entry_id = self.config_entry.entry_id
        devs = self._get_all_devices()

        if user_input is not None:
            dev_name = user_input.get("device_name", "").strip()
            if not dev_name:
                errors["device_name"] = "device_name_required"
            else:
                self._target_device = dev_name
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

                if entry_data.get("add_device_sensor_callback"):
                    try:
                        entry_data["add_device_sensor_callback"](dev_name)
                    except Exception as err:
                        _LOGGER.debug("Could not add device sensor: %s", err)

                return await self.async_step_device_menu()

        if devs:
            dev_selector = SelectSelector(
                SelectSelectorConfig(
                    options=[SelectOptionDict(value=d, label=d) for d in devs],
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
                    vol.Required("device_name", default="Ventilador"): str,
                }
            )

        return self.async_show_form(
            step_id="manage_devices",
            data_schema=schema,
            errors=errors,
        )

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
