// BedLight AI — XIAO ESP32S3 Sense firmware
// Mode: DATASET_RECORDING
//
// Records 16 kHz / mono / 16-bit PCM from the built-in PDM microphone
// (CLK = GPIO42, DATA = GPIO41) into PSRAM, wraps it in a WAV header and
// uploads it to the Voice Service (POST /api/v1/recordings, multipart/form-data).
//
// Toolchain: ESP32 Arduino Core 2.0.x (legacy IDF 4.4 driver/i2s.h).
//            ESP_I2S.h (Core 3.x) is intentionally NOT used.
// Board:     XIAO_ESP32S3, PSRAM: OPI PSRAM, USB CDC On Boot: Enabled
//
// Serial commands (115200 baud, any line ending):
//   o = record "off"    u = record "unknown"    n = record "noise"
//   s = status          h = help

#include <Arduino.h>
#include <WiFi.h>
#include <driver/i2s.h>
#include <esp_heap_caps.h>

#if __has_include("config.h")
#include "config.h"
#else
#error "config.h not found: copy bedlight/config.example.h to bedlight/config.h and set Wi-Fi / server values."
#endif

#include "wav_header.h"

#define FIRMWARE_VERSION "0.2.0-dataset"

// ---------- Audio constants (must match Voice Service validation) ----------
static const uint32_t kSampleRate = 16000;
static const uint16_t kChannels = 1;
static const uint16_t kBitsPerSample = 16;
static const int kPinPdmClk = 42;
static const int kPinPdmData = 41;
static const i2s_port_t kI2sPort = I2S_NUM_0;
static const uint32_t kWarmupMs = 200;  // discard PDM start-up transient after i2s_start()
static const size_t kReadChunkBytes = 1024;

static const uint32_t kSamplesPerClip = (uint32_t)((uint64_t)kSampleRate * RECORD_DURATION_MS / 1000);
static const uint32_t kPcmBytes = kSamplesPerClip * kChannels * (kBitsPerSample / 8);
static const uint32_t kWavBytes = bedlight::kWavHeaderSize + kPcmBytes;

// ---------- State ----------
static bool g_i2sReady = false;
static uint32_t g_recorded = 0, g_uploaded = 0, g_failed = 0;
static String g_lastResult = "(none)";

// RAII holder for a PSRAM buffer: guarantees free() on every return path.
struct PsramBuffer {
  uint8_t* ptr;
  size_t size;
  explicit PsramBuffer(size_t n)
      : ptr((uint8_t*)heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)), size(n) {}
  ~PsramBuffer() { if (ptr) heap_caps_free(ptr); }
  PsramBuffer(const PsramBuffer&) = delete;
  PsramBuffer& operator=(const PsramBuffer&) = delete;
};

struct RecordResult {
  bool ok;
  uint32_t recordMs;
  uint32_t samples;
  bedlight::PcmStats stats;
};

struct UploadResult {
  bool ok;
  int httpStatus;
  uint32_t uploadMs;
  String body;
};

// ============================================================================
// I2S (PDM RX)
// ============================================================================
static bool initI2S() {
  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM);
  cfg.sample_rate = kSampleRate;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 8;
  cfg.dma_buf_len = 512;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = false;
  cfg.fixed_mclk = 0;

  esp_err_t err = i2s_driver_install(kI2sPort, &cfg, 0, NULL);
  if (err != ESP_OK) {
    Serial.printf("[ERROR] i2s_driver_install failed: %s (0x%x)\n", esp_err_to_name(err), err);
    return false;
  }

  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = I2S_PIN_NO_CHANGE;
  pins.ws_io_num = kPinPdmClk;     // PDM clock
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = kPinPdmData;  // PDM data
  err = i2s_set_pin(kI2sPort, &pins);
  if (err != ESP_OK) {
    Serial.printf("[ERROR] i2s_set_pin failed: %s (0x%x)\n", esp_err_to_name(err), err);
    i2s_driver_uninstall(kI2sPort);
    return false;
  }
  // Keep the peripheral stopped while idle; started per recording so DMA never holds stale audio.
  i2s_stop(kI2sPort);
  return true;
}

// Reads exactly `bytes` of PCM into dst. Returns bytes actually read.
static size_t readPcm(uint8_t* dst, size_t bytes, uint32_t timeoutMs) {
  size_t total = 0;
  const uint32_t start = millis();
  while (total < bytes) {
    if (millis() - start > timeoutMs) break;
    size_t want = bytes - total;
    if (want > kReadChunkBytes) want = kReadChunkBytes;
    size_t got = 0;
    esp_err_t err = i2s_read(kI2sPort, dst + total, want, &got, pdMS_TO_TICKS(500));
    if (err != ESP_OK) {
      Serial.printf("[ERROR] i2s_read failed: %s\n", esp_err_to_name(err));
      break;
    }
    total += got;
  }
  return total;
}

static RecordResult recordClip(uint8_t* wav) {
  RecordResult r = {false, 0, 0, {0, 0, 0}};
  uint8_t* pcm = wav + bedlight::kWavHeaderSize;

  // Pre-delay (includes warm-up time) so the user can get ready after typing the command.
  if (PRE_RECORD_DELAY_MS > kWarmupMs) delay(PRE_RECORD_DELAY_MS - kWarmupMs);

  esp_err_t err = i2s_start(kI2sPort);
  if (err != ESP_OK) {
    Serial.printf("[ERROR] i2s_start failed: %s\n", esp_err_to_name(err));
    return r;
  }

  // Discard warm-up samples (PDM filter settling) into the PCM area; overwritten below.
  const size_t warmupBytes = (size_t)kSampleRate * kWarmupMs / 1000 * (kBitsPerSample / 8);
  readPcm(pcm, warmupBytes < kPcmBytes ? warmupBytes : kPcmBytes, kWarmupMs + 1000);

  Serial.println(">>> SPEAK NOW <<<");
  const uint32_t t0 = millis();
  const size_t got = readPcm(pcm, kPcmBytes, RECORD_DURATION_MS + 2000);
  r.recordMs = millis() - t0;
  i2s_stop(kI2sPort);

  if (got != kPcmBytes) {
    Serial.printf("[ERROR] PCM sample count mismatch: got %u bytes, expected %u bytes\n",
                  (unsigned)got, (unsigned)kPcmBytes);
    return r;
  }
  r.samples = got / (kBitsPerSample / 8) / kChannels;
  if (r.samples != kSamplesPerClip) {
    Serial.printf("[ERROR] sample count %u != expected %u\n", (unsigned)r.samples, (unsigned)kSamplesPerClip);
    return r;
  }

  // Wall-clock sanity check: 16 kHz clock really delivers RECORD_DURATION_MS of audio.
  const int32_t drift = (int32_t)r.recordMs - (int32_t)RECORD_DURATION_MS;
  if (drift > (int32_t)RECORD_DURATION_MS / 10 || drift < -(int32_t)RECORD_DURATION_MS / 10) {
    Serial.printf("[WARN] capture took %u ms for %u ms of audio (>10%% off) - check sample rate\n",
                  (unsigned)r.recordMs, (unsigned)RECORD_DURATION_MS);
  }

  r.stats = bedlight::process_pcm16((int16_t*)pcm, r.samples, AUDIO_GAIN, AUDIO_REMOVE_DC != 0);

  if (!bedlight::write_wav_header(wav, kSampleRate, kChannels, kBitsPerSample, kPcmBytes)) {
    Serial.println("[ERROR] WAV header generation failed");
    return r;
  }
  r.ok = true;
  return r;
}

// ============================================================================
// Wi-Fi
// ============================================================================
static bool ensureWifi() {
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
    WiFi.disconnect();
    return false;
  }
  Serial.printf("Wi-Fi connected: IP=%s RSSI=%d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
  return true;
}

static bool serverHostLooksWrong() {
  const char* h = VOICE_SERVER_HOST;
  return strcmp(h, "localhost") == 0 || strncmp(h, "127.", 4) == 0 ||
         strcmp(h, "voice-service") == 0 || strcmp(h, "0.0.0.0") == 0;
}

// ============================================================================
// HTTP upload (multipart/form-data, streamed from PSRAM — no second copy)
// ============================================================================
static String makeUuid() {
  uint8_t b[16];
  for (int i = 0; i < 16; i += 4) {
    uint32_t r = esp_random();
    memcpy(b + i, &r, 4);
  }
  b[6] = (b[6] & 0x0f) | 0x40;  // version 4
  b[8] = (b[8] & 0x3f) | 0x80;  // variant
  char s[37];
  snprintf(s, sizeof(s), "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
           b[0], b[1], b[2], b[3], b[4], b[5], b[6], b[7], b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]);
  return String(s);
}

static void addField(String& out, const String& boundary, const char* name, const String& value) {
  out += "--" + boundary + "\r\n";
  out += "Content-Disposition: form-data; name=\"" + String(name) + "\"\r\n\r\n";
  out += value + "\r\n";
}

static bool writeAll(WiFiClient& c, const uint8_t* data, size_t len) {
  size_t sent = 0;
  const uint32_t start = millis();
  while (sent < len) {
    if (!c.connected() || millis() - start > HTTP_TIMEOUT_MS) return false;
    size_t chunk = len - sent > 4096 ? 4096 : len - sent;
    size_t n = c.write(data + sent, chunk);
    if (n == 0) { delay(5); continue; }
    sent += n;
  }
  return true;
}

static UploadResult uploadOnce(const uint8_t* wav, size_t wavLen, const char* label,
                               const String& captureId, const RecordResult& rec) {
  UploadResult u = {false, 0, 0, ""};
  const String boundary = "----bedlight" + String((uint32_t)esp_random(), HEX);

  String head;
  head.reserve(1024);
  addField(head, boundary, "label", label);
  addField(head, boundary, "device_id", DEVICE_ID);
  addField(head, boundary, "capture_id", captureId);
  addField(head, boundary, "sample_rate", String(kSampleRate));
  addField(head, boundary, "record_ms", String(rec.recordMs));
  addField(head, boundary, "free_heap", String(ESP.getFreeHeap()));
  addField(head, boundary, "free_psram", String(ESP.getFreePsram()));
  addField(head, boundary, "device_uptime_ms", String(millis()));
  addField(head, boundary, "firmware_version", FIRMWARE_VERSION);
  head += "--" + boundary + "\r\n";
  head += "Content-Disposition: form-data; name=\"audio\"; filename=\"" + captureId + ".wav\"\r\n";
  head += "Content-Type: audio/wav\r\n\r\n";
  const String tail = "\r\n--" + boundary + "--\r\n";
  const size_t contentLength = head.length() + wavLen + tail.length();

  WiFiClient client;
  client.setTimeout(HTTP_TIMEOUT_MS / 1000);
  const uint32_t t0 = millis();
  if (!client.connect(VOICE_SERVER_HOST, VOICE_SERVER_PORT, HTTP_TIMEOUT_MS)) {
    Serial.printf("[ERROR] cannot connect to %s:%d\n", VOICE_SERVER_HOST, VOICE_SERVER_PORT);
    return u;
  }

  String req;
  req.reserve(320);
  req += "POST /api/v1/recordings HTTP/1.1\r\n";
  req += "Host: " + String(VOICE_SERVER_HOST) + ":" + String(VOICE_SERVER_PORT) + "\r\n";
  req += "User-Agent: bedlight-xiao/" FIRMWARE_VERSION "\r\n";
  req += "Content-Type: multipart/form-data; boundary=" + boundary + "\r\n";
  req += "Content-Length: " + String((unsigned)contentLength) + "\r\n";
  if (strlen(VOICE_UPLOAD_TOKEN) > 0) req += "X-Device-Token: " VOICE_UPLOAD_TOKEN "\r\n";
  req += "Connection: close\r\n\r\n";

  bool sent = writeAll(client, (const uint8_t*)req.c_str(), req.length()) &&
              writeAll(client, (const uint8_t*)head.c_str(), head.length()) &&
              writeAll(client, wav, wavLen) &&
              writeAll(client, (const uint8_t*)tail.c_str(), tail.length());
  if (!sent) {
    Serial.println("[ERROR] HTTP request body send failed");
    client.stop();
    return u;
  }

  // Status line: "HTTP/1.1 201 Created"
  const uint32_t waitStart = millis();
  while (!client.available() && client.connected() && millis() - waitStart < HTTP_TIMEOUT_MS) delay(5);
  String status = client.readStringUntil('\n');
  int sp = status.indexOf(' ');
  u.httpStatus = sp > 0 ? status.substring(sp + 1, sp + 4).toInt() : 0;
  // Skip headers
  while (client.connected() || client.available()) {
    String line = client.readStringUntil('\n');
    if (line == "\r" || line.length() == 0) break;
  }
  // Body (small JSON)
  const uint32_t bodyStart = millis();
  while ((client.connected() || client.available()) && u.body.length() < 2048 &&
         millis() - bodyStart < HTTP_TIMEOUT_MS) {
    if (client.available()) u.body += (char)client.read();
    else delay(2);
  }
  client.stop();
  u.uploadMs = millis() - t0;
  u.ok = (u.httpStatus == 200 || u.httpStatus == 201);
  return u;
}

// Minimal JSON string extraction ("key":"value") — avoids an extra library dependency.
static String jsonString(const String& body, const char* key) {
  String needle = String("\"") + key + "\":\"";
  int i = body.indexOf(needle);
  if (i < 0) return "";
  i += needle.length();
  int j = body.indexOf('"', i);
  return j > i ? body.substring(i, j) : "";
}

// ============================================================================
// Commands
// ============================================================================
static void recordAndUpload(const char* label) {
  Serial.println();
  String upper = String(label);
  upper.toUpperCase();
  Serial.printf("Recording %s sample...\n", upper.c_str());
  Serial.printf("Recording label: %s\n", label);
  Serial.printf("Sample rate: %uHz\n", (unsigned)kSampleRate);
  Serial.printf("Channels: %u\n", (unsigned)kChannels);
  Serial.printf("Bits per sample: %u\n", (unsigned)kBitsPerSample);
  Serial.printf("Duration: %ums\n", (unsigned)RECORD_DURATION_MS);

  if (!g_i2sReady) {
    Serial.println("[FAIL] I2S microphone not initialized - recording aborted");
    g_failed++;
    g_lastResult = "FAIL: I2S not ready";
    return;
  }

  const String captureId = makeUuid();
  PsramBuffer buf(kWavBytes);  // freed automatically when this function returns
  if (buf.ptr == NULL) {
    Serial.printf("[FAIL] PSRAM allocation of %u bytes failed (free PSRAM=%u)\n",
                  (unsigned)kWavBytes, (unsigned)ESP.getFreePsram());
    g_failed++;
    g_lastResult = "FAIL: PSRAM alloc";
    return;
  }

  RecordResult rec = recordClip(buf.ptr);
  if (!rec.ok) {
    Serial.println("[FAIL] recording failed");
    g_failed++;
    g_lastResult = "FAIL: record";
    return;
  }
  g_recorded++;
  Serial.printf("Recorded %u samples in %u ms (dc_offset=%d, peak=%d, clipped=%u)\n",
                (unsigned)rec.samples, (unsigned)rec.recordMs, (int)rec.stats.dc_offset,
                (int)rec.stats.peak_abs, (unsigned)rec.stats.clipped_samples);
  if (rec.stats.peak_abs < 300) Serial.println("[WARN] very low signal - was anything said? check mic/gain");
  if (rec.stats.clipped_samples > rec.samples / 1000) Serial.println("[WARN] clipping detected - reduce AUDIO_GAIN or speak further away");

  if (!ensureWifi()) {
    Serial.println("[FAIL] upload skipped: Wi-Fi not connected (recording discarded)");
    g_failed++;
    g_lastResult = "FAIL: Wi-Fi";
    return;
  }

  Serial.println("Uploading audio...");
  UploadResult up = {false, 0, 0, ""};
  for (int attempt = 0; attempt <= UPLOAD_RETRIES; ++attempt) {
    if (attempt > 0) {
      Serial.printf("Retrying upload (%d/%d)...\n", attempt, UPLOAD_RETRIES);
      delay(500 * attempt);
      if (!ensureWifi()) continue;
    }
    up = uploadOnce(buf.ptr, kWavBytes, label, captureId, rec);
    // 4xx = server rejected the data; retrying the same bytes will not help.
    if (up.ok || (up.httpStatus >= 400 && up.httpStatus < 500)) break;
  }

  if (up.ok) {
    g_uploaded++;
    String filename = jsonString(up.body, "filename");
    String validation = jsonString(up.body, "validation_status");
    String status = jsonString(up.body, "status");
    Serial.println("Upload successful");
    Serial.printf("Saved: %s\n", filename.c_str());
    Serial.printf("Server status: %s, validation: %s\n", status.c_str(), validation.c_str());
    g_lastResult = "OK: " + filename;
  } else {
    g_failed++;
    Serial.printf("[FAIL] Upload failed (HTTP %d)\n", up.httpStatus);
    if (up.body.length()) Serial.printf("Server response: %s\n", up.body.c_str());
    g_lastResult = "FAIL: upload HTTP " + String(up.httpStatus);
  }

  // One parseable metrics line per capture (capture_id joins it with server metadata).
  Serial.printf("[metrics] capture_id=%s label=%s record_ms=%u wav_bytes=%u upload_ms=%u http=%d "
                "free_heap=%u free_psram=%u\n",
                captureId.c_str(), label, (unsigned)rec.recordMs, (unsigned)kWavBytes,
                (unsigned)up.uploadMs, up.httpStatus, (unsigned)ESP.getFreeHeap(),
                (unsigned)ESP.getFreePsram());
}

static void printHelp() {
  Serial.println();
  Serial.println("BedLight AI - DATASET_RECORDING mode");
  Serial.println("  o : record 'off' sample");
  Serial.println("  u : record 'unknown' sample (other words / speech)");
  Serial.println("  n : record 'noise' sample (background, no speech)");
  Serial.println("  s : status (recording / Wi-Fi / memory)");
  Serial.println("  h : this help");
}

static void printStatus() {
  Serial.println();
  Serial.printf("Firmware: %s  Device: %s\n", FIRMWARE_VERSION, DEVICE_ID);
  Serial.printf("I2S mic: %s  (%u Hz, %u ch, %u-bit, %u ms clips, gain=%d)\n",
                g_i2sReady ? "READY" : "NOT READY", (unsigned)kSampleRate, (unsigned)kChannels,
                (unsigned)kBitsPerSample, (unsigned)RECORD_DURATION_MS, AUDIO_GAIN);
  Serial.printf("PSRAM: %s  free=%u  Heap free=%u  min=%u\n", psramFound() ? "found" : "NOT FOUND",
                (unsigned)ESP.getFreePsram(), (unsigned)ESP.getFreeHeap(), (unsigned)ESP.getMinFreeHeap());
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("Wi-Fi: CONNECTED  SSID=%s  IP=%s  RSSI=%d dBm\n", WiFi.SSID().c_str(),
                  WiFi.localIP().toString().c_str(), WiFi.RSSI());
  } else {
    Serial.printf("Wi-Fi: DISCONNECTED (status=%d)\n", (int)WiFi.status());
  }
  Serial.printf("Server: http://%s:%d/api/v1/recordings%s\n", VOICE_SERVER_HOST, VOICE_SERVER_PORT,
                serverHostLooksWrong() ? "  [ERROR: use the PC's LAN IP]" : "");
  Serial.printf("Counts: recorded=%u uploaded=%u failed=%u  Last: %s\n", (unsigned)g_recorded,
                (unsigned)g_uploaded, (unsigned)g_failed, g_lastResult.c_str());
}

// ============================================================================
void setup() {
  Serial.begin(115200);
  const uint32_t t = millis();
  while (!Serial && millis() - t < 3000) delay(10);  // USB CDC: wait briefly for the monitor
  Serial.println();
  Serial.println("=== BedLight AI firmware " FIRMWARE_VERSION " (DATASET_RECORDING) ===");

  if (!psramFound()) {
    Serial.println("[ERROR] PSRAM not found - set Tools > PSRAM > OPI PSRAM");
  }
  if (serverHostLooksWrong()) {
    Serial.printf("[ERROR] VOICE_SERVER_HOST=\"%s\" is not reachable from the ESP32. Use the PC's LAN IP.\n",
                  VOICE_SERVER_HOST);
  }

  g_i2sReady = initI2S();
  Serial.println(g_i2sReady ? "I2S PDM microphone initialized" : "[ERROR] I2S PDM microphone init FAILED");

  ensureWifi();
  printHelp();
}

void loop() {
  if (!Serial.available()) {
    delay(10);
    return;
  }
  const char c = (char)Serial.read();
  switch (c) {
    case 'o': recordAndUpload("off"); break;
    case 'u': recordAndUpload("unknown"); break;
    case 'n': recordAndUpload("noise"); break;
    case 's': printStatus(); break;
    case 'h': case '?': printHelp(); break;
    case '\r': case '\n': case ' ': break;
    default: Serial.printf("Unknown command '%c' (h = help)\n", c); break;
  }
}
