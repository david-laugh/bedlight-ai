// BedLight AI — Edge Impulse FOMO (Fall / Standing) wrapper.
#pragma once

#include <Arduino.h>

#include "state_manager.h"

namespace bedlight {
namespace vision {

struct Frame {
  VisionResult result;     // highest Fall / Standing confidence + counts (>= threshold)
  uint16_t boxes;          // all boxes returned by FOMO (>= model threshold 0.5)
  int16_t fall_x, fall_y, fall_w, fall_h;  // best Fall box (96x96 coords), -1 if none
  uint32_t capture_ms;
  int32_t dsp_ms;
  int32_t classification_ms;
  uint32_t total_ms;
};

bool available();        // compiled with the Edge Impulse library
bool init();
bool run(const uint8_t* gray96, float threshold, Frame* out, int* ei_error);

const char* modelName();
int projectId();
int deployVersion();
const char* labels();    // "Fall,Standing"

}  // namespace vision
}  // namespace bedlight
