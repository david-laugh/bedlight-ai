import json
import os
import sys
from pathlib import Path

os.environ["CONTROLLER_DISABLE_IO"] = "1"
SERVICE = Path(__file__).resolve().parents[1]
REPO = SERVICE.parents[1]
sys.path.insert(0, str(SERVICE))

from fastapi.testclient import TestClient  # noqa: E402
from jsonschema import Draft202012Validator  # noqa: E402

from app.state import SystemState, light_event  # noqa: E402

EX = REPO / "contracts/v1/examples/valid"


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def load(name):
    return json.loads((EX / name).read_text(encoding="utf-8"))


def test_listening_countdown_and_stop():
    clk = Clock()
    s = SystemState(clock=clk)
    s.on_event(load("runtime-listening-started.json"))
    d = s.snapshot()["devices"][0]
    assert d["listening"]["active"] is True and d["listening"]["remaining_ms"] == 5000
    clk.t += 2.0
    assert s.snapshot()["devices"][0]["listening"]["remaining_ms"] == 3000
    s.on_event(load("runtime-listening-stopped.json"))
    assert s.snapshot()["devices"][0]["listening"]["active"] is False


def test_vision_and_chunk_events_and_offline():
    clk = Clock()
    s = SystemState(clock=clk)
    s.on_event(load("runtime-vision-fall.json"))
    s.on_event(load("runtime-audio-chunk-sent.json"))
    d = s.snapshot()["devices"][0]
    assert d["vision"]["label"] == "Fall" and d["vision"]["fall_confidence"] == 0.8812
    assert d["audio_device"]["chunks_sent"] == 1 and d["online"] is True
    clk.t += 20
    assert s.snapshot()["devices"][0]["online"] is False


def test_status_updates_and_lwt_offline():
    clk = Clock()
    s = SystemState(clock=clk)
    s.on_status("bedlight-01", {"state": "online", "source": "device",
                                "runtime": {"label": "Standing", "listening": True, "remaining_ms": 4000,
                                            "session_id": "x", "hold_ms": 5000}})
    d = s.snapshot()["devices"][0]
    assert d["online"] and d["listening"]["remaining_ms"] == 4000 and d["vision"]["label"] == "Standing"
    s.on_status("bedlight-01", {"state": "offline", "source": "device"})
    assert s.snapshot()["devices"][0]["online"] is False


def test_light_event_matches_contract():
    schema = json.loads((REPO / "contracts/v1/device-runtime-event.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(light_event("bedroom-01", False, "simulator", "manual"))


def test_api_light_and_state():
    from app.main import app

    with TestClient(app) as c:
        st = c.get("/api/v1/state").json()
        assert st["light"]["on"] is True and st["light"]["auto_control"] is False
        r = c.post("/api/v1/light", json={"on": False})
        assert r.json()["on"] is False and r.json()["changed_by"] == "simulator"
        assert c.get("/api/v1/state").json()["events"][0]["source"] == "simulator"
        with c.websocket_connect("/ws") as ws:
            assert "devices" in ws.receive_json()
