// BedLight AI — PDM microphone (I2S0, legacy IDF 4.4 driver) shared by dataset + runtime modes.
#pragma once

#include <Arduino.h>

namespace bedlight {
namespace audio {

static const uint32_t kSampleRate = 16000;
static const uint16_t kChannels = 1;
static const uint16_t kBitsPerSample = 16;
static const uint32_t kBytesPerSecond = kSampleRate * kChannels * (kBitsPerSample / 8);

bool init();                 // install driver once (stopped). Never re-installed afterwards.
bool ready();
bool start(uint32_t warmup_ms);  // i2s_start + discard warm-up samples
void stop();                 // i2s_stop (driver stays installed)
bool running();

// Read exactly `bytes` (or until timeout). Drains the driver event queue and counts RX overflows.
size_t read(uint8_t* dst, size_t bytes, uint32_t timeout_ms);

// DMA RX queue overflows (= audio lost because the reader was too slow).
uint32_t takeOverflows();    // since last call
uint32_t totalOverflows();

// Ownership lock: the runtime capture task and the serial dataset recorder never use I2S at once.
bool lock(uint32_t timeout_ms);
void unlock();

}  // namespace audio
}  // namespace bedlight
