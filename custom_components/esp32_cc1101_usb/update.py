"""Update platform for ESP32 CC1101 USB."""

from __future__ import annotations

import logging
import os
from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
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
    _attr_supported_features = UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS

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

    @property
    def installed_version(self) -> str | None:
        """Version currently installed on device."""
        return self._client.version or "3.5.0"

    @property
    def latest_version(self) -> str | None:
        """Latest available version bundled in the integration."""
        if os.path.isfile(get_default_firmware_path()):
            return TARGET_FW_VERSION
        return self.installed_version

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Flash latest firmware to ESP32."""
        port = self._entry.data.get(CONF_PORT)
        if not port:
            raise HomeAssistantError("Serial port not configured for device")

        firmware_path = get_default_firmware_path()
        if not os.path.isfile(firmware_path):
            raise HomeAssistantError(f"Firmware binary not found at '{firmware_path}'")

        _LOGGER.info("Starting firmware flash to %s...", port)
        self._attr_in_progress = True
        self.async_write_ha_state()

        try:
            # 1. Stop serial client to free the COM/tty port
            await self._client.async_stop()

            # 2. Flash in executor
            def _progress(msg: str) -> None:
                _LOGGER.info("Flash progress: %s", msg)

            await self.hass.async_add_executor_job(
                flash_firmware_sync,
                port,
                firmware_path,
                460800,
                _progress,
            )

            # 3. Reconnect client
            await self._client.async_start()
            self._attr_in_progress = False
            self.async_write_ha_state()
            _LOGGER.info("Firmware flash complete on %s!", port)
        except Exception as exc:
            self._attr_in_progress = False
            self.async_write_ha_state()
            _LOGGER.error("Failed to flash firmware: %s", exc)
            # Reconnect even if flash failed
            try:
                await self._client.async_start()
            except Exception:
                pass
            raise HomeAssistantError(f"Firmware flash failed: {exc}") from exc
