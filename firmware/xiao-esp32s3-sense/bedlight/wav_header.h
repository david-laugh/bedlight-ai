// BedLight AI — WAV header + PCM helpers.
// Pure C++ (no Arduino dependency) so it can be unit-tested on the host:
//   firmware/xiao-esp32s3-sense/tests/test_wav_header.cpp
#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace bedlight {

static const size_t kWavHeaderSize = 44;

inline void put_le16(uint8_t* p, uint16_t v) {
  p[0] = (uint8_t)(v & 0xff);
  p[1] = (uint8_t)((v >> 8) & 0xff);
}

inline void put_le32(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)(v & 0xff);
  p[1] = (uint8_t)((v >> 8) & 0xff);
  p[2] = (uint8_t)((v >> 16) & 0xff);
  p[3] = (uint8_t)((v >> 24) & 0xff);
}

// Writes a canonical 44-byte RIFF/WAVE PCM header (WAVE_FORMAT_PCM = 1).
// All multi-byte fields are little-endian regardless of host byte order.
// Returns false for invalid arguments (data_bytes must be a whole number of frames).
inline bool write_wav_header(uint8_t* out, uint32_t sample_rate, uint16_t channels,
                             uint16_t bits_per_sample, uint32_t data_bytes) {
  if (out == NULL || sample_rate == 0 || channels == 0 || bits_per_sample == 0 ||
      (bits_per_sample % 8) != 0) {
    return false;
  }
  const uint16_t block_align = (uint16_t)(channels * (bits_per_sample / 8));
  const uint32_t byte_rate = sample_rate * block_align;
  if ((data_bytes % block_align) != 0 || data_bytes > 0xFFFFFFFFu - 36u) {
    return false;
  }
  memcpy(out + 0, "RIFF", 4);
  put_le32(out + 4, 36u + data_bytes);
  memcpy(out + 8, "WAVE", 4);
  memcpy(out + 12, "fmt ", 4);
  put_le32(out + 16, 16u);  // fmt chunk size for PCM
  put_le16(out + 20, 1u);   // WAVE_FORMAT_PCM
  put_le16(out + 22, channels);
  put_le32(out + 24, sample_rate);
  put_le32(out + 28, byte_rate);
  put_le16(out + 32, block_align);
  put_le16(out + 34, bits_per_sample);
  memcpy(out + 36, "data", 4);
  put_le32(out + 40, data_bytes);
  return true;
}

struct PcmStats {
  int32_t dc_offset;        // mean removed (0 if remove_dc == false)
  uint32_t clipped_samples; // samples saturated by gain
  int16_t peak_abs;         // after processing
};

// In-place: optional DC offset removal, then integer gain with saturation to int16.
// The XIAO ESP32S3 Sense PDM mic output is quiet; Seeed's example applies a gain too.
inline PcmStats process_pcm16(int16_t* samples, size_t count, int32_t gain, bool remove_dc) {
  PcmStats st = {0, 0, 0};
  if (samples == NULL || count == 0) return st;
  if (remove_dc) {
    int64_t sum = 0;
    for (size_t i = 0; i < count; ++i) sum += samples[i];
    st.dc_offset = (int32_t)(sum / (int64_t)count);
  }
  int32_t peak = 0;
  for (size_t i = 0; i < count; ++i) {
    int32_t v = ((int32_t)samples[i] - st.dc_offset) * gain;
    if (v > 32767) { v = 32767; st.clipped_samples++; }
    else if (v < -32768) { v = -32768; st.clipped_samples++; }
    samples[i] = (int16_t)v;
    int32_t a = v < 0 ? -v : v;
    if (a > peak) peak = a;
  }
  st.peak_abs = (int16_t)(peak > 32767 ? 32767 : peak);
  return st;
}

}  // namespace bedlight
