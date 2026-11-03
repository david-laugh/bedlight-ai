// BedLight AI — XIAO ESP32S3 Sense firmware 0.4.0 (light gate)
//
// Light gate (0.4): follows the virtual light (retained MQTT bedlight/v1/lights/{ROOM_ID}/state).
//   light OFF -> IDLE: no camera inference, no listening (an active session stops immediately, reason light_off)
//   light ON / unknown -> RUNTIME below
//
// Modes
//   RUNTIME (default)   : camera + Edge Impulse FOMO (Fall/Standing). Fall >= threshold -> LISTENING:
//                         PDM mic streamed as 1 s WAV chunks to voice-service (POST /api/v1/audio/chunks)
//                         until LISTEN_HOLD_MS after the LAST Fall detection. MQTT state events.
//   DATASET_RECORDING   : serial o/u/n -> 2 s WAV -> POST /api/v1/recordings (unchanged behaviour)
//
// FreeRTOS tasks
//   visionTask  (core 1) camera capture -> FOMO -> state machine -> MQTT events
//   audioTask   (core 0) I2S PDM read -> DC block/gain -> PSRAM chunk pool -> send queue (never does network I/O)
//   netTask     (core 0) Wi-Fi/MQTT upkeep, chunk HTTP upload, status heartbeat
//   loop()      (core 1) serial commands, 20 ms state-machine tick (hold-window expiry)
//
// Toolchain: ESP32 Arduino Core 2.0.x, Board XIAO_ESP32S3, PSRAM OPI, USB CDC On Boot Enabled.
// Library  : Fall_detection_system_for_workers_in_industry_inferencing (Edge Impulse Arduino export)

#include <Arduino.h>
#include <WiFi.h>
#include <esp_heap_caps.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>
#include <freertos/task.h>

#include "audio_capture.h"
#include "audio_dsp.h"
#include "audio_upload.h"
#include "camera_handler.h"
#include "config_defaults.h"
#include "mqtt_events.h"
#include "state_manager.h"
#include "vision_inference.h"
#include "wav_header.h"

using namespace bedlight;

// ============================================================================
// Shared state
// ============================================================================
static ListeningStateMachine g_sm(LISTEN_HOLD_MS, VISION_THRESHOLD);
static RateLimiter g_extendedLimiter(LISTENING_EXTENDED_EVENT_INTERVAL_MS);
static SemaphoreHandle_t g_mutex = NULL;  // guards g_sm, g_session, g_stats

struct Session {
  bool active;
  char id[37];
  uint32_t start_ms;
  uint32_t chunks_queued, chunks_sent, chunks_failed, chunks_dropped, overflows;
};
static Session g_session = {};

struct Stats {
  uint32_t frames, vision_errors, camera_failures;
  uint64_t inference_ms_sum;
  uint32_t last_total_ms, last_capture_ms;
  float last_fall_conf, last_standing_conf;
  uint32_t window_frames, window_start_ms;
  float fps;
  uint32_t audio_sent_total, audio_failed_total, audio_dropped_total;
  uint32_t last_upload_ms;
  int last_upload_status;
};
static Stats g_stats = {};

static volatile bool g_runtimeEnabled = true;
static volatile bool g_printFrames = false;
static bool g_cameraOk = false, g_visionOk = false, g_micOk = false;

// ---------------- audio chunk pool (PSRAM) ----------------
static const uint32_t kChunkPcmBytes = audio::kBytesPerSecond * AUDIO_CHUNK_MS / 1000;
static const uint32_t kChunkWavBytes = kWavHeaderSize + kChunkPcmBytes;
static uint8_t* g_pool[AUDIO_CHUNK_POOL];
static QueueHandle_t g_freeQ = NULL;  // uint8_t slot index
static QueueHandle_t g_sendQ = NULL;  // ChunkMsg

struct ChunkMsg {
  uint8_t slot;
  char session[37];
  uint32_t sequence, sample_start, samples, capture_uptime_ms, overflows, dropped_before;
  int64_t capture_epoch_ms;
  bool is_final;
};

static const char* kStateName[] = {"WATCHING", "LISTENING"};

// NOTE: keep all function definitions below the type definitions above — the Arduino .ino preprocessor
// inserts auto-generated prototypes right before the first function definition.

// true when the virtual light is known to be OFF (gate closed). Unknown (no retained state yet) = open.
static bool lightGateClosed() { return LIGHT_GATE_ENABLED && mqtt::lightState() == 0; }

static const char* gateName(bool listening) {
  if (!g_runtimeEnabled) return "paused";
  if (lightGateClosed()) return "idle_light_off";
  return listening ? "listening" : "watching";
}

// ============================================================================
// Helpers
// ============================================================================
static void lockState() { xSemaphoreTake(g_mutex, portMAX_DELAY); }
static void unlockState() { xSemaphoreGive(g_mutex); }

static SemaphoreHandle_t g_publishLock = NULL;  // guards the shared JSON buffer

static void publish(const char* type, const char* trace, const char* session, bool model, float conf, float inf,
                    const char* data) {
  static char buf[1024];
  xSemaphoreTake(g_publishLock, portMAX_DELAY);
  mqtt::buildEvent(buf, sizeof(buf), type, trace, session, model, conf, inf, data);
  mqtt::publishEvent(buf);
  xSemaphoreGive(g_publishLock);
}

static void systemError(const char* component, const char* code, const char* msg, bool recoverable) {
  static uint32_t last = 0;
  static bool armed = false;
  uint32_t now = millis();
  Serial.printf("[ERROR] %s %s: %s\n", component, code, msg);
  if (armed && now - last < 5000) return;  // rate limit
  armed = true;
  last = now;
  char data[256];
  snprintf(data, sizeof(data), "{\"component\":\"%s\",\"code\":\"%s\",\"message\":\"%s\",\"recoverable\":%s}", component,
           code, msg, recoverable ? "true" : "false");
  publish("SYSTEM_ERROR", NULL, NULL, false, -1, -1, data);
}

static bool ensureWifiBlocking() {
  if (WiFi.status() == WL_CONNECTED) return true;
  Serial.printf("Connecting to Wi-Fi \"%s\"", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  const uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_CONNECT_TIMEOUT_MS) {
    delay(250);
    Serial.print('.');
  }
  Serial.println();
  if (WiFi.status() != WL_CONNECTED) {
    Serial.printf("[ERROR] Wi-Fi connection failed (status=%d)\n", (int)WiFi.status());
    return false;
  }
  Serial.printf("Wi-Fi connected: IP=%s RSSI=%d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  return true;
}

static bool serverHostLooksWrong(const char* h) {
  return strcmp(h, "localhost") == 0 || strncmp(h, "127.", 4) == 0 || strcmp(h, "voice-service") == 0 ||
         strcmp(h, "mqtt-broker") == 0 || strcmp(h, "0.0.0.0") == 0;
}

// ============================================================================
// State transitions -> session bookkeeping + MQTT
// ============================================================================
static void handleTransition(Transition tr, uint32_t now, float conf, float inf_ms) {
  if (tr == Transition::NONE) return;
  char data[384];
  if (tr == Transition::STARTED) {
    String sid = http::makeUuid();
    uint32_t until, remaining;
    lockState();
    memset(&g_session, 0, sizeof(g_session));
    strncpy(g_session.id, sid.c_str(), sizeof(g_session.id) - 1);
    g_session.start_ms = now;
    g_session.active = RUNTIME_AUDIO_ENABLED;
    until = g_sm.listenUntilMs();
    remaining = g_sm.remainingMs(now);
    unlockState();
    g_extendedLimiter.reset();
    snprintf(data, sizeof(data),
             "{\"hold_ms\":%u,\"remaining_ms\":%u,\"listen_until_uptime_ms\":%u,\"room_id\":\"%s\",\"audio_enabled\":%s,"
             "\"threshold\":%.2f}",
             (unsigned)LISTEN_HOLD_MS, (unsigned)remaining, (unsigned)until, ROOM_ID,
             RUNTIME_AUDIO_ENABLED ? "true" : "false", (double)VISION_THRESHOLD);
    publish("LISTENING_STARTED", sid.c_str(), sid.c_str(), true, conf, inf_ms, data);
    Serial.printf("[STATE] WATCHING -> LISTENING  session=%s fall=%.2f\n", sid.c_str(), (double)conf);
  } else if (tr == Transition::EXTENDED) {
    if (!g_extendedLimiter.allow(now)) return;
    char sid[37];
    uint32_t ext, until, remaining;
    lockState();
    strncpy(sid, g_session.id, sizeof(sid));
    ext = g_sm.extensions();
    until = g_sm.listenUntilMs();
    remaining = g_sm.remainingMs(now);
    unlockState();
    snprintf(data, sizeof(data),
             "{\"hold_ms\":%u,\"remaining_ms\":%u,\"listen_until_uptime_ms\":%u,\"extensions\":%u}",
             (unsigned)LISTEN_HOLD_MS, (unsigned)remaining, (unsigned)until, (unsigned)ext);
    publish("LISTENING_EXTENDED", sid, sid, true, conf, inf_ms, data);
  } else if (tr == Transition::STOPPED) {
    Session s;
    uint32_t ext, dur;
    const char* reason;
    lockState();
    g_session.active = false;  // audioTask flushes the final chunk and stops I2S
    s = g_session;
    ext = g_sm.extensions();
    dur = g_sm.lastSessionDurationMs();
    reason = g_sm.lastStopReason() == StopReason::FORCED ? "light_off" : "hold_expired";
    unlockState();
    snprintf(data, sizeof(data),
             "{\"reason\":\"%s\",\"duration_ms\":%u,\"extensions\":%u,\"hold_ms\":%u,\"chunks_queued\":%u,"
             "\"chunks_sent\":%u,\"chunks_failed\":%u,\"chunks_dropped\":%u,\"dma_overflows\":%u}",
             reason, (unsigned)dur, (unsigned)ext, (unsigned)LISTEN_HOLD_MS, (unsigned)s.chunks_queued,
             (unsigned)s.chunks_sent, (unsigned)s.chunks_failed, (unsigned)s.chunks_dropped, (unsigned)s.overflows);
    publish("LISTENING_STOPPED", s.id, s.id, false, -1, -1, data);
    Serial.printf("[STATE] LISTENING -> WATCHING  session=%s duration=%u ms extensions=%u reason=%s\n", s.id,
                  (unsigned)dur, (unsigned)ext, reason);
  }
}

// ============================================================================
// Vision task
// ============================================================================
static void visionTask(void*) {
  uint8_t* img = (uint8_t*)heap_caps_malloc(camera::kOutWidth * camera::kOutHeight, MALLOC_CAP_8BIT);
  if (!img) {
    systemError("vision-model", "IMAGE_BUFFER_ALLOC_FAILED", "96x96 buffer", false);
    vTaskDelete(NULL);
  }
  bool idle = false;
  for (;;) {
    if (!g_runtimeEnabled || lightGateClosed()) {
      if (!idle) {
        idle = true;
        lockState();
        g_sm.resetLabel();  // the next detection after resuming emits a fresh edge
        unlockState();
      }
      vTaskDelay(pdMS_TO_TICKS(100));
      continue;
    }
    idle = false;
    const uint32_t t0 = millis();
    uint32_t capture_ms = 0;
    if (!camera::captureGray96(img, &capture_ms)) {
      lockState();
      g_stats.camera_failures++;
      unlockState();
      systemError("camera", "CAMERA_CAPTURE_FAILED", "esp_camera_fb_get returned no usable frame", true);
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }
    vision::Frame f;
    int ei_err = 0;
    if (!vision::run(img, VISION_THRESHOLD, &f, &ei_err)) {
      lockState();
      g_stats.vision_errors++;
      unlockState();
      char msg[64];
      snprintf(msg, sizeof(msg), "run_classifier error %d", ei_err);
      systemError("vision-model", "INFERENCE_FAILED", msg, true);
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }
    f.capture_ms = capture_ms;
    const uint32_t now = millis();

    lockState();
    Transition tr = g_sm.onVision(now, f.result);
    VisionLabel edge = g_sm.takeLabelEdge();
    bool listening = g_sm.listening();
    char sid[37];
    strncpy(sid, g_session.id, sizeof(sid));
    g_stats.frames++;
    g_stats.inference_ms_sum += f.total_ms;
    g_stats.last_total_ms = f.total_ms;
    g_stats.last_capture_ms = capture_ms;
    g_stats.last_fall_conf = f.result.fall_confidence;
    g_stats.last_standing_conf = f.result.standing_confidence;
    g_stats.window_frames++;
    if (now - g_stats.window_start_ms >= 5000) {
      g_stats.fps = 1000.0f * g_stats.window_frames / (float)(now - g_stats.window_start_ms);
      g_stats.window_frames = 0;
      g_stats.window_start_ms = now;
    }
    unlockState();

    if (edge != VisionLabel::NONE) {
      char data[384];
      const bool fall = edge == VisionLabel::FALL;
      snprintf(data, sizeof(data),
               "{\"label\":\"%s\",\"fall_confidence\":%.4f,\"standing_confidence\":%.4f,\"fall_count\":%u,"
               "\"standing_count\":%u,\"boxes\":%u,\"box\":{\"x\":%d,\"y\":%d,\"w\":%d,\"h\":%d},\"threshold\":%.2f,"
               "\"capture_ms\":%u,\"dsp_ms\":%d,\"classification_ms\":%d,\"listening\":%s}",
               fall ? "Fall" : "Standing", (double)f.result.fall_confidence, (double)f.result.standing_confidence,
               f.result.fall_count, f.result.standing_count, f.boxes, f.fall_x, f.fall_y, f.fall_w, f.fall_h,
               (double)VISION_THRESHOLD, (unsigned)capture_ms, (int)f.dsp_ms, (int)f.classification_ms,
               (listening || tr == Transition::STARTED) ? "true" : "false");
      publish(fall ? "VISION_FALL_DETECTED" : "VISION_STANDING_DETECTED", listening ? sid : NULL,
              listening ? sid : NULL, true, fall ? f.result.fall_confidence : f.result.standing_confidence,
              (float)f.total_ms, data);
    }
    handleTransition(tr, now, f.result.fall_confidence, (float)f.total_ms);

    if (g_printFrames) {
      Serial.printf("[vision] fall=%.2f standing=%.2f boxes=%u cap=%ums inf=%ums (dsp %d, nn %d) state=%s\n",
                    (double)f.result.fall_confidence, (double)f.result.standing_confidence, f.boxes,
                    (unsigned)capture_ms, (unsigned)f.total_ms, (int)f.dsp_ms, (int)f.classification_ms,
                    kStateName[(int)g_sm.state()]);
    }
    const uint32_t elapsed = millis() - t0;
    if (VISION_MIN_FRAME_INTERVAL_MS > elapsed) vTaskDelay(pdMS_TO_TICKS(VISION_MIN_FRAME_INTERVAL_MS - elapsed));
    else vTaskDelay(1);
  }
}

// ============================================================================
// Audio capture task (no network I/O here)
// ============================================================================
static void queueChunk(uint8_t slot, const char* sid, uint32_t seq, uint32_t sample_start, uint32_t bytes,
                       uint32_t t_uptime, int64_t t_epoch, bool final, uint32_t dropped_before) {
  ChunkMsg m = {};
  m.slot = slot;
  strncpy(m.session, sid, sizeof(m.session) - 1);
  m.sequence = seq;
  m.sample_start = sample_start;
  m.samples = bytes / 2;
  m.capture_uptime_ms = t_uptime;
  m.capture_epoch_ms = t_epoch;
  m.overflows = audio::takeOverflows();
  m.is_final = final;
  m.dropped_before = dropped_before;
  write_wav_header(g_pool[slot], audio::kSampleRate, audio::kChannels, audio::kBitsPerSample, bytes);
  lockState();
  if (strcmp(g_session.id, sid) == 0) {
    g_session.chunks_queued++;
    g_session.overflows += m.overflows;
  }
  unlockState();
  xQueueSend(g_sendQ, &m, portMAX_DELAY);  // capacity == pool size, never blocks in practice
}

static void audioTask(void*) {
  static uint8_t scratch[1024];
  StreamProcessor dsp;
  bool capturing = false;
  char sid[37] = {0};
  uint32_t seq = 0, sample_start = 0, filled = 0, chunk_t0 = 0, dropped = 0;
  int64_t chunk_e0 = -1;
  int slot = -1;
  bool chunk_open = false;

  for (;;) {
    bool want;
    char cur[37];
    lockState();
    want = g_session.active;
    strncpy(cur, g_session.id, sizeof(cur));
    unlockState();

    if (capturing && (!want || strcmp(cur, sid) != 0)) {
      // Session ended: flush what we have as the final chunk (may be 0 samples), then stop I2S.
      uint8_t s;
      if (chunk_open && slot >= 0) {
        queueChunk((uint8_t)slot, sid, seq, sample_start, filled, chunk_t0, chunk_e0, true, dropped);
      } else if (xQueueReceive(g_freeQ, &s, pdMS_TO_TICKS(500)) == pdTRUE) {
        queueChunk(s, sid, seq, sample_start, 0, millis(), mqtt::epochMs(), true, dropped);
      }
      audio::stop();
      audio::unlock();
      capturing = false;
      chunk_open = false;
      slot = -1;
      continue;
    }
    if (!capturing) {
      if (!want) {
        vTaskDelay(pdMS_TO_TICKS(10));
        continue;
      }
      if (!audio::lock(2000)) {
        systemError("microphone", "I2S_BUSY", "microphone in use (dataset recording?)", true);
        vTaskDelay(pdMS_TO_TICKS(100));
        continue;
      }
      if (!audio::start(100)) {  // same driver, no re-install; 100 ms PDM settle
        audio::unlock();
        systemError("microphone", "I2S_START_FAILED", "i2s_start failed", true);
        vTaskDelay(pdMS_TO_TICKS(500));
        continue;
      }
      strncpy(sid, cur, sizeof(sid));
      capturing = true;
      seq = sample_start = filled = dropped = 0;
      chunk_open = false;
      dsp.reset();
      dsp.gain = RUNTIME_AUDIO_GAIN;
      Serial.printf("[audio] capture started session=%s\n", sid);
    }

    if (!chunk_open) {
      uint8_t s;
      slot = (xQueueReceive(g_freeQ, &s, 0) == pdTRUE) ? (int)s : -1;  // -1: pool exhausted -> chunk dropped
      chunk_open = true;
      filled = 0;
      chunk_t0 = millis();
      chunk_e0 = mqtt::epochMs();
    }
    uint32_t want_bytes = kChunkPcmBytes - filled;
    if (want_bytes > sizeof(scratch)) want_bytes = sizeof(scratch);
    uint8_t* dst = slot >= 0 ? g_pool[slot] + kWavHeaderSize + filled : scratch;
    size_t got = audio::read(dst, want_bytes, 300);
    if (got == 0) {
      systemError("microphone", "I2S_READ_TIMEOUT", "no PCM data", true);
      continue;
    }
    dsp.process((int16_t*)dst, got / 2);  // continuous across chunk boundaries
    filled += got;
    if (filled >= kChunkPcmBytes) {
      if (slot >= 0) {
        queueChunk((uint8_t)slot, sid, seq, sample_start, filled, chunk_t0, chunk_e0, false, dropped);
      } else {
        dropped++;  // sequence still advances -> server detects the gap
        lockState();
        g_session.chunks_dropped++;
        g_stats.audio_dropped_total++;
        unlockState();
        Serial.printf("[WARN] audio chunk %u dropped (upload backlog, pool=%d)\n", (unsigned)seq, AUDIO_CHUNK_POOL);
      }
      seq++;
      sample_start += filled / 2;
      chunk_open = false;
    }
  }
}

// ============================================================================
// Network task
// ============================================================================
static void publishStatus() {
  static char buf[1200];
  uint32_t now = millis();
  lockState();
  bool listening = g_sm.listening();
  uint32_t remaining = g_sm.remainingMs(now);
  Stats st = g_stats;
  char sid[37];
  strncpy(sid, g_session.id, sizeof(sid));
  const char* label = g_sm.currentLabel() == VisionLabel::FALL       ? "Fall"
                      : g_sm.currentLabel() == VisionLabel::STANDING ? "Standing"
                                                                       : "None";
  unlockState();
  float inf_avg = st.frames ? (float)st.inference_ms_sum / st.frames : 0;
  snprintf(buf, sizeof(buf),
           "{\"schema_version\":\"1.2.0\",\"source\":\"device\",\"device_id\":\"%s\",\"state\":\"online\","
           "\"firmware_version\":\"%s\",\"uptime_ms\":%lu,\"free_heap_bytes\":%u,\"free_psram_bytes\":%u,"
           "\"wifi_rssi_dbm\":%d,\"models\":[{\"name\":\"%s\",\"version\":\"ei-%d-v%d\",\"task\":\"object-detection\"}],"
           "\"runtime\":{\"gate\":\"%s\",\"light_on\":%s,"
           "\"vision_enabled\":%s,\"camera_ok\":%s,\"mic_ok\":%s,\"runtime_enabled\":%s,"
           "\"listening\":%s,\"remaining_ms\":%u,\"hold_ms\":%u,\"session_id\":%s%s%s,\"label\":\"%s\","
           "\"fall_confidence\":%.4f,\"standing_confidence\":%.4f,\"threshold\":%.2f,\"frames\":%u,\"fps\":%.2f,"
           "\"inference_ms_avg\":%.1f,\"inference_ms_last\":%u,\"capture_ms_last\":%u,\"camera_failures\":%u,"
           "\"vision_errors\":%u,\"audio_chunks_sent\":%u,\"audio_chunks_failed\":%u,\"audio_chunks_dropped\":%u,"
           "\"dma_overflows_total\":%u,\"last_upload_ms\":%u,\"last_upload_status\":%d,\"mqtt_dropped\":%u,"
           "\"min_free_heap_bytes\":%u}}",
           DEVICE_ID, FIRMWARE_VERSION, (unsigned long)now, (unsigned)ESP.getFreeHeap(),
           (unsigned)ESP.getFreePsram(), WiFi.RSSI(), VISION_MODEL_NAME, vision::projectId(), vision::deployVersion(),
           gateName(listening), mqtt::lightState() < 0 ? "null" : (mqtt::lightState() ? "true" : "false"),
           g_visionOk ? "true" : "false", g_cameraOk ? "true" : "false", g_micOk ? "true" : "false",
           g_runtimeEnabled ? "true" : "false", listening ? "true" : "false", (unsigned)remaining,
           (unsigned)LISTEN_HOLD_MS, listening ? "\"" : "", listening ? sid : "null", listening ? "\"" : "", label,
           (double)st.last_fall_conf, (double)st.last_standing_conf, (double)VISION_THRESHOLD, (unsigned)st.frames,
           (double)st.fps, (double)inf_avg, (unsigned)st.last_total_ms, (unsigned)st.last_capture_ms,
           (unsigned)st.camera_failures, (unsigned)st.vision_errors, (unsigned)st.audio_sent_total,
           (unsigned)st.audio_failed_total, (unsigned)st.audio_dropped_total, (unsigned)audio::totalOverflows(),
           (unsigned)st.last_upload_ms, st.last_upload_status, (unsigned)mqtt::dropped(),
           (unsigned)ESP.getMinFreeHeap());
  mqtt::publishStatus(buf);
}

static void uploadChunk(const ChunkMsg& m) {
  const size_t bytes = kWavHeaderSize + m.samples * 2;
  http::Field fields[] = {
      {"device_id", DEVICE_ID},
      {"session_id", m.session},
      {"sequence", String(m.sequence)},
      {"sample_rate", String(audio::kSampleRate)},
      {"channels", String(audio::kChannels)},
      {"bits_per_sample", String(audio::kBitsPerSample)},
      {"duration_ms", String((uint32_t)((uint64_t)m.samples * 1000 / audio::kSampleRate))},
      {"sample_start", String(m.sample_start)},
      {"capture_timestamp_ms", String(m.capture_uptime_ms)},
      {"capture_epoch_ms", m.capture_epoch_ms >= 0 ? String((long long)m.capture_epoch_ms) : String("")},
      {"is_final", m.is_final ? "true" : "false"},
      {"dma_overflows", String(m.overflows)},
      {"dropped_chunks_before", String(m.dropped_before)},
      {"room_id", ROOM_ID},
  };
  http::Result r = {false, 0, 0, ""};
  for (int attempt = 0; attempt <= UPLOAD_RETRIES; ++attempt) {
    if (WiFi.status() != WL_CONNECTED) break;
    r = http::postWav("/api/v1/audio/chunks", fields, sizeof(fields) / sizeof(fields[0]), g_pool[m.slot], bytes,
                      String(m.session) + "_" + String(m.sequence) + ".wav");
    if (r.ok || (r.status >= 400 && r.status < 500)) break;
  }
  lockState();
  if (r.ok) g_stats.audio_sent_total++;
  else g_stats.audio_failed_total++;
  g_stats.last_upload_ms = r.ms;
  g_stats.last_upload_status = r.status;
  if (strcmp(g_session.id, m.session) == 0) {
    if (r.ok) g_session.chunks_sent++;
    else g_session.chunks_failed++;
  }
  unlockState();

  char data[384];
  snprintf(data, sizeof(data),
           "{\"ok\":%s,\"http_status\":%d,\"upload_ms\":%u,\"sequence\":%u,\"sample_start\":%u,\"samples\":%u,"
           "\"bytes\":%u,\"is_final\":%s,\"dma_overflows\":%u,\"server_status\":\"%s\"}",
           r.ok ? "true" : "false", r.status, (unsigned)r.ms, (unsigned)m.sequence, (unsigned)m.sample_start,
           (unsigned)m.samples, (unsigned)bytes, m.is_final ? "true" : "false", (unsigned)m.overflows,
           http::jsonString(r.body, "status").c_str());
  publish("AUDIO_CHUNK_SENT", m.session, m.session, false, -1, (float)r.ms, data);
  if (!r.ok) Serial.printf("[FAIL] audio chunk %u upload failed (HTTP %d)\n", (unsigned)m.sequence, r.status);
}

static void netTask(void*) {
  bool mqttStarted = false;
  uint32_t lastWifiAttempt = 0, lastStatus = 0;
  for (;;) {
    const uint32_t now = millis();
    if (WiFi.status() != WL_CONNECTED) {
      if (now - lastWifiAttempt > 5000) {
        lastWifiAttempt = now;
        WiFi.reconnect();
      }
    } else if (!mqttStarted) {
      configTime(0, 0, NTP_SERVER);
      mqttStarted = mqtt::begin();
      if (!mqttStarted) systemError("mqtt", "MQTT_INIT_FAILED", "esp_mqtt_client_init/start failed", true);
    }
    ChunkMsg m;
    if (xQueueReceive(g_sendQ, &m, pdMS_TO_TICKS(50)) == pdTRUE) {
      uploadChunk(m);
      uint8_t slot = m.slot;
      xQueueSend(g_freeQ, &slot, portMAX_DELAY);
    }
    const uint32_t interval = g_sm.listening() ? 1000 : STATUS_INTERVAL_MS;
    if (mqttStarted && mqtt::connected() && millis() - lastStatus >= interval) {
      lastStatus = millis();
      publishStatus();
    }
  }
}

// ============================================================================
// DATASET_RECORDING (serial o/u/n) — unchanged behaviour, now shares the I2S driver safely
// ============================================================================
static const uint32_t kWarmupMs = 200;
static uint32_t g_recorded = 0, g_uploaded = 0, g_failed = 0;
static String g_lastResult = "(none)";

static void recordDataset(const char* label) {
  const uint32_t samples = (uint32_t)((uint64_t)audio::kSampleRate * RECORD_DURATION_MS / 1000);
  const uint32_t pcm_bytes = samples * 2;
  const uint32_t wav_bytes = kWavHeaderSize + pcm_bytes;
  Serial.println();
  String upper = String(label);
  upper.toUpperCase();
  Serial.printf("Recording %s sample...\n", upper.c_str());
  Serial.printf("Recording label: %s\nSample rate: %uHz\nChannels: 1\nBits per sample: 16\nDuration: %ums\n", label,
                (unsigned)audio::kSampleRate, (unsigned)RECORD_DURATION_MS);
  if (!g_micOk) {
    Serial.println("[FAIL] I2S microphone not initialized - recording aborted");
    g_failed++;
    return;
  }
  bool listening;
  lockState();
  listening = g_sm.listening();
  unlockState();
  if (listening) {
    Serial.println("[FAIL] runtime LISTENING session active - try again after it stops");
    return;
  }
  const bool wasRuntime = g_runtimeEnabled;
  g_runtimeEnabled = false;  // pause vision so no session starts while we own the mic
  if (!audio::lock(5000)) {
    Serial.println("[FAIL] microphone busy");
    g_runtimeEnabled = wasRuntime;
    return;
  }
  uint8_t* wav = (uint8_t*)heap_caps_malloc(wav_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!wav) {
    audio::unlock();
    g_runtimeEnabled = wasRuntime;
    Serial.printf("[FAIL] PSRAM allocation of %u bytes failed\n", (unsigned)wav_bytes);
    g_failed++;
    return;
  }
  if (PRE_RECORD_DELAY_MS > kWarmupMs) delay(PRE_RECORD_DELAY_MS - kWarmupMs);
  bool ok = audio::start(kWarmupMs);
  uint32_t record_ms = 0;
  size_t got = 0;
  if (ok) {
    Serial.println(">>> SPEAK NOW <<<");
    const uint32_t t0 = millis();
    got = audio::read(wav + kWavHeaderSize, pcm_bytes, RECORD_DURATION_MS + 2000);
    record_ms = millis() - t0;
    audio::stop();
  }
  audio::unlock();
  g_runtimeEnabled = wasRuntime;

  if (!ok || got != pcm_bytes) {
    Serial.printf("[FAIL] recording failed (got %u of %u bytes)\n", (unsigned)got, (unsigned)pcm_bytes);
    heap_caps_free(wav);
    g_failed++;
    g_lastResult = "FAIL: record";
    return;
  }
  g_recorded++;
  PcmStats st = process_pcm16((int16_t*)(wav + kWavHeaderSize), samples, AUDIO_GAIN, AUDIO_REMOVE_DC != 0);
  write_wav_header(wav, audio::kSampleRate, 1, 16, pcm_bytes);
  Serial.printf("Recorded %u samples in %u ms (dc_offset=%d, peak=%d, clipped=%u)\n", (unsigned)samples,
                (unsigned)record_ms, (int)st.dc_offset, (int)st.peak_abs, (unsigned)st.clipped_samples);

  const String capture_id = http::makeUuid();
  http::Field fields[] = {
      {"label", label},
      {"device_id", DEVICE_ID},
      {"capture_id", capture_id},
      {"sample_rate", String(audio::kSampleRate)},
      {"record_ms", String(record_ms)},
      {"free_heap", String(ESP.getFreeHeap())},
      {"free_psram", String(ESP.getFreePsram())},
      {"device_uptime_ms", String(millis())},
      {"firmware_version", FIRMWARE_VERSION},
  };
  Serial.println("Uploading audio...");
  http::Result r = {false, 0, 0, ""};
  if (WiFi.status() == WL_CONNECTED) {
    for (int attempt = 0; attempt <= UPLOAD_RETRIES; ++attempt) {
      r = http::postWav("/api/v1/recordings", fields, sizeof(fields) / sizeof(fields[0]), wav, wav_bytes,
                        capture_id + ".wav");
      if (r.ok || (r.status >= 400 && r.status < 500)) break;
    }
  }
  heap_caps_free(wav);
  if (r.ok) {
    g_uploaded++;
    String filename = http::jsonString(r.body, "filename");
    Serial.println("Upload successful");
    Serial.printf("Saved: %s\n", filename.c_str());
    g_lastResult = "OK: " + filename;
  } else {
    g_failed++;
    Serial.printf("[FAIL] Upload failed (HTTP %d)\n", r.status);
    g_lastResult = "FAIL: upload HTTP " + String(r.status);
  }
  Serial.printf("[metrics] capture_id=%s label=%s record_ms=%u wav_bytes=%u upload_ms=%u http=%d free_heap=%u "
                "free_psram=%u\n",
                capture_id.c_str(), label, (unsigned)record_ms, (unsigned)wav_bytes, (unsigned)r.ms, r.status,
                (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getFreePsram());
}

// ============================================================================
// Serial UI
// ============================================================================
static void printHelp() {
  Serial.println();
  Serial.println("BedLight AI " FIRMWARE_VERSION " - RUNTIME (Fall -> listen, gated by virtual light) + DATASET_RECORDING");
  Serial.println("  o / u / n : record 'off' / 'unknown' / 'noise' dataset sample (2 s)");
  Serial.println("  s : status          v : vision/audio statistics");
  Serial.println("  p : toggle per-frame vision print");
  Serial.println("  r : toggle runtime (vision + fall-triggered audio) on/off");
  Serial.println("  h : this help");
}

static void printStatus() {
  uint32_t now = millis();
  lockState();
  bool listening = g_sm.listening();
  uint32_t rem = g_sm.remainingMs(now);
  Session s = g_session;
  Stats st = g_stats;
  unlockState();
  Serial.println();
  Serial.printf("Firmware %s  Device %s  uptime %lu ms\n", FIRMWARE_VERSION, DEVICE_ID, (unsigned long)now);
  Serial.printf("Camera: %s  Vision model: %s (%s)  Mic: %s\n", g_cameraOk ? "OK" : "FAIL",
                g_visionOk ? "LOADED" : "NOT AVAILABLE", vision::modelName(), g_micOk ? "OK" : "FAIL");
  Serial.printf("Gate: %s  Light: %s\n", gateName(listening),
                mqtt::lightState() < 0 ? "unknown (no retained state yet)" : (mqtt::lightState() ? "ON" : "OFF"));
  Serial.printf("Runtime: %s  State: %s  remaining=%u ms  session=%s\n", g_runtimeEnabled ? "ON" : "PAUSED",
                listening ? "LISTENING" : "WATCHING", (unsigned)rem, listening ? s.id : "-");
  Serial.printf("PSRAM free=%u  Heap free=%u min=%u\n", (unsigned)ESP.getFreePsram(), (unsigned)ESP.getFreeHeap(),
                (unsigned)ESP.getMinFreeHeap());
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("Wi-Fi: CONNECTED IP=%s RSSI=%d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  else
    Serial.printf("Wi-Fi: DISCONNECTED (status=%d)\n", (int)WiFi.status());
  Serial.printf("Voice server: http://%s:%d %s\n", VOICE_SERVER_HOST, VOICE_SERVER_PORT,
                serverHostLooksWrong(VOICE_SERVER_HOST) ? "[ERROR: use the PC's LAN IP]" : "");
  Serial.printf("MQTT: mqtt://%s:%d %s  published=%u dropped=%u\n", MQTT_HOST, MQTT_PORT,
                mqtt::connected() ? "CONNECTED" : "DISCONNECTED", (unsigned)mqtt::published(),
                (unsigned)mqtt::dropped());
  Serial.printf("Audio chunks: sent=%u failed=%u dropped=%u dma_overflows=%u\n", (unsigned)st.audio_sent_total,
                (unsigned)st.audio_failed_total, (unsigned)st.audio_dropped_total, (unsigned)audio::totalOverflows());
  Serial.printf("Dataset: recorded=%u uploaded=%u failed=%u last=%s\n", (unsigned)g_recorded, (unsigned)g_uploaded,
                (unsigned)g_failed, g_lastResult.c_str());
}

static void printVision() {
  lockState();
  Stats st = g_stats;
  unlockState();
  Serial.printf("[vision] frames=%u fps=%.2f inference avg=%.1f ms last=%u ms capture last=%u ms camera_failures=%u "
                "errors=%u last fall=%.2f standing=%.2f threshold=%.2f\n",
                (unsigned)st.frames, (double)st.fps,
                st.frames ? (double)st.inference_ms_sum / st.frames : 0.0, (unsigned)st.last_total_ms,
                (unsigned)st.last_capture_ms, (unsigned)st.camera_failures, (unsigned)st.vision_errors,
                (double)st.last_fall_conf, (double)st.last_standing_conf, (double)VISION_THRESHOLD);
}

// ============================================================================
void setup() {
  Serial.begin(115200);
  const uint32_t t = millis();
  while (!Serial && millis() - t < 3000) delay(10);
  Serial.println();
  Serial.println("=== BedLight AI firmware " FIRMWARE_VERSION " ===");
  if (!psramFound()) Serial.println("[ERROR] PSRAM not found - set Tools > PSRAM > OPI PSRAM");
  if (serverHostLooksWrong(VOICE_SERVER_HOST) || serverHostLooksWrong(MQTT_HOST))
    Serial.println("[ERROR] VOICE_SERVER_HOST / MQTT_HOST must be the PC's LAN IP");

  g_mutex = xSemaphoreCreateMutex();
  g_publishLock = xSemaphoreCreateMutex();
  g_stats.window_start_ms = millis();

  g_micOk = audio::init();
  Serial.println(g_micOk ? "I2S PDM microphone initialized" : "[ERROR] I2S PDM microphone init FAILED");
  g_cameraOk = camera::init();
  Serial.println(g_cameraOk ? "Camera initialized (grayscale QVGA -> 96x96)" : "[ERROR] camera init FAILED");
  g_visionOk = vision::available() && vision::init();
  if (!vision::available()) Serial.println("[WARN] built with BEDLIGHT_VISION_ENABLED=0 - no vision model linked");
  Serial.printf("Memory after init: heap=%u psram=%u\n", (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getFreePsram());

  // Audio chunk pool in PSRAM
  g_freeQ = xQueueCreate(AUDIO_CHUNK_POOL, sizeof(uint8_t));
  g_sendQ = xQueueCreate(AUDIO_CHUNK_POOL, sizeof(ChunkMsg));
  int pool_ok = 0;
  for (uint8_t i = 0; i < AUDIO_CHUNK_POOL; ++i) {
    g_pool[i] = (uint8_t*)heap_caps_malloc(kChunkWavBytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (g_pool[i]) {
      xQueueSend(g_freeQ, &i, 0);
      pool_ok++;
    }
  }
  Serial.printf("Audio chunk pool: %d x %u B (PSRAM)\n", pool_ok, (unsigned)kChunkWavBytes);

  ensureWifiBlocking();
  xTaskCreatePinnedToCore(netTask, "net", 8192, NULL, 3, NULL, 0);
  if (g_micOk && pool_ok > 0) xTaskCreatePinnedToCore(audioTask, "audio", 6144, NULL, 6, NULL, 0);
  if (g_cameraOk && g_visionOk) xTaskCreatePinnedToCore(visionTask, "vision", 16384, NULL, 2, NULL, 1);
  else Serial.println("[WARN] vision task not started (camera or model unavailable) - no Fall detection");
  Serial.printf("Fall threshold=%.2f  hold=%u ms  chunk=%u ms\n", (double)VISION_THRESHOLD, (unsigned)LISTEN_HOLD_MS,
                (unsigned)AUDIO_CHUNK_MS);
  printHelp();
}

void loop() {
  // 20 ms tick: expire the listening hold window even when no new frame arrives.
  static uint32_t lastTick = 0;
  const uint32_t now = millis();
  if (now - lastTick >= 20) {
    lastTick = now;
    static bool gateWasClosed = false;
    const bool gateClosed = lightGateClosed();
    lockState();
    Transition tr = gateClosed ? g_sm.forceStop(now) : g_sm.tick(now);  // light OFF ends listening at once
    unlockState();
    handleTransition(tr, now, -1, -1);
    if (gateClosed != gateWasClosed) {
      gateWasClosed = gateClosed;
      Serial.printf("[STATE] light %s -> %s\n", gateClosed ? "OFF" : "ON",
                    gateClosed ? "IDLE (vision + audio stopped)" : "WATCHING (Fall detection resumed)");
    }
  }
  if (!Serial.available()) {
    delay(5);
    return;
  }
  const char c = (char)Serial.read();
  switch (c) {
    case 'o': recordDataset("off"); break;
    case 'u': recordDataset("unknown"); break;
    case 'n': recordDataset("noise"); break;
    case 's': printStatus(); break;
    case 'v': printVision(); break;
    case 'p':
      g_printFrames = !g_printFrames;
      Serial.printf("per-frame print %s\n", g_printFrames ? "ON" : "OFF");
      break;
    case 'r':
      g_runtimeEnabled = !g_runtimeEnabled;
      Serial.printf("runtime %s\n", g_runtimeEnabled ? "ON" : "PAUSED");
      break;
    case 'h': case '?': printHelp(); break;
    case '\r': case '\n': case ' ': break;
    default: Serial.printf("Unknown command '%c' (h = help)\n", c); break;
  }
}
