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
    Logs at WARNING level so output is immediately visible in Home Assistant UI Logs.
    """
    if not firmware_path or not os.path.isfile(firmware_path):
        firmware_path = get_default_firmware_path()

    if not os.path.isfile(firmware_path):
        raise FileNotFoundError(f"Firmware binary not found at '{firmware_path}'")

    file_size = os.path.getsize(firmware_path)
    _LOGGER.warning(
        "[ESP32 CC1101 Flash] Preparing to flash ESP32 on port %s with binary %s (%d bytes)",
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
            "--connect-attempts",
            "4",
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
            _LOGGER.warning(
                "[ESP32 CC1101 Flash] Attempting esptool on %s at %d baud (timeout 35s)...",
                port,
                attempt_baud,
            )
            if progress_callback:
                progress_callback(f"Connecting to ESP32 on {port} at {attempt_baud} baud...")

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            collected_output = []
            try:
                stdout_data, _ = proc.communicate(timeout=35)
                output = stdout_data or ""
                collected_output.append(output)

                for raw_line in output.splitlines():
                    line = raw_line.strip()
                    if line:
                        _LOGGER.warning("[ESP32 CC1101 Flash] %s", line)
                        if progress_callback and any(
                            tag in line for tag in ("Writing at", "Connecting", "Chip is", "Erasing", "Hash of")
                        ):
                            progress_callback(line)

                if proc.returncode == 0:
                    _LOGGER.warning("[ESP32 CC1101 Flash] Flash succeeded at %d baud! ESP32 rebooted.", attempt_baud)
                    if progress_callback:
                        progress_callback("Flash complete! ESP32 rebooted.")
                    return output
                else:
                    err_msg = f"esptool returned non-zero code {proc.returncode} at {attempt_baud} baud"
                    _LOGGER.warning("[ESP32 CC1101 Flash] %s", err_msg)
                    last_error = RuntimeError(f"{err_msg}\n{output}")

            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                err_msg = f"esptool timed out after 35s at {attempt_baud} baud (waiting for bootloader sync)"
                _LOGGER.warning("[ESP32 CC1101 Flash] %s", err_msg)
                last_error = TimeoutError(err_msg)

        except Exception as exc:
            _LOGGER.warning("[ESP32 CC1101 Flash] Subprocess execution error at %d baud: %s", attempt_baud, exc)
            last_error = exc

    # Final fallback: direct Python module call
    _LOGGER.warning("[ESP32 CC1101 Flash] Attempting direct in-process esptool module call...")
    if progress_callback:
        progress_callback("Attempting direct in-process esptool call...")
    try:
        import esptool

        esptool_args = [
            "--chip",
            "esp32",
            "--port",
            port,
            "--baud",
            "115200",
            "--connect-attempts",
            "4",
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
                raise RuntimeError(f"esptool module exited with code {sys_exit.code}") from sys_exit
        _LOGGER.warning("[ESP32 CC1101 Flash] Direct esptool module flash successful.")
        if progress_callback:
            progress_callback("Flash complete via module! ESP32 rebooted.")
        return "ESP32 firmware flashed via internal module successfully."
    except Exception as inner_exc:
        _LOGGER.error("[ESP32 CC1101 Flash] Direct esptool call failed: %s", inner_exc)
        explanation = (
            f"Firmware flash failed to enter bootloader on port '{port}'.\n\n"
            "CAUSE: In VM USB passthrough environments (TrueNAS, Proxmox, ESXi), "
            "the host USB controller delays/batches DTR/RTS signals, so the ESP32 doesn't reset into ROM bootloader automatically.\n\n"
            "SOLUTION: Click 'Install' on the Firmware card again, and IMMEDIATELY press and hold "
            "the physical 'BOOT' (IO0) button on the ESP32 board for 2 seconds until the flashing starts."
        )
        raise RuntimeError(f"{explanation}\n(Error details: {last_error or inner_exc})") from inner_exc
