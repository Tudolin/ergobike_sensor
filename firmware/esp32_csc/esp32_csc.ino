/*
 * BLE cadence sensor (Cycling Speed and Cadence service, 0x1816) for an exercise bike.
 *
 * Wiring: one wire of the "SENSOR" plug to GND, the other to REED_PIN (internal pull-up).
 * Long or noisy cable: add an external 10k pull-up to 3V3 and 100 nF from the pin to GND.
 *
 * Shows up as a cadence sensor in Zwift, Kinomap, Wahoo, nRF Connect and friends.
 * Core: arduino-esp32 2.x or 3.x with the bundled BLE library, no extra dependencies.
 */

#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLE2902.h>

// ---- config
#define DEVICE_NAME   "ErgoBike"
#define REED_PIN      4          // avoid strapping pins (0, 2, 5, 12, 15 on the classic ESP32)
#define DEBOUNCE_MS   10         // reed must be stable this long to change state
#define MIN_REV_MS    200        // ignore closures closer than this (300 rpm max)
#define NOTIFY_MS     1000       // the CSC spec asks for about one notification per second

// 16-bit SIG UUIDs
static BLEUUID SVC_CSC((uint16_t)0x1816);
static BLEUUID CHR_MEASUREMENT((uint16_t)0x2A5B);
static BLEUUID CHR_FEATURE((uint16_t)0x2A5C);
static BLEUUID CHR_LOCATION((uint16_t)0x2A5D);

static BLECharacteristic *measurement;
static volatile bool connected = false;

// sensor state; polled at ~1 kHz from loop(), which debounces a reed easily without an ISR
static bool reedClosed = false;
static bool lastRaw = false;
static uint32_t rawChangedAt = 0;
static uint32_t lastRevMs = 0;
static uint16_t crankRevs = 0;       // cumulative, wraps at 65535 (allowed by the spec)
static uint16_t lastCrankEvent = 0;  // in 1/1024 s, also wraps

class ServerCb : public BLEServerCallbacks {
  void onConnect(BLEServer *) override { connected = true; }
  void onDisconnect(BLEServer *) override {
    connected = false;
    BLEDevice::startAdvertising();   // Bluedroid doesn't restart advertising by itself
  }
};

static uint16_t nowIn1024ths() {
  return (uint16_t)((uint64_t)esp_timer_get_time() * 1024 / 1000000);
}

static void notifyMeasurement() {
  // flags bit 1 = Crank Revolution Data Present
  uint8_t p[5] = {
    0x02,
    (uint8_t)(crankRevs & 0xFF), (uint8_t)(crankRevs >> 8),
    (uint8_t)(lastCrankEvent & 0xFF), (uint8_t)(lastCrankEvent >> 8),
  };
  measurement->setValue(p, sizeof(p));
  measurement->notify();
}

static void pollReed() {
  bool raw = digitalRead(REED_PIN) == LOW;   // LOW = reed closed (pulled to GND)
  uint32_t now = millis();
  if (raw != lastRaw) {
    lastRaw = raw;
    rawChangedAt = now;
    return;
  }
  if (raw == reedClosed || now - rawChangedAt < DEBOUNCE_MS) return;

  reedClosed = raw;
  if (reedClosed && now - lastRevMs >= MIN_REV_MS) {
    // event time is when the contact closed, not when the debounce window ended
    lastRevMs = rawChangedAt;
    crankRevs++;
    lastCrankEvent = nowIn1024ths() - (uint16_t)((uint32_t)(now - rawChangedAt) * 1024 / 1000);
    Serial.printf("rev %u\n", crankRevs);
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(REED_PIN, INPUT_PULLUP);

  BLEDevice::init(DEVICE_NAME);
  BLEServer *server = BLEDevice::createServer();
  server->setCallbacks(new ServerCb());
  BLEService *svc = server->createService(SVC_CSC);

  measurement = svc->createCharacteristic(CHR_MEASUREMENT, BLECharacteristic::PROPERTY_NOTIFY);
  measurement->addDescriptor(new BLE2902());

  uint8_t feature[2] = {0x02, 0x00};          // bit 1 = Crank Revolution Data Supported
  svc->createCharacteristic(CHR_FEATURE, BLECharacteristic::PROPERTY_READ)
     ->setValue(feature, sizeof(feature));

  uint8_t location = 6;                       // 6 = Right Crank (0 = Other)
  svc->createCharacteristic(CHR_LOCATION, BLECharacteristic::PROPERTY_READ)
     ->setValue(&location, 1);

  svc->start();

  BLEAdvertising *adv = BLEDevice::getAdvertising();
  adv->addServiceUUID(SVC_CSC);
  adv->setAppearance(0x0483);                 // Cycling: Cadence Sensor
  adv->setScanResponse(true);
  BLEDevice::startAdvertising();
  Serial.println("Advertising CSC as \"" DEVICE_NAME "\"");
}

void loop() {
  static uint32_t lastNotify = 0;
  pollReed();
  // repeating the same (revs, time) pair is how apps know cadence dropped to 0
  if (connected && millis() - lastNotify >= NOTIFY_MS) {
    lastNotify = millis();
    notifyMeasurement();
  }
  delay(1);
}
