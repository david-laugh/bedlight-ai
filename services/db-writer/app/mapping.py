"""Event validation + mapping to public.device_events rows."""

from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from jsonschema import Draft202012Validator

RUNTIME_TYPES = {
    "VISION_FALL_DETECTED", "VISION_STANDING_DETECTED", "LISTENING_STARTED", "LISTENING_EXTENDED",
    "AUDIO_CHUNK_SENT", "LISTENING_STOPPED", "SYSTEM_ERROR", "LIGHT_STATE_CHANGED",
}


class Validator:
    def __init__(self, contracts_dir: Path):
        load = lambda n: json.loads((Path(contracts_dir) / n).read_text(encoding="utf-8"))  # noqa: E731
        self.runtime = Draft202012Validator(load("device-runtime-event.schema.json"))
        self.envelope = Draft202012Validator(load("event-envelope.schema.json"))

    def errors(self, event: dict) -> list[str]:
        v = self.runtime if str(event.get("schema_version", "")).startswith("1.2.") else self.envelope
        return [e.message for e in v.iter_errors(event)][:3]


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def to_row(event: dict) -> dict:
    """Map a validated event to a device_events row (raw audio is never included)."""
    data = event.get("data") or {}
    et = event["event_type"]
    if "timing" in event:  # v1.0/1.1 envelope
        timing = event.get("timing") or {}
        return {
            "event_id": event["event_id"], "trace_id": event["trace_id"], "source": event["source"],
            "device_id": event["device_id"], "event_type": et, "sequence": event.get("sequence"),
            "schema_version": event["schema_version"], "confidence": _num(data.get("confidence")),
            "inference_ms": _num(timing.get("inference_ms")), "payload": event,
        }
    is_chunk = et == "AUDIO_CHUNK_SENT"
    return {
        "event_id": event["event_id"],
        "trace_id": event["trace_id"],
        "source": event["source"],
        "device_id": event["device_id"],
        "event_type": et,
        "sequence": data.get("sequence") if is_chunk else None,
        "schema_version": event["schema_version"],
        "confidence": _num(event.get("confidence")),
        "inference_ms": None if is_chunk else _num(event.get("inference_ms")),
        "session_id": event.get("session_id"),
        "device_timestamp_ms": event.get("timestamp_ms"),
        "network_ms": _num(data.get("upload_ms")) if is_chunk else None,
        "ok": data.get("ok") if is_chunk else None,
        "payload": event,
    }


class SeenIds:
    """Bounded LRU of event_ids (MQTT QoS 1 may redeliver)."""

    def __init__(self, maxlen: int = 10000):
        self.maxlen = maxlen
        self._d: OrderedDict[str, None] = OrderedDict()

    def add(self, event_id: str) -> bool:
        """True if new, False if already seen."""
        if event_id in self._d:
            self._d.move_to_end(event_id)
            return False
        self._d[event_id] = None
        if len(self._d) > self.maxlen:
            self._d.popitem(last=False)
        return True
