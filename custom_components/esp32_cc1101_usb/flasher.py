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
    Uses modern esptool v5 syntax with multi-baud and no-stub fallbacks.
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

    attempts = [
        {"baud": baud, "no_stub": False, "desc": f"{baud} baud with stub"},
        {"baud": 115200, "no_stub": False, "desc": "115200 baud with stub"},
        {"baud": 115200, "no_stub": True, "desc": "115200 baud direct ROM (no-stub)"},
    ]

    last_error: Exception | None = None

    for attempt in attempts:
        attempt_baud = attempt["baud"]
        use_no_stub = attempt["no_stub"]
        desc = attempt["desc"]

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
        ]

        if use_no_stub:
            cmd.append("--no-stub")

        cmd.extend([
            "--before",
            "default-reset",
            "--after",
            "hard-reset",
            "write-flash",
            "-z",
            "--flash-mode",
            "keep",
            "--flash-freq",
            "40m",
            "0x10000",
            firmware_path,
        ])

        try:
            _LOGGER.warning(
                "[ESP32 CC1101 Flash] Attempting esptool on %s (%s, timeout 35s)...",
                port,
                desc,
            )
            if progress_callback:
                progress_callback(f"Connecting to ESP32 ({desc})...")

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            stdout_data, _ = proc.communicate(timeout=35)
            output = stdout_data or ""

            for raw_line in output.splitlines():
                line = raw_line.strip()
                if line:
                    _LOGGER.warning("[ESP32 CC1101 Flash] %s", line)
                    if progress_callback and any(
                        tag in line for tag in ("Writing at", "Connecting", "Chip is", "Erasing", "Hash of", "%")
                    ):
                        progress_callback(line)

            if proc.returncode == 0:
                _LOGGER.warning("[ESP32 CC1101 Flash] Flash succeeded (%s)! ESP32 rebooted.", desc)
                if progress_callback:
                    progress_callback("Flash complete! ESP32 rebooted.")
                return output
            else:
                err_msg = f"esptool failed (exit code {proc.returncode}) with {desc}"
                _LOGGER.warning("[ESP32 CC1101 Flash] %s", err_msg)
                last_error = RuntimeError(f"{err_msg}\n{output}")

        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.communicate()
            except Exception:
                pass
            err_msg = f"esptool timed out after 35s with {desc}"
            _LOGGER.warning("[ESP32 CC1101 Flash] %s", err_msg)
            last_error = TimeoutError(err_msg)

        except Exception as exc:
            _LOGGER.warning("[ESP32 CC1101 Flash] Execution error with %s: %s", desc, exc)
            last_error = exc

    # If all attempts failed
    explanation = (
        f"Firmware flash failed on port '{port}'.\n\n"
        "POSSIBLE CAUSES & SOLUTIONS:\n"
        "1. DO NOT HOLD THE BOOT BUTTON CONTINUOUSLY: Press and hold BOOT, click 'Install', "
        "and as soon as you see 'Connecting...' (1-2 seconds), IMMEDIATELY RELEASE the BOOT button. "
        "Holding it permanently during the write corrupts the SPI flash communication.\n"
        "2. CC1101 PIN 5 INTERFERENCE: The CC1101 CSN pin is wired to GPIO 5, which is an ESP32 strapping pin. "
        "If flash communication continues to fail, temporarily disconnect the CSN wire (or unplug CC1101 VCC) "
        "during the flash process, then reconnect it once updated."
    )
    _LOGGER.error("[ESP32 CC1101 Flash] %s", explanation)
    raise RuntimeError(f"{explanation}\n\n(Error details: {last_error})") from last_error
