"""The ESP32 CC1101 USB RF integration."""

from __future__ import annotations

import asyncio
from collections import Counter
import logging
import os
import statistics
import time
from typing import Any
import voluptuous as vol

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.storage import Store

from .client import Esp32Cc1101Client
from .flasher import flash_firmware_sync, get_default_firmware_path
from .const import (
    DOMAIN,
    CONF_FREQUENCY,
    CONF_MODULATION,
    CONF_BANDWIDTH,
    CONF_TX_POWER,
    CONF_REPEATS,
    CONF_MIN_RSSI,
    CONF_MIN_REPEATS,
    CONF_RX_MODE,
    DEFAULT_FREQUENCY,
    DEFAULT_MODULATION,
    DEFAULT_BANDWIDTH,
    DEFAULT_TX_POWER,
    DEFAULT_REPEATS,
    DEFAULT_MIN_RSSI,
    DEFAULT_MIN_REPEATS,
    RX_MODE_DECODED,
    STORAGE_KEY,
    STORAGE_VERSION,
    LEARN_STEPS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["remote", "sensor", "select", "number", "button", "update", "text"]

SERVICE_SEND_RAW_SCHEMA = vol.Schema(
    {
        vol.Required("code"): cv.string,
        vol.Optional("repeats"): vol.Coerce(int),
        vol.Optional("gap"): vol.Coerce(int),
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_SEND_CODE_SCHEMA = vol.Schema(
    {
        vol.Required("code"): vol.Coerce(int),
        vol.Optional("bits", default=24): vol.Coerce(int),
        vol.Optional("protocol", default=1): vol.Coerce(int),
        vol.Optional("pulse_length", default=350): vol.Coerce(int),
        vol.Optional("repeats"): vol.Coerce(int),
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_SET_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional("frequency"): vol.Coerce(float),
        vol.Optional("modulation"): cv.string,
        vol.Optional("bandwidth"): vol.Coerce(float),
        vol.Optional("tx_power"): vol.Coerce(int),
        vol.Optional("min_rssi"): vol.Coerce(int),
        vol.Optional("min_repeats"): vol.Coerce(int),
        vol.Optional("rx_mode"): cv.string,
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_LEARN_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("repeats"): vol.Coerce(int),
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_DELETE_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_PING_SCHEMA = vol.Schema(
    {
        vol.Optional("config_entry_id"): cv.string,
    }
)

SERVICE_FLASH_FIRMWARE_SCHEMA = vol.Schema(
    {
        vol.Optional("file_path"): cv.string,
        vol.Optional("config_entry_id"): cv.string,
    }
)


def _analyze_learning_consensus(
    samples: list[str], decoded_samples: list[dict[str, Any]]
) -> tuple[dict[str, Any] | str, str]:
    """Analyzes multiple repetitions across button presses to find the true, confirmed RF code."""
    # 1. Analyze decoded samples across all button presses
    if decoded_samples:
        bit_counts = Counter(s.get("bits", 0) for s in decoded_samples if s.get("bits"))
        if bit_counts:
            # Prioritize the longest complete bit length that appeared repeatedly
            dominant_bits = None
            for b, count in sorted(bit_counts.items(), key=lambda x: x[0], reverse=True):
                if count >= 2 or count == len(decoded_samples):
                    dominant_bits = b
                    break
            if not dominant_bits:
                dominant_bits = bit_counts.most_common(1)[0][0]

            valid_samples = [s for s in decoded_samples if s.get("bits") == dominant_bits]
            if valid_samples:
                codes = [s["code"] for s in valid_samples]
                proto = valid_samples[0].get("protocol", 1)
                pulse_lengths = [s.get("pulse_length", 350) for s in valid_samples if s.get("pulse_length")]
                pulse_len = round(statistics.median(pulse_lengths)) if pulse_lengths else 350

                # Check for rolling counter across ordered distinct button presses
                unique_ordered: list[int] = []
                for c in codes:
                    if not unique_ordered or unique_ordered[-1] != c:
                        unique_ordered.append(c)

                if len(unique_ordered) >= 3:
                    deltas = [unique_ordered[i + 1] - unique_ordered[i] for i in range(len(unique_ordered) - 1)]
                    delta_counts = Counter(deltas)
                    common_delta, delta_freq = delta_counts.most_common(1)[0]
                    if delta_freq >= len(deltas) * 0.6 and common_delta > 0:
                        latest_code = unique_ordered[-1]
                        base_id = latest_code >> 8
                        desc = (
                            f"Learned as **Decoded Rolling Code** "
                            f"(Base: `0x{base_id:06X}`, Step: `+{common_delta}`, {dominant_bits} bits, Protocol {proto})"
                        )
                        return {
                            "type": "rolling_code",
                            "code": latest_code,
                            "base_id": base_id,
                            "delta": common_delta,
                            "bits": dominant_bits,
                            "protocol": proto,
                            "pulse_length": pulse_len,
                        }, desc

                # Fixed code (majority consensus)
                most_common_code, _ = Counter(codes).most_common(1)[0]
                desc = (
                    f"Learned as **Decoded Protocol** "
                    f"(Code: `{most_common_code}`, {dominant_bits} bits, Protocol {proto})"
                )
                return {
                    "type": "code",
                    "code": most_common_code,
                    "bits": dominant_bits,
                    "protocol": proto,
                    "pulse_length": pulse_len,
                }, desc

    # 2. Raw pulse analysis across repetitions if not decoded
    parsed: list[list[int]] = []
    for s in samples:
        try:
            pulses = [int(x.strip()) for x in s.split(",") if x.strip()]
            if len(pulses) >= 16:
                parsed.append(pulses)
        except ValueError:
            continue

    if not parsed:
        raw_final = samples[-1] if samples else ""
        return raw_final, f"Learned as **Raw Pulses** ({len(raw_final.split(','))} pulses)"

    target_len = Counter(len(p) for p in parsed).most_common(1)[0][0]
    matching = [p for p in parsed if abs(len(p) - target_len) <= 2]

    min_len = min(len(p) for p in matching)
    cleaned_pulses = []
    for i in range(min_len):
        vals = [p[i] for p in matching]
        cleaned_pulses.append(str(round(statistics.median(vals))))

    raw_str = ",".join(cleaned_pulses)
    desc = f"Learned as **Cleaned Multi-Sample Raw RF** ({len(cleaned_pulses)} pulses across {len(matching)} repetitions)"
    return raw_str, desc


def _get_entry_data(
    hass: HomeAssistant, config_entry_id: str | None = None
) -> tuple[str, dict[str, Any]]:
    """Retrieve entry data dictionary, targeting entry_id or using the first registered device."""
    domain_data = hass.data.get(DOMAIN, {})
    if not domain_data:
        raise HomeAssistantError("No ESP32 CC1101 USB device configured")

    if config_entry_id and config_entry_id in domain_data:
        return config_entry_id, domain_data[config_entry_id]

    # Default to first active entry
    for entry_id, data in domain_data.items():
        if isinstance(data, dict) and "client" in data:
            return entry_id, data

    raise HomeAssistantError("No active ESP32 CC1101 USB connection available")


async def async_start_learning_flow(
    hass: HomeAssistant, entry_id: str, name: str
) -> None:
    """Start RF learning flow for a given command name."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not entry_data:
        raise HomeAssistantError(f"Integration entry '{entry_id}' not found")
    name = name.strip()
    if not name:
        raise HomeAssistantError("Command name cannot be empty")

    prev_mode = entry_data["client"].rx_mode
    entry_data["learning"] = {
        "active": True,
        "name": name,
        "samples": [],
        "decoded_samples": [],
        "last_sample_time": 0.0,
        "prev_mode": prev_mode,
    }
    await entry_data["client"].async_set_config(rx_mode="Learn Mode")
    _LOGGER.info(
        "Starting RF learning mode for command '%s' (press remote button %d times)",
        name,
        LEARN_STEPS,
    )

    persistent_notification.async_create(
        hass,
        f"Ready to learn **{name}**.\n\n"
        f"Please press the button on your physical remote **{LEARN_STEPS} times** "
        f"(pause 1 second between presses).",
        title=f"Learning RF Code: {name} (0/{LEARN_STEPS})",
        notification_id=f"{DOMAIN}_learning_{entry_id}",
    )


async def async_cancel_learning_flow(
    hass: HomeAssistant, entry_id: str
) -> None:
    """Cancel ongoing RF learning flow."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not entry_data:
        return
    learning = entry_data.get("learning")
    if learning and learning.get("active"):
        learning["active"] = False
        prev_mode = learning.get("prev_mode", RX_MODE_DECODED)
        await entry_data["client"].async_set_config(rx_mode=prev_mode)
        persistent_notification.async_dismiss(
            hass, notification_id=f"{DOMAIN}_learning_{entry_id}"
        )
        _LOGGER.info("RF learning mode cancelled for entry %s", entry_id)


async def async_delete_command_flow(
    hass: HomeAssistant, entry_id: str, name: str
) -> None:
    """Delete a learned command, persist storage, and remove its button entity."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not entry_data:
        raise HomeAssistantError(f"Integration entry '{entry_id}' not found")
    name = name.strip()
    if not name or name == "None":
        raise HomeAssistantError("Please select a valid command to delete")

    saved = entry_data.get("saved_codes", {})

    # Match in saved_codes (exact, case-insensitive, or slug)
    matched_key = None
    if name in saved:
        matched_key = name
    else:
        for k in list(saved.keys()):
            if k.lower() == name.lower() or k.lower().replace(" ", "_") == name.lower().replace(" ", "_"):
                matched_key = k
                break

    if matched_key:
        del saved[matched_key]
        await entry_data["store"].async_save(saved)
        _LOGGER.info("Deleted saved RF command '%s' from storage for entry %s", matched_key, entry_id)

    # 1. Safely remove button entity dynamically if registered and active
    if entry_data.get("remove_button_callback"):
        try:
            await entry_data["remove_button_callback"](matched_key or name)
        except Exception as err:
            _LOGGER.debug("Dynamic button entity removal notice: %s", err)

    # 2. Comprehensive cleanup of Home Assistant Entity Registry
    # Handles disabled entities, renamed entity_ids, and orphaned registry entries!
    slug = (matched_key or name).lower().replace(" ", "_")
    target_unique_id = f"esp32_cc1101_cmd_{entry_id}_{slug}"
    purged_count = 0

    try:
        from homeassistant.helpers import entity_registry as er
        ent_reg = er.async_get(hass)
        entries_to_remove = []
        for ent_id, ent_entry in list(ent_reg.entities.items()):
            if ent_entry.config_entry_id == entry_id and ent_entry.platform == DOMAIN:
                if (
                    ent_entry.unique_id == target_unique_id
                    or ent_entry.original_name == (matched_key or name)
                    or ent_entry.name == (matched_key or name)
                    or ent_entry.entity_id == name
                    or ent_entry.unique_id.endswith(f"_{slug}")
                ):
                    entries_to_remove.append(ent_id)

        for ent_id in entries_to_remove:
            ent_reg.async_remove(ent_id)
            purged_count += 1
            _LOGGER.info("Purged entity registry entry '%s'", ent_id)
    except Exception as err:
        _LOGGER.warning("Could not purge entity registry for '%s': %s", name, err)

    if not matched_key and purged_count == 0:
        raise HomeAssistantError(f"RF command '{name}' was not found in storage or registry")

    # 3. Notify select entity to update available choices
    if "update_delete_select_callback" in entry_data:
        try:
            entry_data["update_delete_select_callback"]()
        except Exception:
            pass

    persistent_notification.async_create(
        hass,
        f"Learned RF command **{name}** has been completely deleted and purged.",
        title=f"Deleted RF Command: {name}",
        notification_id=f"{DOMAIN}_deleted_{entry_id}_{slug}",
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ESP32 CC1101 USB from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    client = Esp32Cc1101Client(hass, entry)
    await client.async_start()

    store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry.entry_id}")
    saved_codes = await store.async_load() or {}

    entry_data: dict[str, Any] = {
        "client": client,
        "store": store,
        "saved_codes": saved_codes,
        "learning": {"active": False, "name": "", "samples": [], "decoded_samples": [], "last_sample_time": 0.0},
        "add_button_callback": None,
    }
    hass.data[DOMAIN][entry.entry_id] = entry_data

    # Setup listener for learning mode captures
    def on_client_event(data: dict[str, Any]) -> None:
        event_type = data.get("type")
        if event_type in ("rx_code", "rx_raw"):
            learning = entry_data.get("learning")
            if learning and learning.get("active"):
                raw_code = client.last_code
                payload = data.get("payload", {})
                if raw_code:
                    hass.async_create_task(
                        _handle_learning_sample(hass, entry.entry_id, raw_code, payload)
                    )

    client.register_listener(on_client_event)

    # Listen for options flow changes
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    # Register services once
    if not hass.services.has_service(DOMAIN, "send_raw"):

        async def async_send_raw_service(call: ServiceCall) -> None:
            _, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            code = call.data["code"]
            repeats = call.data.get("repeats")
            gap = call.data.get("gap", 8250)
            await data["client"].async_send_raw(code, repeats=repeats, gap=gap)

        async def async_send_code_service(call: ServiceCall) -> None:
            _, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            code = call.data["code"]
            bits = call.data.get("bits", 24)
            protocol = call.data.get("protocol", 1)
            pulse_len = call.data.get("pulse_length", 350)
            repeats = call.data.get("repeats")
            await data["client"].async_send_code(
                code=code,
                bits=bits,
                protocol=protocol,
                pulse_length=pulse_len,
                repeats=repeats,
            )

        async def async_set_config_service(call: ServiceCall) -> None:
            _, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            freq = call.data.get("frequency")
            mod = call.data.get("modulation")
            bw = call.data.get("bandwidth")
            pa = call.data.get("tx_power")
            min_rssi = call.data.get("min_rssi")
            min_repeats = call.data.get("min_repeats")
            rx_mode = call.data.get("rx_mode")
            await data["client"].async_set_config(
                freq=freq,
                mod=mod,
                bw=bw,
                pa=pa,
                min_rssi=min_rssi,
                min_repeats=min_repeats,
                rx_mode=rx_mode,
            )

        async def async_learn_command_service(call: ServiceCall) -> None:
            entry_id, _ = _get_entry_data(hass, call.data.get("config_entry_id"))
            name = call.data["name"].strip()
            await async_start_learning_flow(hass, entry_id, name)

        async def async_send_command_service(call: ServiceCall) -> None:
            _, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            name = call.data["name"].strip()
            saved = data.get("saved_codes", {})
            if name not in saved:
                raise HomeAssistantError(f"Saved RF command '{name}' not found")
            cmd_data = saved[name]
            repeats = call.data.get("repeats")
            _LOGGER.info("Transmitting saved RF command '%s'", name)
            if isinstance(cmd_data, dict) and "code" in cmd_data:
                await data["client"].async_send_code(
                    code=cmd_data["code"],
                    bits=cmd_data.get("bits", 24),
                    protocol=cmd_data.get("protocol", 1),
                    pulse_length=cmd_data.get("pulse_length", 350),
                    repeats=repeats,
                )
            elif isinstance(cmd_data, str):
                await data["client"].async_send_raw(cmd_data, repeats=repeats)
            elif isinstance(cmd_data, dict) and "raw" in cmd_data:
                await data["client"].async_send_raw(cmd_data["raw"], repeats=repeats)

        async def async_delete_command_service(call: ServiceCall) -> None:
            entry_id, _ = _get_entry_data(hass, call.data.get("config_entry_id"))
            name = call.data["name"].strip()
            await async_delete_command_flow(hass, entry_id, name)

        async def async_ping_service(call: ServiceCall) -> None:
            _, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            await data["client"].async_ping()

        async def async_flash_firmware_service(call: ServiceCall) -> None:
            entry_id, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            client: Esp32Cc1101Client = data["client"]
            port = client.port
            if not port:
                raise HomeAssistantError("Serial port not available for device")

            file_path = call.data.get("file_path") or get_default_firmware_path()
            if not os.path.isfile(file_path):
                raise HomeAssistantError(f"Firmware binary not found at '{file_path}'")

            _LOGGER.info("Flashing firmware to %s using %s...", port, file_path)
            persistent_notification.async_create(
                hass,
                f"Starting ESP32 firmware flash on `{port}` with `{os.path.basename(file_path)}`...\n\n"
                f"Please wait ~10 seconds while the binary is transferred.",
                title="Flashing ESP32 Firmware",
                notification_id=f"{DOMAIN}_flash_{entry_id}",
            )

            try:
                # 1. Stop serial reader to release serial port
                await client.async_stop()

                # 2. Flash in executor
                await hass.async_add_executor_job(
                    flash_firmware_sync,
                    port,
                    file_path,
                    460800,
                    None,
                )

                # 3. Reconnect
                await client.async_start()

                persistent_notification.async_create(
                    hass,
                    f"Firmware flash **SUCCESSFUL**!\n\nESP32 has been rebooted and reconnected on `{port}`.",
                    title="ESP32 Firmware Updated",
                    notification_id=f"{DOMAIN}_flash_{entry_id}",
                )
            except Exception as exc:
                try:
                    await client.async_start()
                except Exception:
                    pass
                persistent_notification.async_create(
                    hass,
                    f"Firmware flash **FAILED**:\n\n```\n{exc}\n```",
                    title="ESP32 Firmware Flash Error",
                    notification_id=f"{DOMAIN}_flash_{entry_id}",
                )
                raise HomeAssistantError(f"Firmware flash failed: {exc}") from exc

        hass.services.async_register(
            DOMAIN, "send_raw", async_send_raw_service, schema=SERVICE_SEND_RAW_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, "send_code", async_send_code_service, schema=SERVICE_SEND_CODE_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, "set_config", async_set_config_service, schema=SERVICE_SET_CONFIG_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, "learn_command", async_learn_command_service, schema=SERVICE_LEARN_COMMAND_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, "send_command", async_send_command_service, schema=SERVICE_SEND_COMMAND_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, "delete_command", async_delete_command_service, schema=SERVICE_DELETE_COMMAND_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, "ping", async_ping_service, schema=SERVICE_PING_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, "flash_firmware", async_flash_firmware_service, schema=SERVICE_FLASH_FIRMWARE_SCHEMA
        )

    # Automatically purge obsolete entities from previous versions to clean up UI
    from homeassistant.helpers import entity_registry as er
    ent_reg = er.async_get(hass)
    existing_entries = er.async_entries_for_config_entry(ent_reg, entry.entry_id)

    valid_button_prefix = f"esp32_cc1101_cmd_{entry.entry_id}_"
    valid_repeat_button = f"esp32_cc1101_repeat_{entry.entry_id}"
    valid_numbers = {
        f"esp32_cc1101_tx_power_{entry.entry_id}",
        f"esp32_cc1101_repeats_{entry.entry_id}",
        f"esp32_cc1101_sensitivity_{entry.entry_id}",
        f"esp32_cc1101_min_repeats_{entry.entry_id}",
    }

    for reg_entry in existing_entries:
        # Purge any obsolete/duplicate buttons (e.g. old ping, reset, flash, or duplicate repeat button)
        if reg_entry.domain == "button":
            if reg_entry.unique_id != valid_repeat_button and not reg_entry.unique_id.startswith(valid_button_prefix):
                _LOGGER.info("Purging obsolete button from registry: %s (%s)", reg_entry.entity_id, reg_entry.unique_id)
                ent_reg.async_remove(reg_entry.entity_id)
        # Purge obsolete numbers (e.g. old frequency number slider)
        elif reg_entry.domain == "number":
            if reg_entry.unique_id not in valid_numbers:
                _LOGGER.info("Purging obsolete number from registry: %s (%s)", reg_entry.entity_id, reg_entry.unique_id)
                ent_reg.async_remove(reg_entry.entity_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _handle_learning_sample(
    hass: HomeAssistant, entry_id: str, raw_code: str, payload: dict[str, Any] | None = None
) -> None:
    """Handle individual capture during multi-step learning mode."""
    entry_data = hass.data[DOMAIN].get(entry_id)
    if not entry_data:
        return

    learning = entry_data.get("learning")
    if not learning or not learning.get("active"):
        return

    now = time.time()
    last_sample_time = learning.get("last_sample_time", 0.0)
    # Require at least 0.7s between learning presses so 1 physical click only counts once
    if (now - last_sample_time) < 0.7:
        return

    # Filter out noisy single-glitch pulses (< 16 pulses)
    pulse_parts = [p.strip() for p in raw_code.split(",") if p.strip()]
    if len(pulse_parts) < 16:
        return

    learning["last_sample_time"] = now
    learning["samples"].append(raw_code)

    if payload and "code" in payload and "bits" in payload:
        learning.setdefault("decoded_samples", []).append({
            "code": payload["code"],
            "bits": payload["bits"],
            "protocol": payload.get("protocol", 1),
            "pulse_length": payload.get("pulse_length", 350),
        })

    count = len(learning["samples"])
    cmd_name = learning["name"]

    _LOGGER.info(
        "Captured learning repetition %d/%d for '%s' (%d pulses)",
        count,
        LEARN_STEPS,
        cmd_name,
        len(pulse_parts),
    )

    if count < LEARN_STEPS:
        persistent_notification.async_create(
            hass,
            f"Captured repetition **{count} of {LEARN_STEPS}** for `{cmd_name}`.\n\n"
            f"Please press the remote button again ({LEARN_STEPS - count} remaining).",
            title=f"Learning RF Code: {cmd_name} ({count}/{LEARN_STEPS})",
            notification_id=f"{DOMAIN}_learning_{entry_id}",
        )
    else:
        result_code, desc = _analyze_learning_consensus(
            learning["samples"], learning.get("decoded_samples", [])
        )
        learning["active"] = False
        prev_mode = learning.get("prev_mode", RX_MODE_DECODED)
        client: Esp32Cc1101Client = entry_data["client"]
        hass.async_create_task(client.async_set_config(rx_mode=prev_mode))

        entry_data["saved_codes"][cmd_name] = result_code
        await entry_data["store"].async_save(entry_data["saved_codes"])
        _LOGGER.info("Saved learned RF command '%s' after %d repetitions (%s)", cmd_name, LEARN_STEPS, desc)

        if entry_data.get("add_button_callback"):
            entry_data["add_button_callback"](cmd_name)

        if "update_delete_select_callback" in entry_data:
            entry_data["update_delete_select_callback"]()

        next_count = len(entry_data["saved_codes"]) + 1
        next_name = f"Remote Button {next_count}"
        entry_data["learn_name_input"] = next_name
        if "update_learn_name_callback" in entry_data:
            entry_data["update_learn_name_callback"](next_name)

        persistent_notification.async_create(
            hass,
            f"Successfully learned and confirmed **{cmd_name}** after {LEARN_STEPS} repetitions!\n\n"
            f"{desc}\n\n"
            f"A button entity for **{cmd_name}** has been added to your dashboard.",
            title=f"RF Code Learned: {cmd_name}",
            notification_id=f"{DOMAIN}_learning_{entry_id}",
        )


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update and push settings directly to CC1101."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not entry_data:
        return

    client: Esp32Cc1101Client = entry_data["client"]
    new_freq = entry.options.get(CONF_FREQUENCY, entry.data.get(CONF_FREQUENCY, DEFAULT_FREQUENCY))
    new_mod = entry.options.get(CONF_MODULATION, entry.data.get(CONF_MODULATION, DEFAULT_MODULATION))
    new_bw = entry.options.get(CONF_BANDWIDTH, entry.data.get(CONF_BANDWIDTH, DEFAULT_BANDWIDTH))
    new_pa = entry.options.get(CONF_TX_POWER, entry.data.get(CONF_TX_POWER, DEFAULT_TX_POWER))
    new_repeats = entry.options.get(CONF_REPEATS, entry.data.get(CONF_REPEATS, DEFAULT_REPEATS))
    new_min_rssi = entry.options.get(CONF_MIN_RSSI, entry.data.get(CONF_MIN_RSSI, DEFAULT_MIN_RSSI))
    new_min_repeats = entry.options.get(CONF_MIN_REPEATS, entry.data.get(CONF_MIN_REPEATS, DEFAULT_MIN_REPEATS))
    new_rx_mode = entry.options.get(CONF_RX_MODE, entry.data.get(CONF_RX_MODE, RX_MODE_DECODED))

    client.repeats = new_repeats
    await client.async_set_config(
        freq=new_freq,
        mod=new_mod,
        bw=new_bw,
        pa=new_pa,
        min_rssi=new_min_rssi,
        min_repeats=new_min_repeats,
        rx_mode=new_rx_mode,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if entry_data and "client" in entry_data:
            await entry_data["client"].async_stop()
    return unload_ok
