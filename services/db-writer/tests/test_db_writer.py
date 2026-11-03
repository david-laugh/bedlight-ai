import json
import sys
from pathlib import Path

import httpx
import pytest

SERVICE = Path(__file__).resolve().parents[1]
REPO = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))

from app.main import Writer  # noqa: E402
from app.mapping import Validator, to_row  # noqa: E402
from app.sink import DryRunSink, SinkError, SupabaseSink, insert_with_retry  # noqa: E402

EXAMPLES = REPO / "contracts/v1/examples/valid"


def ev(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.fixture
def validator():
    return Validator(REPO / "contracts/v1")


def test_runtime_event_mapping(validator):
    e = ev("runtime-audio-chunk-sent.json")
    assert validator.errors(e) == []
    row = to_row(e)
    assert row["event_type"] == "AUDIO_CHUNK_SENT"
    assert row["network_ms"] == 142 and row["ok"] is True and row["sequence"] == 0
    assert row["inference_ms"] is None
    assert row["session_id"] == e["session_id"]
    assert "audio" not in json.dumps(row["payload"]).lower().replace("audio_chunk_sent", "")


def test_vision_event_mapping(validator):
    row = to_row(ev("runtime-vision-fall.json"))
    assert row["confidence"] == pytest.approx(0.8812)
    assert row["inference_ms"] == 180.0


def test_legacy_envelope_still_accepted(validator):
    e = ev("event-lying-detected.json")
    assert validator.errors(e) == []
    row = to_row(e)
    assert row["confidence"] == 0.91 and row["inference_ms"] == 120.3


def test_writer_dedup_invalid_and_dryrun(tmp_path, validator):
    sink = DryRunSink(tmp_path / "events.jsonl")
    w = Writer(validator, sink, batch_size=10)
    payload = json.dumps(ev("runtime-listening-started.json")).encode()
    assert w.handle(payload) == "queued"
    assert w.handle(payload) == "duplicate"          # QoS 1 redelivery
    assert w.handle(b"{not json") == "invalid"
    bad = ev("runtime-listening-started.json")
    bad["event_id"] = "a1b2c3d4-0009-4000-8000-000000000009"
    bad["session_id"] = None
    assert w.handle(json.dumps(bad).encode()) == "invalid"
    assert w.flush_once(0.05) == 1
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["event_type"] == "LISTENING_STARTED"


def _transport(responses):
    calls = []

    def handler(request):
        calls.append(request)
        status = responses.pop(0)
        if isinstance(status, Exception):
            raise status
        return httpx.Response(status, text="err" if status >= 400 else "")

    return httpx.MockTransport(handler), calls


def test_supabase_sink_headers_and_ignore_duplicates():
    transport, calls = _transport([201])
    sink = SupabaseSink("https://x.supabase.co", "sb_secret_abc", client=httpx.Client(transport=transport))
    sink.insert([{"event_id": "e"}])
    req = calls[0]
    assert req.url.path == "/rest/v1/device_events"
    assert req.url.params["on_conflict"] == "event_id"
    assert req.headers["apikey"] == "sb_secret_abc"
    assert "resolution=ignore-duplicates" in req.headers["prefer"]
    assert "authorization" not in req.headers  # new secret keys are not JWTs


def test_retry_on_5xx_then_success():
    transport, calls = _transport([503, httpx.ConnectError("down"), 201])
    sink = SupabaseSink("https://x.supabase.co", "eyJfake", client=httpx.Client(transport=transport))
    ok, _, attempts = insert_with_retry(sink, [{"a": 1}], max_retries=5, sleep=lambda s: None)
    assert ok and attempts == 3
    assert calls[0].headers["authorization"] == "Bearer eyJfake"


def test_no_retry_on_4xx():
    transport, calls = _transport([400, 201])
    sink = SupabaseSink("https://x.supabase.co", "k", client=httpx.Client(transport=transport))
    ok, _, attempts = insert_with_retry(sink, [{"a": 1}], sleep=lambda s: None)
    assert not ok and attempts == 1 and len(calls) == 1


def test_gives_up_after_max_retries():
    class Down:
        name = "x"

        def insert(self, rows):
            raise SinkError("down", retryable=True)

    ok, _, attempts = insert_with_retry(Down(), [{}], max_retries=2, sleep=lambda s: None)
    assert not ok and attempts == 3


def test_failed_insert_goes_to_dead_letter_and_replays(tmp_path, validator):
    class TableMissing:
        name = "supabase"

        def insert(self, rows):
            raise SinkError("HTTP 404: PGRST205 table not found", retryable=False)

    dl = tmp_path / "failed.jsonl"
    w = Writer(validator, TableMissing(), dead_letter=dl)
    w.handle(json.dumps(ev("runtime-vision-fall.json")).encode())
    w.handle(json.dumps(ev("runtime-listening-started.json")).encode())
    w.flush_once(0.05)
    assert w.stats["dead_lettered"] == 2
    assert len(dl.read_text(encoding="utf-8").splitlines()) == 2

    from app.replay import load_rows, replay

    rows = load_rows([dl, dl])  # duplicates across files collapse
    assert len(rows) == 2

    class Ok:
        name = "ok"
        got = []

        def insert(self, rows):
            self.got.extend(rows)
            return 1.0

    sink = Ok()
    assert replay(sink, rows) == (2, 0)
    assert {r["event_type"] for r in sink.got} == {"VISION_FALL_DETECTED", "LISTENING_STARTED"}


def test_voice_keyword_event_is_stored(validator):
    e = ev("runtime-voice-keyword-detected.json")
    assert validator.errors(e) == []
    row = to_row(e)
    assert row["source"] == "voice-service" and row["confidence"] == 0.93 and row["inference_ms"] == 6.4
