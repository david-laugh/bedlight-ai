"""RUNTIME_AUDIO chunk API tests (synthetic audio only)."""

import io
import json
from array import array

import pytest
from conftest import REPO_ROOT, make_wav, synth_samples
from jsonschema import Draft202012Validator

from app.wav_utils import parse_wav, pcm16_samples

SR = 16000
SID = "7c1e0a9e-2b3f-4d5a-8b6c-1d2e3f4a5b6c"
SCHEMA = json.loads((REPO_ROOT / "contracts/v1/voice-audio-chunk-response.schema.json").read_text(encoding="utf-8"))
validate = Draft202012Validator(SCHEMA).validate


def stream(total_ms=3500, seed=1):
    """One continuous synthetic signal; chunks are cut from it so reconstruction can be compared."""
    return synth_samples(duration_ms=total_ms, freq=523.0, amp=0.3, seed=seed)


def post_chunk(client, samples, seq, start, final=False, sid=SID, device="bedlight-01", **over):
    wav = make_wav(samples=samples) if samples else make_wav(samples=[])
    data = {
        "device_id": device, "session_id": sid, "sequence": str(seq), "sample_rate": str(SR), "channels": "1",
        "bits_per_sample": "16", "duration_ms": str(round(1000 * len(samples) / SR)), "sample_start": str(start),
        "capture_timestamp_ms": str(1000 + seq * 1000), "is_final": "true" if final else "false",
        "dma_overflows": "0", "room_id": "bedroom-01",
    }
    data.update({k: str(v) for k, v in over.items()})
    return client.post("/api/v1/audio/chunks", data=data, files={"audio": ("c.wav", io.BytesIO(wav), "audio/wav")})


def send_all(client, s, order=None, chunk=SR, skip=()):
    parts = [(i, s[i * chunk : (i + 1) * chunk]) for i in range((len(s) + chunk - 1) // chunk)]
    last = len(parts) - 1
    for i in order or range(len(parts)):
        if i in skip:
            continue
        seq, part = parts[i]
        r = post_chunk(client, part, seq, seq * chunk, final=(seq == last))
        assert r.status_code == 201, r.text
    return parts


def test_chunk_upload_and_response_schema(client):
    s = stream(1000)
    r = post_chunk(client, s, 0, 0)
    assert r.status_code == 201, r.text
    body = r.json()
    validate(body)
    assert body["status"] == "saved"
    assert body["samples"] == 16000
    assert body["session"]["received_chunks"] == 1
    assert body["inference"]["performed"] is False
    assert set(body["timing"]) == {"receive_ms", "validate_ms", "save_ms", "total_ms"}
    stored = client.get(f"/api/v1/audio/sessions/{SID}").json()["chunks"][0]["timing"]
    assert stored == body["timing"]


def test_in_order_session_is_complete_and_reassembles_exactly(client):
    s = stream(3500)
    send_all(client, s)
    info = client.get(f"/api/v1/audio/sessions/{SID}").json()
    assert info["complete"] is True
    assert info["received_chunks"] == 4
    assert info["missing_sequences"] == []
    assert info["discontinuities"] == []
    assert info["received_samples"] == len(s)
    wav = client.get(f"/api/v1/audio/sessions/{SID}/audio.wav").content
    assert list(pcm16_samples(wav, parse_wav(wav))) == s


def test_out_of_order_arrival_is_reordered(client):
    s = stream(3500)
    send_all(client, s, order=[2, 0, 3, 1])
    wav = client.get(f"/api/v1/audio/sessions/{SID}/audio.wav").content
    assert list(pcm16_samples(wav, parse_wav(wav))) == s
    assert client.get(f"/api/v1/audio/sessions/{SID}").json()["complete"] is True


def test_duplicate_chunk_is_idempotent_and_conflict_rejected(client):
    s = stream(1000)
    assert post_chunk(client, s, 0, 0).status_code == 201
    again = post_chunk(client, s, 0, 0)
    assert again.status_code == 200 and again.json()["status"] == "duplicate"
    other = post_chunk(client, stream(1000, seed=9), 0, 0)
    assert other.status_code == 409
    info = client.get(f"/api/v1/audio/sessions/{SID}").json()
    assert info["received_chunks"] == 1


def test_missing_chunk_detected_and_gap_filled_with_silence(client):
    s = stream(3500)
    send_all(client, s, skip={1})
    info = client.get(f"/api/v1/audio/sessions/{SID}").json()
    assert info["missing_sequences"] == [1]
    assert info["complete"] is False
    assert info["loss_ratio"] == pytest.approx(16000 / len(s), abs=1e-3)
    r = client.get(f"/api/v1/audio/sessions/{SID}/audio.wav")
    assert r.headers["X-Gap-Samples"] == "16000"
    out = list(pcm16_samples(r.content, parse_wav(r.content)))
    assert out[:16000] == s[:16000]
    assert out[16000:32000] == [0] * 16000          # exact position preserved
    assert out[32000:] == s[32000:]
    assert client.get(f"/api/v1/audio/sessions/{SID}/audio.wav?fill_gaps=false").status_code == 409


def test_sample_discontinuity_detected(client):
    s = stream(2000)
    post_chunk(client, s[:16000], 0, 0)
    # device lost 160 samples (DMA overflow) -> next chunk starts later than expected
    post_chunk(client, s[16000:], 1, 16160, final=True, dma_overflows=1)
    info = client.get(f"/api/v1/audio/sessions/{SID}").json()
    assert info["discontinuities"] == [
        {"sequence": 1, "expected_sample_start": 16000, "sample_start": 16160, "gap_samples": 160}
    ]
    assert info["dma_overflows"] == 1
    assert info["complete"] is False


def test_two_second_window_across_chunk_boundary(client):
    s = stream(3000)
    send_all(client, s)
    start = 12000  # 0.75 s .. 2.75 s spans chunks 0, 1, 2
    r = client.get(f"/api/v1/audio/sessions/{SID}/window?start_sample={start}&duration_ms=2000")
    assert r.status_code == 200
    out = list(pcm16_samples(r.content, parse_wav(r.content)))
    assert out == s[start : start + 32000]
    assert client.get(f"/api/v1/audio/sessions/{SID}/window?start_sample=30000&duration_ms=2000").status_code == 416


def test_window_over_gap_rejected_unless_allowed(client):
    s = stream(3000)
    send_all(client, s, skip={1})
    url = f"/api/v1/audio/sessions/{SID}/window?start_sample=8000&duration_ms=2000"
    assert client.get(url).status_code == 409
    assert client.get(url + "&allow_gaps=true").status_code == 200


def test_empty_final_chunk_allowed_only_when_final(client):
    s = stream(1000)
    post_chunk(client, s, 0, 0)
    assert post_chunk(client, [], 1, 16000, final=False).status_code == 422
    r = post_chunk(client, [], 1, 16000, final=True)
    assert r.status_code == 201
    assert r.json()["session"]["complete"] is True
    # nothing may come after the final chunk
    assert post_chunk(client, s, 2, 16000).status_code == 409


@pytest.mark.parametrize(
    "over, code",
    [
        ({"device_id": "../x"}, "invalid_device_id"),
        ({"session_id": "../../etc/passwd"}, "invalid_session_id"),
        ({"sequence": -1}, "invalid_sequence"),
        ({"sample_rate": 8000}, "unsupported_format"),
        ({"channels": 2}, "unsupported_format"),
        ({"duration_ms": 900}, "duration_mismatch"),
        ({"is_final": "maybe"}, "invalid_is_final"),
    ],
)
def test_chunk_field_validation(client, over, code):
    r = post_chunk(client, stream(1000), 0, 0, **over)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == code


def test_wrong_wav_format_and_garbage_rejected(client):
    data = {"device_id": "bedlight-01", "session_id": SID, "sequence": "0", "sample_rate": "16000", "channels": "1",
            "bits_per_sample": "16", "duration_ms": "1000", "sample_start": "0", "capture_timestamp_ms": "0",
            "is_final": "false"}
    for payload in (make_wav(duration_ms=1000, channels=2), b"garbage"):
        r = client.post("/api/v1/audio/chunks", data=data, files={"audio": ("c.wav", io.BytesIO(payload), "audio/wav")})
        assert r.status_code == 422


def test_session_belongs_to_one_device(client):
    s = stream(1000)
    post_chunk(client, s, 0, 0)
    assert post_chunk(client, s, 1, 16000, device="other-device").status_code == 409


def test_sessions_persist_across_restart(make_client, clock):
    from fastapi.testclient import TestClient

    from app.main import create_app

    c1, settings = make_client()
    s = stream(2000)
    send_all(c1, s)
    c2 = TestClient(create_app(settings, now=clock))
    lst = c2.get("/api/v1/audio/sessions?device_id=bedlight-01").json()["sessions"]
    assert len(lst) == 1 and lst[0]["complete"] is True
    assert post_chunk(c2, s[:16000], 0, 0).json()["status"] == "duplicate"


def test_runtime_audio_is_separate_from_training_dataset(client):
    post_chunk(client, stream(1000), 0, 0)
    data_dir = client.app.state.settings.data_dir
    assert not list((data_dir / "raw").rglob("*.wav"))
    assert client.get("/api/v1/recordings/stats").json()["total_files"] == 0
