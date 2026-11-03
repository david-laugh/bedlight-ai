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


# ---------------------------------------------------------------- light-gated voice control
from app.fusion import decide  # noqa: E402
from app.state import light_state_msg  # noqa: E402

SID = "5d0c7a3e-1f2b-4c3d-9e4f-5a6b7c8d9e0f"


def voice_event(source="voice-service", sid=SID):
    e = load("runtime-voice-keyword-detected.json")
    e["source"] = source
    e["session_id"] = sid
    return e


def lying_state(clk):
    s = SystemState(clock=clk)
    s.on_event(load("runtime-listening-started.json"))  # session SID, device bedlight-01
    return s


def test_decide_accepts_keyword_in_active_session():
    clk = Clock()
    s = lying_state(clk)
    d = decide(voice_event(), True, s.device_view("bedlight-01"), clk())
    assert d.accepted and "active" in d.reason


def test_decide_accepts_keyword_shortly_after_session_end_but_not_later():
    clk = Clock()
    s = lying_state(clk)
    s.on_event(load("runtime-listening-stopped.json"))
    assert decide(voice_event(), True, s.device_view("bedlight-01"), clk() + 5).accepted
    assert not decide(voice_event(), True, s.device_view("bedlight-01"), clk() + 30).accepted


def test_decide_rejections():
    clk = Clock()
    s = lying_state(clk)
    dv = s.device_view("bedlight-01")
    assert decide(voice_event(), False, dv, clk()).reason == "light already off"
    assert not decide(voice_event(sid="00000000-0000-4000-8000-000000000000"), True, dv, clk()).accepted
    assert not decide(voice_event(source="device"), True, dv, clk()).accepted
    assert not decide(voice_event(), True, None, clk()).accepted
    assert decide(voice_event(source="mock"), True, dv, clk()).accepted  # pipeline tests


def test_light_state_message_matches_contract():
    schema = json.loads((REPO / "contracts/v1/light-state.schema.json").read_text(encoding="utf-8"))
    s = SystemState()
    s.set_light(False, "controller", "voice keyword during lying session")
    Draft202012Validator(schema).validate(light_state_msg("bedroom-01", s.snapshot()["light"], "e", SID))
    s.set_light(True, "simulator", "wall switch")
    Draft202012Validator(schema).validate(light_state_msg("bedroom-01", s.snapshot()["light"]))


def test_handle_voice_turns_light_off_once():
    import app.main as m

    m.state = SystemState()
    m.state.on_event(load("runtime-listening-started.json"))
    m.state.set_light(True, "simulator", "test")
    first = m.handle_voice(voice_event())
    second = m.handle_voice(voice_event())
    snap = m.state.snapshot()
    assert first["accepted"] and not second["accepted"] and second["reason"] == "light already off"
    assert snap["light"]["on"] is False and snap["light"]["changed_by"] == "controller"
    assert snap["decisions"][0]["reason"] == "light already off"
    assert snap["events"][0]["event_type"] == "LIGHT_STATE_CHANGED"


def test_voice_model_status_drives_auto_control_flag():
    s = SystemState()
    assert s.snapshot()["light"]["auto_control"] is False
    s.set_voice_model({"loaded": True})
    assert s.snapshot()["light"]["auto_control"] is True
