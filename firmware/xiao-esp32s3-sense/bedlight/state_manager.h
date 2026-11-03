// BedLight AI — Fall-triggered LISTENING state machine.
// Pure C++ (no Arduino / FreeRTOS) so it can be unit-tested on the host:
//   firmware/xiao-esp32s3-sense/tests/test_state_manager.cpp
//
// Rules
//   WATCHING  --(Fall conf >= threshold)-->  LISTENING          (STARTED)
//   LISTENING --(Fall conf >= threshold)-->  LISTENING          (EXTENDED: lastFall = now)
//   LISTENING --(now - lastFall >= hold)-->  WATCHING           (STOPPED)
//   Standing / no detection never stops LISTENING by itself.
// All time arithmetic uses uint32_t subtraction, so millis() wrap-around (~49.7 days) is safe.
#pragma once

#include <stdint.h>

namespace bedlight {

enum class ListenState : uint8_t { WATCHING = 0, LISTENING = 1 };
enum class Transition : uint8_t { NONE = 0, STARTED, EXTENDED, STOPPED };
enum class VisionLabel : uint8_t { NONE = 0, FALL, STANDING };

struct VisionResult {
  float fall_confidence;      // highest Fall box confidence in the frame (0 if none)
  float standing_confidence;  // highest Standing box confidence (0 if none)
  uint16_t fall_count;        // Fall boxes >= threshold
  uint16_t standing_count;    // Standing boxes >= threshold
};

class ListeningStateMachine {
 public:
  ListeningStateMachine(uint32_t hold_ms, float threshold)
      : hold_ms_(hold_ms), threshold_(threshold) {}

  // Feed one vision result at time `now_ms`. Returns the listening transition (if any).
  Transition onVision(uint32_t now_ms, const VisionResult& r) {
    Transition t = Transition::NONE;
    if (r.fall_confidence >= threshold_) {
      last_fall_ms_ = now_ms;
      if (state_ == ListenState::WATCHING) {
        state_ = ListenState::LISTENING;
        session_start_ms_ = now_ms;
        extensions_ = 0;
        sessions_++;
        t = Transition::STARTED;
      } else {
        extensions_++;
        t = Transition::EXTENDED;
      }
    } else {
      t = tick(now_ms);
    }
    updateLabel(r);
    return t;
  }

  // Call periodically (also when no frame arrives) to expire the hold window.
  Transition tick(uint32_t now_ms) {
    if (state_ == ListenState::LISTENING && (uint32_t)(now_ms - last_fall_ms_) >= hold_ms_) {
      state_ = ListenState::WATCHING;
      last_session_duration_ms_ = now_ms - session_start_ms_;
      return Transition::STOPPED;
    }
    return Transition::NONE;
  }

  // Edge of the dominant vision label since the previous frame (for event rate limiting).
  // Returns NONE when unchanged.
  VisionLabel takeLabelEdge() {
    VisionLabel e = label_edge_;
    label_edge_ = VisionLabel::NONE;
    return e;
  }

  bool listening() const { return state_ == ListenState::LISTENING; }
  ListenState state() const { return state_; }
  uint32_t lastFallMs() const { return last_fall_ms_; }
  uint32_t sessionStartMs() const { return session_start_ms_; }
  uint32_t listenUntilMs() const { return last_fall_ms_ + hold_ms_; }
  uint32_t remainingMs(uint32_t now_ms) const {
    if (state_ != ListenState::LISTENING) return 0;
    uint32_t elapsed = now_ms - last_fall_ms_;
    return elapsed >= hold_ms_ ? 0 : hold_ms_ - elapsed;
  }
  uint32_t extensions() const { return extensions_; }
  uint32_t sessions() const { return sessions_; }
  uint32_t lastSessionDurationMs() const { return last_session_duration_ms_; }
  uint32_t holdMs() const { return hold_ms_; }
  float threshold() const { return threshold_; }
  VisionLabel currentLabel() const { return label_; }

 private:
  void updateLabel(const VisionResult& r) {
    VisionLabel now = VisionLabel::NONE;
    if (r.fall_confidence >= threshold_) now = VisionLabel::FALL;
    else if (r.standing_confidence >= threshold_) now = VisionLabel::STANDING;
    if (now != label_) {
      label_ = now;
      if (now != VisionLabel::NONE) label_edge_ = now;
    }
  }

  uint32_t hold_ms_;
  float threshold_;
  ListenState state_ = ListenState::WATCHING;
  uint32_t last_fall_ms_ = 0;
  uint32_t session_start_ms_ = 0;
  uint32_t last_session_duration_ms_ = 0;
  uint32_t extensions_ = 0;
  uint32_t sessions_ = 0;
  VisionLabel label_ = VisionLabel::NONE;
  VisionLabel label_edge_ = VisionLabel::NONE;
};

// Simple time-based rate limiter (wrap-safe) for LISTENING_EXTENDED events.
class RateLimiter {
 public:
  explicit RateLimiter(uint32_t interval_ms) : interval_ms_(interval_ms) {}
  bool allow(uint32_t now_ms) {
    if (!armed_ || (uint32_t)(now_ms - last_ms_) >= interval_ms_) {
      armed_ = true;
      last_ms_ = now_ms;
      return true;
    }
    return false;
  }
  void reset() { armed_ = false; }

 private:
  uint32_t interval_ms_;
  uint32_t last_ms_ = 0;
  bool armed_ = false;
};

}  // namespace bedlight
