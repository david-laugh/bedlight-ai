"""Voice Service configuration (environment variables)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("/data/voice")
    model_dir: Path = Path("/models")
    runtime_dir: Path = Path("/data/runtime")
    max_chunk_ms: int = 5000
    allowed_labels: tuple[str, ...] = ("off", "unknown", "noise")
    sample_rate: int = 16000
    channels: int = 1
    bits_per_sample: int = 16
    min_duration_ms: int = 500
    max_duration_ms: int = 10000
    max_upload_bytes: int = 1_000_000
    upload_token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        labels = tuple(
            l.strip() for l in os.getenv("VOICE_ALLOWED_LABELS", "off,unknown,noise").split(",") if l.strip()
        )
        return cls(
            data_dir=Path(os.getenv("VOICE_DATA_DIR", "/data/voice")),
            model_dir=Path(os.getenv("VOICE_MODEL_DIR", "/models")),
            runtime_dir=Path(os.getenv("VOICE_RUNTIME_DIR", "/data/runtime")),
            max_chunk_ms=_int("VOICE_MAX_CHUNK_MS", 5000),
            allowed_labels=labels,
            sample_rate=_int("VOICE_SAMPLE_RATE", 16000),
            min_duration_ms=_int("VOICE_MIN_DURATION_MS", 500),
            max_duration_ms=_int("VOICE_MAX_DURATION_MS", 10000),
            max_upload_bytes=_int("VOICE_MAX_UPLOAD_BYTES", 1_000_000),
            upload_token=os.getenv("VOICE_UPLOAD_TOKEN", ""),
        )
