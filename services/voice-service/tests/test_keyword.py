"""Keyword detection pipeline tests.

- Fake spotter (pure Python) tests run everywhere.
- The fake `.eim` process test needs Unix domain sockets (Linux CI / containers); skipped on Windows.
"""

import io
import json
import socket
import sys
from pathlib import Path

import pytest
from conftest import REPO_ROOT, make_wav, synth_samples
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.inference import InferenceResult
from app.keyword import NullPublisher

SR = 16000
SID = "9a8b7c6d-1e2f-4a3b-8c4d-5e6f7a8b9c0d"
SCHEMA = Draft202012Validator(json.loads((REPO_ROOT / "contracts/v1/device-runtime-event.schema.json").read_text("utf-8")))


class LoudnessSpotter:
    """Deterministic stand-in: loud window -> 'off'. Not a model."""

    name, version, window_samples, sample_rate, labels = "fake", "t1", 16000, 16000, ["noise", "off", "unknown"]

    def __init__(self):
        self.calls = 0

    def classify(self, samples):
        self.calls += 1
        rms = (sum(x * x for x in samples) / len(samples)) ** 0.5
        off = 0.95 if rms > 3000 else 0.02
        return InferenceResult("off" if off > 0.5 else "noise", max(off, 1 - off), {"off": off, "noise": 1 - off,
                                                                                     "unknown": 0.0}, 1.0, "fake", "t1")


def session_audio(total_ms=4000, burst=(2000, 600), amp=0.5):
    return synth_samples(duration_ms=total_ms, freq=900.0, amp=amp, burst=burst)


def post(client, samples, seq, start, final=False):
    data = {"device_id": "bedlight-01", "session_id": SID, "sequence": str(seq), "sample_rate": "16000",
            "channels": "1", "bits_per_sample": "16", "duration_ms": str(round(1000 * len(samples) / SR)),
            "sample_start": str(start), "capture_timestamp_ms": "0", "is_final": "true" if final else "false",
            "capture_epoch_ms": str(1793707200000 + seq * 1000)}
    wav = make_wav(samples=samples)
    return client.post("/api/v1/audio/chunks", data=data, files={"audio": ("c.wav", io.BytesIO(wav), "audio/wav")})


@pytest.fixture
def kw_client(tmp_path, clock):
    from app.main import create_app
    from app.settings import Settings

    settings = Settings(data_dir=tmp_path / "voice", model_dir=tmp_path / "models", runtime_dir=tmp_path / "runtime")
    pub = NullPublisher()
    spotter = LoudnessSpotter()
    c = TestClient(create_app(settings, now=clock, spotter=spotter, publisher=pub, start_worker=False))
    return c, pub, spotter


def run_all(client):
    det = client.app.state.detector
    return det.process(SID)


def test_detects_burst_once_and_publishes_contract_event(kw_client):
    c, pub, _ = kw_client
    s = session_audio()
    for i in range(4):
        assert post(c, s[i * SR : (i + 1) * SR], i, i * SR, final=(i == 3)).status_code == 201
    events = run_all(c)
    assert len(events) == 1                         # edge trigger: overlapping windows do not re-trigger
    topic, ev = pub.sent[0]
    assert topic == "bedlight/v1/voice/bedlight-01/events"
    SCHEMA.validate(ev)
    assert ev["source"] == "voice-service" and ev["session_id"] == SID
    start = ev["data"]["window_start_sample"]
    assert start < 2600 * 16 and start + 16000 > 2000 * 16  # detection window overlaps the burst
    assert ev["data"]["audio_end_to_detection_ms"] is not None


def test_quiet_session_publishes_nothing(kw_client):
    c, pub, _ = kw_client
    s = session_audio(burst=None, amp=0.0)  # background noise only
    for i in range(3):
        post(c, s[i * SR : (i + 1) * SR], i, i * SR)
    assert run_all(c) == [] and pub.sent == []


def test_windows_with_missing_audio_are_not_classified(kw_client):
    c, pub, spotter = kw_client
    s = session_audio()
    post(c, s[:SR], 0, 0)
    post(c, s[2 * SR : 3 * SR], 2, 2 * SR)  # seq 1 missing
    run_all(c)
    # only windows fully inside chunk 0 or chunk 2 are classified (none spans the gap)
    det = c.app.state.detector
    assert det.cursors[SID].windows == spotter.calls
    assert spotter.calls == 2  # start=0 (chunk 0) and start=32000 (chunk 2) with hop 4000


def test_incremental_processing_does_not_reclassify(kw_client):
    c, _, spotter = kw_client
    s = session_audio()
    post(c, s[:SR], 0, 0)
    run_all(c)
    first = spotter.calls
    run_all(c)
    assert spotter.calls == first  # nothing new
    post(c, s[SR : 2 * SR], 1, SR)
    run_all(c)
    assert spotter.calls > first


def test_model_endpoint_and_recognize(kw_client):
    c, _, _ = kw_client
    m = c.get("/api/v1/model").json()
    assert m["loaded"] is True and m["keyword"] == "off"
    r = c.post("/api/v1/recognize", files={"audio": ("a.wav", io.BytesIO(make_wav(samples=session_audio(2000, (500, 600)))),
                                                      "audio/wav")})
    assert r.status_code == 200 and r.json()["detected"] is True


def test_no_model_means_no_detector(client):
    assert client.app.state.detector is None
    assert client.post("/api/v1/recognize").status_code == 503


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or sys.platform == "win32", reason="needs Unix domain sockets")
def test_eim_runner_protocol_with_fake_model(tmp_path):
    from app.eim_runner import EimRunner

    r = EimRunner(Path(__file__).parent / "fake_eim.py")
    info = r.start()
    try:
        assert info["model_parameters"]["input_features_count"] == 16000
        loud = r.classify([8000, -8000] * 8000)
        quiet = r.classify([10, -10] * 8000)
        assert loud["result"]["classification"]["off"] > 0.9
        assert quiet["result"]["classification"]["off"] < 0.1
    finally:
        r.stop()


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX") or sys.platform == "win32", reason="needs Unix domain sockets")
def test_load_spotter_from_model_dir(tmp_path):
    import shutil

    from app.inference import EimSpotter

    d = tmp_path / "models"
    d.mkdir()
    shutil.copy(Path(__file__).parent / "fake_eim.py", d / "fake_eim.py")
    sp = EimSpotter(d / "fake_eim.py")
    try:
        assert sp.labels == ["noise", "off", "unknown"] and sp.window_samples == 16000
        assert sp.classify([9000, -9000] * 8000).scores["off"] > 0.9
    finally:
        sp.close()


def test_two_separate_sound_events_give_two_detections(kw_client):
    c, pub, _ = kw_client
    s = synth_samples(duration_ms=6000, freq=900.0, amp=0.5, burst=(1000, 400))
    s2 = synth_samples(duration_ms=6000, freq=900.0, amp=0.5, burst=(4500, 400))
    s = [a if abs(a) > abs(b) else b for a, b in zip(s, s2)]
    for i in range(6):
        post(c, s[i * SR : (i + 1) * SR], i, i * SR)
    assert len(run_all(c)) == 2
