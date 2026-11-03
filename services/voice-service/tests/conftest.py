"""Test fixtures.

All audio here is SYNTHETIC (sine tones / noise) generated at test time.
It exists only to exercise parsing/validation code and must never be used as training data.
"""

from __future__ import annotations

import io
import math
import random
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_DIR.parents[1]
sys.path.insert(0, str(SERVICE_DIR))


def synth_samples(duration_ms=2000, sample_rate=16000, freq=440.0, amp=0.3, burst=None, seed=0):
    """Mono int16 samples. burst=(start_ms, len_ms) -> tone only inside burst, low noise elsewhere."""
    rng = random.Random(seed)
    n = sample_rate * duration_ms // 1000
    out = []
    for i in range(n):
        t_ms = 1000.0 * i / sample_rate
        noise = rng.uniform(-0.002, 0.002)
        tone = amp * math.sin(2 * math.pi * freq * i / sample_rate)
        if burst is not None and not (burst[0] <= t_ms < burst[0] + burst[1]):
            tone = 0.0
        out.append(max(-32768, min(32767, int((tone + noise) * 32767))))
    return out


def make_wav(
    duration_ms=2000,
    sample_rate=16000,
    channels=1,
    bits=16,
    audio_format=1,
    samples=None,
    **synth_kwargs,
) -> bytes:
    """Build a WAV byte string by hand so that malformed variants can be produced."""
    if samples is None:
        samples = synth_samples(duration_ms, sample_rate, **synth_kwargs)
    if bits == 16:
        frame = b"".join(struct.pack("<h", s) * channels for s in samples)
    elif bits == 8:
        frame = b"".join(struct.pack("<B", (s >> 8) + 128) * channels for s in samples)
    elif bits == 32:
        frame = b"".join(struct.pack("<f", s / 32768.0) * channels for s in samples)
    else:
        raise ValueError(bits)
    block_align = channels * bits // 8
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(frame), b"WAVE",
        b"fmt ", 16, audio_format, channels, sample_rate, sample_rate * block_align, block_align, bits,
        b"data", len(frame),
    )
    return header + frame


@pytest.fixture
def wav_bytes():
    return make_wav()


class FrozenClock:
    def __init__(self):
        self.value = datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.value


@pytest.fixture
def clock():
    return FrozenClock()


@pytest.fixture
def make_client(tmp_path, clock):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.settings import Settings

    def _make(**overrides):
        settings = Settings(
            data_dir=tmp_path / "voice", model_dir=tmp_path / "models", runtime_dir=tmp_path / "runtime", **overrides
        )
        return TestClient(create_app(settings, now=clock)), settings

    return _make


@pytest.fixture
def client(make_client):
    c, _ = make_client()
    return c


def form(label="off", capture_id="3f6c1a2e-8b4d-4c1e-9a7f-2d5e6b7c8a90", device_id="bedlight-01", sample_rate=16000, **extra):
    d = {"label": label, "capture_id": capture_id, "device_id": device_id, "sample_rate": str(sample_rate)}
    d.update({k: str(v) for k, v in extra.items()})
    return d


def upload(client, wav: bytes, filename="clip.wav", headers=None, **form_kwargs):
    return client.post(
        "/api/v1/recordings",
        data=form(**form_kwargs),
        files={"audio": (filename, io.BytesIO(wav), "audio/wav")},
        headers=headers or {},
    )
