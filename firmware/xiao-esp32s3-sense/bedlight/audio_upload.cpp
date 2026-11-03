#include "audio_upload.h"

#include <WiFi.h>

#include "config_defaults.h"

namespace bedlight {
namespace http {

String makeUuid() {
  uint8_t b[16];
  for (int i = 0; i < 16; i += 4) {
    uint32_t r = esp_random();
    memcpy(b + i, &r, 4);
  }
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  char s[37];
  snprintf(s, sizeof(s), "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x", b[0], b[1], b[2],
           b[3], b[4], b[5], b[6], b[7], b[8], b[9], b[10], b[11], b[12], b[13], b[14], b[15]);
  return String(s);
}

static bool writeAll(WiFiClient& c, const uint8_t* data, size_t len) {
  size_t sent = 0;
  const uint32_t start = millis();
  while (sent < len) {
    if (!c.connected() || millis() - start > HTTP_TIMEOUT_MS) return false;
    size_t chunk = len - sent > 4096 ? 4096 : len - sent;
    size_t n = c.write(data + sent, chunk);
    if (n == 0) {
      delay(2);
      continue;
    }
    sent += n;
  }
  return true;
}

Result postWav(const char* path, const Field* fields, size_t field_count, const uint8_t* wav, size_t wav_len,
               const String& filename) {
  Result r = {false, 0, 0, ""};
  const String boundary = "----bedlight" + String((uint32_t)esp_random(), HEX);
  String head;
  head.reserve(1024);
  for (size_t i = 0; i < field_count; ++i) {
    head += "--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + String(fields[i].name) + "\"\r\n\r\n";
    head += fields[i].value + "\r\n";
  }
  head += "--" + boundary + "\r\n";
  head += "Content-Disposition: form-data; name=\"audio\"; filename=\"" + filename + "\"\r\n";
  head += "Content-Type: audio/wav\r\n\r\n";
  const String tail = "\r\n--" + boundary + "--\r\n";
  const size_t content_length = head.length() + wav_len + tail.length();

  WiFiClient client;
  client.setTimeout(HTTP_TIMEOUT_MS / 1000);
  const uint32_t t0 = millis();
  if (!client.connect(VOICE_SERVER_HOST, VOICE_SERVER_PORT, HTTP_TIMEOUT_MS)) {
    r.ms = millis() - t0;
    return r;
  }
  String req;
  req.reserve(320);
  req += String("POST ") + path + " HTTP/1.1\r\n";
  req += "Host: " + String(VOICE_SERVER_HOST) + ":" + String(VOICE_SERVER_PORT) + "\r\n";
  req += "User-Agent: bedlight-xiao/" FIRMWARE_VERSION "\r\n";
  req += "Content-Type: multipart/form-data; boundary=" + boundary + "\r\n";
  req += "Content-Length: " + String((unsigned)content_length) + "\r\n";
  if (strlen(VOICE_UPLOAD_TOKEN) > 0) req += "X-Device-Token: " VOICE_UPLOAD_TOKEN "\r\n";
  req += "Connection: close\r\n\r\n";

  bool sent = writeAll(client, (const uint8_t*)req.c_str(), req.length()) &&
              writeAll(client, (const uint8_t*)head.c_str(), head.length()) && writeAll(client, wav, wav_len) &&
              writeAll(client, (const uint8_t*)tail.c_str(), tail.length());
  if (!sent) {
    client.stop();
    r.ms = millis() - t0;
    return r;
  }
  const uint32_t wait0 = millis();
  while (!client.available() && client.connected() && millis() - wait0 < HTTP_TIMEOUT_MS) delay(2);
  String status = client.readStringUntil('\n');
  int sp = status.indexOf(' ');
  r.status = sp > 0 ? status.substring(sp + 1, sp + 4).toInt() : 0;
  while (client.connected() || client.available()) {
    String line = client.readStringUntil('\n');
    if (line == "\r" || line.length() == 0) break;
  }
  const uint32_t body0 = millis();
  while ((client.connected() || client.available()) && r.body.length() < 2048 && millis() - body0 < HTTP_TIMEOUT_MS) {
    if (client.available()) r.body += (char)client.read();
    else delay(1);
  }
  client.stop();
  r.ms = millis() - t0;
  r.ok = r.status == 200 || r.status == 201;
  return r;
}

String jsonString(const String& body, const char* key) {
  String needle = String("\"") + key + "\":\"";
  int i = body.indexOf(needle);
  if (i < 0) return "";
  i += needle.length();
  int j = body.indexOf('"', i);
  return j > i ? body.substring(i, j) : "";
}

}  // namespace http
}  // namespace bedlight
