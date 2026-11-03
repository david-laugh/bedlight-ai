"""Device / listening / light state aggregation (pure Python, unit-tested)."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone

OFFLINE_AFTER_S = 15.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SystemState:
    def __init__(self, room_id: str = "bedroom-01", clock=time.monotonic):
        self.clock = clock
        self.room_id = room_id
        self._lock = threading.Lock()
        self.devices: dict[str, dict] = {}
        self.events: deque = deque(maxlen=40)
        self.light = {
            "on": True,
            "updated_at": _now_iso(),
            "updated_at_ms": int(time.time() * 1000),
            "changed_by": "initial",
            "reason": "default (no retained state yet)",
            "auto_control": False,
            "auto_control_reason": "voice keyword model not deployed - automatic light-off is NOT active",
        }
        self.voice: dict = {"model": None, "recent": []}
        self.decisions: deque = deque(maxlen=20)
        self.server_audio: dict = {"reachable": None, "latest_session": None, "checked_at": None}

    # --------------------------------------------------------------- devices
    def _device(self, device_id: str) -> dict:
        d = self.devices.get(device_id)
        if d is None:
            d = {
                "device_id": device_id, "source": None, "online": False, "last_seen_mono": None, "status": None,
                "vision": {"label": None, "fall_confidence": None, "standing_confidence": None,
                           "inference_ms": None, "updated_at": None},
                "listening": {"active": False, "session_id": None, "until_mono": None, "hold_ms": None,
                              "extensions": 0, "started_at": None},
                "last_session": None,
                "audio_device": {"chunks_sent": 0, "chunks_failed": 0, "last_upload_ms": None, "last_ok": None},
            }
            self.devices[device_id] = d
        return d

    def on_status(self, device_id: str, status: dict) -> None:
        with self._lock:
            d = self._device(device_id)
            d["status"] = status
            d["source"] = status.get("source", d["source"])
            d["online"] = status.get("state") == "online"
            d["last_seen_mono"] = self.clock()
            rt = status.get("runtime") or {}
            if "label" in rt:
                d["vision"].update(label=rt.get("label"), fall_confidence=rt.get("fall_confidence"),
                                   standing_confidence=rt.get("standing_confidence"),
                                   inference_ms=rt.get("inference_ms_last"), updated_at=_now_iso())
            if "listening" in rt:
                lst = d["listening"]
                if rt["listening"]:
                    lst["active"] = True
                    lst["session_id"] = rt.get("session_id") or lst["session_id"]
                    lst["until_mono"] = self.clock() + rt.get("remaining_ms", 0) / 1000.0
                    lst["hold_ms"] = rt.get("hold_ms", lst["hold_ms"])
                elif lst["active"]:
                    d["last_session"] = {"session_id": lst["session_id"], "ended_mono": self.clock(), "reason": None}
                    lst["active"] = False
                    lst["until_mono"] = None

    def on_event(self, event: dict) -> None:
        et = event.get("event_type")
        device_id = event.get("device_id", "unknown")
        data = event.get("data") or {}
        with self._lock:
            d = self._device(device_id)
            d["source"] = event.get("source", d["source"])
            d["last_seen_mono"] = self.clock()
            if event.get("source") in ("device", "mock"):
                d["online"] = True
            lst = d["listening"]
            if et in ("VISION_FALL_DETECTED", "VISION_STANDING_DETECTED"):
                d["vision"].update(label=data.get("label"), fall_confidence=data.get("fall_confidence"),
                                   standing_confidence=data.get("standing_confidence"),
                                   inference_ms=event.get("inference_ms"), updated_at=_now_iso())
            elif et in ("LISTENING_STARTED", "LISTENING_EXTENDED"):
                lst["active"] = True
                lst["session_id"] = event.get("session_id")
                lst["until_mono"] = self.clock() + data.get("remaining_ms", data.get("hold_ms", 0)) / 1000.0
                lst["hold_ms"] = data.get("hold_ms")
                if et == "LISTENING_STARTED":
                    lst["extensions"] = 0
                    lst["started_at"] = _now_iso()
                else:
                    lst["extensions"] = data.get("extensions", lst["extensions"])
            elif et == "LISTENING_STOPPED":
                if lst["session_id"]:
                    d["last_session"] = {"session_id": event.get("session_id") or lst["session_id"],
                                         "ended_mono": self.clock(), "reason": data.get("reason")}
                lst["active"] = False
                lst["until_mono"] = None
            elif et == "AUDIO_CHUNK_SENT":
                a = d["audio_device"]
                a["last_ok"] = bool(data.get("ok"))
                a["last_upload_ms"] = data.get("upload_ms")
                a["chunks_sent" if data.get("ok") else "chunks_failed"] += 1
            self.events.appendleft({
                "event_type": et, "source": event.get("source"), "device_id": device_id,
                "confidence": event.get("confidence"), "session_id": event.get("session_id"),
                "received_at": _now_iso(),
            })

    def set_light(self, on: bool, changed_by: str, reason: str = "") -> dict:
        with self._lock:
            self.light.update(on=on, changed_by=changed_by, reason=reason, updated_at=_now_iso(),
                              updated_at_ms=int(time.time() * 1000))
            return dict(self.light)

    def restore_light(self, msg: dict) -> None:
        """Apply the retained light state from the broker (controller restart)."""
        with self._lock:
            self.light.update(on=bool(msg.get("light_on", True)), changed_by=msg.get("source", "retained"),
                              reason=msg.get("reason", ""), updated_at_ms=msg.get("updated_at_ms"),
                              updated_at=_now_iso())

    def device_view(self, device_id: str) -> dict | None:
        with self._lock:
            d = self.devices.get(device_id)
            if d is None:
                return None
            return {"listening": dict(d["listening"]), "last_session": dict(d["last_session"] or {}) or None}

    def set_voice_model(self, model: dict | None) -> None:
        with self._lock:
            self.voice["model"] = model
            loaded = bool(model and model.get("loaded"))
            self.light["auto_control"] = loaded
            self.light["auto_control_reason"] = ("voice keyword model loaded - automatic light-off is active" if loaded
                                                 else "voice keyword model not deployed - automatic light-off is NOT active")

    def add_voice_event(self, event: dict) -> None:
        with self._lock:
            self.voice["recent"] = ([{"session_id": event.get("session_id"), "device_id": event.get("device_id"),
                                      "confidence": event.get("confidence"), "source": event.get("source"),
                                      "received_at": _now_iso()}] + self.voice["recent"])[:10]

    def add_decision(self, decision: dict) -> None:
        with self._lock:
            self.decisions.appendleft(decision)

    def set_server_audio(self, reachable: bool, latest: dict | None) -> None:
        with self._lock:
            self.server_audio = {"reachable": reachable, "latest_session": latest, "checked_at": _now_iso()}

    def any_listening(self) -> list[str]:
        with self._lock:
            return [k for k, d in self.devices.items() if d["listening"]["active"]]

    # -------------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        now = self.clock()
        with self._lock:
            devices = []
            for d in self.devices.values():
                online = d["online"] and d["last_seen_mono"] is not None and now - d["last_seen_mono"] < OFFLINE_AFTER_S
                lst = d["listening"]
                remaining = 0
                if lst["active"] and lst["until_mono"] is not None:
                    remaining = max(0, int((lst["until_mono"] - now) * 1000))
                devices.append({
                    "device_id": d["device_id"], "source": d["source"], "online": online,
                    "seconds_since_seen": None if d["last_seen_mono"] is None else round(now - d["last_seen_mono"], 1),
                    "vision": dict(d["vision"]),
                    "listening": {"active": lst["active"], "session_id": lst["session_id"], "remaining_ms": remaining,
                                  "hold_ms": lst["hold_ms"], "extensions": lst["extensions"],
                                  "started_at": lst["started_at"]},
                    "audio_device": dict(d["audio_device"]),
                    "runtime": (d["status"] or {}).get("runtime"),
                    "firmware_version": (d["status"] or {}).get("firmware_version"),
                })
            return {
                "room_id": self.room_id,
                "light": dict(self.light),
                "devices": devices,
                "server_audio": dict(self.server_audio),
                "voice": {"model": self.voice["model"], "recent": list(self.voice["recent"])},
                "decisions": list(self.decisions),
                "events": list(self.events),
                "generated_at": _now_iso(),
            }


def light_state_msg(room_id: str, light: dict, trigger_event_id: str | None = None, session_id: str | None = None) -> dict:
    """Retained bedlight/v1/lights/{room_id}/state payload (contracts/v1/light-state.schema.json)."""
    return {
        "schema_version": "1.2.0",
        "room_id": room_id,
        "light_on": bool(light["on"]),
        "source": light["changed_by"] if light["changed_by"] in ("simulator", "controller", "mock") else "controller",
        "reason": (light.get("reason") or "")[:128],
        "updated_at_ms": int(light.get("updated_at_ms") or time.time() * 1000),
        "trigger_event_id": trigger_event_id,
        "session_id": session_id,
    }


def light_event(room_id: str, on: bool, source: str, reason: str, device_id: str = "bedlight-01",
                extra: dict | None = None) -> dict:
    """LIGHT_STATE_CHANGED event (contracts/v1/device-runtime-event.schema.json)."""
    return {
        "schema_version": "1.2.0",
        "event_id": str(uuid.uuid4()),
        "trace_id": str(uuid.uuid4()),
        "source": source,
        "device_id": device_id,
        "event_type": "LIGHT_STATE_CHANGED",
        "model": None,
        "confidence": None,
        "timestamp_ms": int(time.time() * 1000),
        "inference_ms": None,
        "session_id": None,
        "data": {"light_on": on, "reason": reason, "room_id": room_id, **(extra or {})},
    }
