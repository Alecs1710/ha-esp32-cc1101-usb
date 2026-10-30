#include "EspRCSwitch.h"

static const RCSwitchProtocol proto[] = {
  { 350, {  1, 31 }, {  1,  3 }, {  3,  1 }, false },    // protocol 1 (Standard EV1527 31T sync)
  { 650, {  1, 10 }, {  1,  2 }, {  2,  1 }, false },    // protocol 2
  { 100, { 30, 71 }, {  4, 11 }, {  9,  6 }, false },    // protocol 3
  { 380, {  1,  6 }, {  1,  3 }, {  3,  1 }, false },    // protocol 4
  { 500, {  6, 14 }, {  1,  2 }, {  2,  1 }, false },    // protocol 5
  { 450, { 23,  1 }, {  1,  2 }, {  2,  1 }, true  },    // protocol 6 (HT6P20B)
  { 150, {  2, 62 }, {  1,  6 }, {  6,  1 }, false },    // protocol 7 (HS2303-PT)
  { 200, {  3, 130}, {  7, 16 }, {  3,  16}, false },    // protocol 8 Conrad RS-200 RX
  { 200, { 130, 7 }, { 16,  7 }, { 16,  3 }, true  },    // protocol 9 Conrad RS-200 TX
  { 365, { 18,  1 }, {  3,  1 }, {  1,  3 }, true  },    // protocol 10 (1ByOne Doorbell)
  { 270, { 36,  1 }, {  1,  2 }, {  2,  1 }, true  },    // protocol 11 (HT12E)
  { 320, { 36,  1 }, {  1,  2 }, {  2,  1 }, true  },    // protocol 12 (SM5212)
  { 300, {  1, 10 }, {  1,  3 }, {  3,  1 }, false }     // protocol 13 (Controller 2: 10T sync, EV1527 1:3 data)
};

static const unsigned int numProto = sizeof(proto) / sizeof(proto[0]);

int EspRCSwitch::nReceiveTolerance = 60;
unsigned int EspRCSwitch::nSeparationLimit = 1800; // 1800us safely slices Controller 2 (2.5-3.5ms) & Controller 1 (8ms) sync gaps
volatile uint64_t EspRCSwitch::nReceivedValue = 0;
volatile unsigned int EspRCSwitch::nReceivedBitlength = 0;
volatile unsigned int EspRCSwitch::nReceivedDelay = 0;
volatile unsigned int EspRCSwitch::nReceivedProtocol = 0;
volatile unsigned int EspRCSwitch::timings[ESP_RCSWITCH_MAX_CHANGES];
volatile bool EspRCSwitch::bRawAvailable = false;
volatile unsigned int EspRCSwitch::nRawChangeCount = 0;
volatile unsigned int EspRCSwitch::rawTimings[ESP_RCSWITCH_MAX_CHANGES];
volatile bool EspRCSwitch::bReceiving = false;
volatile unsigned long EspRCSwitch::lastEdgeMicros = 0;

EspRCSwitch::EspRCSwitch() {
  this->nTransmitterPin = -1;
  this->nReceiverInterrupt = -1;
  this->nRepeatTransmit = 10;
  this->protocol = proto[0];
}

void EspRCSwitch::setPulseLength(int nPulseLength) {
  this->protocol.pulseLength = nPulseLength;
}

void EspRCSwitch::setRepeatTransmit(int nRepeatTransmit) {
  this->nRepeatTransmit = nRepeatTransmit;
}

void EspRCSwitch::setReceiveTolerance(int nPercent) {
  EspRCSwitch::nReceiveTolerance = nPercent;
}

void EspRCSwitch::setSeparationLimit(unsigned int limitMicros) {
  EspRCSwitch::nSeparationLimit = limitMicros;
}

void EspRCSwitch::enableTransmit(int nTransmitterPin) {
  this->nTransmitterPin = nTransmitterPin;
  pinMode(this->nTransmitterPin, OUTPUT);
}

void EspRCSwitch::disableTransmit() {
  this->nTransmitterPin = -1;
}

void EspRCSwitch::enableReceive(int interruptPin) {
  this->nReceiverInterrupt = interruptPin;
  this->enableReceive();
}

void EspRCSwitch::enableReceive() {
  if (this->nReceiverInterrupt != -1) {
    EspRCSwitch::nReceivedValue = 0;
    EspRCSwitch::nReceivedBitlength = 0;
    EspRCSwitch::bReceiving = false;
    attachInterrupt(digitalPinToInterrupt(this->nReceiverInterrupt), EspRCSwitch::handleInterrupt, CHANGE);
  }
}

void EspRCSwitch::disableReceive() {
  if (this->nReceiverInterrupt != -1) {
    detachInterrupt(digitalPinToInterrupt(this->nReceiverInterrupt));
    EspRCSwitch::bReceiving = false;
  }
}

bool EspRCSwitch::available() {
  return EspRCSwitch::nReceivedValue != 0;
}

void EspRCSwitch::resetAvailable() {
  EspRCSwitch::nReceivedValue = 0;
}

bool EspRCSwitch::rawAvailable() {
  return EspRCSwitch::bRawAvailable;
}

void EspRCSwitch::resetRawAvailable() {
  EspRCSwitch::bRawAvailable = false;
  EspRCSwitch::nRawChangeCount = 0;
}

bool EspRCSwitch::isReceiving() {
  if (!bReceiving) return false;
  if ((micros() - lastEdgeMicros) > 15000) {
    bReceiving = false;
    return false;
  }
  return true;
}

uint64_t EspRCSwitch::getReceivedValue() {
  return EspRCSwitch::nReceivedValue;
}

unsigned int EspRCSwitch::getReceivedBitlength() {
  return EspRCSwitch::nReceivedBitlength;
}

unsigned int EspRCSwitch::getReceivedDelay() {
  return EspRCSwitch::nReceivedDelay;
}

unsigned int EspRCSwitch::getReceivedProtocol() {
  return EspRCSwitch::nReceivedProtocol;
}

unsigned int* EspRCSwitch::getReceivedRawdata() {
  return (unsigned int*)EspRCSwitch::timings;
}

unsigned int EspRCSwitch::getRawChangeCount() {
  return EspRCSwitch::nRawChangeCount;
}

unsigned int* EspRCSwitch::getRawTimings() {
  return (unsigned int*)EspRCSwitch::rawTimings;
}

void EspRCSwitch::transmit(HighLow pulses) {
  uint8_t firstHigh = protocol.invertedSignal ? LOW : HIGH;
  uint8_t secondLow = protocol.invertedSignal ? HIGH : LOW;

  digitalWrite(this->nTransmitterPin, firstHigh);
  delayMicroseconds(protocol.pulseLength * pulses.high);
  digitalWrite(this->nTransmitterPin, secondLow);
  delayMicroseconds(protocol.pulseLength * pulses.low);
}

void EspRCSwitch::sendTriState(const char* sCodeWord) {
  unsigned long code = 0;
  unsigned int length = 0;
  for (const char* p = sCodeWord; *p; p++) {
    code <<= 2L;
    switch (*p) {
      case '0':
        break;
      case 'F':
        code |= 1L;
        break;
      case '1':
        code |= 3L;
        break;
    }
    length += 2;
  }
  this->send(code, length);
}

void EspRCSwitch::send(const char* sCodeWord) {
  unsigned long code = 0;
  unsigned int length = 0;
  for (const char* p = sCodeWord; *p; p++) {
    code <<= 1L;
    if (*p != '0') code |= 1L;
    length++;
  }
  this->send(code, length);
}

void EspRCSwitch::send(uint64_t code, unsigned int length) {
  if (this->nTransmitterPin == -1) return;

  int nReceiverInterrupt_backup = nReceiverInterrupt;
  if (nReceiverInterrupt_backup != -1) {
    this->disableReceive();
  }

  for (int nRepeat = 0; nRepeat < nRepeatTransmit; nRepeat++) {
    for (int i = length - 1; i >= 0; i--) {
      if (code & (1ULL << i)) {
        this->transmit(protocol.one);
      } else {
        this->transmit(protocol.zero);
      }
    }
    this->transmit(protocol.syncFactor);
  }

  digitalWrite(this->nTransmitterPin, LOW);

  if (nReceiverInterrupt_backup != -1) {
    this->enableReceive(nReceiverInterrupt_backup);
  }
}

bool IRAM_ATTR EspRCSwitch::receiveProtocol(const int p, unsigned int changeCount) {
  const RCSwitchProtocol &pro = proto[p - 1];

  // Discard frames that are obviously too short (need at least 12 bits = 24 data edges)
  if (changeCount < 25) return false;

  // Evaluate both Offset 1 (direct bitstream) and Offset 2 (preceded by lead-in / preamble edge)
  // This phase alignment is essential because physical remotes can start with or without a sync high edge
  for (unsigned int offset = 1; offset <= 2; offset++) {
    if (changeCount <= offset + 23) continue;

    unsigned int numBits = (changeCount - offset) / 2;

    // Strict whole-frame filtering: only allow standard complete commercial bit counts
    // Completely eliminates partial fragments (11b, 13b, 14b, 15b, 29b, etc.)
    if (numBits != 12 && numBits != 16 && numBits != 20 && numBits != 24 && numBits != 28 && numBits != 32 && numBits != 36 && numBits != 40 && numBits != 48 && numBits != 64) {
      continue;
    }

    unsigned int delay = 0;

    // For EV1527 1:3 data pulses: calculate base clock directly from data pulse widths (1T + 3T = 4T)
    // Guarantees sub-microsecond precision for Controller 1 (~260us) and Controller 2 (~300us)
    if (pro.zero.high == 1 && pro.zero.low == 3 && pro.one.high == 3 && pro.one.low == 1) {
      unsigned long sum4 = 0;
      unsigned int sampleCount = 0;
      for (unsigned int i = offset; i + 1 < changeCount && sampleCount < 16; i += 2) {
        sum4 += timings[i] + timings[i + 1];
        sampleCount++;
      }
      if (sampleCount > 0) {
        delay = sum4 / (sampleCount * 4);
      }
    } else {
      const unsigned int syncLen = (pro.syncFactor.low > pro.syncFactor.high)
                                     ? pro.syncFactor.low
                                     : pro.syncFactor.high;
      if (syncLen > 0) {
        delay = timings[0] / syncLen;
      }
    }

    if (delay < 60 || delay > 1200) continue;

    const unsigned int delayTolerance = delay * nReceiveTolerance / 100;

    uint64_t code = 0;
    bool matched = true;
    for (unsigned int i = offset; i < offset + numBits * 2; i += 2) {
      code <<= 1;
      if (abs((long)timings[i] - (long)(delay * pro.zero.high)) <= (long)delayTolerance &&
          abs((long)timings[i + 1] - (long)(delay * pro.zero.low)) <= (long)delayTolerance) {
        // zero
      } else if (abs((long)timings[i] - (long)(delay * pro.one.high)) <= (long)delayTolerance &&
                 abs((long)timings[i + 1] - (long)(delay * pro.one.low)) <= (long)delayTolerance) {
        // one
        code |= 1;
      } else {
        matched = false;
        break;
      }
    }

    if (matched) {
      nReceivedValue = code;
      nReceivedBitlength = numBits;
      nReceivedDelay = delay;
      nReceivedProtocol = p;
      return true;
    }
  }

  return false;
}

void IRAM_ATTR EspRCSwitch::handleInterrupt() {
  static unsigned int changeCount = 0;
  static unsigned long lastTime = 0;

  const unsigned long time = micros();
  const unsigned int duration = time - lastTime;
  lastEdgeMicros = time;

  if (duration > nSeparationLimit) {
    bReceiving = false;
    // Gap between packets detected: decode completed frame immediately (0ms latency)
    if (changeCount >= 16) {
      bool decoded = false;
      for (unsigned int i = 1; i <= numProto; i++) {
        if (receiveProtocol(i, changeCount)) {
          decoded = true;
          break;
        }
      }
      // If frame was not decoded by any protocol, save raw burst for diagnostics!
      if (!decoded && !bRawAvailable) {
        nRawChangeCount = changeCount;
        for (unsigned int i = 0; i < changeCount && i < ESP_RCSWITCH_MAX_CHANGES; i++) {
          rawTimings[i] = timings[i];
        }
        bRawAvailable = true;
      }
    }
    changeCount = 0;
  } else {
    if (changeCount >= 4) {
      bReceiving = true;
    }
  }

  if (changeCount >= ESP_RCSWITCH_MAX_CHANGES) {
    changeCount = 0;
  }

  timings[changeCount++] = duration;
  lastTime = time;
}
