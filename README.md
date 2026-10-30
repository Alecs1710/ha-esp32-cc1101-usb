# ESP32-WROOM-32 + CC1101 USB RF Transceiver for Home Assistant (v3.8.7)

A professional, direct USB-connected RF (Radio Frequency) transceiver integration for Home Assistant using an **ESP32-WROOM-32** and Texas Instruments **CC1101** transceiver module.

Operates with 100% local serial communication over USB cable — zero Wi-Fi, zero MQTT, zero cloud dependencies, and lowest possible latency.

---

## Key Features in Version 2.0

-  **Multi-Frequency Agility:** Switch frequencies on-the-fly directly from Home Assistant entities or Options Flow (Presets: `433.92 MHz`, `315.00 MHz`, `868.35 MHz`, `915.00 MHz`, plus continuous tuning between `300.0 - 928.0 MHz`).
-  **Standard RF Protocol Support:** Integrated RCSwitch protocol decoder & transmitter for standard remotes (PT2262, EV1527, SC5262, HT6P20B, etc.).
- ⏱️ **Universal Raw Pulse Capture:** High-capacity buffer (512 edges) with microsecond silence detection (10 ms timeout) to capture raw rolling codes, Somfy RTS, weather stations, and custom remotes without dropping pulses or hanging.
-  **Real-time Signal Quality (RSSI):** Direct hardware RSSI measurement in dBm on every captured packet and background noise tracking.
- ️ **Live Hardware Controls:**
  - `select`: Frequency Preset selector (`433.92`, `315`, `868.35`, `915`, `Custom`), Modulation mode (`ASK/OOK`, `2-FSK`).
  - `number`: Custom Frequency (`MHz`), Transmit Output Power (`-30 to +10 dBm`), Default Transmit Repeats (`1 to 25`).
-  **Native Home Assistant Remote Platform (`remote`):** Standard `remote.send_command`, `remote.learn_command`, and `remote.delete_command` support.
- ️ **Auto-Recovery & Hardware Watchdog:** Automatic serial reconnect loop with exponential backoff on USB disconnect/reconnect, and periodic radio state verification.
- ️ **Device Registry Integration:** All sensors, switches, numbers, selects, and buttons cleanly linked to a single unified Device Card in Home Assistant.

---

## Hardware Pinout (ESP32-WROOM-32 to CC1101)

| CC1101 Pin | Function | ESP32-WROOM-32 Pin | Wire Color | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Pin 1** | GND | **GND** | Black | Common Ground |
| **Pin 2** | VCC | **3V3** | Brown | ⚠️ **Use 3.3V Only!** (5V will destroy the CC1101) |
| **Pin 3** | GDO0 | **GPIO 16** | Green | TX Modulation Output |
| **Pin 4** | CSN | **GPIO 5** | Yellow | SPI Chip Select |
| **Pin 5** | SCK | **GPIO 18** | White | SPI Clock |
| **Pin 6** | MOSI | **GPIO 23** | Blue | Data ESP32 -> CC1101 |
| **Pin 7** | MISO | **GPIO 19** | Purple | Data CC1101 -> ESP32 |
| **Pin 8** | GDO2 | **GPIO 17** | Grey/Extra | Demodulated RX Pulse Input (Interrupt) |

---

## Flashing the Firmware

### Option 1: Direct Flashing via Home Assistant (Easiest)
Once the integration is installed in Home Assistant, you can flash or update the firmware directly over USB using the built-in firmware flasher service (`esp32_cc1101_usb.flash_firmware`), as the latest compiled `firmware.bin` is already bundled with the integration!

### Option 2: Compile from Source (Arduino IDE / Arduino CLI)
1. Install the required libraries in Arduino IDE / Arduino CLI:
   - `SmartRC-CC1101-Driver-Lib` (by LSatan)
   - `rc-switch` (by sui77)
2. Open `firmware/Esp32_RF.ino` in Arduino IDE.
3. Select board: **ESP32 Dev Module** (or `esp32:esp32:esp32`).
4. Connect the ESP32 via USB and hit **Upload** (or use Arduino CLI: `arduino-cli compile --upload -p <PORT> --fqbn esp32:esp32:esp32 firmware`).

---

## Home Assistant Setup

1. Copy `custom_components/esp32_cc1101_usb` into your Home Assistant `/config/custom_components/` folder.
2. Restart Home Assistant.
3. Navigate to **Settings** > **Devices & Services** > **Add Integration**.
4. Search for **ESP32 CC1101 USB RF**.
5. Select your USB serial port (`/dev/serial/by-id/...` or `COMx` on Windows), default frequency preset, and modulation.
6. Click **Submit**.

---

## Entities Created

| Platform | Entity Name | Description |
| :--- | :--- | :--- |
| `remote` | `remote.esp32_cc1101_usb_transceiver` | Standard HA remote for learning and transmitting RF codes |
| `select` | `select.esp32_cc1101_frequency_preset` | Quick switch between 433.92 MHz, 315 MHz, 868.35 MHz, 915 MHz, Custom |
| `select` | `select.esp32_cc1101_modulation` | Switch between `ASK/OOK` and `2-FSK` |
| `number` | `number.esp32_cc1101_frequency` | Fine-tune carrier frequency (300.00 – 928.00 MHz) |
| `number` | `number.esp32_cc1101_transmit_power` | Transmit power adjustment (-30 to +10 dBm) |
| `number` | `number.esp32_cc1101_transmit_repeats`| Default packet repetition count (1–25) |
| `sensor` | `sensor.esp32_cc1101_last_code` | Last captured code (decoded or raw, with attributes) |
| `sensor` | `sensor.esp32_cc1101_signal_rssi` | Last received signal strength (dBm) |
| `sensor` | `sensor.esp32_cc1101_active_frequency`| Active tuned radio frequency (MHz) |
| `sensor` | `sensor.esp32_cc1101_radio_state` | Hardware status (Listening RX, Transmitting TX, Idle) |
| `sensor` | `sensor.esp32_cc1101_learning_status` | Status during multi-press code learning |
| `button` | `button.esp32_cc1101_repeat_last_code` | Immediately re-transmits the last captured signal |
| `button` | `button.esp32_cc1101_ping_radio` | Verifies USB serial connectivity and radio state |
| `button` | `button.esp32_cc1101_reset_radio` | Resets CC1101 and forces RX listening mode |

---

## Services Reference

### 1. `esp32_cc1101_usb.send_code`
Transmits a standard RF code (PT2262, EV1527, SC5262, etc.):
```yaml
action: esp32_cc1101_usb.send_code
data:
  code: 1234567
  bits: 24
  protocol: 1
  pulse_length: 350
  repeats: 10
```

### 2. `esp32_cc1101_usb.send_raw`
Transmits an arbitrary raw pulse train (e.g. captured from a non-standard remote):
```yaml
action: esp32_cc1101_usb.send_raw
data:
  code: "350,1050,350,1050,1050,350,1050,350"
  repeats: 6
  gap: 8250
```

### 3. `esp32_cc1101_usb.set_config`
Dynamically reconfigures the CC1101 radio in real time:
```yaml
action: esp32_cc1101_usb.set_config
data:
  frequency: 868.35
  modulation: "ASK/OOK"
  tx_power: 10
```

### 4. `esp32_cc1101_usb.learn_command`
Starts multi-sample learning (press 5 times):
```yaml
action: esp32_cc1101_usb.learn_command
data:
  name: "Garage Door"
```

### 5. `remote.send_command`
Using standard Home Assistant remote service:
```yaml
action: remote.send_command
target:
  entity_id: remote.esp32_cc1101_usb_transceiver
data:
  command: "Garage Door"
  num_repeats: 8
```
