// BedLight AI firmware configuration TEMPLATE.
//
//   1. Copy this file to config.h in the same folder (bedlight/config.h).
//   2. Fill in your real values. config.h is git-ignored — never commit it.
//   Settings not present in config.h fall back to config_defaults.h, so an older config.h still works.
#pragma once

// ---------- Wi-Fi (2.4 GHz only: ESP32-S3 does not support 5 GHz) ----------
#define WIFI_SSID "your-wifi-ssid"
#define WIFI_PASSWORD "your-wifi-password"

// ---------- Voice Server ----------
// LAN IP address of the PC running Docker (e.g. "192.168.0.23").
// Do NOT use "localhost", "127.0.0.1" or Docker service names ("voice-service"):
// the ESP32 is a separate device on the network.
#define VOICE_SERVER_HOST "192.168.0.10"
#define VOICE_SERVER_PORT 8001
// Must equal VOICE_UPLOAD_TOKEN in the server's .env. Empty string = no token header sent.
#define VOICE_UPLOAD_TOKEN ""

// ---------- Device ----------
#define DEVICE_ID "bedlight-01"

// ---------- Dataset recording (serial o/u/n) ----------
#define RECORD_DURATION_MS 2000   // 2 s clip
#define PRE_RECORD_DELAY_MS 1000  // pause after the serial command before capture starts
#define AUDIO_GAIN 4              // integer gain applied after DC removal (saturating). 1 = raw
#define AUDIO_REMOVE_DC 1         // 1 = subtract mean (PDM DC offset) before gain

// ---------- Network timeouts ----------
#define WIFI_CONNECT_TIMEOUT_MS 15000
#define HTTP_TIMEOUT_MS 10000
#define UPLOAD_RETRIES 2          // additional attempts after the first failure

// ---------- Vision: Edge Impulse FOMO (Fall / Standing) ----------
// Requires the Arduino library "Fall_detection_system_for_workers_in_industry_inferencing".
#define BEDLIGHT_VISION_ENABLED 1
#define VISION_THRESHOLD 0.75f    // Fall/Standing box confidence threshold (>= 0.5, see config_defaults.h)

// ---------- Fall-triggered RUNTIME_AUDIO ----------
#define LISTEN_HOLD_MS 5000       // listen until 5 s after the LAST Fall detection
#define RUNTIME_AUDIO_ENABLED 1
#define AUDIO_CHUNK_MS 1000       // 1 s chunks (32000 B PCM) -> POST /api/v1/audio/chunks

// ---------- MQTT (Mosquitto on the same PC) ----------
// The broker must be reachable from the LAN: set MQTT_BIND_ADDR=0.0.0.0 in the server .env.
#define MQTT_HOST VOICE_SERVER_HOST
#define MQTT_PORT 1883
#define ROOM_ID "bedroom-01"

// ---------- Virtual light gate ----------
// Follow the simulator wall switch: light OFF -> stop vision/listening (IDLE), ON -> watch for Fall.
#define LIGHT_GATE_ENABLED 1
