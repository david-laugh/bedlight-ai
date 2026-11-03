"""Runtime audio chunk sessions (POST /api/v1/audio/chunks).

Each LISTENING session from the ESP32 sends 1 s WAV chunks with (session_id, sequence, sample_start).
`sample_start` is the absolute sample index of the chunk's first sample within the session, so the
server can rebuild the exact timeline, detect missing sequences, detect sample discontinuities
(device-side DMA overflow) and cut windows (e.g. 2 s) that cross chunk boundaries.

Storage (separate from the training dataset):
  <runtime_dir>/sessions/<device_id>/<session_id>/chunk_<sequence:06d>.wav
  <runtime_dir>/sessions/<device_id>/<session_id>/session.json
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from array import array
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .wav_utils import parse_wav, pcm16_samples

log = logging.getLogger("voice-service.chunks")


class ChunkConflict(RuntimeError):
    pass


@dataclass
class ChunkInfo:
    sequence: int
    sample_start: int
    samples: int
    sha256: str
    file: str
    received_at: str
    capture_timestamp_ms: int | None = None
    capture_epoch_ms: int | None = None
    dma_overflows: int = 0
    dropped_chunks_before: int = 0
    is_final: bool = False
    timing: dict = field(default_factory=dict)


@dataclass
class Session:
    session_id: str
    device_id: str
    room_id: str | None
    sample_rate: int
    created_at: str
    updated_at: str
    chunks: dict[int, ChunkInfo] = field(default_factory=dict)
    final_sequence: int | None = None

    # ------------------------------------------------------------- analysis
    def missing_sequences(self) -> list[int]:
        if not self.chunks:
            return []
        last = self.final_sequence if self.final_sequence is not None else max(self.chunks)
        return [s for s in range(0, last + 1) if s not in self.chunks]

    def discontinuities(self) -> list[dict]:
        """Adjacent sequences whose sample_start does not continue the previous chunk."""
        out = []
        for seq in sorted(self.chunks):
            prev = self.chunks.get(seq - 1)
            if prev is None:
                continue
            expected = prev.sample_start + prev.samples
            cur = self.chunks[seq]
            if cur.sample_start != expected:
                out.append({"sequence": seq, "expected_sample_start": expected, "sample_start": cur.sample_start,
                            "gap_samples": cur.sample_start - expected})
        return out

    def total_samples(self) -> int:
        return sum(c.samples for c in self.chunks.values())

    def span_samples(self) -> int:
        if not self.chunks:
            return 0
        return max(c.sample_start + c.samples for c in self.chunks.values())

    def complete(self) -> bool:
        return self.final_sequence is not None and not self.missing_sequences() and not self.discontinuities()

    def summary(self) -> dict:
        missing = self.missing_sequences()
        span = self.span_samples()
        received = self.total_samples()
        return {
            "session_id": self.session_id,
            "device_id": self.device_id,
            "room_id": self.room_id,
            "sample_rate": self.sample_rate,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "received_chunks": len(self.chunks),
            "final_received": self.final_sequence is not None,
            "final_sequence": self.final_sequence,
            "missing_sequences": missing,
            "discontinuities": self.discontinuities(),
            "received_samples": received,
            "span_samples": span,
            "received_duration_ms": round(1000 * received / self.sample_rate, 1),
            "span_duration_ms": round(1000 * span / self.sample_rate, 1),
            "loss_ratio": round(1 - received / span, 4) if span else 0.0,
            "dma_overflows": sum(c.dma_overflows for c in self.chunks.values()),
            "complete": self.complete(),
        }

    def to_json(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if k != "chunks"}
        d["chunks"] = [asdict(c) for c in sorted(self.chunks.values(), key=lambda c: c.sequence)]
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Session":
        chunks = {c["sequence"]: ChunkInfo(**c) for c in d.pop("chunks", [])}
        s = cls(**d)
        s.chunks = chunks
        return s


class ChunkStore:
    def __init__(self, runtime_dir: Path):
        self.root = Path(runtime_dir).resolve() / "sessions"
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}
        self._load()

    def _load(self) -> None:
        for meta in self.root.glob("*/*/session.json"):
            try:
                s = Session.from_json(json.loads(meta.read_text(encoding="utf-8")))
                self._sessions[s.session_id] = s
            except (OSError, ValueError, TypeError, KeyError) as e:
                log.warning("could not load %s: %s", meta, e)

    def _dir(self, s: Session) -> Path:
        d = (self.root / s.device_id / s.session_id).resolve()
        if d.parent.parent != self.root:
            raise ChunkConflict("path traversal detected")
        return d

    def _persist(self, s: Session) -> None:
        d = self._dir(s)
        tmp = d / "session.json.tmp"
        tmp.write_text(json.dumps(s.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, d / "session.json")

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(session_id)

    def list(self, device_id: str | None = None, limit: int = 20) -> list[Session]:
        with self._lock:
            items = [s for s in self._sessions.values() if device_id is None or s.device_id == device_id]
        items.sort(key=lambda s: s.updated_at, reverse=True)
        return items[:limit]

    def add(self, *, device_id: str, session_id: str, room_id: str | None, sample_rate: int, info: ChunkInfo,
            wav: bytes, now: datetime) -> tuple[Session, bool]:
        """Store one chunk. Returns (session, created). Raises ChunkConflict."""
        iso = now.isoformat()
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                s = Session(session_id=session_id, device_id=device_id, room_id=room_id, sample_rate=sample_rate,
                            created_at=iso, updated_at=iso)
            elif s.device_id != device_id:
                raise ChunkConflict(f"session {session_id} belongs to device {s.device_id}")
            elif s.sample_rate != sample_rate:
                raise ChunkConflict("sample_rate changed within session")

            existing = s.chunks.get(info.sequence)
            if existing is not None:
                if existing.sha256 == info.sha256:
                    return s, False  # idempotent retry
                raise ChunkConflict(f"sequence {info.sequence} already received with different content")
            if info.is_final and s.final_sequence is not None and s.final_sequence != info.sequence:
                raise ChunkConflict(f"final chunk already received at sequence {s.final_sequence}")
            if s.final_sequence is not None and info.sequence > s.final_sequence:
                raise ChunkConflict(f"sequence {info.sequence} is after final sequence {s.final_sequence}")

            d = self._dir(s)
            d.mkdir(parents=True, exist_ok=True)
            name = f"chunk_{info.sequence:06d}.wav"
            tmp = d / (name + ".tmp")
            with open(tmp, "wb") as f:
                f.write(wav)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, d / name)
            info.file = name
            s.chunks[info.sequence] = info
            if info.is_final:
                s.final_sequence = info.sequence
            s.updated_at = iso
            self._sessions[session_id] = s
            self._persist(s)
            return s, True

    def set_timing(self, s: Session, sequence: int, timing: dict) -> None:
        """Record server processing time of a newly stored chunk in session.json."""
        with self._lock:
            c = s.chunks.get(sequence)
            if c is not None:
                c.timing = timing
                self._persist(s)

    # ---------------------------------------------------------- reconstruction
    def assemble(self, s: Session, start_sample: int = 0, num_samples: int | None = None,
                 fill_gaps: bool = True) -> tuple[array, list[tuple[int, int]]]:
        """Rebuild PCM for [start_sample, start_sample+num_samples) using sample_start positions.

        Returns (samples, gaps) where gaps are (start, length) ranges with no received audio
        (filled with zeros when fill_gaps, otherwise the caller should reject the window).
        """
        span = s.span_samples()
        end = span if num_samples is None else start_sample + num_samples
        end = max(end, start_sample)
        out = array("h", bytes(2 * (end - start_sample)))
        covered = array("b", bytes(end - start_sample))
        d = self._dir(s)
        for c in sorted(s.chunks.values(), key=lambda c: c.sample_start):
            c_end = c.sample_start + c.samples
            lo, hi = max(c.sample_start, start_sample), min(c_end, end)
            if lo >= hi:
                continue
            data = (d / c.file).read_bytes()
            pcm = pcm16_samples(data, parse_wav(data))
            out[lo - start_sample : hi - start_sample] = pcm[lo - c.sample_start : hi - c.sample_start]
            for i in range(lo - start_sample, hi - start_sample):
                covered[i] = 1
        gaps = []
        i = 0
        n = len(covered)
        while i < n:
            if not covered[i]:
                j = i
                while j < n and not covered[j]:
                    j += 1
                gaps.append((start_sample + i, j - i))
                i = j
            else:
                i += 1
        return out, gaps


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
