// Host unit tests for bedlight/state_manager.h — scenarios A–D from the requirements + millis() wrap.
//   g++ -std=c++17 -Wall -Wextra -Werror -I../bedlight test_state_manager.cpp -o t && ./t
#include <cstdio>
#include <vector>

#include "state_manager.h"

using namespace bedlight;

static int failures = 0;
#define CHECK(cond)                                                          \
  do {                                                                       \
    if (!(cond)) {                                                           \
      std::fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);   \
      ++failures;                                                            \
    }                                                                        \
  } while (0)

static const VisionResult FALL{0.90f, 0.0f, 1, 0};
static const VisionResult STANDING{0.0f, 0.90f, 0, 1};
static const VisionResult NOTHING{0.0f, 0.0f, 0, 0};
static const VisionResult WEAK_FALL{0.60f, 0.0f, 0, 0};

// Frames every 200 ms; returns the ms at which LISTENING stopped (or -1).
static long run(ListeningStateMachine& sm, uint32_t t0, uint32_t until, const VisionResult& (*at)(uint32_t),
                int* starts, int* extends) {
  long stopped = -1;
  for (uint32_t dt = 0; dt <= until; dt += 100) {
    uint32_t now = t0 + dt;
    Transition tr = (dt % 200 == 0) ? sm.onVision(now, at(dt)) : sm.tick(now);
    if (tr == Transition::STARTED) (*starts)++;
    if (tr == Transition::EXTENDED) (*extends)++;
    if (tr == Transition::STOPPED && stopped < 0) stopped = (long)dt;
  }
  return stopped;
}

static void scenario_A(uint32_t t0) {
  // 0 s Fall -> ON, 2 s Standing, no more Fall -> OFF at 5 s
  ListeningStateMachine sm(5000, 0.75f);
  int s = 0, e = 0;
  long stop = run(sm, t0, 9000, [](uint32_t dt) -> const VisionResult& {
    if (dt == 0) return FALL;
    if (dt >= 2000) return STANDING;
    return NOTHING;
  }, &s, &e);
  CHECK(s == 1);
  CHECK(e == 0);
  CHECK(stop == 5000);
  CHECK(!sm.listening());
}

static void scenario_A_boundary() {
  ListeningStateMachine sm(5000, 0.75f);
  CHECK(sm.onVision(1000, FALL) == Transition::STARTED);
  CHECK(sm.onVision(3000, STANDING) == Transition::NONE);  // Standing does not stop
  CHECK(sm.listening());
  CHECK(sm.tick(5999) == Transition::NONE);
  CHECK(sm.remainingMs(5999) == 1);
  CHECK(sm.tick(6000) == Transition::STOPPED);
  CHECK(sm.remainingMs(6000) == 0);
}

static void scenario_B(uint32_t t0) {
  // 0 s Fall, 3 s Fall (extend), 6 s Standing (keep), OFF at 8 s
  ListeningStateMachine sm(5000, 0.75f);
  int s = 0, e = 0;
  long stop = run(sm, t0, 12000, [](uint32_t dt) -> const VisionResult& {
    if (dt == 0 || dt == 3000) return FALL;
    if (dt >= 6000) return STANDING;
    return NOTHING;
  }, &s, &e);
  CHECK(s == 1);
  CHECK(e == 1);
  CHECK(stop == 8000);
}

static void scenario_C() {
  // Fall below threshold never activates audio
  ListeningStateMachine sm(5000, 0.75f);
  for (uint32_t t = 0; t < 10000; t += 200) CHECK(sm.onVision(t, WEAK_FALL) == Transition::NONE);
  CHECK(!sm.listening());
  CHECK(sm.sessions() == 0);
  // exactly at threshold counts as detection
  CHECK(sm.onVision(10000, VisionResult{0.75f, 0, 1, 0}) == Transition::STARTED);
}

static void scenario_D() {
  // Already listening: repeated Fall only extends (no second STARTED -> no I2S re-init)
  ListeningStateMachine sm(5000, 0.75f);
  int starts = 0, extends = 0;
  for (uint32_t t = 0; t <= 20000; t += 200) {
    Transition tr = sm.onVision(t, FALL);
    if (tr == Transition::STARTED) starts++;
    if (tr == Transition::EXTENDED) extends++;
  }
  CHECK(starts == 1);
  CHECK(extends == 100);
  CHECK(sm.listening());
  CHECK(sm.extensions() == 100);
  CHECK(sm.sessions() == 1);
  CHECK(sm.lastFallMs() == 20000);
  CHECK(sm.tick(24999) == Transition::NONE);
  CHECK(sm.tick(25000) == Transition::STOPPED);
  CHECK(sm.lastSessionDurationMs() == 25000);
}

static void millis_wraparound() {
  // Fall 1 s before uint32 wrap; hold must still be 5 s.
  const uint32_t t0 = 0xFFFFFFFFu - 999u;
  ListeningStateMachine sm(5000, 0.75f);
  CHECK(sm.onVision(t0, FALL) == Transition::STARTED);
  CHECK(sm.tick(t0 + 3000u) == Transition::NONE);  // wrapped value is small
  CHECK(sm.listening());
  CHECK(sm.remainingMs(t0 + 3000u) == 2000);
  CHECK(sm.tick(t0 + 4999u) == Transition::NONE);
  CHECK(sm.tick(t0 + 5000u) == Transition::STOPPED);
}

static void label_edges() {
  ListeningStateMachine sm(5000, 0.75f);
  sm.onVision(0, FALL);
  CHECK(sm.takeLabelEdge() == VisionLabel::FALL);
  sm.onVision(200, FALL);
  CHECK(sm.takeLabelEdge() == VisionLabel::NONE);  // unchanged -> no event flood
  sm.onVision(400, STANDING);
  CHECK(sm.takeLabelEdge() == VisionLabel::STANDING);
  sm.onVision(600, NOTHING);
  CHECK(sm.takeLabelEdge() == VisionLabel::NONE);
  sm.onVision(800, STANDING);
  CHECK(sm.takeLabelEdge() == VisionLabel::STANDING);  // re-appeared after absence
}

static void force_stop_light_off() {
  // Light switched OFF while listening -> immediate stop, reason FORCED; no effect while WATCHING.
  ListeningStateMachine sm(5000, 0.75f);
  CHECK(sm.forceStop(0) == Transition::NONE);
  CHECK(sm.onVision(1000, FALL) == Transition::STARTED);
  CHECK(sm.onVision(2000, FALL) == Transition::EXTENDED);
  CHECK(sm.forceStop(2500) == Transition::STOPPED);
  CHECK(!sm.listening());
  CHECK(sm.lastStopReason() == StopReason::FORCED);
  CHECK(sm.lastSessionDurationMs() == 1500);
  CHECK(sm.tick(10000) == Transition::NONE);  // already stopped
  // normal expiry records HOLD_EXPIRED
  CHECK(sm.onVision(20000, FALL) == Transition::STARTED);
  CHECK(sm.tick(25000) == Transition::STOPPED);
  CHECK(sm.lastStopReason() == StopReason::HOLD_EXPIRED);
  // after resetLabel a resumed Fall produces a new edge
  sm.onVision(26000, FALL);
  sm.takeLabelEdge();
  sm.resetLabel();
  sm.onVision(27000, FALL);
  CHECK(sm.takeLabelEdge() == VisionLabel::FALL);
}

static void rate_limiter() {
  RateLimiter rl(1000);
  CHECK(rl.allow(0));
  CHECK(!rl.allow(500));
  CHECK(rl.allow(1000));
  CHECK(!rl.allow(1999));
  CHECK(rl.allow(0xFFFFFFFFu));  // wrap
  rl.reset();
  CHECK(rl.allow(5));
}

int main() {
  scenario_A(0);
  scenario_A(0xFFFFF000u);  // same scenario across millis() overflow
  scenario_A_boundary();
  scenario_B(0);
  scenario_B(0xFFFFE000u);
  scenario_C();
  scenario_D();
  millis_wraparound();
  label_edges();
  force_stop_light_off();
  rate_limiter();
  if (failures) {
    std::fprintf(stderr, "%d check(s) failed\n", failures);
    return 1;
  }
  std::printf("state_manager tests passed (scenarios A, B, C, D, wrap-around, light-off force stop)\n");
  return 0;
}
