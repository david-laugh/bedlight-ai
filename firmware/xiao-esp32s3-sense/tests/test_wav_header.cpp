// Host unit test for bedlight/wav_header.h (built with g++ in firmware-ci).
//   g++ -std=c++17 -Wall -Wextra -Werror -I../bedlight test_wav_header.cpp -o test_wav_header
//   ./test_wav_header out.wav   -> writes a SYNTHETIC 2 s 16 kHz WAV that scripts/check_wav.py re-validates.
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include "wav_header.h"

static int failures = 0;
#define CHECK(cond)                                                     \
  do {                                                                  \
    if (!(cond)) {                                                      \
      std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
      ++failures;                                                       \
    }                                                                   \
  } while (0)

static uint32_t rd32(const uint8_t* p) { return p[0] | (p[1] << 8) | (p[2] << 16) | ((uint32_t)p[3] << 24); }
static uint16_t rd16(const uint8_t* p) { return (uint16_t)(p[0] | (p[1] << 8)); }

int main(int argc, char** argv) {
  using namespace bedlight;
  const uint32_t sr = 16000, samples = 32000, data = samples * 2;
  std::vector<uint8_t> wav(kWavHeaderSize + data);

  CHECK(write_wav_header(wav.data(), sr, 1, 16, data));
  CHECK(std::memcmp(wav.data(), "RIFF", 4) == 0);
  CHECK(rd32(&wav[4]) == 36 + data);
  CHECK(std::memcmp(&wav[8], "WAVE", 4) == 0);
  CHECK(std::memcmp(&wav[12], "fmt ", 4) == 0);
  CHECK(rd32(&wav[16]) == 16);
  CHECK(rd16(&wav[20]) == 1);        // PCM
  CHECK(rd16(&wav[22]) == 1);        // mono
  CHECK(rd32(&wav[24]) == 16000);    // sample rate
  CHECK(rd32(&wav[28]) == 32000);    // byte rate
  CHECK(rd16(&wav[32]) == 2);        // block align
  CHECK(rd16(&wav[34]) == 16);       // bits
  CHECK(std::memcmp(&wav[36], "data", 4) == 0);
  CHECK(rd32(&wav[40]) == data);

  // invalid arguments
  CHECK(!write_wav_header(nullptr, sr, 1, 16, data));
  CHECK(!write_wav_header(wav.data(), sr, 1, 16, 3));   // not a whole frame
  CHECK(!write_wav_header(wav.data(), sr, 0, 16, data));
  CHECK(!write_wav_header(wav.data(), 0, 1, 16, data));
  CHECK(!write_wav_header(wav.data(), sr, 1, 12, data));

  // process_pcm16: DC removal + saturating gain
  {
    std::vector<int16_t> s = {1100, 900, 1100, 900};  // mean 1000
    PcmStats st = process_pcm16(s.data(), s.size(), 4, true);
    CHECK(st.dc_offset == 1000);
    CHECK(s[0] == 400 && s[1] == -400);
    CHECK(st.clipped_samples == 0);
    CHECK(st.peak_abs == 400);
  }
  {
    std::vector<int16_t> s = {20000, -20000, 100, -100};
    PcmStats st = process_pcm16(s.data(), s.size(), 4, false);
    CHECK(s[0] == 32767 && s[1] == -32768);
    CHECK(st.clipped_samples == 2);
  }

  // Synthetic sample file for cross-validation by scripts/check_wav.py (NOT training data).
  if (argc > 1) {
    int16_t* pcm = reinterpret_cast<int16_t*>(wav.data() + kWavHeaderSize);
    for (uint32_t i = 0; i < samples; ++i) {
      double v = 0.25 * std::sin(2.0 * M_PI * 440.0 * i / sr) * 32767.0;
      int16_t s = (int16_t)v;
      uint8_t* p = reinterpret_cast<uint8_t*>(&pcm[i]);
      p[0] = (uint8_t)(s & 0xff);  // explicit little-endian
      p[1] = (uint8_t)((s >> 8) & 0xff);
    }
    FILE* f = std::fopen(argv[1], "wb");
    CHECK(f != nullptr);
    if (f) {
      CHECK(std::fwrite(wav.data(), 1, wav.size(), f) == wav.size());
      std::fclose(f);
    }
  }

  if (failures) {
    std::fprintf(stderr, "%d check(s) failed\n", failures);
    return 1;
  }
  std::printf("wav_header tests passed\n");
  return 0;
}
