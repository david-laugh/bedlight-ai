// BedLight AI — HTTP multipart/form-data WAV upload (streamed from PSRAM, no extra copy).
#pragma once

#include <Arduino.h>

namespace bedlight {
namespace http {

struct Field {
  const char* name;
  String value;
};

struct Result {
  bool ok;
  int status;
  uint32_t ms;
  String body;
};

// POST `path` on VOICE_SERVER_HOST:VOICE_SERVER_PORT with text fields + one WAV file part "audio".
Result postWav(const char* path, const Field* fields, size_t field_count, const uint8_t* wav, size_t wav_len,
               const String& filename);

// Minimal JSON string field extraction ("key":"value").
String jsonString(const String& body, const char* key);

String makeUuid();

}  // namespace http
}  // namespace bedlight
