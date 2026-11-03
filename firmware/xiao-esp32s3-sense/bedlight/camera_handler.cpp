#include "camera_handler.h"

#include <esp_camera.h>

namespace bedlight {
namespace camera {

// Seeed Studio XIAO ESP32S3 Sense camera pins (CAMERA_MODEL_XIAO_ESP32S3)
#define CAM_PIN_PWDN -1
#define CAM_PIN_RESET -1
#define CAM_PIN_XCLK 10
#define CAM_PIN_SIOD 40
#define CAM_PIN_SIOC 39
#define CAM_PIN_D7 48
#define CAM_PIN_D6 11
#define CAM_PIN_D5 12
#define CAM_PIN_D4 14
#define CAM_PIN_D3 16
#define CAM_PIN_D2 18
#define CAM_PIN_D1 17
#define CAM_PIN_D0 15
#define CAM_PIN_VSYNC 38
#define CAM_PIN_HREF 47
#define CAM_PIN_PCLK 13

static bool s_ready = false;
static uint32_t s_failures = 0;

bool init() {
  camera_config_t c = {};
  c.pin_pwdn = CAM_PIN_PWDN;
  c.pin_reset = CAM_PIN_RESET;
  c.pin_xclk = CAM_PIN_XCLK;
  c.pin_sccb_sda = CAM_PIN_SIOD;
  c.pin_sccb_scl = CAM_PIN_SIOC;
  c.pin_d7 = CAM_PIN_D7;
  c.pin_d6 = CAM_PIN_D6;
  c.pin_d5 = CAM_PIN_D5;
  c.pin_d4 = CAM_PIN_D4;
  c.pin_d3 = CAM_PIN_D3;
  c.pin_d2 = CAM_PIN_D2;
  c.pin_d1 = CAM_PIN_D1;
  c.pin_d0 = CAM_PIN_D0;
  c.pin_vsync = CAM_PIN_VSYNC;
  c.pin_href = CAM_PIN_HREF;
  c.pin_pclk = CAM_PIN_PCLK;
  c.xclk_freq_hz = 20000000;
  c.ledc_timer = LEDC_TIMER_0;
  c.ledc_channel = LEDC_CHANNEL_0;
  c.pixel_format = PIXFORMAT_GRAYSCALE;  // model input is grayscale; avoids JPEG decode
  c.frame_size = FRAMESIZE_QVGA;         // 320x240
  c.jpeg_quality = 12;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;      // always the freshest frame

  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("[ERROR] esp_camera_init failed: %s (0x%x)\n", esp_err_to_name(err), err);
    return false;
  }
  sensor_t* s = esp_camera_sensor_get();
  if (s) {
    Serial.printf("Camera sensor PID=0x%04x\n", s->id.PID);
  }
  s_ready = true;
  return true;
}

bool ready() { return s_ready; }
uint32_t failures() { return s_failures; }

bool captureGray96(uint8_t* out, uint32_t* capture_ms) {
  if (!s_ready || out == NULL) return false;
  const uint32_t t0 = millis();
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    s_failures++;
    return false;
  }
  if (fb->format != PIXFORMAT_GRAYSCALE || fb->width < kOutWidth || fb->height < kOutHeight) {
    esp_camera_fb_return(fb);
    s_failures++;
    return false;
  }
  const int W = fb->width, H = fb->height;
  const int side = W < H ? W : H;           // fit shortest: centered square crop
  const int x0 = (W - side) / 2, y0 = (H - side) / 2;
  const uint8_t* src = fb->buf;

  // bilinear resize side x side -> 96 x 96 (fixed point 16.16)
  const uint32_t scale = ((uint32_t)(side - 1) << 16) / (kOutWidth - 1);
  for (int oy = 0; oy < kOutHeight; ++oy) {
    uint32_t sy = oy * scale;
    int iy = sy >> 16;
    uint32_t fy = sy & 0xffff;
    int iy1 = iy + 1 < side ? iy + 1 : iy;
    const uint8_t* r0 = src + (size_t)(y0 + iy) * W + x0;
    const uint8_t* r1 = src + (size_t)(y0 + iy1) * W + x0;
    for (int ox = 0; ox < kOutWidth; ++ox) {
      uint32_t sx = ox * scale;
      int ix = sx >> 16;
      uint32_t fx = sx & 0xffff;
      int ix1 = ix + 1 < side ? ix + 1 : ix;
      uint32_t top = r0[ix] * (65536 - fx) + r0[ix1] * fx;
      uint32_t bot = r1[ix] * (65536 - fx) + r1[ix1] * fx;
      uint64_t v = ((uint64_t)(top >> 8) * (65536 - fy) + (uint64_t)(bot >> 8) * fy) >> 24;
      out[oy * kOutWidth + ox] = (uint8_t)(v > 255 ? 255 : v);
    }
  }
  esp_camera_fb_return(fb);
  if (capture_ms) *capture_ms = millis() - t0;
  return true;
}

}  // namespace camera
}  // namespace bedlight
