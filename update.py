"""Update platform for ESP32 CC1101 USB."""

from __future__ import annotations

import logging
import os
from typing import Any

from homeassistant.components import persistent_notification
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
NOTIFICATION_ID = "esp32_cc1101_firmware_update"


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

    def _notify(self, message: str, title: str = "ESP32 Firmware Update") -> None:
        """Post a persistent notification safely from either the event loop or an executor worker thread."""
        def _schedule() -> None:
            persistent_notification.async_create(
                self.hass,
                message,
                title=title,
                notification_id=NOTIFICATION_ID,
            )

        try:
            self.hass.loop.call_soon_threadsafe(_schedule)
        except Exception as err:
            _LOGGER.debug("Could not schedule persistent notification: %s", err)

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
        self.async_write_ha_state()

        self._notify(
            f"Starting firmware update on port `{port}`...\n\n"
            "⏳ *Connecting to ESP32 bootloader...*\n\n"
            "> **Note for VM USB Passthrough:** If your board does not enter bootloader automatically, "
            "press and hold the physical **BOOT** button on the ESP32 for 2 seconds (or use a 10µF capacitor between EN and GND).",
            title="ESP32 Firmware Update",
        )

        try:
            # 1. Attempt software bootloader transition before releasing serial port
            try:
                await self._client.async_enter_bootloader()
            except Exception:
                pass

            # 2. Stop serial client to free the COM/tty port
            await self._client.async_stop()

            # 3. Flash in executor
            def _progress(msg: str) -> None:
                _LOGGER.warning("[ESP32 CC1101 Flash Progress] %s", msg)
                self._notify(
                    f"Flashing firmware on `{port}`:\n\n"
                    f"**Progress:** `{msg}`\n\n"
                    "> **Tip:** If stuck on *Connecting*, press and hold the physical **BOOT** button on the ESP32 for 2 seconds.",
                    title="ESP32 Firmware Update",
                )

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
            self.async_write_ha_state()
            _LOGGER.warning("[ESP32 CC1101] Firmware flash complete on %s! Reconnected.", port)

            self._notify(
                f"🎉 **Firmware successfully updated to v{TARGET_FW_VERSION}!**\n\n"
                f"The ESP32 on port `{port}` has rebooted and reconnected to Home Assistant.",
                title="ESP32 Firmware Updated",
            )
        except Exception as exc:
            self._attr_in_progress = False
            self.async_write_ha_state()
            _LOGGER.error("[ESP32 CC1101] Failed to flash firmware: %s", exc)

            self._notify(
                f"⚠️ **Firmware Update Incomplete on `{port}`**\n\n"
                f"**Details:**\n`{exc}`\n\n"
                "**How to solve on VM USB Passthrough:**\n"
                "1. Click **Install** on the Firmware update card again.\n"
                "2. **Immediately press and hold the physical 'BOOT' (IO0) button** on the ESP32 board for 2-3 seconds until flashing begins (or add a 10µF capacitor between EN and GND).",
                title="ESP32 Flash Action Needed",
            )

            # Reconnect even if flash failed
            try:
                await self._client.async_start()
            except Exception:
                pass
            raise HomeAssistantError(f"Firmware flash failed: {exc}") from exc
