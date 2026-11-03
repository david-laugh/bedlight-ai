"""End-to-end pipeline test with the mock device (source=mock, synthetic audio).

Requires the compose stack to be running (services-ci integration job):
  docker compose up -d --wait mqtt-broker voice-service controller db-writer simulator-web
Runs mock-device scenario B (Fall 0 s, Fall 3 s, Standing 6 s, stop 8 s) and checks:
  controller state, voice-service session integrity, db-writer stored events.
"""

import json
import subprocess
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[2]
CONTROLLER = "http://127.0.0.1:8000"
VOICE = "http://127.0.0.1:8001"


def test_scenario_b_end_to_end():
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

    rows = [json.loads(l) for l in (REPO / "runtime-data/db-writer/events.jsonl").read_text().splitlines()]
    types = {r["event_type"] for r in rows if r["device_id"] == "mock-xiao-01"}
    assert {"VISION_FALL_DETECTED", "LISTENING_STARTED", "LISTENING_EXTENDED", "AUDIO_CHUNK_SENT",
            "LISTENING_STOPPED"} <= types
    assert all("audio" not in r or r["payload"] for r in rows)
