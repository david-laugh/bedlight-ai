"""Row sinks: Supabase (PostgREST) or local DRY-RUN JSONL when credentials are absent."""

from __future__ import annotations

import json
import logging
import random
import time
from pathlib import Path

import httpx

log = logging.getLogger("db-writer.sink")


class SinkError(RuntimeError):
    def __init__(self, msg: str, retryable: bool):
        super().__init__(msg)
        self.retryable = retryable


class DryRunSink:
    """Used when SUPABASE_URL / SUPABASE_SECRET_KEY are not configured. Writes rows to a local file.
    Nothing is sent to Supabase in this mode."""

    name = "dry-run"

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def insert(self, rows: list[dict]) -> float:
        t0 = time.perf_counter()
        with self.path.open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return (time.perf_counter() - t0) * 1000


class SupabaseSink:
    name = "supabase"

    def __init__(self, url: str, key: str, table: str = "device_events", timeout: float = 10.0,
                 client: httpx.Client | None = None):
        self.endpoint = f"{url.rstrip('/')}/rest/v1/{table}"
        headers = {
            "apikey": key,
            "Content-Type": "application/json",
            "Prefer": "resolution=ignore-duplicates,return=minimal",  # ON CONFLICT (event_id) DO NOTHING
        }
        if key.startswith("eyJ"):  # legacy JWT service_role key
            headers["Authorization"] = f"Bearer {key}"
        self.client = client or httpx.Client(timeout=timeout)
        self.headers = headers

    def insert(self, rows: list[dict]) -> float:
        t0 = time.perf_counter()
        try:
            r = self.client.post(self.endpoint, params={"on_conflict": "event_id"}, headers=self.headers,
                                 content=json.dumps(rows))
        except httpx.HTTPError as e:
            raise SinkError(f"network error: {e}", retryable=True)
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code >= 500 or r.status_code == 429:
            raise SinkError(f"HTTP {r.status_code}: {r.text[:200]}", retryable=True)
        if r.status_code >= 400:
            raise SinkError(f"HTTP {r.status_code}: {r.text[:200]}", retryable=False)
        return ms


def insert_with_retry(sink, rows: list[dict], max_retries: int = 5, base_delay: float = 0.5,
                      sleep=time.sleep) -> tuple[bool, float, int]:
    """Returns (ok, insert_ms, attempts). Retries only retryable errors with exponential backoff + jitter."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return True, sink.insert(rows), attempt
        except SinkError as e:
            if not e.retryable or attempt > max_retries:
                log.error("insert failed permanently after %d attempt(s): %s", attempt, e)
                return False, 0.0, attempt
            delay = base_delay * (2 ** (attempt - 1)) * (0.8 + 0.4 * random.random())
            log.warning("insert failed (attempt %d): %s - retry in %.2fs", attempt, e, delay)
            sleep(delay)
