"""Dataset storage: class directories, collision-safe file names, metadata.jsonl."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger("voice-service.storage")

SAFE_TOKEN = re.compile(r"[^a-z0-9]")
METADATA_FILE = "metadata.jsonl"


class StorageError(RuntimeError):
    pass


class DuplicateConflict(StorageError):
    """Same capture_id was already stored with different content/label."""


class RecordingStore:
    """Stores WAV files under <data_dir>/raw/<label>/ and appends metadata.jsonl.

    Thread-safe within one process. capture_id is the idempotency key: a retried
    upload of an already stored capture returns the existing record.
    """

    def __init__(self, data_dir: Path, labels: tuple[str, ...]):
        self.root = Path(data_dir).resolve()
        self.raw = self.root / "raw"
        self.labels = labels
        self.metadata_path = self.root / METADATA_FILE
        self._lock = threading.Lock()
        self._index: dict[str, dict] = {}
        for label in labels:
            (self.raw / label).mkdir(parents=True, exist_ok=True)
        (self.root / "prepared" / "training").mkdir(parents=True, exist_ok=True)
        (self.root / "prepared" / "testing").mkdir(parents=True, exist_ok=True)
        self._load_index()

    # ------------------------------------------------------------------ index
    def _load_index(self) -> None:
        if not self.metadata_path.exists():
            return
        with self.metadata_path.open(encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    self._index[rec["capture_id"]] = rec
                except (json.JSONDecodeError, KeyError):
                    log.warning("metadata.jsonl line %d is corrupt; skipped", lineno)

    def records(self) -> list[dict]:
        with self._lock:
            return list(self._index.values())

    def find(self, capture_id: str) -> dict | None:
        with self._lock:
            rec = self._index.get(capture_id)
            if rec and (self.root / rec["relative_path"]).exists():
                return rec
            return None

    def is_writable(self) -> bool:
        probe = self.root / ".write-probe"
        try:
            probe.write_bytes(b"")
            probe.unlink()
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------- save
    def _label_dir(self, label: str) -> Path:
        if label not in self.labels:
            raise StorageError(f"label not allowed: {label!r}")
        d = (self.raw / label).resolve()
        if d.parent != self.raw.resolve():
            raise StorageError("path traversal detected")
        return d

    @staticmethod
    def build_base_name(label: str, captured_at: datetime, capture_id: str) -> str:
        short = SAFE_TOKEN.sub("", capture_id.lower())[:8] or "capture"
        return f"{label}_{captured_at.strftime('%Y%m%dT%H%M%S')}_{short}"

    def save(self, *, label: str, capture_id: str, wav: bytes, captured_at: datetime, record: dict) -> tuple[dict, bool]:
        """Persist the WAV and metadata. Returns (record, created)."""
        directory = self._label_dir(label)
        base = self.build_base_name(label, captured_at, capture_id)
        with self._lock:
            existing = self._index.get(capture_id)
            if existing and (self.root / existing["relative_path"]).exists():
                if existing["label"] != label or existing.get("file_size") != len(wav):
                    raise DuplicateConflict(
                        f"capture_id {capture_id} already stored as {existing['relative_path']}"
                    )
                return existing, False

            path = None
            for i in range(1000):
                name = f"{base}.wav" if i == 0 else f"{base}_{i}.wav"
                candidate = directory / name
                if candidate.resolve().parent != directory:
                    raise StorageError("path traversal detected")
                try:
                    fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
                except FileExistsError:
                    continue
                path = candidate
                try:
                    with os.fdopen(fd, "wb") as f:
                        f.write(wav)
                        f.flush()
                        os.fsync(f.fileno())
                except OSError:
                    path.unlink(missing_ok=True)
                    raise
                break
            if path is None:
                raise StorageError("could not allocate a unique file name")

            rec = dict(record)
            rec.update(
                capture_id=capture_id,
                filename=path.name,
                relative_path=path.relative_to(self.root).as_posix(),
                label=label,
                file_size=len(wav),
            )
            try:
                with self.metadata_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
            except OSError:
                path.unlink(missing_ok=True)
                raise
            self._index[capture_id] = rec
            return rec, True
