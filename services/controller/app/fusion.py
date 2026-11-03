"""Light control decision: voice keyword + lying (Fall) session -> light OFF.

Rules (all must hold):
  1. light is currently ON
  2. the keyword event belongs to the device's current LISTENING session, or to its most recent session
     that ended at most RECENT_SESSION_S ago (inference runs after upload, so it may arrive after the
     5 s hold expired)
  3. source is voice-service (or mock for pipeline tests)
Every decision (accepted or rejected) is recorded with a reason for the simulator/logs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

RECENT_SESSION_S = 15.0
ACCEPTED_SOURCES = {"voice-service", "mock"}


@dataclass
class Decision:
    accepted: bool
    reason: str
    event_id: str
    session_id: str | None
    device_id: str
    confidence: float | None
    decided_at_ms: int
    keyword_to_decision_ms: int | None

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def decide(event: dict, light_on: bool, device: dict | None, now_mono: float, now_ms: int | None = None) -> Decision:
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    sid = event.get("session_id")
    ts = event.get("timestamp_ms")
    base = dict(event_id=event.get("event_id", ""), session_id=sid, device_id=event.get("device_id", ""),
                confidence=event.get("confidence"), decided_at_ms=now_ms,
                keyword_to_decision_ms=(now_ms - ts) if isinstance(ts, int) else None)
    if event.get("source") not in ACCEPTED_SOURCES:
        return Decision(False, f"source {event.get('source')!r} not allowed", **base)
    if not light_on:
        return Decision(False, "light already off", **base)
    if device is None:
        return Decision(False, "unknown device", **base)
    lst = device["listening"]
    if lst["active"] and lst["session_id"] == sid:
        return Decision(True, "keyword during active lying session", **base)
    last = device.get("last_session") or {}
    if last.get("session_id") == sid and last.get("ended_mono") is not None \
            and now_mono - last["ended_mono"] <= RECENT_SESSION_S:
        return Decision(True, "keyword from lying session that just ended", **base)
    return Decision(False, "no matching lying (Fall) session", **base)
