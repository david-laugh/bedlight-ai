// BedLight AI — MQTT event publisher (ESP-IDF esp-mqtt bundled with Arduino Core 2.0.x; thread-safe).
// Named mqtt_events.* (not mqtt_client.*) to avoid shadowing esp-mqtt's own "mqtt_client.h".
#pragma once

#include <Arduino.h>

namespace bedlight {
namespace mqtt {

bool begin();            // call after Wi-Fi is up; esp-mqtt reconnects automatically
bool connected();
uint32_t dropped();      // events dropped while disconnected
uint32_t published();

// Topic: bedlight/v1/devices/{DEVICE_ID}/events (QoS 1)
bool publishEvent(const char* json);
// Topic: bedlight/v1/devices/{DEVICE_ID}/status (QoS 1, retained)
bool publishStatus(const char* json);

// Builds a runtime event (contracts/v1/device-runtime-event.schema.json) into `out`.
// confidence / inference_ms < 0 => null. trace_id/session_id NULL => new uuid / null.
// data_json must be a JSON object literal ("{}" when empty).
size_t buildEvent(char* out, size_t n, const char* event_type, const char* trace_id, const char* session_id,
                  bool with_model, float confidence, float inference_ms, const char* data_json);

int64_t epochMs();       // -1 until SNTP synced

// Virtual light state (retained bedlight/v1/lights/{ROOM_ID}/state, published for the simulator wall switch).
// -1 = unknown (no message yet), 0 = OFF, 1 = ON. Thread-safe read.
int lightState();
uint32_t lightStateUpdates();

}  // namespace mqtt
}  // namespace bedlight
