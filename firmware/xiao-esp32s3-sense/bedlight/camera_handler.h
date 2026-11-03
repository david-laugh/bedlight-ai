// BedLight AI — XIAO ESP32S3 Sense camera (OV2640/OV3660) -> 96x96 grayscale model input.
#pragma once

#include <Arduino.h>

namespace bedlight {
namespace camera {

static const int kOutWidth = 96;
static const int kOutHeight = 96;

bool init();
bool ready();

// Capture one frame (grayscale QVGA), center-crop to a square (Edge Impulse "fit shortest"),
// bilinear-resize to 96x96 into `out` (kOutWidth*kOutHeight bytes).
bool captureGray96(uint8_t* out, uint32_t* capture_ms);

uint32_t failures();

}  // namespace camera
}  // namespace bedlight
