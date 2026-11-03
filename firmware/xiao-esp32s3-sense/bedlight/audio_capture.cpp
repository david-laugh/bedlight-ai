#include "audio_capture.h"

#include <driver/i2s.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>
#include <freertos/semphr.h>

namespace bedlight {
namespace audio {

static const i2s_port_t kPort = I2S_NUM_0;
static const int kPinPdmClk = 42;
static const int kPinPdmData = 41;
static const size_t kChunk = 1024;

static QueueHandle_t s_events = NULL;
static SemaphoreHandle_t s_lock = NULL;
static bool s_ready = false;
static volatile bool s_running = false;
static volatile uint32_t s_ovf_since = 0;
static volatile uint32_t s_ovf_total = 0;

static void drainEvents() {
  if (!s_events) return;
  i2s_event_t ev;
  while (xQueueReceive(s_events, &ev, 0) == pdTRUE) {
    if (ev.type == I2S_EVENT_RX_Q_OVF) {
      s_ovf_since++;
      s_ovf_total++;
    }
  }
}

bool init() {
  if (s_ready) return true;
  if (!s_lock) s_lock = xSemaphoreCreateMutex();

  i2s_config_t cfg = {};
  cfg.mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX | I2S_MODE_PDM);
  cfg.sample_rate = kSampleRate;
  cfg.bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT;
  cfg.channel_format = I2S_CHANNEL_FMT_ONLY_LEFT;
  cfg.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  cfg.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  cfg.dma_buf_count = 16;   // 16 x 512 samples = 512 ms of DMA slack
  cfg.dma_buf_len = 512;
  cfg.use_apll = false;
  cfg.tx_desc_auto_clear = false;
  cfg.fixed_mclk = 0;

  esp_err_t err = i2s_driver_install(kPort, &cfg, 32, &s_events);
  if (err != ESP_OK) {
    Serial.printf("[ERROR] i2s_driver_install failed: %s (0x%x)\n", esp_err_to_name(err), err);
    return false;
  }
  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = I2S_PIN_NO_CHANGE;
  pins.ws_io_num = kPinPdmClk;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = kPinPdmData;
  err = i2s_set_pin(kPort, &pins);
  if (err != ESP_OK) {
    Serial.printf("[ERROR] i2s_set_pin failed: %s (0x%x)\n", esp_err_to_name(err), err);
    i2s_driver_uninstall(kPort);
    s_events = NULL;
    return false;
  }
  i2s_stop(kPort);
  s_ready = true;
  return true;
}

bool ready() { return s_ready; }
bool running() { return s_running; }

bool start(uint32_t warmup_ms) {
  if (!s_ready) return false;
  esp_err_t err = i2s_start(kPort);
  if (err != ESP_OK) {
    Serial.printf("[ERROR] i2s_start failed: %s\n", esp_err_to_name(err));
    return false;
  }
  s_running = true;
  drainEvents();
  s_ovf_since = 0;
  if (warmup_ms) {
    static uint8_t scratch[kChunk];
    size_t remaining = (size_t)kBytesPerSecond * warmup_ms / 1000;
    while (remaining) {
      size_t want = remaining > kChunk ? kChunk : remaining;
      size_t got = read(scratch, want, 500);
      if (got == 0) break;
      remaining -= got;
    }
  }
  s_ovf_since = 0;
  return true;
}

void stop() {
  if (!s_ready) return;
  i2s_stop(kPort);
  s_running = false;
  drainEvents();
}

size_t read(uint8_t* dst, size_t bytes, uint32_t timeout_ms) {
  size_t total = 0;
  const uint32_t t0 = millis();
  while (total < bytes) {
    drainEvents();
    if (millis() - t0 > timeout_ms) break;
    size_t want = bytes - total;
    if (want > kChunk) want = kChunk;
    size_t got = 0;
    esp_err_t err = i2s_read(kPort, dst + total, want, &got, pdMS_TO_TICKS(200));
    if (err != ESP_OK) {
      Serial.printf("[ERROR] i2s_read failed: %s\n", esp_err_to_name(err));
      break;
    }
    total += got;
  }
  drainEvents();
  return total;
}

uint32_t takeOverflows() {
  uint32_t v = s_ovf_since;
  s_ovf_since = 0;
  return v;
}

uint32_t totalOverflows() { return s_ovf_total; }

bool lock(uint32_t timeout_ms) {
  if (!s_lock) s_lock = xSemaphoreCreateMutex();
  return xSemaphoreTake(s_lock, pdMS_TO_TICKS(timeout_ms)) == pdTRUE;
}

void unlock() {
  if (s_lock) xSemaphoreGive(s_lock);
}

}  // namespace audio
}  // namespace bedlight
