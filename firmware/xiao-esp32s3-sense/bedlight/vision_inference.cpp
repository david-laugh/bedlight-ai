#include "vision_inference.h"

#include "config_defaults.h"

#if BEDLIGHT_VISION_ENABLED
#include <Fall_detection_system_for_workers_in_industry_inferencing.h>

#if !defined(EI_CLASSIFIER_OBJECT_DETECTION) || EI_CLASSIFIER_OBJECT_DETECTION != 1
#error "Edge Impulse library is not an object detection (FOMO) model"
#endif
#if EI_CLASSIFIER_INPUT_WIDTH != 96 || EI_CLASSIFIER_INPUT_HEIGHT != 96
#error "Expected a 96x96 FOMO model"
#endif
#endif

#include <string.h>

namespace bedlight {
namespace vision {

#if BEDLIGHT_VISION_ENABLED

static const uint8_t* s_img = NULL;

// Edge Impulse image DSP expects packed RGB888 per pixel; grayscale -> R=G=B.
static int getData(size_t offset, size_t length, float* out_ptr) {
  for (size_t i = 0; i < length; ++i) {
    uint32_t g = s_img[offset + i];
    out_ptr[i] = (float)((g << 16) | (g << 8) | g);
  }
  return 0;
}

bool available() { return true; }

bool init() {
  Serial.printf("Vision model: %s (project %d, deploy v%d) %dx%d, labels:", EI_CLASSIFIER_PROJECT_NAME,
                EI_CLASSIFIER_PROJECT_ID, EI_CLASSIFIER_PROJECT_DEPLOY_VERSION, EI_CLASSIFIER_INPUT_WIDTH,
                EI_CLASSIFIER_INPUT_HEIGHT);
  for (size_t i = 0; i < EI_CLASSIFIER_LABEL_COUNT; ++i) Serial.printf(" %s", ei_classifier_inferencing_categories[i]);
  Serial.println();
  return true;
}

bool run(const uint8_t* gray96, float threshold, Frame* f, int* ei_error) {
  memset(f, 0, sizeof(*f));
  f->fall_x = f->fall_y = f->fall_w = f->fall_h = -1;
  s_img = gray96;
  signal_t signal;
  signal.total_length = EI_CLASSIFIER_INPUT_WIDTH * EI_CLASSIFIER_INPUT_HEIGHT;
  signal.get_data = &getData;

  ei_impulse_result_t result = {};
  const uint32_t t0 = millis();
  EI_IMPULSE_ERROR err = run_classifier(&signal, &result, false);
  f->total_ms = millis() - t0;
  if (ei_error) *ei_error = (int)err;
  if (err != EI_IMPULSE_OK) return false;

  f->dsp_ms = result.timing.dsp;
  f->classification_ms = result.timing.classification;
  for (uint32_t i = 0; i < result.bounding_boxes_count; ++i) {
    const ei_impulse_result_bounding_box_t& bb = result.bounding_boxes[i];
    if (bb.value <= 0.0f) continue;
    f->boxes++;
    if (strcmp(bb.label, "Fall") == 0) {
      if (bb.value >= threshold) f->result.fall_count++;
      if (bb.value > f->result.fall_confidence) {
        f->result.fall_confidence = bb.value;
        f->fall_x = bb.x;
        f->fall_y = bb.y;
        f->fall_w = bb.width;
        f->fall_h = bb.height;
      }
    } else if (strcmp(bb.label, "Standing") == 0) {
      if (bb.value >= threshold) f->result.standing_count++;
      if (bb.value > f->result.standing_confidence) f->result.standing_confidence = bb.value;
    }
  }
  return true;
}

const char* modelName() { return EI_CLASSIFIER_PROJECT_NAME; }
int projectId() { return EI_CLASSIFIER_PROJECT_ID; }
int deployVersion() { return EI_CLASSIFIER_PROJECT_DEPLOY_VERSION; }
const char* labels() { return "Fall,Standing"; }

#else  // vision disabled: no model is linked. Never reports detections.

bool available() { return false; }
bool init() { return false; }
bool run(const uint8_t*, float, Frame* f, int* ei_error) {
  memset(f, 0, sizeof(*f));
  if (ei_error) *ei_error = -1;
  return false;
}
const char* modelName() { return "none"; }
int projectId() { return 0; }
int deployVersion() { return 0; }
const char* labels() { return ""; }

#endif

}  // namespace vision
}  // namespace bedlight
