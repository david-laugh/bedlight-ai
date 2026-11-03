// BedLight AI firmware configuration TEMPLATE.
//
//   1. Copy this file to config.h in the same folder (bedlight/config.h).
//   2. Fill in your real values. config.h is git-ignored — never commit it.
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

// ---------- Recording ----------
#define RECORD_DURATION_MS 2000   // 2 s clip
#define PRE_RECORD_DELAY_MS 1000  // pause after the serial command before capture starts
#define AUDIO_GAIN 4              // integer gain applied after DC removal (saturating). 1 = raw
#define AUDIO_REMOVE_DC 1         // 1 = subtract mean (PDM DC offset) before gain

// ---------- Network timeouts ----------
#define WIFI_CONNECT_TIMEOUT_MS 15000
#define HTTP_TIMEOUT_MS 10000
#define UPLOAD_RETRIES 2          // additional attempts after the first failure
