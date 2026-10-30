#ifndef _ESP_RCSWITCH_H_
#define _ESP_RCSWITCH_H_

#include <Arduino.h>

#define ESP_RCSWITCH_MAX_CHANGES 136

struct HighLow {
  uint8_t high;
  uint8_t low;
};

struct RCSwitchProtocol {
  uint16_t pulseLength;
  HighLow syncFactor;
  HighLow zero;
  HighLow one;
  bool invertedSignal;
};

class EspRCSwitch {
public:
  EspRCSwitch();

  void enableReceive(int interruptPin);
  void enableReceive();
  void disableReceive();
  bool available();
  void resetAvailable();
  bool rawAvailable();
  void resetRawAvailable();
  bool isReceiving();

  uint64_t getReceivedValue();
  unsigned int getReceivedBitlength();
  unsigned int getReceivedDelay();
  unsigned int getReceivedProtocol();
  unsigned int* getReceivedRawdata();
  unsigned int getRawChangeCount();
  unsigned int* getRawTimings();

  void enableTransmit(int nTransmitterPin);
  void disableTransmit();
  void setPulseLength(int nPulseLength);
  void setRepeatTransmit(int nRepeatTransmit);
  void setReceiveTolerance(int nPercent);
  void setSeparationLimit(unsigned int limitMicros);

  void send(uint64_t code, unsigned int length);
  void send(const char* sCodeWord);
  void sendTriState(const char* sCodeWord);

  static void IRAM_ATTR handleInterrupt();

private:
  static bool IRAM_ATTR receiveProtocol(const int p, unsigned int changeCount);
  void transmit(HighLow pulses);

  int nReceiverInterrupt;
  int nTransmitterPin;
  int nRepeatTransmit;
  RCSwitchProtocol protocol;

  static int nReceiveTolerance;
  static unsigned int nSeparationLimit;
  static volatile uint64_t nReceivedValue;
  static volatile unsigned int nReceivedBitlength;
  static volatile unsigned int nReceivedDelay;
  static volatile unsigned int nReceivedProtocol;
  static volatile unsigned int timings[ESP_RCSWITCH_MAX_CHANGES];
  static volatile bool bRawAvailable;
  static volatile unsigned int nRawChangeCount;
  static volatile unsigned int rawTimings[ESP_RCSWITCH_MAX_CHANGES];
  static volatile bool bReceiving;
  static volatile unsigned long lastEdgeMicros;
};

#endif // _ESP_RCSWITCH_H_
