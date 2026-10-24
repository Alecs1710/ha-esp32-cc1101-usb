"""Update platform for ESP32 CC1101 USB."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import Esp32Cc1101Client
from .const import DOMAIN, CONF_PORT
from .flasher import flash_firmware_sync, get_default_firmware_path

_LOGGER = logging.getLogger(__name__)

TARGET_FW_VERSION = "3.6.0"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the ESP32 CC1101 USB update entity."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    client: Esp32Cc1101Client = entry_data["client"]
    async_add_entities([Esp32Cc1101FirmwareUpdate(entry, client)])


class Esp32Cc1101FirmwareUpdate(UpdateEntity):
    """Update entity for ESP32 CC1101 USB firmware."""

    _attr_has_entity_name = True
    _attr_name = "Firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_icon = "mdi:chip"
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS

    @property
    def entity_picture(self) -> str | None:
        """Return None so Home Assistant renders the standard MDI icon instead of a broken brand image."""
        return None

    def __init__(self, entry: ConfigEntry, client: Esp32Cc1101Client) -> None:
        """Initialize the update entity."""
        self._entry = entry
        self._client = client
        self._attr_unique_id = f"{entry.entry_id}_firmware_update"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"ESP32 CC1101 ({entry.data.get(CONF_PORT, 'USB')})",
            manufacturer="Texas Instruments / Espressif",
            model="CC1101 + ESP32",
            sw_version=client.version or "3.5.0",
        )

    async def async_added_to_hass(self) -> None:
        """Register client listener."""
        self.async_on_remove(
            self._client.register_listener(self._handle_client_update)
        )

    def _handle_client_update(self, data: dict[str, Any]) -> None:
        if data.get("type") in ("telemetry", "connection"):
            self.async_write_ha_state()

    @property
    def installed_version(self) -> str | None:
        """Version currently installed on device."""
        if not self._client.version or self._client.version in ("Unknown", "None", ""):
            return "3.5.0"
        return self._client.version

    @property
    def latest_version(self) -> str | None:
        """Latest available version bundled in the integration."""
        return TARGET_FW_VERSION

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Flash latest firmware to ESP32."""
        port = self._entry.data.get(CONF_PORT)
        if not port:
            raise HomeAssistantError("Serial port not configured for device")

        firmware_path = get_default_firmware_path()
        if not os.path.isfile(firmware_path):
            raise HomeAssistantError(f"Firmware binary not found at '{firmware_path}'")

        _LOGGER.warning("[ESP32 CC1101] Starting firmware flash to %s...", port)
        self._attr_in_progress = True
        self._attr_update_percentage = 0
        self.async_write_ha_state()

        try:
            # 1. Attempt software bootloader transition before releasing serial port
            try:
                await self._client.async_enter_bootloader()
            except Exception:
                pass

            # 2. Stop serial client to free the COM/tty port
            await self._client.async_stop()

            # 3. Flash in executor with thread-safe UI percentage updates
            def _progress(msg: str) -> None:
                _LOGGER.warning("[ESP32 CC1101 Flash Progress] %s", msg)
                # Parse progress percentages e.g. "(25 %)" or "25%"
                pct_match = re.search(r"(\d{1,3})\s*%", msg)
                if pct_match:
                    try:
                        pct = int(pct_match.group(1))
                        self._attr_update_percentage = min(100, max(0, pct))
                        self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)
                    except Exception:
                        pass

            await self.hass.async_add_executor_job(
                flash_firmware_sync,
                port,
                firmware_path,
                460800,
                _progress,
            )

            # 4. Reconnect client
            await self._client.async_start()
            self._attr_in_progress = False
            self._attr_update_percentage = 100
            self.async_write_ha_state()
            _LOGGER.warning("[ESP32 CC1101] Firmware flash complete on %s! Reconnected.", port)

        except Exception as exc:
            self._attr_in_progress = False
            self._attr_update_percentage = None
            self.async_write_ha_state()
            _LOGGER.error("[ESP32 CC1101] Failed to flash firmware: %s", exc)

            # Reconnect client even if flash failed
            try:
                await self._client.async_start()
            except Exception:
                pass

            raise HomeAssistantError(
                f"Firmware flash failed: {exc}. "
                "Tip for VM USB passthrough: Click Install again and immediately hold the physical BOOT button for 2 seconds (or use a 10µF capacitor between EN and GND)."
            ) from exc
