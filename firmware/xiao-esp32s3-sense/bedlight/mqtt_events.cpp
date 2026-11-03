#include "mqtt_events.h"

#include <mqtt_client.h>  // esp-mqtt (ESP-IDF)
#include <sys/time.h>

#include "audio_upload.h"
#include "config_defaults.h"
#include "vision_inference.h"

namespace bedlight {
namespace mqtt {

static esp_mqtt_client_handle_t s_client = NULL;
static volatile bool s_connected = false;
static volatile uint32_t s_dropped = 0;
static volatile uint32_t s_published = 0;
static char s_events_topic[96];
static char s_status_topic[96];
static char s_uri[96];
static char s_lwt[160];
static char s_light_topic[96];
static volatile int s_light = -1;
static volatile uint32_t s_light_updates = 0;

// Minimal parse of {"light_on": true|false, ...} (no JSON library dependency).
static int parseLightOn(const char* data, int len) {
  const char* key = "\"light_on\"";
  const int klen = 10;
  for (int i = 0; i + klen < len; ++i) {
    if (memcmp(data + i, key, klen) != 0) continue;
    int j = i + klen;
    while (j < len && (data[j] == ' ' || data[j] == ':')) j++;
    if (j + 4 <= len && memcmp(data + j, "true", 4) == 0) return 1;
    if (j + 5 <= len && memcmp(data + j, "false", 5) == 0) return 0;
    return -1;
  }
  return -1;
}

static void onEvent(void*, esp_event_base_t, int32_t event_id, void* event_data) {
  esp_mqtt_event_handle_t ev = (esp_mqtt_event_handle_t)event_data;
  switch ((esp_mqtt_event_id_t)event_id) {
    case MQTT_EVENT_CONNECTED:
      s_connected = true;
      esp_mqtt_client_subscribe(s_client, s_light_topic, 1);  // retained state arrives right after
      Serial.printf("MQTT connected, subscribed %s\n", s_light_topic);
      break;
    case MQTT_EVENT_DATA:
      if (ev && ev->current_data_offset == 0 && ev->data_len == ev->total_data_len &&
          ev->topic_len == (int)strlen(s_light_topic) && memcmp(ev->topic, s_light_topic, ev->topic_len) == 0) {
        int v = parseLightOn(ev->data, ev->data_len);
        if (v >= 0) {
          if (v != s_light) Serial.printf("[LIGHT] virtual light %s\n", v ? "ON" : "OFF");
          s_light = v;
          s_light_updates++;
        }
      }
      break;
    case MQTT_EVENT_DISCONNECTED:
      if (s_connected) Serial.println("MQTT disconnected");
      s_connected = false;
      break;
    default:
      break;
  }
}

bool begin() {
  if (s_client) return true;
  snprintf(s_events_topic, sizeof(s_events_topic), "bedlight/v1/devices/%s/events", DEVICE_ID);
  snprintf(s_status_topic, sizeof(s_status_topic), "bedlight/v1/devices/%s/status", DEVICE_ID);
  snprintf(s_light_topic, sizeof(s_light_topic), "bedlight/v1/lights/%s/state", ROOM_ID);
  snprintf(s_uri, sizeof(s_uri), "mqtt://%s:%d", MQTT_HOST, MQTT_PORT);
  snprintf(s_lwt, sizeof(s_lwt),
           "{\"schema_version\":\"1.2.0\",\"source\":\"device\",\"device_id\":\"%s\",\"state\":\"offline\"}",
           DEVICE_ID);

  esp_mqtt_client_config_t cfg = {};
  cfg.uri = s_uri;
  cfg.client_id = DEVICE_ID;
  if (strlen(MQTT_USERNAME)) cfg.username = MQTT_USERNAME;
  if (strlen(MQTT_PASSWORD)) cfg.password = MQTT_PASSWORD;
  cfg.lwt_topic = s_status_topic;
  cfg.lwt_msg = s_lwt;
  cfg.lwt_qos = 1;
  cfg.lwt_retain = 1;
  cfg.keepalive = 15;
  cfg.reconnect_timeout_ms = 3000;
  cfg.out_buffer_size = 2048;
  s_client = esp_mqtt_client_init(&cfg);
  if (!s_client) return false;
  esp_mqtt_client_register_event(s_client, (esp_mqtt_event_id_t)ESP_EVENT_ANY_ID, onEvent, NULL);
  return esp_mqtt_client_start(s_client) == ESP_OK;
}

bool connected() { return s_connected; }
int lightState() { return s_light; }
uint32_t lightStateUpdates() { return s_light_updates; }
uint32_t dropped() { return s_dropped; }
uint32_t published() { return s_published; }

static bool publish(const char* topic, const char* json, int retain) {
  if (!s_client || !s_connected) {
    s_dropped++;
    return false;
  }
  // enqueue = non-blocking; actual network I/O happens in the esp-mqtt task.
  int id = esp_mqtt_client_enqueue(s_client, topic, json, 0, 1, retain, true);
  if (id < 0) {
    s_dropped++;
    return false;
  }
  s_published++;
  return true;
}

bool publishEvent(const char* json) { return publish(s_events_topic, json, 0); }
bool publishStatus(const char* json) { return publish(s_status_topic, json, 1); }

int64_t epochMs() {
  struct timeval tv;
  gettimeofday(&tv, NULL);
  if (tv.tv_sec < 1700000000) return -1;  // not synced
  return (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

static void fmtNum(char* b, size_t n, float v, int decimals) {
  if (v < 0) snprintf(b, n, "null");
  else snprintf(b, n, "%.*f", decimals, (double)v);
}

size_t buildEvent(char* out, size_t n, const char* event_type, const char* trace_id, const char* session_id,
                  bool with_model, float confidence, float inference_ms, const char* data_json) {
  String eid = http::makeUuid();
  String tid = trace_id ? String(trace_id) : http::makeUuid();
  char conf[16], inf[16], ts[24], sess[48], model[200];
  fmtNum(conf, sizeof(conf), confidence, 4);
  fmtNum(inf, sizeof(inf), inference_ms, 1);
  int64_t e = epochMs();
  if (e < 0) snprintf(ts, sizeof(ts), "null");
  else snprintf(ts, sizeof(ts), "%lld", (long long)e);
  if (session_id) snprintf(sess, sizeof(sess), "\"%s\"", session_id);
  else snprintf(sess, sizeof(sess), "null");
  if (with_model && vision::available()) {
    snprintf(model, sizeof(model),
             "{\"name\":\"%s\",\"version\":\"ei-%d-v%d\",\"task\":\"object-detection\",\"runtime\":\"edge-impulse\"}",
             VISION_MODEL_NAME, vision::projectId(), vision::deployVersion());
  } else {
    snprintf(model, sizeof(model), "null");
  }
  int len = snprintf(out, n,
                     "{\"schema_version\":\"1.2.0\",\"event_id\":\"%s\",\"trace_id\":\"%s\",\"source\":\"device\","
                     "\"device_id\":\"%s\",\"event_type\":\"%s\",\"model\":%s,\"confidence\":%s,"
                     "\"timestamp_ms\":%s,\"device_uptime_ms\":%lu,\"inference_ms\":%s,\"session_id\":%s,"
                     "\"data\":%s}",
                     eid.c_str(), tid.c_str(), DEVICE_ID, event_type, model, conf, ts, (unsigned long)millis(), inf,
                     sess, data_json ? data_json : "{}");
  return len < 0 ? 0 : (size_t)len;
}

}  // namespace mqtt
}  // namespace bedlight
