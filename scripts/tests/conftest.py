"""SYNTHETIC audio fixtures for script tests only — never training data."""

import math
import random
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

from _voice_common import wav_utils as wu  # noqa: E402


def tone_with_burst(duration_ms=2000, burst=(800, 400), amp=0.4, seed=0, sr=16000):
    rng = random.Random(seed)
    out = []
    for i in range(sr * duration_ms // 1000):
        t = 1000 * i / sr
        v = rng.uniform(-0.003, 0.003)
        if burst and burst[0] <= t < burst[0] + burst[1]:
            v += amp * math.sin(2 * math.pi * (300 + 50 * seed) * i / sr)
        out.append(int(v * 32767))
    return out


@pytest.fixture
def dataset(tmp_path):
    root = tmp_path / "voice"
    for label in ("off", "unknown", "noise"):
        (root / "raw" / label).mkdir(parents=True)

    def add(label, name, samples, sr=16000):
        p = root / "raw" / label / name
        wu.write_pcm16_wav(p, samples, sr)
        return p

    return root, add
