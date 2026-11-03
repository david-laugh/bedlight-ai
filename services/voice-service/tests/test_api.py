import json
from pathlib import Path

import pytest
from conftest import REPO_ROOT, make_wav, upload
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from app.main import create_app

RESPONSE_SCHEMA = json.loads((REPO_ROOT / "contracts/v1/voice-recording-response.schema.json").read_text(encoding="utf-8"))
validate_response = Draft202012Validator(RESPONSE_SCHEMA).validate


def data_dir(client) -> Path:
    return client.app.state.settings.data_dir


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["data_dir_writable"] is True
    assert r.json()["model_loaded"] is False


def test_upload_saves_file_and_metadata(client, wav_bytes):
    r = upload(client, wav_bytes, record_ms=2003, free_heap=200000, free_psram=7000000)
    assert r.status_code == 201, r.text
    body = r.json()
    validate_response(body)
    assert body["status"] == "saved"
    assert body["label"] == "off"
    assert body["sample_rate"] == 16000
    assert body["duration_ms"] == pytest.approx(2000.0)
    assert body["file_size"] == len(wav_bytes)
    assert body["relative_path"] == f"raw/off/{body['filename']}"
    assert body["filename"].startswith("off_20261103T120000_3f6c1a2e")
    assert set(body["timing"]) == {"receive_ms", "validate_ms", "save_ms", "total_ms"}
    assert "Server-Timing" in r.headers

    saved = data_dir(client) / body["relative_path"]
    assert saved.read_bytes() == wav_bytes

    lines = (data_dir(client) / "metadata.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    meta = json.loads(lines[0])
    for key in ("capture_id", "filename", "label", "device_id", "sample_rate", "duration_ms",
                "file_size", "captured_at", "validation_status"):
        assert key in meta
    assert meta["device"]["record_ms"] == 2003


@pytest.mark.parametrize("label", ["noise", "unknown"])
def test_upload_other_classes(client, wav_bytes, label):
    r = upload(client, wav_bytes, label=label)
    assert r.status_code == 201
    assert (data_dir(client) / "raw" / label / r.json()["filename"]).exists()


@pytest.mark.parametrize(
    "form_override, status, code",
    [
        ({"label": "on"}, 422, "invalid_label"),
        ({"label": "../off"}, 422, "invalid_label"),
        ({"label": "off/../../etc"}, 422, "invalid_label"),
        ({"capture_id": "../../../../tmp/evil"}, 422, "invalid_capture_id"),
        ({"capture_id": "abc"}, 422, "invalid_capture_id"),
        ({"device_id": "Bedlight 01"}, 422, "invalid_device_id"),
        ({"device_id": "../x"}, 422, "invalid_device_id"),
        ({"sample_rate": 44100}, 422, "invalid_sample_rate"),
    ],
)
def test_rejects_bad_form_fields(client, wav_bytes, form_override, status, code):
    r = upload(client, wav_bytes, **form_override)
    assert r.status_code == status, r.text
    assert r.json()["detail"]["error"] == code
    assert not list((data_dir(client) / "raw").rglob("*.wav"))


@pytest.mark.parametrize(
    "wav_kwargs, code",
    [
        ({"channels": 2}, "unsupported_format"),
        ({"sample_rate": 8000}, "unsupported_format"),
        ({"bits": 8}, "unsupported_format"),
        ({"bits": 32, "audio_format": 3}, "unsupported_format"),
        ({"duration_ms": 200}, "invalid_duration"),
        ({"duration_ms": 12000}, "invalid_duration"),
        ({"samples": [0] * 32000}, "all_zero_audio"),
    ],
)
def test_rejects_bad_wav(client, wav_kwargs, code):
    r = upload(client, make_wav(**wav_kwargs))
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["error"] == code
    assert not list((data_dir(client) / "raw").rglob("*.wav"))


def test_rejects_empty_file(client):
    r = upload(client, b"")
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "empty_file"


@pytest.mark.parametrize("payload", [b"not a wav at all", b"RIFF\x10\x00\x00\x00WAVEjunk"])
def test_rejects_corrupt_file(client, payload):
    r = upload(client, payload)
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "invalid_wav"


def test_rejects_truncated_file(client, wav_bytes):
    r = upload(client, wav_bytes[: len(wav_bytes) // 2])
    assert r.status_code == 422
    assert "truncated" in r.json()["detail"]["message"]


def test_rejects_too_large(make_client):
    c, _ = make_client(max_upload_bytes=10_000)
    r = upload(c, make_wav(duration_ms=1000))  # 32 KB > 10 KB
    assert r.status_code == 413


def test_client_filename_is_ignored(client, wav_bytes):
    r = upload(client, wav_bytes, filename="../../../../etc/passwd")
    assert r.status_code == 201
    saved = data_dir(client) / r.json()["relative_path"]
    assert saved.resolve().parent == (data_dir(client) / "raw" / "off").resolve()


def test_duplicate_capture_id_is_idempotent(client, wav_bytes):
    first = upload(client, wav_bytes)
    second = upload(client, wav_bytes)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    assert second.json()["relative_path"] == first.json()["relative_path"]
    assert len(list((data_dir(client) / "raw" / "off").glob("*.wav"))) == 1
    assert len((data_dir(client) / "metadata.jsonl").read_text().splitlines()) == 1


def test_duplicate_capture_id_with_different_label_conflicts(client, wav_bytes):
    assert upload(client, wav_bytes, label="off").status_code == 201
    r = upload(client, wav_bytes, label="noise")
    assert r.status_code == 409


def test_filename_collision_gets_suffix(client, wav_bytes):
    # Same second (frozen clock) and same 8-char capture prefix -> same base name.
    a = upload(client, wav_bytes, capture_id="aaaaaaaa-0001")
    b = upload(client, wav_bytes, capture_id="aaaaaaaa-0002")
    assert a.status_code == b.status_code == 201
    assert a.json()["filename"] != b.json()["filename"]
    assert b.json()["filename"].endswith("_1.wav")
    assert len(list((data_dir(client) / "raw" / "off").glob("*.wav"))) == 2


def test_warnings_for_quiet_audio(client):
    r = upload(client, make_wav(amp=0.0))  # only +-0.002 synthetic noise
    assert r.status_code == 201
    assert r.json()["validation_status"] == "valid_with_warnings"
    assert "likely_silent" in r.json()["warnings"]


def test_upload_token(make_client, wav_bytes):
    c, _ = make_client(upload_token="s3cret")
    assert upload(c, wav_bytes).status_code == 401
    assert upload(c, wav_bytes, headers={"X-Device-Token": "wrong"}).status_code == 401
    assert upload(c, wav_bytes, headers={"X-Device-Token": "s3cret"}).status_code == 201


def test_stats_counts_files_on_disk(client, wav_bytes):
    upload(client, wav_bytes, capture_id="off-00000001")
    upload(client, wav_bytes, capture_id="off-00000002")
    upload(client, wav_bytes, label="noise", capture_id="noise-0000001")
    s = client.get("/api/v1/recordings/stats").json()
    assert s["labels"]["off"]["files"] == 2
    assert s["labels"]["noise"]["files"] == 1
    assert s["labels"]["unknown"]["files"] == 0
    assert s["labels"]["off"]["total_duration_ms"] == pytest.approx(4000.0)
    assert s["total_files"] == 3

    # a manually deleted (rejected-by-user) recording disappears from stats
    victim = next((data_dir(client) / "raw" / "off").glob("*.wav"))
    victim.unlink()
    assert client.get("/api/v1/recordings/stats").json()["labels"]["off"]["files"] == 1


def test_state_survives_restart(make_client, wav_bytes, clock):
    c1, settings = make_client()
    first = upload(c1, wav_bytes)
    c2 = TestClient(create_app(settings, now=clock))  # simulates container restart on same volume
    assert c2.get("/api/v1/recordings/stats").json()["labels"]["off"]["files"] == 1
    again = upload(c2, wav_bytes)
    assert again.status_code == 200 and again.json()["relative_path"] == first.json()["relative_path"]


def test_recognize_returns_503_without_model(client):
    r = client.post("/api/v1/recognize")
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "model_not_deployed"
    assert client.get("/api/v1/model").json()["loaded"] is False
