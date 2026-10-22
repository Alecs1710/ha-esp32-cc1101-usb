# ESP32-WROOM-32 + CC1101 USB RF Transceiver for Home Assistant

Custom Home Assistant integration for a 433.92 MHz RF transceiver connected via direct USB serial cable (`/dev/ttyUSB0`). Operates fully locally without Wi-Fi, MQTT, or network dependencies.

## Supported Hardware

* **Microcontroller:** ESP32-WROOM-32 (38-pin NodeMCU-32S / HW-395 board).
* **RF Transceiver:** Ebyte E07-M1101D (Texas Instruments CC1101, 433.92 MHz).
* **Host Connection:** Micro-USB cable connected directly to the Home Assistant host.

## Wiring Pinout (ESP32-WROOM-32 to CC1101)

| CC1101 Pin | Function | ESP32-WROOM-32 Pin | Wire Color | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Pin 1** | GND | **GND** | Black | Common ground |
| **Pin 2** | VCC | **3V3** | Brown | 3.3V logic power (do not connect to 5V) |
| **Pin 3** | GDO0 | **GPIO 16** | Green | TX modulation output |
| **Pin 4** | CSN | **GPIO 5** | Yellow | SPI Chip Select |
| **Pin 5** | SCK | **GPIO 18** | White | SPI Clock |
| **Pin 6** | MOSI | **GPIO 23** | Blue | SPI Master Out Slave In |
| **Pin 7** | MISO | **GPIO 19** | Purple | SPI Master In Slave Out |
| **Pin 8** | GDO2 | **GPIO 17** | Grey / Extra | RX pulse input (hardware interrupt) |

## Serial Communication Protocol

* **Baud Rate:** 115200, 8N1.
* **Received payload (ESP32 -> Home Assistant):**
  `{"raw":"250,750,250,750,750,250..."}`
* **Transmit command (Home Assistant -> ESP32):**
  `TX:250,750,250,750,750,250...`

## Installation

1. In Home Assistant, open **HACS** > top-right menu (`⋮`) > **Custom repositories**.
2. Add this repository URL with category set to **Integration**.
3. Download the integration and restart Home Assistant.
4. Go to **Settings** > **Devices & Services** > **Add Integration**.
5. Search for **ESP32 CC1101 USB RF** and confirm the serial port (`/dev/ttyUSB0`).

## Entities and Services

* **Sensor:** `sensor.esp32_cc1101_usb_last_code` (contains the latest raw microsecond sequence).
* **Action/Service:** `esp32_cc1101_usb.send_raw` (accepts the `code` parameter containing comma-separated microsecond timings).
