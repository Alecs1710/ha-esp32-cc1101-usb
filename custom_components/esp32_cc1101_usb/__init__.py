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
from homeassistant.helpers import device_registry as dr
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
    RX_MODE_ALL,
    DEFAULT_RX_MODE,
    STORAGE_KEY,
    STORAGE_VERSION,
    LEARN_STEPS,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["remote", "sensor", "select", "number", "button", "update"]

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
        vol.Optional("device"): cv.string,
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


def _codes_match(c1: Any, c2: Any) -> bool:
    """Robust equality check between two RF codes across types (int, str, hex)."""
    if c1 is None or c2 is None:
        return False
    if c1 == c2:
        return True
    try:
        if int(c1) == int(c2):
            return True
    except (ValueError, TypeError):
        pass
    s1 = str(c1).strip().lower()
    s2 = str(c2).strip().lower()
    if s1 == s2:
        return True
    try:
        h1 = hex(int(c1)) if not s1.startswith("0x") else s1
        h2 = hex(int(c2)) if not s2.startswith("0x") else s2
        if h1 == h2:
            return True
    except (ValueError, TypeError):
        pass
    return False


def _compare_raw_pulses(raw1: str, raw2: str) -> float:
    """Compare two comma-separated raw pulse strings and return correlation score (0.0 to 1.0)."""
    try:
        p1 = [int(x.strip()) for x in raw1.split(",") if x.strip()]
        p2 = [int(x.strip()) for x in raw2.split(",") if x.strip()]
    except (ValueError, AttributeError):
        return 0.0

    if not p1 or not p2:
        return 0.0

    if abs(len(p1) - len(p2)) > 6:
        return 0.0

    start_idx = 1 if (p1[0] > 2500 and p2[0] > 2500) else 0
    min_len = min(len(p1), len(p2))
    total = min_len - start_idx
    if total < 10:
        return 0.0

    matches = 0
    for i in range(start_idx, min_len):
        v1 = p1[i]
        v2 = p2[i]
        tol = max(200, int(0.30 * max(v1, v2)))
        if abs(v1 - v2) <= tol:
            matches += 1

    return matches / total


def _analyze_learning_consensus(
    samples: list[str], decoded_samples: list[dict[str, Any]]
) -> tuple[dict[str, Any] | str | None, str]:
    """Analyzes continuous 5-second button press to extract the true, confirmed RF code."""
    rep_raw = samples[0] if samples else ""

    # 1. Analyze decoded samples across continuous button press
    if decoded_samples:
        bit_counts = Counter(s.get("bits", 0) for s in decoded_samples if s.get("bits"))
        if bit_counts:
            dominant_bits = None
            for b, count in sorted(bit_counts.items(), key=lambda x: x[0], reverse=True):
                if count >= 3 or count == len(decoded_samples):
                    dominant_bits = b
                    break
            if not dominant_bits and bit_counts:
                dominant_bits = bit_counts.most_common(1)[0][0]

            valid_samples = [s for s in decoded_samples if s.get("bits") == dominant_bits]
            if len(valid_samples) >= 3:
                codes = [s["code"] for s in valid_samples]
                proto = valid_samples[0].get("protocol", 1)
                pulse_lengths = [s.get("pulse_length", 350) for s in valid_samples if s.get("pulse_length")]
                pulse_len = round(statistics.median(pulse_lengths)) if pulse_lengths else 350

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
                            f"Decoded Rolling Code "
                            f"(Base: 0x{base_id:06X}, Step: +{common_delta}, {dominant_bits}b, Proto {proto})"
                        )
                        return {
                            "type": "rolling_code",
                            "code": latest_code,
                            "base_id": base_id,
                            "delta": common_delta,
                            "bits": dominant_bits,
                            "protocol": proto,
                            "pulse_length": pulse_len,
                            "raw": rep_raw,
                        }, desc

                most_common_code, code_count = Counter(codes).most_common(1)[0]
                if code_count >= 3:
                    desc = (
                        f"Decoded Protocol "
                        f"(Code: {most_common_code}, {dominant_bits}b, Proto {proto}, {code_count} frames)"
                    )
                    return {
                        "type": "code",
                        "code": most_common_code,
                        "bits": dominant_bits,
                        "protocol": proto,
                        "pulse_length": pulse_len,
                        "raw": rep_raw,
                    }, desc

    # 2. Raw pulse analysis across continuous button press
    parsed: list[list[int]] = []
    for s in samples:
        try:
            pulses = [int(x.strip()) for x in s.split(",") if x.strip()]
            if len(pulses) >= 16:
                parsed.append(pulses)
        except ValueError:
            continue

    if not parsed:
        return None, "No pulses detected"

    target_len = Counter(len(p) for p in parsed).most_common(1)[0][0]
    matching = [p for p in parsed if abs(len(p) - target_len) <= 2]
    if len(matching) < 3:
        return None, f"Insufficient clean frames ({len(matching)} frames)"

    min_len = min(len(p) for p in matching)
    cleaned_pulses = []
    for i in range(min_len):
        vals = [p[i] for p in matching]
        cleaned_pulses.append(str(round(statistics.median(vals))))

    raw_str = ",".join(cleaned_pulses)
    desc = f"Cleaned Raw RF ({len(cleaned_pulses)} pulses across {len(matching)} continuous frames)"
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


def find_saved_command(
    hass: HomeAssistant,
    entry_id: str,
    query: str,
) -> tuple[str | None, Any | None]:
    """Find a saved RF command by key, friendly name, entity_id, or slug, resilient to renaming."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id, {})
    saved = entry_data.get("saved_codes", {})
    query = query.strip()
    if not query:
        return None, None

    # 1. Exact match in saved_codes
    if query in saved:
        return query, saved[query]

    # 2. Case-insensitive or slug match
    query_lower = query.lower()
    query_slug = query_lower.replace(" ", "_")
    for k, v in saved.items():
        k_lower = k.lower()
        if k_lower == query_lower or k_lower.replace(" ", "_") == query_slug:
            return k, v

    # 3. Match without device prefix (e.g. query is "Speed 1" and stored is "Ceiling Fan - Speed 1")
    for k, v in saved.items():
        if " - " in k:
            short_k = k.split(" - ", 1)[1].strip().lower()
            if short_k == query_lower or short_k.replace(" ", "_") == query_slug:
                return k, v

    # 4. Search Home Assistant's Entity Registry
    # This ensures user renaming the button or changing entity_id never breaks finding the code!
    try:
        from homeassistant.helpers import entity_registry as er
        ent_reg = er.async_get(hass)
        prefix = f"esp32_cc1101_cmd_{entry_id}_"
        for ent in list(ent_reg.entities.values()):
            if ent.config_entry_id == entry_id and ent.platform == DOMAIN:
                if (
                    ent.entity_id == query
                    or (ent.name and ent.name.lower() == query_lower)
                    or (ent.original_name and ent.original_name.lower() == query_lower)
                ):
                    if ent.unique_id.startswith(prefix):
                        ent_slug = ent.unique_id[len(prefix):]
                        for k, v in saved.items():
                            if k.lower().replace(" ", "_") == ent_slug:
                                return k, v
    except Exception as err:
        _LOGGER.debug("Entity registry lookup notice: %s", err)

    return None, None


async def async_start_learning_flow(
    hass: HomeAssistant,
    entry_id: str,
    name: str,
    device_name: str | None = None,
    repeats: int | None = None,
    min_rssi: int | None = None,
) -> None:
    """Start continuous 5-second RF learning flow for a given command name."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not entry_data:
        raise HomeAssistantError(f"Integration entry '{entry_id}' not found")
    name = name.strip()
    if not name:
        raise HomeAssistantError("Command name cannot be empty")

    dev = (device_name or "Dispositivo").strip()
    client: Esp32Cc1101Client = entry_data["client"]
    prev_mode = client.rx_mode
    prev_rssi = client.min_rssi
    learn_rssi = min_rssi if min_rssi is not None else -60

    # Cancel any previous learning timers
    old_learning = entry_data.get("learning")
    if old_learning:
        for t_key in ("timer_task", "verify_task"):
            old_task = old_learning.get(t_key)
            if old_task and not old_task.done():
                old_task.cancel()

    timer_task = hass.async_create_task(_async_learning_recording_timer(hass, entry_id, 5.0))

    entry_data["learning"] = {
        "active": True,
        "phase": "recording",
        "name": name,
        "device": dev,
        "repeats": repeats,
        "samples": [],
        "decoded_samples": [],
        "last_sample_time": 0.0,
        "prev_mode": prev_mode,
        "prev_rssi": prev_rssi,
        "learn_rssi": learn_rssi,
        "candidate_code": None,
        "candidate_desc": "",
        "timer_task": timer_task,
        "verify_task": None,
    }
    # Restrict CC1101 squelch specifically for learning mode
    await client.async_set_config(min_rssi=learn_rssi, rx_mode="Learn Mode")

    display_label = f"{dev} - {name}" if dev else name
    _LOGGER.info(
        "Starting continuous RF learning for '%s' (Hold button for 5 seconds, min_rssi=%d dBm)",
        display_label,
        learn_rssi,
    )

    if "update_learning_sensor_callback" in entry_data:
        try:
            entry_data["update_learning_sensor_callback"]()
        except Exception:
            pass

    persistent_notification.async_create(
        hass,
        f"⏳ **Paso 1 de 2: Grabando señal de `{display_label}`**\n\n"
        f"👉 **Mantén pulsado el botón en tu mando físico durante 5 segundos de forma CONTINUA.**\n\n"
        f"📶 *Filtro de potencia activo:* `{learn_rssi} dBm` (solo señales cercanas).\n\n"
        f"*(Mantén el mando a 10–30 cm del transceptor CC1101 para una captura limpia sin interferencias).* ",
        title=f"Grabando Botón: {display_label} (Paso 1/2)",
        notification_id=f"{DOMAIN}_learning_{entry_id}",
    )


async def async_cancel_learning_flow(
    hass: HomeAssistant, entry_id: str
) -> None:
    """Cancel ongoing RF learning flow and restore normal squelch."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not entry_data:
        return
    learning = entry_data.get("learning")
    if learning and learning.get("active"):
        learning["active"] = False
        learning["phase"] = "idle"
        for t_key in ("timer_task", "verify_task"):
            t = learning.get(t_key)
            if t and not t.done():
                t.cancel()
        prev_mode = learning.get("prev_mode", RX_MODE_ALL)
        prev_rssi = learning.get("prev_rssi", -75)
        client = entry_data["client"]
        await client.async_set_config(min_rssi=prev_rssi, rx_mode=prev_mode)
        persistent_notification.async_dismiss(
            hass, notification_id=f"{DOMAIN}_learning_{entry_id}"
        )
        if "update_learning_sensor_callback" in entry_data:
            try:
                entry_data["update_learning_sensor_callback"]()
            except Exception:
                pass
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

    # Match in saved_codes using find_saved_command for total resilience to renaming
    matched_key, _ = find_saved_command(hass, entry_id, name)
    if not matched_key and name in saved:
        matched_key = name

    if matched_key:
        saved.pop(matched_key, None)
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
    effective_name = matched_key or name
    slug = effective_name.lower().replace(" ", "_")
    short_slug = (effective_name.split(" - ", 1)[1] if " - " in effective_name else effective_name).lower().replace(" ", "_")
    target_unique_id = f"esp32_cc1101_cmd_{entry_id}_{slug}"
    purged_count = 0

    try:
        from homeassistant.helpers import entity_registry as er
        ent_reg = er.async_get(hass)
        entries_to_remove = []
        for ent_entry in list(ent_reg.entities.values()):
            if ent_entry.config_entry_id == entry_id and ent_entry.platform == DOMAIN:
                if (
                    ent_entry.unique_id == target_unique_id
                    or ent_entry.unique_id.endswith(f"_{slug}")
                    or ent_entry.unique_id.endswith(f"_{short_slug}")
                    or ent_entry.original_name == effective_name
                    or ent_entry.name == effective_name
                    or (ent_entry.original_name and ent_entry.original_name.lower() == effective_name.lower())
                    or (ent_entry.name and ent_entry.name.lower() == effective_name.lower())
                    or ent_entry.entity_id == name
                    or ent_entry.entity_id.endswith(f"_{short_slug}")
                ):
                    entries_to_remove.append(ent_entry.entity_id)

        for ent_id in entries_to_remove:
            ent_reg.async_remove(ent_id)
            purged_count += 1
            _LOGGER.info("Purged entity registry entry '%s'", ent_id)
    except Exception as err:
        _LOGGER.warning("Could not purge entity registry for '%s': %s", name, err)

    if not matched_key and purged_count == 0:
        raise HomeAssistantError(f"RF command '{name}' was not found in storage or registry")

    # 3. Notify callbacks
    if "update_delete_select_callback" in entry_data:
        try:
            entry_data["update_delete_select_callback"]()
        except Exception:
            pass

    if "update_learning_sensor_callback" in entry_data:
        try:
            entry_data["update_learning_sensor_callback"]()
        except Exception:
            pass

    persistent_notification.async_create(
        hass,
        f"Learned RF command **{name}** has been completely deleted and purged.",
        title=f"Deleted RF Command: {name}",
        notification_id=f"{DOMAIN}_deleted_{entry_id}_{slug}",
    )


async def async_delete_device_flow(
    hass: HomeAssistant, entry_id: str, dev_name: str
) -> None:
    """Delete a learned logical device and all its stored buttons."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not entry_data:
        raise HomeAssistantError(f"Integration entry '{entry_id}' not found")
    dev_name = dev_name.strip()
    if not dev_name:
        raise HomeAssistantError("Device name cannot be empty")

    saved = entry_data.get("saved_codes", {})
    keys_to_delete = []
    for k, v in list(saved.items()):
        if k.startswith("__"):
            continue
        code_dev = ""
        if isinstance(v, dict) and v.get("device"):
            code_dev = v["device"].strip()
        elif " - " in k:
            code_dev = k.split(" - ", 1)[0].strip()
        if code_dev.lower() == dev_name.lower():
            keys_to_delete.append(k)

    for k in keys_to_delete:
        try:
            await async_delete_command_flow(hass, entry_id, k)
        except Exception as err:
            _LOGGER.debug("Could not delete code '%s' for device '%s': %s", k, dev_name, err)

    devices_list = saved.get("__devices__", [])
    for d in list(devices_list):
        if d.lower() == dev_name.lower() or d.lower().replace(" ", "_") == dev_name.lower().replace(" ", "_"):
            devices_list.remove(d)
    await entry_data["store"].async_save(saved)

    if entry_data.get("remove_device_sensor_callback"):
        try:
            await entry_data["remove_device_sensor_callback"](dev_name)
        except Exception as err:
            _LOGGER.debug("Could not remove device sensor for '%s': %s", dev_name, err)

    try:
        from homeassistant.helpers import device_registry as dr
        dev_reg = dr.async_get(hass)
        slug_dev = dev_name.lower().replace(" ", "_")
        dev_ident = (DOMAIN, f"{entry_id}_dev_{slug_dev}")
        dev_entry = dev_reg.async_get_device(identifiers={dev_ident})
        if dev_entry:
            dev_reg.async_remove_device(dev_entry.id)
            _LOGGER.info("Removed device '%s' from Device Registry", dev_name)
    except Exception as err:
        _LOGGER.debug("Could not remove device from registry: %s", err)

    if "device_button_listeners" in entry_data:
        entry_data["device_button_listeners"].pop(dev_name.lower(), None)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Remove a learned device and all its stored RF codes when deleted by user in HA."""
    entry_data = hass.data.get(DOMAIN, {}).get(config_entry.entry_id)
    if not entry_data:
        return True

    # Disallow deleting the core USB bridge
    root_id = (DOMAIN, config_entry.entry_id)
    if root_id in device_entry.identifiers:
        return False

    dev_name = device_entry.name or ""
    saved = entry_data.get("saved_codes", {})
    keys_to_delete: set[str] = set()

    # 1. Match codes via all entities registered to this device (immune to user renaming!)
    try:
        from homeassistant.helpers import entity_registry as er
        ent_reg = er.async_get(hass)
        entries_for_device = er.async_entries_for_device(ent_reg, device_entry.id)
        prefix = f"esp32_cc1101_cmd_{config_entry.entry_id}_"
        for ent_entry in entries_for_device:
            if ent_entry.unique_id.startswith(prefix):
                ent_slug = ent_entry.unique_id[len(prefix):]
                for key in saved:
                    if key.lower().replace(" ", "_") == ent_slug:
                        keys_to_delete.add(key)
    except Exception as err:
        _LOGGER.debug("Error mapping device entities for deletion: %s", err)

    # 2. Match codes via device identifier slug
    for ident_domain, ident_val in device_entry.identifiers:
        if ident_domain == DOMAIN and f"{config_entry.entry_id}_dev_" in ident_val:
            orig_dev_slug = ident_val.split(f"{config_entry.entry_id}_dev_", 1)[1]
            for key, code_val in saved.items():
                stored_dev = code_val.get("device", "") if isinstance(code_val, dict) else (key.split(" - ", 1)[0] if " - " in key else "")
                if stored_dev.lower().replace(" ", "_") == orig_dev_slug:
                    keys_to_delete.add(key)

    # 3. Match codes via name/stored_dev string as fallback
    for key, code_val in saved.items():
        stored_dev = code_val.get("device", "") if isinstance(code_val, dict) else (key.split(" - ", 1)[0] if " - " in key else "")
        if (
            (stored_dev and stored_dev.lower() == dev_name.lower())
            or key.lower().startswith(f"{dev_name.lower()} - ")
            or key.lower() == dev_name.lower()
        ):
            keys_to_delete.add(key)

    for k in keys_to_delete:
        saved.pop(k, None)
        _LOGGER.info("Deleted code '%s' due to device '%s' removal", k, dev_name)

    # Also remove device from persistent devices list
    devices_list = saved.get("__devices__", [])
    for d in list(devices_list):
        if d.lower() == dev_name.lower() or d.lower().replace(" ", "_") == dev_name.lower().replace(" ", "_"):
            devices_list.remove(d)

    await entry_data["store"].async_save(saved)

    # Remove in-memory button entities
    if entry_data.get("remove_button_callback"):
        for k in keys_to_delete:
            try:
                await entry_data["remove_button_callback"](k)
            except Exception as err:
                _LOGGER.debug("Could not remove button for '%s': %s", k, err)

    # Purge all entity registry entries attached to this device
    try:
        from homeassistant.helpers import entity_registry as er
        ent_reg = er.async_get(hass)
        entries_for_device = er.async_entries_for_device(ent_reg, device_entry.id)
        for e in entries_for_device:
            ent_reg.async_remove(e.entity_id)
            _LOGGER.info("Purged entity registry entry '%s' for deleted device '%s'", e.entity_id, dev_name)
    except Exception as err:
        _LOGGER.warning("Could not purge entities for deleted device '%s': %s", dev_name, err)

    if "update_learning_sensor_callback" in entry_data:
        try:
            entry_data["update_learning_sensor_callback"]()
        except Exception:
            pass

    _LOGGER.info("Successfully removed device '%s' and purged %d RF codes", dev_name, len(keys_to_delete))
    return True


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
        "add_device_sensor_callback": None,
        "device_button_listeners": {},
        "state_listeners": {},
    }
    hass.data[DOMAIN][entry.entry_id] = entry_data

    # Setup listener for learning mode captures & passive state updates
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
            else:
                _handle_passive_rf_receive(hass, entry.entry_id, client, data)

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
            device = call.data.get("device")
            repeats = call.data.get("repeats")
            min_rssi = call.data.get("min_rssi")
            await async_start_learning_flow(hass, entry_id, name, device, repeats=repeats, min_rssi=min_rssi)

        async def async_send_command_service(call: ServiceCall) -> None:
            entry_id, data = _get_entry_data(hass, call.data.get("config_entry_id"))
            name = call.data["name"].strip()
            matched_key, cmd_data = find_saved_command(hass, entry_id, name)
            if not cmd_data:
                raise HomeAssistantError(f"Saved RF command '{name}' not found")
            repeats = call.data.get("repeats")
            _LOGGER.info("Transmitting saved RF command '%s' (matched '%s')", name, matched_key)
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
    obsolete_entity_prefixes = (
        f"esp32_cc1101_target_device_{entry.entry_id}",
        f"esp32_cc1101_learn_name_{entry.entry_id}",
        f"esp32_cc1101_target_dev_select_{entry.entry_id}",
        f"esp32_cc1101_delete_select_{entry.entry_id}",
        f"esp32_cc1101_confirm_delete_{entry.entry_id}",
        f"esp32_cc1101_learn_start_{entry.entry_id}",
        f"esp32_cc1101_learn_cancel_{entry.entry_id}",
    )

    for reg_entry in existing_entries:
        # Purge obsolete learner entities
        if any(reg_entry.unique_id.startswith(p) for p in obsolete_entity_prefixes):
            _LOGGER.info("Purging obsolete learner entity from registry: %s", reg_entry.entity_id)
            ent_reg.async_remove(reg_entry.entity_id)
        # Purge any obsolete/duplicate buttons
        elif reg_entry.domain == "button":
            if reg_entry.unique_id != valid_repeat_button and not reg_entry.unique_id.startswith(valid_button_prefix):
                _LOGGER.info("Purging obsolete button from registry: %s (%s)", reg_entry.entity_id, reg_entry.unique_id)
                ent_reg.async_remove(reg_entry.entity_id)
        # Purge obsolete numbers
        elif reg_entry.domain == "number":
            if reg_entry.unique_id not in valid_numbers:
                _LOGGER.info("Purging obsolete number from registry: %s (%s)", reg_entry.entity_id, reg_entry.unique_id)
                ent_reg.async_remove(reg_entry.entity_id)
        # Purge obsolete synthetic fan or light entities
        elif reg_entry.domain in ("fan", "light"):
            _LOGGER.info("Purging synthetic %s entity from registry: %s", reg_entry.domain, reg_entry.entity_id)
            ent_reg.async_remove(reg_entry.entity_id)

    # Ensure all user-created devices exist in Home Assistant's Device Registry
    dev_reg = dr.async_get(hass)
    device_names = set(saved_codes.get("__devices__", []))
    for k, v in saved_codes.items():
        if k.startswith("__"):
            continue
        if isinstance(v, dict) and v.get("device"):
            device_names.add(v["device"].strip())
        elif " - " in k:
            device_names.add(k.split(" - ", 1)[0].strip())

    for dev_name in device_names:
        if not dev_name:
            continue
        slug_dev = dev_name.lower().replace(" ", "_")
        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{entry.entry_id}_dev_{slug_dev}")},
            name=dev_name,
            manufacturer="ESP32 CC1101 RF",
            model="Learned RF Device",
            via_device=(DOMAIN, entry.entry_id),
        )

    @callback
    def _on_entity_registry_updated(event: Any) -> None:
        if event.data.get("action") == "remove":
            removed_ent_id = event.data.get("entity_id", "")
            if not removed_ent_id.startswith("button."):
                return
            saved = entry_data.get("saved_codes", {})
            to_delete = []
            for full_key in list(saved.keys()):
                if full_key.startswith("__"):
                    continue
                slug = full_key.lower().replace(" ", "_")
                short = (full_key.split(" - ", 1)[1] if " - " in full_key else full_key).lower().replace(" ", "_")
                if removed_ent_id.endswith(f"_{slug}") or removed_ent_id.endswith(f"_{short}"):
                    to_delete.append(full_key)
            if to_delete:
                for k in to_delete:
                    saved.pop(k, None)
                    _LOGGER.info("Entity '%s' removed from HA -> removed RF code '%s' from storage", removed_ent_id, k)
                hass.async_create_task(entry_data["store"].async_save(saved))

    try:
        from homeassistant.helpers import entity_registry as er
        entry.async_on_unload(
            hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _on_entity_registry_updated)
        )
    except Exception as err:
        _LOGGER.debug("Could not register entity registry listener: %s", err)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_learning_recording_timer(
    hass: HomeAssistant, entry_id: str, duration: float = 5.0
) -> None:
    """Timer that waits 5.0 seconds for continuous button press and evaluates consensus."""
    await asyncio.sleep(duration)
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not entry_data:
        return
    learning = entry_data.get("learning")
    if not learning or not learning.get("active") or learning.get("phase") != "recording":
        return

    samples = learning.get("samples", [])
    decoded_samples = learning.get("decoded_samples", [])
    cmd_name = learning.get("name", "")
    dev_name = learning.get("device", "").strip()
    display_label = f"{dev_name} - {cmd_name}" if dev_name else cmd_name

    result_code, desc = _analyze_learning_consensus(samples, decoded_samples)

    if not result_code:
        # Consensus failed (e.g. ambient noise or fewer than 3 matching frames)
        learning["active"] = False
        learning["phase"] = "failed"
        prev_mode = learning.get("prev_mode", RX_MODE_ALL)
        prev_rssi = learning.get("prev_rssi", DEFAULT_MIN_RSSI)
        client: Esp32Cc1101Client = entry_data["client"]
        hass.async_create_task(client.async_set_config(min_rssi=prev_rssi, rx_mode=prev_mode))

        if "update_learning_sensor_callback" in entry_data:
            try:
                entry_data["update_learning_sensor_callback"]()
            except Exception:
                pass

        _LOGGER.warning(
            "Continuous RF learning failed for '%s': %s (samples: %d)",
            display_label,
            desc,
            len(samples),
        )
        persistent_notification.async_create(
            hass,
            f"⚠️ **No se pudo aprender el botón `{display_label}`**\n\n"
            f"**Motivo:** {desc}.\n\n"
            f"Se recibieron {len(samples)} paquetes durante los 5 segundos, pero no hubo suficiente consistencia de señal o se detectaron interferencias.\n\n"
            f"👉 **Consejos:**\n"
            f"- Mantén el mando a 10–30 cm del transceptor CC1101.\n"
            f"- Mantén el botón pulsado firmemente y sin soltar durante los 5 segundos completos.\n"
            f"- Si hay interferencias de otros mandos, puedes ajustar el filtro de potencia de señal para restringir solo a señales potentes.\n"
            f"- Vuelve a intentar el aprendizaje desde el menú.",
            title=f"Error al Aprender: {display_label}",
            notification_id=f"{DOMAIN}_learning_{entry_id}",
        )
        return

    # Phase 1 Success! Move to verification phase
    learning["phase"] = "verifying"
    learning["candidate_code"] = result_code
    learning["candidate_desc"] = desc
    learning["last_sample_time"] = time.time()  # reset debounce for single click

    # Listen passively in RX_MODE_ALL with restored normal squelch to verify real detection
    prev_rssi = learning.get("prev_rssi", DEFAULT_MIN_RSSI)
    client = entry_data["client"]
    hass.async_create_task(client.async_set_config(min_rssi=prev_rssi, rx_mode=RX_MODE_ALL))

    if "update_learning_sensor_callback" in entry_data:
        try:
            entry_data["update_learning_sensor_callback"]()
        except Exception:
            pass

    _LOGGER.info(
        "Recording phase complete for '%s' (%s). Awaiting single-click verification...",
        display_label,
        desc,
    )

    persistent_notification.async_create(
        hass,
        f"✅ **Paso 2 de 2: Verificación de `{display_label}`**\n\n"
        f"Señal analizada con éxito: `{desc}`.\n\n"
        f"👉 **Ahora, pulsa el botón en tu mando físico UNA SOLA VEZ para confirmar que Home Assistant lo detecta.**\n\n"
        f"*(Tienes 15 segundos para pulsar)*",
        title=f"Paso 2/2: Pulsa para Verificar ({display_label})",
        notification_id=f"{DOMAIN}_learning_{entry_id}",
    )

    # Launch 15-second verification timeout
    verify_task = hass.async_create_task(
        _async_learning_verification_timeout(hass, entry_id, 15.0)
    )
    learning["verify_task"] = verify_task


async def _async_learning_verification_timeout(
    hass: HomeAssistant, entry_id: str, timeout: float = 15.0
) -> None:
    """Handles verification phase timeout if the user does not press the button within 15 seconds."""
    await asyncio.sleep(timeout)
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not entry_data:
        return
    learning = entry_data.get("learning")
    if not learning or not learning.get("active") or learning.get("phase") != "verifying":
        return

    cmd_name = learning.get("name", "")
    dev_name = learning.get("device", "").strip()
    display_label = f"{dev_name} - {cmd_name}" if dev_name else cmd_name

    learning["active"] = False
    learning["phase"] = "timeout"
    prev_mode = learning.get("prev_mode", RX_MODE_ALL)
    prev_rssi = learning.get("prev_rssi", DEFAULT_MIN_RSSI)
    client: Esp32Cc1101Client = entry_data["client"]
    hass.async_create_task(client.async_set_config(min_rssi=prev_rssi, rx_mode=prev_mode))

    if "update_learning_sensor_callback" in entry_data:
        try:
            entry_data["update_learning_sensor_callback"]()
        except Exception:
            pass

    _LOGGER.warning(
        "Verification timed out after 15s for '%s'. Candidate code was discarded.",
        display_label,
    )
    persistent_notification.async_create(
        hass,
        f"⏱️ **Tiempo agotado en la verificación de `{display_label}`**\n\n"
        f"No se detectó la pulsación de confirmación dentro del plazo de 15 segundos.\n\n"
        f"Para garantizar que no se guarden señales corruptas ni interferencias de otros dispositivos, **el código capturado ha sido descartado**.\n\n"
        f"Puedes volver a iniciar el proceso cuando estés listo.",
        title=f"Verificación Cancelada: {display_label}",
        notification_id=f"{DOMAIN}_learning_{entry_id}",
    )


async def _async_learning_verification_success(
    hass: HomeAssistant,
    entry_id: str,
    matched_signal: Any,
) -> None:
    """Save the confirmed code, add the button entity, notify sensors and user."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not entry_data:
        return
    learning = entry_data.get("learning")
    if not learning or not learning.get("active"):
        return

    # Cancel verify task so it doesn't fire timeout
    verify_task = learning.get("verify_task")
    if verify_task and not verify_task.done():
        verify_task.cancel()

    result_code = learning.get("candidate_code")
    desc = learning.get("candidate_desc", "Confirmed")
    cmd_name = learning["name"]
    dev_name = learning.get("device", "").strip()
    display_label = f"{dev_name} - {cmd_name}" if dev_name else cmd_name

    learning["active"] = False
    learning["phase"] = "done"
    prev_mode = learning.get("prev_mode", RX_MODE_ALL)
    prev_rssi = learning.get("prev_rssi", DEFAULT_MIN_RSSI)
    client: Esp32Cc1101Client = entry_data["client"]
    hass.async_create_task(client.async_set_config(min_rssi=prev_rssi, rx_mode=prev_mode))

    full_cmd_name = display_label
    if isinstance(result_code, dict):
        result_code["device"] = dev_name
        result_code["command"] = cmd_name
    else:
        result_code = {
            "type": "raw",
            "raw": result_code,
            "device": dev_name,
            "command": cmd_name,
        }

    if learning.get("repeats") is not None and isinstance(result_code, dict):
        result_code["repeats"] = int(learning["repeats"])

    entry_data["saved_codes"][full_cmd_name] = result_code
    if dev_name:
        devices_list = entry_data["saved_codes"].setdefault("__devices__", [])
        if dev_name not in devices_list:
            devices_list.append(dev_name)
    await entry_data["store"].async_save(entry_data["saved_codes"])
    _LOGGER.info(
        "Successfully verified and saved RF command '%s' (%s)",
        full_cmd_name,
        desc,
    )

    # Add the button entity
    if entry_data.get("add_button_callback"):
        entry_data["add_button_callback"](full_cmd_name)

    # Ensure device sensor exists and update it immediately with this verified button press
    if dev_name:
        if entry_data.get("add_device_sensor_callback"):
            try:
                entry_data["add_device_sensor_callback"](dev_name)
            except Exception as err:
                _LOGGER.debug("Could not add device sensor on learn: %s", err)

        device_button_listeners = entry_data.get("device_button_listeners", {})
        listener = device_button_listeners.get(dev_name.lower())
        if listener:
            try:
                listener(cmd_name, client.last_rssi, matched_signal)
            except Exception as err:
                _LOGGER.debug("Error notifying device button listener on learn: %s", err)

    if "update_delete_select_callback" in entry_data:
        try:
            entry_data["update_delete_select_callback"]()
        except Exception:
            pass

    if "update_target_device_callback" in entry_data:
        try:
            entry_data["update_target_device_callback"]()
        except Exception:
            pass

    if "update_target_device_select_callback" in entry_data:
        try:
            entry_data["update_target_device_select_callback"]()
        except Exception:
            pass

    if "update_learning_sensor_callback" in entry_data:
        try:
            entry_data["update_learning_sensor_callback"]()
        except Exception:
            pass

    persistent_notification.async_create(
        hass,
        f"🎉 **¡Botón `{display_label}` aprendido y verificado con éxito!**\n\n"
        f"**Detalles:** {desc}\n\n"
        f"Home Assistant ha detectado la pulsación de confirmación correctamente.\n\n"
        f"Se ha añadido la entidad botón **{cmd_name}** a tu dispositivo **{dev_name or 'Mandos RF'}** y el sensor de último botón pulsado ha sido actualizado.",
        title=f"¡Botón Verificado!: {display_label}",
        notification_id=f"{DOMAIN}_learning_{entry_id}",
    )


async def _handle_learning_sample(
    hass: HomeAssistant, entry_id: str, raw_code: str, payload: dict[str, Any] | None = None
) -> None:
    """Handle individual capture during continuous hold (Phase 1) or verification press (Phase 2)."""
    entry_data = hass.data[DOMAIN].get(entry_id)
    if not entry_data:
        return

    learning = entry_data.get("learning")
    if not learning or not learning.get("active"):
        return

    phase = learning.get("phase", "recording")
    now = time.time()
    last_sample_time = learning.get("last_sample_time", 0.0)

    if phase == "recording":
        learn_rssi = learning.get("learn_rssi")
        if learn_rssi is not None:
            pkt_rssi = payload.get("rssi") if payload else None
            if pkt_rssi is None:
                client_inst = entry_data.get("client")
                if client_inst:
                    pkt_rssi = client_inst.last_rssi
            if pkt_rssi is not None and pkt_rssi < learn_rssi:
                _LOGGER.debug(
                    "Dropped weak learning sample: RSSI %s < %s dBm",
                    pkt_rssi,
                    learn_rssi,
                )
                return

        # During continuous 5s hold, bursts arrive every ~40-100ms.
        # Debounce with 25ms to avoid exact duplicate reads in 1ms.
        if (now - last_sample_time) < 0.025:
            return

        pulse_parts = [p.strip() for p in raw_code.split(",") if p.strip()] if raw_code else []
        # Filter out noisy single-glitch pulses (< 16 pulses) if raw
        if raw_code and len(pulse_parts) < 16:
            return

        learning["last_sample_time"] = now
        if raw_code:
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
        dev_name = learning.get("device", "").strip()
        display_label = f"{dev_name} - {cmd_name}" if dev_name else cmd_name

        _LOGGER.debug(
            "Captured continuous learning burst #%d for '%s' (%d pulses)",
            count,
            display_label,
            len(pulse_parts),
        )

        if "update_learning_sensor_callback" in entry_data:
            try:
                entry_data["update_learning_sensor_callback"]()
            except Exception:
                pass

    elif phase == "verifying":
        # Phase 2: Single test click to verify!
        # Debounce to ensure remote button release before testing
        if (now - last_sample_time) < 0.4:
            return

        candidate = learning.get("candidate_code")
        if not candidate:
            return

        rx_code = payload.get("code") if payload else None
        rx_raw = raw_code or (payload.get("raw") if payload else "")

        matched = False
        matched_sig = rx_code or rx_raw

        # 1. Match by decoded code
        if isinstance(candidate, dict):
            cand_type = candidate.get("type")
            cand_code = candidate.get("code")
            if cand_code is not None and rx_code is not None:
                if _codes_match(cand_code, rx_code):
                    matched = True
                elif cand_type == "rolling_code":
                    base_id = candidate.get("base_id")
                    try:
                        rx_int = int(rx_code)
                        if base_id and (rx_int >> 8) == int(base_id):
                            matched = True
                    except (ValueError, TypeError):
                        pass

            # 2. Match by raw pulse correlation if code didn't match or not decoded
            if not matched and rx_raw:
                cand_raw = candidate.get("raw", "")
                if cand_raw:
                    score = _compare_raw_pulses(rx_raw, cand_raw)
                    if score >= 0.80:
                        matched = True
        elif isinstance(candidate, str) and rx_raw:
            score = _compare_raw_pulses(rx_raw, candidate)
            if score >= 0.80:
                matched = True

        if matched:
            _LOGGER.info(
                "Verification successful for '%s'! Signal matched: %s",
                learning.get("name"),
                matched_sig,
            )
            await _async_learning_verification_success(hass, entry_id, matched_sig)


def _handle_passive_rf_receive(
    hass: HomeAssistant, entry_id: str, client: Esp32Cc1101Client, data: dict[str, Any]
) -> None:
    """Handle incoming RF packets in normal listening mode to synchronize device states."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not entry_data:
        return

    saved_codes = entry_data.get("saved_codes", {})
    payload = data.get("payload", {})
    rx_code = payload.get("code")
    rx_raw = payload.get("raw") or client.last_code or ""

    matched_key: str | None = None
    matched_data: dict[str, Any] | str | None = None

    # 1. Try matching by decoded code (EV1527, PT2262, fixed, rolling codes)
    if rx_code is not None:
        for k, v in saved_codes.items():
            if k.startswith("__"):
                continue
            if isinstance(v, dict):
                stored_type = v.get("type")
                if stored_type == "code" or "code" in v:
                    stored_code = v.get("code")
                    if _codes_match(stored_code, rx_code):
                        matched_key = k
                        matched_data = v
                        break
                elif stored_type == "rolling_code":
                    base_id = v.get("base_id")
                    try:
                        rx_int = int(rx_code)
                        if base_id and (rx_int >> 8) == int(base_id):
                            matched_key = k
                            matched_data = v
                            v["code"] = rx_int
                            hass.async_create_task(entry_data["store"].async_save(saved_codes))
                            break
                    except (ValueError, TypeError):
                        pass

    # 2. If no match by code, or code is unavailable/unsupported protocol, match by raw pulse correlation
    if not matched_key and rx_raw:
        best_score = 0.0
        best_match_key = None
        best_match_data = None

        for k, v in saved_codes.items():
            if k.startswith("__"):
                continue
            stored_raw = ""
            if isinstance(v, str):
                stored_raw = v
            elif isinstance(v, dict):
                stored_raw = v.get("raw", "")

            if stored_raw:
                score = _compare_raw_pulses(rx_raw, stored_raw)
                if score >= 0.80 and score > best_score:
                    best_score = score
                    best_match_key = k
                    best_match_data = v

        if best_match_key:
            matched_key = best_match_key
            matched_data = best_match_data
            _LOGGER.debug(
                "Matched raw RF signal for '%s' with correlation score %.2f",
                matched_key,
                best_score,
            )

    if not matched_key:
        _LOGGER.debug(
            "Passive RF signal not matched (code=%s, raw_len=%d)",
            rx_code,
            len(rx_raw.split(',')) if rx_raw else 0,
        )
        return

    dev_name = ""
    cmd_name = matched_key
    if isinstance(matched_data, dict) and matched_data.get("device"):
        dev_name = matched_data["device"].strip()
        cmd_name = matched_data.get("command", matched_key).strip()
    elif " - " in matched_key:
        dev_name, cmd_name = matched_key.split(" - ", 1)
        dev_name = dev_name.strip()
        cmd_name = cmd_name.strip()

    device_button_listeners = entry_data.get("device_button_listeners", {})
    if not dev_name and len(device_button_listeners) == 1:
        dev_name = list(device_button_listeners.keys())[0]

    _LOGGER.info(
        "Physical RF remote detected: Device '%s' -> Button '%s' (RSSI: %s dBm)",
        dev_name,
        cmd_name,
        client.last_rssi,
    )

    # Fire HA event for automations
    hass.bus.async_fire(
        f"{DOMAIN}_button_pressed",
        {
            "device": dev_name,
            "command": cmd_name,
            "code": rx_code or rx_raw,
            "rssi": client.last_rssi,
        },
    )

    # Notify device-specific button listeners (Last Button Pressed sensor)
    target_dev_lower = dev_name.strip().lower()
    listener = device_button_listeners.get(target_dev_lower)
    if not listener:
        for d_key, d_listener in device_button_listeners.items():
            if d_key.replace(" ", "_") == target_dev_lower.replace(" ", "_"):
                listener = d_listener
                break

    if listener:
        try:
            listener(cmd_name, client.last_rssi, rx_code or rx_raw)
        except Exception as err:
            _LOGGER.debug("Device button listener error: %s", err)

    # Notify any state listeners
    state_listeners = entry_data.get("state_listeners", {})
    for slistener in list(state_listeners.values()):
        try:
            slistener(dev_name, cmd_name)
        except Exception as err:
            _LOGGER.debug("State listener callback error: %s", err)


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
    new_rx_mode = entry.options.get(CONF_RX_MODE, entry.data.get(CONF_RX_MODE, RX_MODE_ALL))

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
