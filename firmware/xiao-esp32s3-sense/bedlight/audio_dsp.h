// BedLight AI — streaming audio DSP helpers (pure C++, host-testable).
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace bedlight {

// One-pole DC blocker + integer gain with int16 saturation.
// State persists across calls, so consecutive 1 s chunks have NO discontinuity at the boundary
// (unlike per-clip mean removal used for dataset clips).
struct StreamProcessor {
  int32_t gain = 1;
  int32_t x1 = 0;
  int32_t y1_q15 = 0;  // previous output in Q15
  uint32_t clipped = 0;

  void reset() { x1 = 0; y1_q15 = 0; clipped = 0; }

  // y[n] = x[n] - x[n-1] + 0.995 * y[n-1]   (cut-off ~13 Hz at 16 kHz)
  void process(int16_t* s, size_t n) {
    const int32_t R_Q15 = 32604;  // 0.995 * 32768
    for (size_t i = 0; i < n; ++i) {
      int32_t x = s[i];
      int32_t y_q15 = ((x - x1) << 15) + (int32_t)(((int64_t)R_Q15 * y1_q15) >> 15);
      x1 = x;
      y1_q15 = y_q15;
      int32_t v = (y_q15 >> 15) * gain;
      if (v > 32767) { v = 32767; clipped++; }
      else if (v < -32768) { v = -32768; clipped++; }
      s[i] = (int16_t)v;
    }
  }
};

}  // namespace bedlight
