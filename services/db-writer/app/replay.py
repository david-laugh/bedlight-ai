"""Replay stored rows into Supabase (idempotent: ON CONFLICT (event_id) DO NOTHING).

Sources: DRY-RUN file (events.jsonl) or dead-letter file (failed.jsonl), one device_events row per line.
  docker compose exec db-writer python -m app.replay /data/db-writer/events.jsonl /data/db-writer/failed.jsonl
Requires SUPABASE_URL + SUPABASE_SECRET_KEY. Raw audio is never part of a row.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .main import build_sink
from .sink import insert_with_retry


def load_rows(paths: list[Path]) -> list[dict]:
    seen, rows = set(), []
    for p in paths:
        if not p.exists():
            print(f"skip {p}: not found")
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if "event_id" in r and r["event_id"] not in seen:
                seen.add(r["event_id"])
                rows.append(r)
    return rows


def replay(sink, rows: list[dict], batch: int = 100) -> tuple[int, int]:
    ok_n = fail_n = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        ok, ms, _ = insert_with_retry(sink, chunk)
        if ok:
            ok_n += len(chunk)
            print(f"inserted {len(chunk)} row(s) in {ms:.0f} ms")
        else:
            fail_n += len(chunk)
    return ok_n, fail_n


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or [Path("/data/db-writer/events.jsonl"), Path("/data/db-writer/failed.jsonl")]
    rows = load_rows(paths)
    print(f"{len(rows)} unique row(s) from {[str(p) for p in paths]}")
    ok_n, fail_n = replay(build_sink(dry_run_allowed=False), rows)
    print(f"done: sent={ok_n} failed={fail_n} (duplicates already in Supabase are ignored)")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
