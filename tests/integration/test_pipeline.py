"""End-to-end pipeline test with the mock device (source=mock, synthetic audio).

Requires the compose stack to be running (services-ci integration job):
  docker compose up -d --wait mqtt-broker voice-service controller db-writer simulator-web
Runs mock-device scenario B (Fall 0 s, Fall 3 s, Standing 6 s, stop 8 s) and checks:
  controller state, voice-service session integrity, db-writer stored events.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
CONTROLLER = "http://127.0.0.1:8000"
VOICE = "http://127.0.0.1:8001"
EVENTS_FILE = Path(os.getenv("DB_WRITER_EVENTS_FILE", str(REPO / "runtime-data/db-writer/events.jsonl")))


def set_light(on: bool) -> None:
    httpx.post(f"{CONTROLLER}/api/v1/light", json={"on": on}).raise_for_status()
    time.sleep(1)


def run_mock(*args: str) -> str:
    proc = subprocess.run(["docker", "compose", "--profile", "test", "run", "--rm", "mock-device", *args],
                          cwd=REPO, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def retained_light_state() -> dict:
    out = subprocess.run(["docker", "compose", "exec", "-T", "mqtt-broker", "mosquitto_sub", "-t",
                          "bedlight/v1/lights/bedroom-01/state", "-C", "1", "-W", "5"],
                         cwd=REPO, capture_output=True, text=True, timeout=30).stdout
    return json.loads(out.strip().splitlines()[-1])


def test_scenario_b_end_to_end():
    set_light(True)
    proc = subprocess.run(
        ["docker", "compose", "--profile", "test", "run", "--rm", "mock-device", "--scenario", "B", "--speed", "2"],
        cwd=REPO, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    time.sleep(3)

    sessions = httpx.get(f"{VOICE}/api/v1/audio/sessions", params={"device_id": "mock-xiao-01"}).json()["sessions"]
    assert sessions, "voice-service received no session"
    s = sessions[0]
    assert s["final_received"] is True
    assert s["missing_sequences"] == []
    assert s["discontinuities"] == []
    assert s["complete"] is True
    assert s["received_samples"] >= 7 * 16000

    state = httpx.get(f"{CONTROLLER}/api/v1/state").json()
    dev = next(d for d in state["devices"] if d["device_id"] == "mock-xiao-01")
    assert dev["source"] == "mock"
    assert dev["listening"]["active"] is False
    assert dev["audio_device"]["chunks_sent"] >= 8
    assert state["light"]["auto_control"] is False  # no voice model -> no automatic light-off

    rows = [json.loads(l) for l in EVENTS_FILE.read_text().splitlines()]
    types = {r["event_type"] for r in rows if r["device_id"] == "mock-xiao-01"}
    assert {"VISION_FALL_DETECTED", "LISTENING_STARTED", "LISTENING_EXTENDED", "AUDIO_CHUNK_SENT",
            "LISTENING_STOPPED"} <= types
    assert all("audio" not in r or r["payload"] for r in rows)


def test_voice_keyword_while_lying_turns_light_off_and_device_goes_idle():
    """Scripted keyword (source=mock, no model) -> controller decision -> retained light OFF -> mock stops."""
    set_light(True)
    out = run_mock("--scenario", "B", "--speed", "2", "--voice-keyword-at", "2000")
    assert "light OFF -> IDLE" in out
    time.sleep(2)

    state = httpx.get(f"{CONTROLLER}/api/v1/state").json()
    assert state["light"]["on"] is False and state["light"]["changed_by"] == "controller"
    assert state["decisions"][0]["accepted"] is True

    retained = retained_light_state()
    assert retained["light_on"] is False and retained["source"] == "controller"

    rows = [json.loads(l) for l in EVENTS_FILE.read_text().splitlines()]
    stops = [r for r in rows if r["event_type"] == "LISTENING_STOPPED" and r["device_id"] == "mock-xiao-01"]
    assert stops[-1]["payload"]["data"]["reason"] == "light_off"
    assert any(r["event_type"] == "LIGHT_STATE_CHANGED" and r["source"] == "controller" for r in rows)
    assert any(r["event_type"] == "VOICE_KEYWORD_DETECTED" for r in rows)

    # keyword while the light is already off is ignored
    set_light(False)
    run_mock("--scenario", "A", "--speed", "2", "--voice-keyword-at", "1000")
    time.sleep(2)
    assert httpx.get(f"{CONTROLLER}/api/v1/state").json()["light"]["on"] is False
    set_light(True)
