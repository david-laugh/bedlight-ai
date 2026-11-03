// BedLight AI — configuration defaults.
// Includes the user's config.h, then fills in every setting that is missing so that an
// older config.h (DATASET_RECORDING era) keeps compiling. Override any value in config.h.
#pragma once

#if __has_include("config.h")
#include "config.h"
#else
#error "config.h not found: copy bedlight/config.example.h to bedlight/config.h and set Wi-Fi / server values."
#endif

// ---------- Dataset recording (existing) ----------
#ifndef RECORD_DURATION_MS
#define RECORD_DURATION_MS 2000
#endif
#ifndef PRE_RECORD_DELAY_MS
#define PRE_RECORD_DELAY_MS 1000
#endif
#ifndef AUDIO_GAIN
#define AUDIO_GAIN 4
#endif
#ifndef AUDIO_REMOVE_DC
#define AUDIO_REMOVE_DC 1
#endif
#ifndef WIFI_CONNECT_TIMEOUT_MS
#define WIFI_CONNECT_TIMEOUT_MS 15000
#endif
#ifndef HTTP_TIMEOUT_MS
#define HTTP_TIMEOUT_MS 10000
#endif
#ifndef UPLOAD_RETRIES
#define UPLOAD_RETRIES 2
#endif
#ifndef VOICE_UPLOAD_TOKEN
#define VOICE_UPLOAD_TOKEN ""
#endif

// ---------- Vision (Edge Impulse FOMO) ----------
// 1 = link the Edge Impulse Arduino library "Fall_detection_system_for_workers_in_industry_inferencing".
// 0 = build without vision (dataset recording only). A 0 build is NOT a vision integration.
#ifndef BEDLIGHT_VISION_ENABLED
#define BEDLIGHT_VISION_ENABLED 1
#endif
// Detection threshold for Fall / Standing boxes. FOMO itself drops boxes below
// EI_CLASSIFIER_OBJECT_DETECTION_THRESHOLD (0.5 in this model), so values < 0.5 behave as 0.5.
#ifndef VISION_THRESHOLD
#define VISION_THRESHOLD 0.75f
#endif
#ifndef VISION_MODEL_NAME
#define VISION_MODEL_NAME "fall-detection-fomo"
#endif
#ifndef VISION_MIN_FRAME_INTERVAL_MS
#define VISION_MIN_FRAME_INTERVAL_MS 0  // 0 = run as fast as possible
#endif

// ---------- Fall-triggered listening ----------
#ifndef LISTEN_HOLD_MS
#define LISTEN_HOLD_MS 5000  // keep listening this long after the LAST Fall detection
#endif
#ifndef LISTENING_EXTENDED_EVENT_INTERVAL_MS
#define LISTENING_EXTENDED_EVENT_INTERVAL_MS 1000  // MQTT rate limit for LISTENING_EXTENDED
#endif

// ---------- RUNTIME_AUDIO ----------
#ifndef RUNTIME_AUDIO_ENABLED
#define RUNTIME_AUDIO_ENABLED 1
#endif
#ifndef AUDIO_CHUNK_MS
#define AUDIO_CHUNK_MS 1000  // 1 s = 32000 B PCM per chunk
#endif
#ifndef AUDIO_CHUNK_POOL
#define AUDIO_CHUNK_POOL 4   // PSRAM chunk buffers = seconds of network slack
#endif
#ifndef RUNTIME_AUDIO_GAIN
#define RUNTIME_AUDIO_GAIN AUDIO_GAIN
#endif

// ---------- MQTT ----------
#ifndef MQTT_HOST
#define MQTT_HOST VOICE_SERVER_HOST  // same PC by default (LAN IP, not localhost)
#endif
#ifndef MQTT_PORT
#define MQTT_PORT 1883
#endif
#ifndef MQTT_USERNAME
#define MQTT_USERNAME ""
#endif
#ifndef MQTT_PASSWORD
#define MQTT_PASSWORD ""
#endif
#ifndef ROOM_ID
#define ROOM_ID "bedroom-01"
#endif
#ifndef STATUS_INTERVAL_MS
#define STATUS_INTERVAL_MS 5000
#endif
#ifndef NTP_SERVER
#define NTP_SERVER "pool.ntp.org"
#endif

#define FIRMWARE_VERSION "0.3.0-runtime"
