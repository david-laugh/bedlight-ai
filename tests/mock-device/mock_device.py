"""BedLight AI mock device (source="mock").

Replays the firmware 0.3 runtime protocol without hardware so the server pipeline
(MQTT -> controller/db-writer, chunk upload -> voice-service, simulator) can be tested.

Scenario B (default): Fall at 0 s, Fall again at 3 s (extend), Standing at 6 s, stop at 8 s.
Audio is a SYNTHETIC tone (pipeline test only, never training data). Vision results are scripted,
NOT model output.

Light gate (firmware 0.4 behaviour): subscribes bedlight/v1/lights/{room}/state; when the light turns OFF
while listening, the session stops immediately (LISTENING_STOPPED reason=light_off).
--voice-keyword-at MS publishes a scripted VOICE_KEYWORD_DETECTED (source=mock) on bedlight/v1/voice/{id}/events
to exercise controller -> light OFF without a trained model.

Usage: python mock_device.py [--scenario A|B] [--speed 1.0] [--drop-seq N] [--voice-keyword-at MS]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import time
import uuid

import httpx
import paho.mqtt.client as mqtt

SR = 16000
DEVICE = os.getenv("MOCK_DEVICE_ID", "mock-xiao-01")
ROOM = os.getenv("ROOM_ID", "bedroom-01")
HOLD_MS = 5000
SCENARIOS = {
    # (time_ms, label) — label frames; everything else "None"
    "A": {"falls": [0], "standing": [2000], "end_ms": 5000},
    "B": {"falls": [0, 3000], "standing": [6000], "end_ms": 8000},
}


def wav(samples: list[int]) -> bytes:
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    return struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ", 16, 1, 1, SR, SR * 2, 2, 16,
                       b"data", len(pcm)) + pcm


class Mock:
    def __init__(self, args):
        self.args = args
        self.t0 = time.monotonic()
        self.http = httpx.Client(base_url=os.getenv("VOICE_SERVICE_URL", "http://voice-service:8001"), timeout=10)
        self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{DEVICE}-mock")
        self.mq.will_set(f"bedlight/v1/devices/{DEVICE}/status",
                         json.dumps({"schema_version": "1.2.0", "source": "mock", "device_id": DEVICE,
                                     "state": "offline"}), qos=1, retain=True)
        self.light_on = None
        self.mq.on_message = self._on_message
        self.mq.connect(os.getenv("MQTT_HOST", "mqtt-broker"), int(os.getenv("MQTT_PORT", "1883")))
        self.mq.subscribe(f"bedlight/v1/lights/{ROOM}/state", qos=1)
        self.mq.loop_start()
        self.session = None

    def _on_message(self, _c, _u, msg):
        try:
            self.light_on = bool(json.loads(msg.payload).get("light_on", True))
            print(f"[mock] light state -> {'ON' if self.light_on else 'OFF'}")
        except ValueError:
            pass

    def voice_keyword(self, start_sample):
        ev = {
            "schema_version": "1.2.0", "event_id": str(uuid.uuid4()), "trace_id": self.session, "source": "mock",
            "device_id": DEVICE, "event_type": "VOICE_KEYWORD_DETECTED",
            "model": {"name": "mock-kws", "version": "mock", "task": "keyword-spotting", "runtime": "mock"},
            "confidence": 0.91, "timestamp_ms": int(time.time() * 1000), "inference_ms": 1.0,
            "session_id": self.session,
            "data": {"keyword": "off", "threshold": 0.8, "window_start_sample": start_sample, "window_samples": SR,
                     "scores": {"off": 0.91, "noise": 0.05, "unknown": 0.04}, "audio_end_to_detection_ms": None},
        }
        self.mq.publish(f"bedlight/v1/voice/{DEVICE}/events", json.dumps(ev), qos=1).wait_for_publish(5)
        print(f"[mock] {self.uptime():6d} ms  VOICE_KEYWORD_DETECTED (scripted, source=mock)")

    def uptime(self) -> int:
        return int((time.monotonic() - self.t0) * 1000)

    def event(self, et, conf=None, inf=None, data=None, model=False):
        ev = {
            "schema_version": "1.2.0", "event_id": str(uuid.uuid4()), "trace_id": self.session or str(uuid.uuid4()),
            "source": "mock", "device_id": DEVICE, "event_type": et,
            "model": {"name": "mock-fall-detector", "version": "mock", "task": "object-detection", "runtime": "mock"}
            if model else None,
            "confidence": conf, "timestamp_ms": int(time.time() * 1000), "device_uptime_ms": self.uptime(),
            "inference_ms": inf, "session_id": self.session, "data": data or {},
        }
        self.mq.publish(f"bedlight/v1/devices/{DEVICE}/events", json.dumps(ev), qos=1).wait_for_publish(5)
        print(f"[mock] {self.uptime():6d} ms  {et}")

    def status(self, listening, remaining):
        st = {"schema_version": "1.2.0", "source": "mock", "device_id": DEVICE, "state": "online",
              "firmware_version": "mock", "uptime_ms": self.uptime(),
              "runtime": {"listening": listening, "remaining_ms": remaining, "hold_ms": HOLD_MS,
                          "session_id": self.session, "label": self.label, "fall_confidence": self.fall_conf,
                          "standing_confidence": self.stand_conf, "fps": 5.0}}
        self.mq.publish(f"bedlight/v1/devices/{DEVICE}/status", json.dumps(st), qos=1, retain=True)

    def chunk(self, seq, start, n, final):
        tone = [int(6000 * math.sin(2 * math.pi * 440 * (start + i) / SR)) for i in range(n)]
        if self.args.drop_seq == seq:
            print(f"[mock] simulating lost chunk seq={seq}")
            return
        files = {"audio": (f"{seq}.wav", wav(tone), "audio/wav")}
        data = {"device_id": DEVICE, "session_id": self.session, "sequence": seq, "sample_rate": SR, "channels": 1,
                "bits_per_sample": 16, "duration_ms": round(1000 * n / SR), "sample_start": start,
                "capture_timestamp_ms": self.uptime(), "is_final": "true" if final else "false", "room_id": ROOM}
        t = time.monotonic()
        r = self.http.post("/api/v1/audio/chunks", data={k: str(v) for k, v in data.items()}, files=files)
        ms = round((time.monotonic() - t) * 1000)
        self.event("AUDIO_CHUNK_SENT", inf=ms, data={"ok": r.is_success, "http_status": r.status_code, "upload_ms": ms,
                                                     "sequence": seq, "sample_start": start, "samples": n,
                                                     "bytes": 44 + 2 * n, "is_final": final,
                                                     "server_status": r.json().get("status", "") if r.is_success else ""})

    def run(self):
        sc = SCENARIOS[self.args.scenario]
        speed = self.args.speed
        self.label, self.fall_conf, self.stand_conf = "None", 0.0, 0.0
        last_fall = None
        listening = False
        seq = start = 0
        chunk_n = SR  # 1 s
        next_chunk_ms = 1000
        tick = 100
        for t in range(0, sc["end_ms"] + 3000, tick):
            if t in sc["falls"]:
                self.label, self.fall_conf, self.stand_conf = "Fall", 0.88, 0.0
                if not listening:
                    self.session = str(uuid.uuid4())
                    self.event("VISION_FALL_DETECTED", 0.88, 150, {"label": "Fall", "fall_confidence": 0.88,
                                                                   "standing_confidence": 0, "threshold": 0.75}, True)
                    self.event("LISTENING_STARTED", 0.88, 150, {"hold_ms": HOLD_MS, "remaining_ms": HOLD_MS}, True)
                    listening, last_fall, seq, start, next_chunk_ms = True, t, 0, 0, t + 1000
                else:
                    last_fall = t
                    self.event("LISTENING_EXTENDED", 0.88, 150, {"hold_ms": HOLD_MS, "remaining_ms": HOLD_MS,
                                                                 "extensions": 1}, True)
            if t in sc["standing"]:
                self.label, self.fall_conf, self.stand_conf = "Standing", 0.0, 0.9
                self.event("VISION_STANDING_DETECTED", 0.9, 150, {"label": "Standing", "fall_confidence": 0,
                                                                  "standing_confidence": 0.9, "threshold": 0.75},
                           True)
            if listening and t >= next_chunk_ms:
                self.chunk(seq, start, chunk_n, False)
                seq, start, next_chunk_ms = seq + 1, start + chunk_n, next_chunk_ms + 1000
            if listening and self.args.voice_keyword_at is not None and t == self.args.voice_keyword_at:
                self.voice_keyword(start)
            light_off = self.light_on is False
            if listening and (t - last_fall >= HOLD_MS or light_off):
                partial = int(SR * ((t - (next_chunk_ms - 1000)) / 1000))
                self.chunk(seq, start, partial, True)
                self.event("LISTENING_STOPPED", data={"reason": "light_off" if light_off else "hold_expired",
                                                      "duration_ms": t, "extensions": len(sc["falls"]) - 1,
                                                      "chunks_sent": seq + 1})
                listening = False
                if light_off:
                    print("[mock] light OFF -> IDLE (scenario ends)")
                    break
            if t % 1000 == 0:
                self.status(listening, max(0, HOLD_MS - (t - last_fall)) if listening else 0)
            time.sleep(tick / 1000 / speed)
        self.session = None
        self.mq.loop_stop()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="B")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--drop-seq", type=int, default=None, help="skip uploading this sequence (gap test)")
    ap.add_argument("--voice-keyword-at", type=int, default=None,
                    help="publish a scripted VOICE_KEYWORD_DETECTED at this scenario time (ms, multiple of 100)")
    Mock(ap.parse_args()).run()
