"""Sliding-window keyword detection over runtime audio sessions + MQTT publication.

For every session, windows of `spotter.window_samples` are classified with hop `hop_samples` as soon as the
audio for the window has been received contiguously (no gaps). A detection (`keyword` score >= threshold)
publishes VOICE_KEYWORD_DETECTED on bedlight/v1/voice/{device_id}/events and starts a refractory period
(edge trigger: after a detection, the next one requires the score to drop below the threshold first),
so one continuous sound event (e.g. a double clap) produces exactly one detection despite overlapping windows.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field

from .chunks import ChunkStore

log = logging.getLogger("voice-service.keyword")


class NullPublisher:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    def publish(self, topic: str, payload: dict) -> None:
        self.sent.append((topic, payload))


class MqttPublisher:
    def __init__(self, host: str, port: int):
        import paho.mqtt.client as mqtt

        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"bedlight-voice-{uuid.uuid4().hex[:6]}")
        self.client.reconnect_delay_set(1, 30)
        self.client.connect_async(host, port, keepalive=30)
        self.client.loop_start()

    def publish(self, topic: str, payload: dict) -> None:
        self.client.publish(topic, json.dumps(payload), qos=1)


@dataclass
class SessionCursor:
    next_start: int = 0
    armed: bool = True
    windows: int = 0
    detections: int = 0
    last_scores: dict = field(default_factory=dict)


class KeywordDetector:
    def __init__(self, store: ChunkStore, spotter, publisher, keyword: str = "off", threshold: float = 0.8,
                 hop_samples: int = 4000, clock=time.time):
        self.store = store
        self.spotter = spotter
        self.publisher = publisher
        self.keyword = keyword
        self.threshold = threshold
        self.hop = hop_samples
        self.clock = clock
        self.cursors: dict[str, SessionCursor] = {}
        self.recent: list[dict] = []
        self.stats = {"windows": 0, "detections": 0, "errors": 0}
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ threading
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, session_id: str) -> None:
        self._q.put(session_id)

    def _run(self) -> None:
        while True:
            sid = self._q.get()
            try:
                self.process(sid)
            except Exception:  # noqa: BLE001 - keep the worker alive
                self.stats["errors"] += 1
                log.exception("keyword processing failed for %s", sid)

    # -------------------------------------------------------------- core
    def process(self, session_id: str) -> list[dict]:
        s = self.store.get(session_id)
        if s is None:
            return []
        cur = self.cursors.setdefault(session_id, SessionCursor())
        win = self.spotter.window_samples
        span = s.span_samples()
        events = []
        while cur.next_start + win <= span:
            start = cur.next_start
            cur.next_start += self.hop
            pcm, gaps = self.store.assemble(s, start, win)
            if gaps:
                continue  # never classify windows with missing audio
            res = self.spotter.classify(list(pcm))
            cur.windows += 1
            self.stats["windows"] += 1
            score = res.scores.get(self.keyword, 0.0)
            cur.last_scores = res.scores
            if score < self.threshold:
                cur.armed = True
            elif cur.armed:
                cur.armed = False
                cur.detections += 1
                self.stats["detections"] += 1
                events.append(self._publish(s, start, win, score, res))
        return events

    def _publish(self, s, start: int, win: int, score: float, res) -> dict:
        now_ms = int(self.clock() * 1000)
        # latency from the end of the detected window (device capture time) to now, when the device sent epoch time
        end_chunk = max((c for c in s.chunks.values() if c.sample_start <= start + win - 1),
                        key=lambda c: c.sample_start, default=None)
        latency = None
        if end_chunk and end_chunk.capture_epoch_ms:
            window_end_epoch = end_chunk.capture_epoch_ms + 1000 * (start + win - end_chunk.sample_start) / s.sample_rate
            latency = round(now_ms - window_end_epoch)
        ev = {
            "schema_version": "1.2.0",
            "event_id": str(uuid.uuid4()),
            "trace_id": s.session_id,
            "source": "voice-service",
            "device_id": s.device_id,
            "event_type": "VOICE_KEYWORD_DETECTED",
            "model": {"name": self.spotter.name, "version": self.spotter.version, "task": "keyword-spotting",
                      "runtime": "edge-impulse-linux"},
            "confidence": round(score, 4),
            "timestamp_ms": now_ms,
            "inference_ms": round(res.inference_ms, 2),
            "session_id": s.session_id,
            "data": {"keyword": self.keyword, "threshold": self.threshold, "window_start_sample": start,
                     "window_samples": win, "scores": {k: round(v, 4) for k, v in res.scores.items()},
                     "audio_end_to_detection_ms": latency},
        }
        self.publisher.publish(f"bedlight/v1/voice/{s.device_id}/events", ev)
        self.recent = ([{"session_id": s.session_id, "device_id": s.device_id, "confidence": ev["confidence"],
                         "window_start_sample": start, "at_ms": now_ms}] + self.recent)[:20]
        log.info("KEYWORD '%s' session=%s start=%d score=%.3f latency=%s ms", self.keyword, s.session_id, start,
                 score, latency)
        return ev
