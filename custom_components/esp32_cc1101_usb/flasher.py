"""ESP32 Firmware Flashing Utility using esptool."""

import logging
import os
import subprocess
import sys
from typing import Callable

_LOGGER = logging.getLogger(__name__)

DEFAULT_FIRMWARE_FILENAME = "firmware.bin"


def get_default_firmware_path() -> str:
    """Return the absolute path to the bundled firmware.bin."""
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(curr_dir, DEFAULT_FIRMWARE_FILENAME)


def flash_firmware_sync(
    port: str,
    firmware_path: str | None = None,
    baud: int = 460800,
    progress_callback: Callable[[str], None] | None = None,
) -> str:
    """Flash firmware binary to ESP32 using esptool in a synchronous call.
    
    Must be executed in an executor thread.
    """
    if not firmware_path or not os.path.isfile(firmware_path):
        firmware_path = get_default_firmware_path()

    if not os.path.isfile(firmware_path):
        raise FileNotFoundError(f"Firmware binary not found at '{firmware_path}'")

    file_size = os.path.getsize(firmware_path)
    _LOGGER.info(
        "Preparing to flash ESP32 on port %s with binary %s (%d bytes)",
        port,
        firmware_path,
        file_size,
    )

    if progress_callback:
        progress_callback("Connecting to ESP32 bootloader...")

    bauds_to_try = [baud]
    if baud != 115200:
        bauds_to_try.append(115200)

    last_error: Exception | None = None

    for attempt_baud in bauds_to_try:
        cmd = [
            sys.executable,
            "-m",
            "esptool",
            "--chip",
            "esp32",
            "--port",
            port,
            "--baud",
            str(attempt_baud),
            "--before",
            "default_reset",
            "--after",
            "hard_reset",
            "write_flash",
            "-z",
            "--flash_mode",
            "dio",
            "--flash_freq",
            "40m",
            "0x10000",
            firmware_path,
        ]

        try:
            _LOGGER.info("Flashing ESP32 via esptool at %d baud on %s...", attempt_baud, port)
            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                check=True,
                timeout=120,
            )
            output = result.stdout
            _LOGGER.info("ESP32 flash completed successfully:\n%s", output)
            if progress_callback:
                progress_callback("Flash complete! ESP32 rebooted.")
            return output
        except subprocess.CalledProcessError as exc:
            err_msg = f"esptool at {attempt_baud} baud failed (exit code {exc.returncode}):\n{exc.stdout}"
            _LOGGER.warning("%s", err_msg)
            last_error = exc
        except Exception as exc:
            _LOGGER.warning("Subprocess flash at %d baud failed: %s", attempt_baud, exc)
            last_error = exc

    # Final fallback: direct Python module call
    _LOGGER.info("Attempting direct in-process esptool module call...")
    try:
        import esptool

        esptool_args = [
            "--chip",
            "esp32",
            "--port",
            port,
            "--baud",
            "115200",
            "--before",
            "default_reset",
            "--after",
            "hard_reset",
            "write_flash",
            "-z",
            "--flash_mode",
            "dio",
            "--flash_freq",
            "40m",
            "0x10000",
            firmware_path,
        ]
        try:
            esptool.main(esptool_args)
        except SystemExit as sys_exit:
            if sys_exit.code != 0:
                raise RuntimeError(f"esptool exited with code {sys_exit.code}") from sys_exit
        return "ESP32 firmware flashed via internal module successfully."
    except Exception as inner_exc:
        _LOGGER.error("Direct esptool call also failed: %s", inner_exc)
        raise RuntimeError(f"Firmware flash failed on port {port}: {last_error or inner_exc}") from inner_exc
