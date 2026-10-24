"""Constants for the ESP32 CC1101 USB RF integration."""

DOMAIN = "esp32_cc1101_usb"

# Configuration keys
CONF_PORT = "port"
CONF_BAUDRATE = "baudrate"
CONF_FREQUENCY = "frequency"
CONF_MODULATION = "modulation"
CONF_BANDWIDTH = "bandwidth"
CONF_TX_POWER = "tx_power"
CONF_REPEATS = "repeats"
CONF_MIN_RSSI = "min_rssi"
CONF_MIN_REPEATS = "min_repeats"
CONF_RX_MODE = "rx_mode"

# Defaults
DEFAULT_PORT = "/dev/ttyUSB0"
DEFAULT_BAUDRATE = 460800
DEFAULT_FREQUENCY = 433.92
DEFAULT_MODULATION = "ASK/OOK"
DEFAULT_BANDWIDTH = 650.0
DEFAULT_TX_POWER = 10
DEFAULT_REPEATS = 6
DEFAULT_MIN_RSSI = -75
DEFAULT_MIN_REPEATS = 1

# Reception Modes
RX_MODE_DECODED = "Decoded Only (Clean)"
RX_MODE_ALL = "All (Decoded + Raw)"
RX_MODE_LEARN = "Learn Mode"

RX_MODE_OPTIONS = [
    RX_MODE_DECODED,
    RX_MODE_ALL,
    RX_MODE_LEARN,
]

RX_MODE_MAP = {
    RX_MODE_DECODED: 0,
    RX_MODE_ALL: 1,
    RX_MODE_LEARN: 2,
}

RX_MODE_REV_MAP = {
    0: RX_MODE_DECODED,
    1: RX_MODE_ALL,
    2: RX_MODE_LEARN,
}

# Frequency presets with clear, distinct descriptions
PRESET_433 = "433.92 MHz (Standard RF - Band: 387-464 MHz)"
PRESET_315 = "315.00 MHz (US / Asia - Band: 300-348 MHz)"
PRESET_868 = "868.35 MHz (EU Security / Sensors - Band: 779-900 MHz)"
PRESET_915 = "915.00 MHz (US ISM - Band: 902-928 MHz)"
PRESET_433_SOMFY = "433.42 MHz (Somfy RTS Blinds)"
PRESET_434 = "434.42 MHz (Fine-Tuned 434 MHz)"
PRESET_868_MBUS = "868.95 MHz (Wireless M-Bus Meters)"

FREQUENCY_PRESETS = [
    PRESET_433,
    PRESET_315,
    PRESET_868,
    PRESET_915,
    PRESET_433_SOMFY,
    PRESET_434,
    PRESET_868_MBUS,
]

FREQUENCY_PRESET_MAP = {
    PRESET_433: 433.92,
    PRESET_315: 315.00,
    PRESET_868: 868.35,
    PRESET_915: 915.00,
    PRESET_433_SOMFY: 433.42,
    PRESET_434: 434.42,
    PRESET_868_MBUS: 868.95,
    # Backward compatibility with older string formats
    "433.92 MHz (Band: 387.0 - 464.0 MHz)": 433.92,
    "315.00 MHz (Band: 300.0 - 348.0 MHz)": 315.00,
    "868.35 MHz (Band: 779.0 - 900.0 MHz)": 868.35,
    "915.00 MHz (Band: 902.0 - 928.0 MHz)": 915.00,
    "433.42 MHz (Band: 387.0 - 464.0 MHz)": 433.42,
    "434.42 MHz (Band: 387.0 - 464.0 MHz)": 434.42,
    "868.95 MHz (Band: 779.0 - 900.0 MHz)": 868.95,
}


PRESET_CUSTOM = "Custom Frequency"


def get_preset_for_freq(freq: float) -> str:
    """Return the closest matching preset for a numeric frequency."""
    for name, val in FREQUENCY_PRESET_MAP.items():
        if abs(val - freq) < 0.05:
            return name
    return PRESET_433


# Modulations
MODULATION_OOK = "ASK/OOK"
MODULATION_2FSK = "2-FSK"
MODULATION_OPTIONS = [MODULATION_OOK, MODULATION_2FSK]
MODULATION_NAME_TO_CODE = {
    MODULATION_2FSK: 0,
    MODULATION_OOK: 2,
}
MODULATION_CODE_TO_NAME = {
    0: MODULATION_2FSK,
    2: MODULATION_OOK,
}

# Events
EVENT_RF_RECEIVED = "esp32_cc1101_usb_raw_received"
EVENT_CODE_RECEIVED = "esp32_cc1101_usb_code_received"

STORAGE_KEY = "esp32_cc1101_usb_codes"
STORAGE_VERSION = 1
LEARN_STEPS = 5
