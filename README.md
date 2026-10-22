# ESP32-WROOM-32 + CC1101 USB RF Transceiver for Home Assistant

Custom integration to operate a 433.92 MHz RF transceiver connected via direct USB cable to Home Assistant, without relying on Wi-Fi, MQTT, or intermediate routers.

## Supported Hardware

- **Microcontroller:** ESP32-WROOM-32 (NodeMCU-32S / HW-395 38-pin development board).
- **RF Module:** Ebyte E07-M1101D (Texas Instruments CC1101 at 433.92 MHz).
- **Host Connection:** Direct Micro-USB cable to the Home Assistant server.

## Pinout (ESP32-WROOM-32 to CC1101)

| CC1101 Pin | Function | ESP32-WROOM-32 Pin | Wire Color | Description |
| :--- | :--- | :--- | :--- | :--- |
| **Pin 1** | GND | **GND** | Black | Common Ground |
| **Pin 2** | VCC | **3V3** | Brown | 3.3V Logic Power (DO NOT use 5V) |
| **Pin 3** | GDO0 | **GPIO 16** | Green | TX Modulation Output |
| **Pin 4** | CSN | **GPIO 5** | Yellow | SPI Chip Select |
| **Pin 5** | SCK | **GPIO 18** | White | SPI Clock |
| **Pin 6** | MOSI | **GPIO 23** | Blue | Data to CC1101 |
| **Pin 7** | MISO | **GPIO 19** | Purple | Data to ESP32 |
| **Pin 8** | GDO2 | **GPIO 17** | Grey/Extra | RX Pulse Input (Interrupt) |

## Installation

1. In Home Assistant, open **HACS** > top right menu (`⋮`) > **Custom repositories**.
2. Add the URL of this repository with the category **Integration**.
3. Download the integration and restart Home Assistant.
4. Go to **Settings** > **Devices and services** > **Add integration**.
5. Search for **ESP32 CC1101 USB RF** and select your ESP32 from the USB dropdown menu.

## How to Capture and Execute RF Codes

### Step 1: Capture a code
Press your physical 433 MHz remote near the CC1101 antenna. Check the entity `sensor.esp32_cc1101_last_code` in Home Assistant. Click on the sensor and expand **Attributes** to copy the `raw_code` string.

### Step 2: Test the captured code immediately
Press the entity `button.esp32_cc1101_repeat_last_code` on your dashboard to immediately re-transmit the last captured signal.

### Step 3: Execute any code from a Dashboard Button, Script, or Automation
Use the service `esp32_cc1101_usb.send_raw`:

```yaml
action: esp32_cc1101_usb.send_raw
data:
  code: "250,750,250,750,750,250"
