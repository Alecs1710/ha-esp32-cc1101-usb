#include <SPI.h>
#include <ELECHOUSE_CC1101_SRC_DRV.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/queue.h>
#include "EspRCSwitch.h"

#define FW_VERSION "3.8.7"
#define UART_BAUD_RATE 460800

// Hardware Pin Configuration
const int pinSCK     = 18; // White
const int pinMISO    = 19; // Purple
const int pinMOSI    = 23; // Blue
const int pinCSN     = 5;  // Yellow
const int pinGDO0_TX = 16; // Green (TX modulation)
const int pinGDO2_RX = 17; // 8th wire (demodulated RX signal)

#define MAX_EDGES 512

enum TxCmdType {
  CMD_TX_CODE = 1,
  CMD_TX_RAW  = 2,
  CMD_RECONFIG = 3,
  CMD_RESET   = 4
};

struct TxCommand {
  uint8_t cmd_type;
  uint64_t code;
  uint8_t bits;
  uint8_t protocol;
  uint16_t pulse_len;
  int repeats;
  int gap;
  uint16_t pulse_count;
  uint16_t pulses[MAX_EDGES];
};

QueueHandle_t tx_cmd_queue = NULL;
TaskHandle_t commTaskHandle = NULL;

// Radio Configuration State (Shared/Volatile)
volatile float current_freq = 433.92;
volatile byte current_mod = 2; // 2 = ASK/OOK, 0 = 2-FSK
volatile float current_bw = 650.0;
volatile int current_pa = 10;
volatile int default_repeats = 6;
volatile int rx_mode = 0; // 0 = Decoded Only, 1 = All, 2 = Learn Mode
volatile int min_rssi_threshold = -75;
volatile int min_consecutive_repeats = 1;

EspRCSwitch mySwitch;
unsigned long last_watchdog_millis = 0;

// Duplicate filter state (OpenMQTTGateway isAduplicate pattern)
uint64_t last_rx_code = 0;
unsigned int last_rx_bits = 0;
unsigned long last_rx_time_ms = 0;
#define DUPLICATE_AVOID_MS 150

byte getRadioState() {
  return ELECHOUSE_cc1101.SpiReadStatus(0xF5) & 0x1F;
}

void applyRadioConfig() {
  ELECHOUSE_cc1101.setSidle(); // Crucial: Must be in IDLE state for CC1101 to re-lock PLL to new frequency!
  ELECHOUSE_cc1101.setMHZ(current_freq);
  ELECHOUSE_cc1101.setModulation(current_mod);
  ELECHOUSE_cc1101.setRxBW(current_bw);
  ELECHOUSE_cc1101.setPA(current_pa);
  ELECHOUSE_cc1101.SetRx();
}

void setupRadio() {
  ELECHOUSE_cc1101.setSpiPin(pinSCK, pinMISO, pinMOSI, pinCSN);
  ELECHOUSE_cc1101.setGDO(pinGDO0_TX, pinGDO2_RX);
  ELECHOUSE_cc1101.Init();

  ELECHOUSE_cc1101.setCCMode(0);               // Asynchronous serial mode
  ELECHOUSE_cc1101.setModulation(current_mod); // ASK/OOK default
  ELECHOUSE_cc1101.setMHZ(current_freq);
  ELECHOUSE_cc1101.setRxBW(current_bw);
  ELECHOUSE_cc1101.setPA(current_pa);

  ELECHOUSE_cc1101.SetRx();
}

// Forward declaration of Core 0 Task Loop
void commTaskLoop(void* pvParameters);

void setup() {
  Serial.begin(UART_BAUD_RATE);
  Serial.setTimeout(50);
  delay(300);

  pinMode(pinGDO2_RX, INPUT);
  pinMode(pinGDO0_TX, OUTPUT);
  digitalWrite(pinGDO0_TX, LOW);

  setupRadio();

  // Attach rock-solid RCSwitch receiver engine with 2.2ms separation & repeat verification
  mySwitch.enableReceive(pinGDO2_RX);
  mySwitch.enableTransmit(pinGDO0_TX);

  // Initialize FreeRTOS Command Queue
  tx_cmd_queue = xQueueCreate(4, sizeof(TxCommand));

  // Spawn Core 0 Communication Worker Task
  xTaskCreatePinnedToCore(
    commTaskLoop,
    "comm_task",
    8192,
    NULL,
    1,
    &commTaskHandle,
    0
  );

  Serial.print("{\"event\":\"ready\",\"version\":\"");
  Serial.print(FW_VERSION);
  Serial.print("\",\"chip_detected\":");
  Serial.print(ELECHOUSE_cc1101.getCC1101() ? "true" : "false");
  Serial.print(",\"freq\":");
  Serial.print(current_freq, 2);
  Serial.print(",\"mod\":");
  Serial.print(current_mod);
  Serial.print(",\"bw\":");
  Serial.print(current_bw, 1);
  Serial.print(",\"pa\":");
  Serial.print(current_pa);
  Serial.print(",\"min_rssi\":");
  Serial.print(min_rssi_threshold);
  Serial.print(",\"min_repeats\":");
  Serial.print(min_consecutive_repeats);
  Serial.print(",\"rx_mode\":");
  Serial.print(rx_mode);
  Serial.print(",\"baud\":");
  Serial.print(UART_BAUD_RATE);
  Serial.print(",\"core_pipeline\":\"dual\"");
  Serial.print(",\"radio_state\":");
  Serial.print(getRadioState());
  Serial.println("}");
}

// ==========================================
// CORE 1: HARDWARE & RADIO CONTROL PLANE
// ==========================================
volatile int active_carrier_peak_rssi = -120;
unsigned long last_rssi_sample_micros = 0;

void loop() {
  // Actively track peak RSSI while carrier pulses are incoming
  if (mySwitch.isReceiving()) {
    unsigned long now_us = micros();
    if (now_us - last_rssi_sample_micros >= 250) {
      last_rssi_sample_micros = now_us;
      int cur_rssi = ELECHOUSE_cc1101.getRssi();
      if (cur_rssi > active_carrier_peak_rssi) {
        active_carrier_peak_rssi = cur_rssi;
      }
    }
  }

  // 1. Check for incoming decoded RF packet
  if (mySwitch.available()) {
    uint64_t code = mySwitch.getReceivedValue();
    unsigned int bits = mySwitch.getReceivedBitlength();
    unsigned int delay_us = mySwitch.getReceivedDelay();
    unsigned int proto = mySwitch.getReceivedProtocol();
    unsigned int* raw_timings = mySwitch.getReceivedRawdata();

    int rssi = (active_carrier_peak_rssi > -120) ? active_carrier_peak_rssi : ELECHOUSE_cc1101.getRssi();
    active_carrier_peak_rssi = -120; // reset for next packet

    // Squelch filter: drop signals below user-configured sensitivity threshold
    if (rssi < min_rssi_threshold) {
      mySwitch.resetAvailable();
      return;
    }

    // Consecutive repeats filter: if min_consecutive_repeats > 1, require N identical codes within 800ms
    static uint64_t candidate_code = 0;
    static unsigned int candidate_count = 0;
    static unsigned long last_candidate_ms = 0;

    if (code == candidate_code && (millis() - last_candidate_ms) < 800) {
      candidate_count++;
    } else {
      candidate_code = code;
      candidate_count = 1;
    }
    last_candidate_ms = millis();

    if (candidate_count < (unsigned int)min_consecutive_repeats) {
      mySwitch.resetAvailable();
      return;
    }

    // Trailing fragment and duplicate suppression:
    // If a long code was just received within 400ms, ignore lower-bit fragments
    bool is_trailing_fragment = (last_rx_bits >= 32 && bits < last_rx_bits && (millis() - last_rx_time_ms) < 400);
    bool is_duplicate = (code == last_rx_code && (millis() - last_rx_time_ms) < DUPLICATE_AVOID_MS);

    if (!is_trailing_fragment && !is_duplicate) {
      last_rx_code = code;
      last_rx_bits = bits;
      last_rx_time_ms = millis();

      // rx_code is emitted in modes 0 (Decoded Only), 1 (All), 2 (Learn)
      if (rx_mode == 0 || rx_mode == 1 || rx_mode == 2) {
        Serial.print("{\"event\":\"rx_code\",\"code\":");
        Serial.print((unsigned long long)code);
        Serial.print(",\"bits\":");
        Serial.print(bits);
        Serial.print(",\"protocol\":");
        Serial.print(proto);
        Serial.print(",\"pulse_length\":");
        Serial.print(delay_us);
        Serial.print(",\"rssi\":");
        Serial.print(rssi);
        Serial.print(",\"freq\":");
        Serial.print(current_freq, 2);
        Serial.print(",\"raw\":\"");
        if (raw_timings != NULL && bits > 0) {
          unsigned int numChanges = bits * 2 + 1;
          if (numChanges > ESP_RCSWITCH_MAX_CHANGES) numChanges = ESP_RCSWITCH_MAX_CHANGES;
          for (unsigned int i = 0; i < numChanges; i++) {
            if (i > 0) Serial.print(",");
            Serial.print(raw_timings[i]);
          }
        }
        Serial.println("\"}");
      }
    }

    mySwitch.resetAvailable();
  } else if (mySwitch.rawAvailable()) {
    unsigned int raw_count = mySwitch.getRawChangeCount();
    unsigned int* raw_data = mySwitch.getRawTimings();
    int rssi = (active_carrier_peak_rssi > -120) ? active_carrier_peak_rssi : ELECHOUSE_cc1101.getRssi();
    active_carrier_peak_rssi = -120;

    // In rx_mode 0 ("Decoded Only (Clean)"), raw bursts are NEVER emitted to serial port!
    // They are only emitted in rx_mode 1 ("All (Decoded + Raw)") and rx_mode 2 ("Learn Mode")
    if ((rx_mode == 1 || rx_mode == 2) && raw_count >= 16 && rssi >= min_rssi_threshold && (millis() - last_rx_time_ms) > 200) {
      last_rx_time_ms = millis();
      Serial.print("{\"event\":\"rx_raw\",\"count\":");
      Serial.print(raw_count);
      Serial.print(",\"rssi\":");
      Serial.print(rssi);
      Serial.print(",\"freq\":");
      Serial.print(current_freq, 2);
      Serial.print(",\"raw\":\"");
      for (unsigned int i = 0; i < raw_count; i++) {
        if (i > 0) Serial.print(",");
        Serial.print(raw_data[i]);
      }
      Serial.println("\"}");
    }

    mySwitch.resetRawAvailable();
  }

  // Clear lingering peak RSSI if transmission ended with no frame
  if (active_carrier_peak_rssi > -120 && !mySwitch.isReceiving() && (micros() - last_rssi_sample_micros) > 50000) {
    active_carrier_peak_rssi = -120;
  }

  // 2. Transmit commands from Core 0
  TxCommand tx_cmd;
  if (xQueueReceive(tx_cmd_queue, &tx_cmd, 0) == pdTRUE) {
    mySwitch.disableReceive();

    if (tx_cmd.cmd_type == CMD_TX_CODE) {
      ELECHOUSE_cc1101.SetTx();
      mySwitch.setPulseLength(tx_cmd.pulse_len);
      mySwitch.setRepeatTransmit(tx_cmd.repeats);
      mySwitch.send(tx_cmd.code, tx_cmd.bits);
      ELECHOUSE_cc1101.setSidle();
      ELECHOUSE_cc1101.SpiStrobe(0x3A); // Flush RX FIFO
      ELECHOUSE_cc1101.SpiStrobe(0x3B); // Flush TX FIFO
      ELECHOUSE_cc1101.SetRx();
      mySwitch.enableReceive(pinGDO2_RX);

      Serial.println("{\"event\":\"tx_complete\",\"status\":\"ok\"}");
    } else if (tx_cmd.cmd_type == CMD_TX_RAW) {
      ELECHOUSE_cc1101.SetTx();

      for (int r = 0; r < tx_cmd.repeats; r++) {
        bool state = HIGH;
        for (uint16_t i = 0; i < tx_cmd.pulse_count; i++) {
          digitalWrite(pinGDO0_TX, state);
          delayMicroseconds(tx_cmd.pulses[i]);
          state = !state;
        }
        digitalWrite(pinGDO0_TX, LOW);
        if (r < tx_cmd.repeats - 1) {
          delayMicroseconds(tx_cmd.gap);
        }
      }

      digitalWrite(pinGDO0_TX, LOW);
      ELECHOUSE_cc1101.setSidle();
      ELECHOUSE_cc1101.SpiStrobe(0x3A); // Flush RX FIFO
      ELECHOUSE_cc1101.SpiStrobe(0x3B); // Flush TX FIFO
      ELECHOUSE_cc1101.SetRx();
      mySwitch.enableReceive(pinGDO2_RX);

      Serial.println("{\"event\":\"tx_complete\",\"status\":\"ok\"}");
    } else if (tx_cmd.cmd_type == CMD_RECONFIG) {
      applyRadioConfig();
      mySwitch.enableReceive(pinGDO2_RX);
    } else if (tx_cmd.cmd_type == CMD_RESET) {
      setupRadio();
      mySwitch.enableReceive(pinGDO2_RX);
    }
  }

  // 3. HARDWARE WATCHDOG: ENSURE CC1101 IS ALWAYS IN RX & RECOVER FROM ANOMALIES
  // Runs every 30ms during idle periods. Recovery sequence takes < 0.1ms (8us SPI + 80us PLL lock).
  if (millis() - last_watchdog_millis > 30) {
    last_watchdog_millis = millis();
    byte state = getRadioState();
    if (state != 13 && state != 19) { // 13 is RX, 19 is TX
      ELECHOUSE_cc1101.setSidle();
      ELECHOUSE_cc1101.SpiStrobe(0x3A); // SFRX - Flush RX FIFO
      ELECHOUSE_cc1101.SpiStrobe(0x3B); // SFTX - Flush TX FIFO
      ELECHOUSE_cc1101.SetRx();
    }
  }
}

// ==========================================
// CORE 0 TASK: ASYNC SERIAL & COMMAND PARSER
// ==========================================
void parseJsonCommand(const String& cmd) {
  if (cmd.indexOf("\"ping\"") >= 0) {
    Serial.print("{\"event\":\"pong\",\"status\":\"ok\",\"version\":\"");
    Serial.print(FW_VERSION);
    Serial.println("\"}");
    return;
  }

  if (cmd.indexOf("\"bootloader\"") >= 0 || cmd.indexOf("\"flash\"") >= 0) {
    Serial.println("{\"event\":\"bootloader\",\"status\":\"entering\"}");
    Serial.flush();
    delay(100);
    #if defined(RTC_CNTL_FORCE_DOWNLOAD_BOOT) && defined(RTC_CNTL_OPTION1_REG)
    REG_WRITE(RTC_CNTL_OPTION1_REG, RTC_CNTL_FORCE_DOWNLOAD_BOOT);
    #endif
    pinMode(0, OUTPUT);
    digitalWrite(0, LOW);
    delay(50);
    esp_restart();
    return;
  }

  if (cmd.indexOf("\"get_status\"") >= 0 || cmd.indexOf("\"get_config\"") >= 0 || cmd.indexOf("\"status\"") >= 0) {
    Serial.print("{\"event\":\"status\",\"version\":\"");
    Serial.print(FW_VERSION);
    Serial.print("\",\"chip_detected\":");
    Serial.print(ELECHOUSE_cc1101.getCC1101() ? "true" : "false");
    Serial.print(",\"freq\":");
    Serial.print(current_freq, 2);
    Serial.print(",\"mod\":");
    Serial.print(current_mod);
    Serial.print(",\"bw\":");
    Serial.print(current_bw, 1);
    Serial.print(",\"pa\":");
    Serial.print(current_pa);
    Serial.print(",\"min_rssi\":");
    Serial.print(min_rssi_threshold);
    Serial.print(",\"min_repeats\":");
    Serial.print(min_consecutive_repeats);
    Serial.print(",\"rx_mode\":");
    Serial.print(rx_mode);
    Serial.print(",\"baud\":");
    Serial.print(UART_BAUD_RATE);
    Serial.print(",\"core_pipeline\":\"dual\"");
    Serial.print(",\"radio_state\":");
    Serial.print(getRadioState());
    Serial.println("}");
    return;
  }

  if (cmd.indexOf("\"reset\"") >= 0) {
    TxCommand tx_cmd;
    tx_cmd.cmd_type = CMD_RESET;
    xQueueSend(tx_cmd_queue, &tx_cmd, pdMS_TO_TICKS(1500));
    return;
  }

  if (cmd.indexOf("\"set_config\"") >= 0 || cmd.indexOf("\"reconfig\"") >= 0) {
    int freqIdx = cmd.indexOf("\"freq\":");
    if (freqIdx >= 0) {
      float f = cmd.substring(freqIdx + 7).toFloat();
      if (f >= 300.0 && f <= 928.0) {
        current_freq = f;
      }
    }

    int modIdx = cmd.indexOf("\"mod\":");
    if (modIdx >= 0) {
      int m = cmd.substring(modIdx + 6).toInt();
      if (m == 0 || m == 2) {
        current_mod = m;
      }
    }

    int bwIdx = cmd.indexOf("\"bw\":");
    if (bwIdx >= 0) {
      float bw = cmd.substring(bwIdx + 5).toFloat();
      if (bw >= 58.0 && bw <= 812.0) {
        current_bw = bw;
      }
    }

    int paIdx = cmd.indexOf("\"pa\":");
    if (paIdx >= 0) {
      int pa = cmd.substring(paIdx + 5).toInt();
      if (pa >= -30 && pa <= 10) {
        current_pa = pa;
      }
    }

    int rssiIdx = cmd.indexOf("\"min_rssi\":");
    if (rssiIdx >= 0) {
      min_rssi_threshold = cmd.substring(rssiIdx + 11).toInt();
    }

    int repIdx = cmd.indexOf("\"min_repeats\":");
    if (repIdx >= 0) {
      min_consecutive_repeats = cmd.substring(repIdx + 14).toInt();
      if (min_consecutive_repeats < 1) min_consecutive_repeats = 1;
    }

    int rxmIdx = cmd.indexOf("\"rx_mode\":");
    if (rxmIdx >= 0) {
      int rxm = cmd.substring(rxmIdx + 10).toInt();
      if (rxm >= 0 && rxm <= 2) {
        rx_mode = rxm;
      }
    }

    TxCommand tx_cmd;
    tx_cmd.cmd_type = CMD_RECONFIG;
    xQueueSend(tx_cmd_queue, &tx_cmd, pdMS_TO_TICKS(1500));

    Serial.print("{\"event\":\"config_updated\",\"status\":\"ok\",\"version\":\"");
    Serial.print(FW_VERSION);
    Serial.print("\",\"freq\":");
    Serial.print(current_freq, 2);
    Serial.print(",\"mod\":");
    Serial.print(current_mod);
    Serial.print(",\"bw\":");
    Serial.print(current_bw, 1);
    Serial.print(",\"pa\":");
    Serial.print(current_pa);
    Serial.print(",\"min_rssi\":");
    Serial.print(min_rssi_threshold);
    Serial.print(",\"min_repeats\":");
    Serial.print(min_consecutive_repeats);
    Serial.print(",\"rx_mode\":");
    Serial.print(rx_mode);
    Serial.print(",\"radio_state\":");
    Serial.print(getRadioState());
    Serial.println("}");
    return;
  }

  if (cmd.indexOf("\"tx_code\"") >= 0) {
    TxCommand tx_cmd;
    tx_cmd.cmd_type = CMD_TX_CODE;
    tx_cmd.code = 0;
    tx_cmd.bits = 24;
    tx_cmd.protocol = 1;
    tx_cmd.pulse_len = 350;
    tx_cmd.repeats = default_repeats;

    int idx = cmd.indexOf("\"code\":");
    if (idx >= 0) tx_cmd.code = strtoull(cmd.substring(idx + 7).c_str(), NULL, 10);
    idx = cmd.indexOf("\"bits\":");
    if (idx >= 0) tx_cmd.bits = cmd.substring(idx + 7).toInt();
    idx = cmd.indexOf("\"protocol\":");
    if (idx >= 0) tx_cmd.protocol = cmd.substring(idx + 11).toInt();
    idx = cmd.indexOf("\"pulse_length\":");
    if (idx >= 0) tx_cmd.pulse_len = cmd.substring(idx + 15).toInt();
    idx = cmd.indexOf("\"repeats\":");
    if (idx >= 0) tx_cmd.repeats = cmd.substring(idx + 10).toInt();

    if (tx_cmd.repeats <= 0) tx_cmd.repeats = default_repeats;

    xQueueSend(tx_cmd_queue, &tx_cmd, pdMS_TO_TICKS(1500));
    return;
  }

  if (cmd.indexOf("\"tx_raw\"") >= 0) {
    TxCommand tx_cmd;
    tx_cmd.cmd_type = CMD_TX_RAW;
    tx_cmd.repeats = default_repeats;
    tx_cmd.gap = 8250;
    tx_cmd.pulse_count = 0;

    int idx = cmd.indexOf("\"repeats\":");
    if (idx >= 0) tx_cmd.repeats = cmd.substring(idx + 10).toInt();
    idx = cmd.indexOf("\"gap\":");
    if (idx >= 0) tx_cmd.gap = cmd.substring(idx + 6).toInt();

    int pStart = cmd.indexOf("\"pulses\":[");
    if (pStart >= 0) {
      pStart += 10;
      int pEnd = cmd.indexOf(']', pStart);
      if (pEnd > pStart) {
        String pStr = cmd.substring(pStart, pEnd);
        int s = 0;
        int e = pStr.indexOf(',');
        while (e != -1 && tx_cmd.pulse_count < MAX_EDGES) {
          tx_cmd.pulses[tx_cmd.pulse_count++] = pStr.substring(s, e).toInt();
          s = e + 1;
          e = pStr.indexOf(',', s);
        }
        if (s < pStr.length() && tx_cmd.pulse_count < MAX_EDGES) {
          tx_cmd.pulses[tx_cmd.pulse_count++] = pStr.substring(s).toInt();
        }
        if (tx_cmd.pulse_count > 0) {
          xQueueSend(tx_cmd_queue, &tx_cmd, pdMS_TO_TICKS(1500));
          return;
        }
      }
    }
  }

  Serial.println("{\"event\":\"error\",\"message\":\"unknown_command\"}");
}

void commTaskLoop(void* pvParameters) {
  while (true) {
    while (Serial.available()) {
      String cmd = Serial.readStringUntil('\n');
      cmd.trim();
      if (cmd.length() > 0) {
        if (cmd.startsWith("{")) {
          parseJsonCommand(cmd);
        } else if (cmd.startsWith("TX:")) {
          String data = cmd.substring(3);
          TxCommand tx_cmd;
          tx_cmd.cmd_type = CMD_TX_RAW;
          tx_cmd.repeats = default_repeats;
          tx_cmd.gap = 8250;
          tx_cmd.pulse_count = 0;

          int start = 0;
          int end = data.indexOf(',');
          while (end != -1 && tx_cmd.pulse_count < MAX_EDGES) {
            tx_cmd.pulses[tx_cmd.pulse_count++] = data.substring(start, end).toInt();
            start = end + 1;
            end = data.indexOf(',', start);
          }
          if (start < data.length() && tx_cmd.pulse_count < MAX_EDGES) {
            tx_cmd.pulses[tx_cmd.pulse_count++] = data.substring(start).toInt();
          }
          if (tx_cmd.pulse_count > 0) {
            xQueueSend(tx_cmd_queue, &tx_cmd, pdMS_TO_TICKS(1500));
          }
        }
      }
    }

    vTaskDelay(pdMS_TO_TICKS(1));
  }
}