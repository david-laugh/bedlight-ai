"""BedLight AI DB Writer: MQTT events -> validation -> de-dup -> Supabase public.device_events.

Subscribes:
  bedlight/v1/devices/+/events     (ESP32 / mock runtime events)
  bedlight/v1/simulator/+/events   (controller / simulator events)
Without SUPABASE_URL + SUPABASE_SECRET_KEY it runs in DRY-RUN mode (local JSONL, nothing sent to Supabase).
"""

from __future__ import annotations

import json
import logging
import os
import queue
import signal
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

from .mapping import SeenIds, Validator, to_row
from .sink import DryRunSink, SupabaseSink, insert_with_retry

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("db-writer")

TOPICS = ["bedlight/v1/devices/+/events", "bedlight/v1/simulator/+/events"]


class Writer:
    def __init__(self, validator: Validator, sink, batch_size: int = 50, flush_s: float = 1.0, max_retries: int = 5,
                 queue_max: int = 1000, dead_letter: Path | None = None):
        self.validator = validator
        self.sink = sink
        self.seen = SeenIds()
        self.q: queue.Queue = queue.Queue(maxsize=queue_max)
        self.batch_size = batch_size
        self.flush_s = flush_s
        self.max_retries = max_retries
        self.dead_letter = Path(dead_letter) if dead_letter else None
        self.stats = {"received": 0, "invalid": 0, "duplicate": 0, "queued": 0, "dropped_queue_full": 0,
                      "inserted": 0, "failed": 0, "dead_lettered": 0, "last_insert_ms": None}

    def handle(self, payload: bytes) -> str:
        self.stats["received"] += 1
        try:
            event = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            self.stats["invalid"] += 1
            return "invalid"
        errs = self.validator.errors(event) if isinstance(event, dict) else ["not an object"]
        if errs:
            self.stats["invalid"] += 1
            log.warning("invalid event dropped: %s", errs)
            return "invalid"
        if not self.seen.add(event["event_id"]):
            self.stats["duplicate"] += 1
            return "duplicate"
        try:
            self.q.put_nowait(to_row(event))
        except queue.Full:
            self.stats["dropped_queue_full"] += 1
            log.error("queue full - event %s dropped", event["event_id"])
            return "dropped"
        self.stats["queued"] += 1
        return "queued"

    def flush_once(self, block_s: float) -> int:
        rows = []
        deadline = time.monotonic() + block_s
        while len(rows) < self.batch_size:
            timeout = deadline - time.monotonic()
            if timeout <= 0:
                break
            try:
                rows.append(self.q.get(timeout=timeout))
            except queue.Empty:
                break
        if not rows:
            return 0
        ok, ms, attempts = insert_with_retry(self.sink, rows, self.max_retries)
        if ok:
            self.stats["inserted"] += len(rows)
            self.stats["last_insert_ms"] = round(ms, 2)
            log.info("%s insert %d row(s) in %.1f ms (attempts=%d)", self.sink.name, len(rows), ms, attempts)
        else:
            self.stats["failed"] += len(rows)
            self._dead_letter(rows)
        return len(rows)

    def _dead_letter(self, rows: list[dict]) -> None:
        """Keep rows that could not be inserted (e.g. table not migrated yet) for `python -m app.replay`."""
        if not self.dead_letter:
            return
        self.dead_letter.parent.mkdir(parents=True, exist_ok=True)
        with self.dead_letter.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        self.stats["dead_lettered"] += len(rows)
        log.error("%d row(s) written to dead-letter %s (replay: python -m app.replay %s)", len(rows),
                  self.dead_letter, self.dead_letter)


def build_sink(dry_run_allowed: bool = True):
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SECRET_KEY", "")
    if url.startswith("https://") and "<" not in url and key and "<" not in key:
        log.info("Supabase sink enabled: %s (table %s)", url, os.getenv("SUPABASE_EVENTS_TABLE", "device_events"))
        return SupabaseSink(url, key, os.getenv("SUPABASE_EVENTS_TABLE", "device_events"))
    if not dry_run_allowed:
        raise SystemExit("SUPABASE_URL / SUPABASE_SECRET_KEY are required")
    path = Path(os.getenv("DB_WRITER_DRYRUN_FILE", "/data/db-writer/events.jsonl"))
    log.warning("SUPABASE_URL / SUPABASE_SECRET_KEY not configured -> DRY-RUN: rows appended to %s, "
                "NOTHING is written to Supabase", path)
    return DryRunSink(path)


def main() -> None:
    contracts = Path(os.getenv("CONTRACTS_DIR", "/app/contracts/v1"))
    writer = Writer(Validator(contracts), build_sink(), max_retries=int(os.getenv("DB_WRITER_MAX_RETRIES", "5")),
                    queue_max=int(os.getenv("DB_WRITER_QUEUE_MAX", "1000")),
                    dead_letter=Path(os.getenv("DB_WRITER_DEADLETTER_FILE", "/data/db-writer/failed.jsonl")))
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="bedlight-db-writer")

    def on_connect(c, _u, _f, rc, _p=None):
        log.info("MQTT connected rc=%s; subscribing %s", rc, TOPICS)
        for t in TOPICS:
            c.subscribe(t, qos=1)

    client.on_connect = on_connect
    client.on_message = lambda _c, _u, msg: writer.handle(msg.payload)
    client.reconnect_delay_set(1, 30)
    host, port = os.getenv("MQTT_HOST", "mqtt-broker"), int(os.getenv("MQTT_PORT", "1883"))
    client.connect_async(host, port, keepalive=30)
    client.loop_start()

    last_stats = time.monotonic()
    while not stop.is_set():
        writer.flush_once(1.0)
        if time.monotonic() - last_stats > 30:
            last_stats = time.monotonic()
            log.info("stats %s", writer.stats)
    while writer.flush_once(0.1):
        pass
    client.loop_stop()
    log.info("stopped; stats %s", writer.stats)


if __name__ == "__main__":
    main()
