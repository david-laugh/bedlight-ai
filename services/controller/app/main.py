"""BedLight AI Controller (FastAPI).

- Virtual light owned on behalf of the simulator wall switch (button sensor):
    retained MQTT bedlight/v1/lights/{room_id}/state  (ESP32 subscribes: OFF -> stop vision/listening)
- MQTT subscribe: devices/+/events, devices/+/status, voice/+/events, lights/{room}/state (restore)
- Decision: VOICE_KEYWORD_DETECTED during (or just after) a lying session while the light is ON -> light OFF
- Polls voice-service for audio sessions and model status (auto control only when a model is loaded)
- WebSocket /ws (4 Hz snapshot), GET /api/v1/state, POST /api/v1/light (wall switch)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager

import httpx
import paho.mqtt.client as mqtt
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import __version__
from .fusion import decide
from .state import SystemState, light_event, light_state_msg

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("controller")

ROOM_ID = os.getenv("ROOM_ID", "bedroom-01")
VOICE_URL = os.getenv("VOICE_SERVICE_URL", "http://voice-service:8001")
LIGHT_TOPIC = f"bedlight/v1/lights/{ROOM_ID}/state"
state = SystemState(ROOM_ID)
_mqtt: mqtt.Client | None = None


def publish_light(trigger_event_id: str | None = None, session_id: str | None = None, extra: dict | None = None) -> None:
    """Retained light state (for ESP32 + restart) and a LIGHT_STATE_CHANGED event (for DB/simulator)."""
    light = state.snapshot()["light"]
    msg = light_state_msg(ROOM_ID, light, trigger_event_id, session_id)
    ev = light_event(ROOM_ID, light["on"], msg["source"], msg["reason"], extra=extra)
    if _mqtt:
        _mqtt.publish(LIGHT_TOPIC, json.dumps(msg), qos=1, retain=True)
        _mqtt.publish(f"bedlight/v1/simulator/{ROOM_ID}/events", json.dumps(ev), qos=1)
    state.on_event(ev)


def handle_voice(event: dict) -> dict:
    state.add_voice_event(event)
    light_on = state.snapshot()["light"]["on"]
    d = decide(event, light_on, state.device_view(event.get("device_id", "")), time.monotonic())
    state.add_decision(d.as_dict())
    log.info("voice decision accepted=%s reason=%s session=%s", d.accepted, d.reason, d.session_id)
    if d.accepted:
        state.set_light(False, "controller", f"voice keyword during lying session (conf {event.get('confidence')})")
        publish_light(event.get("event_id"), event.get("session_id"),
                      extra={"trigger_event_id": event.get("event_id"), "session_id": event.get("session_id"),
                             "keyword_to_decision_ms": d.keyword_to_decision_ms})
    return d.as_dict()


def _start_mqtt() -> mqtt.Client:
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="bedlight-controller")
    restored = {"done": False}

    def on_connect(client, _u, _f, rc, _p=None):
        log.info("MQTT connected rc=%s", rc)
        for t in ("bedlight/v1/devices/+/events", "bedlight/v1/devices/+/status", "bedlight/v1/voice/+/events",
                  LIGHT_TOPIC):
            client.subscribe(t, qos=1)

        def ensure_state():  # no retained state on the broker yet -> publish the default (light ON)
            time.sleep(1.5)
            if not restored["done"]:
                publish_light()

        threading.Thread(target=ensure_state, daemon=True).start()

    def on_message(_c, _u, msg):
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            log.warning("non-JSON message on %s", msg.topic)
            return
        parts = msg.topic.split("/")
        if msg.topic == LIGHT_TOPIC:
            restored["done"] = True
            if msg.retain:  # only adopt broker state on (re)subscribe; our own publishes are already applied
                state.restore_light(payload)
            return
        if len(parts) >= 5 and parts[2] == "devices" and parts[4] == "status":
            state.on_status(parts[3], payload)
        elif len(parts) >= 5 and parts[2] == "voice" and isinstance(payload, dict):
            if payload.get("event_type") == "VOICE_KEYWORD_DETECTED":
                handle_voice(payload)
        elif isinstance(payload, dict) and payload.get("event_type"):
            state.on_event(payload)

    c.on_connect = on_connect
    c.on_message = on_message
    c.reconnect_delay_set(1, 30)
    c.connect_async(os.getenv("MQTT_HOST", "mqtt-broker"), int(os.getenv("MQTT_PORT", "1883")), keepalive=30)
    c.loop_start()
    return c


def _poll_voice(stop: threading.Event) -> None:
    with httpx.Client(timeout=3.0) as http:
        while not stop.is_set():
            try:
                r = http.get(f"{VOICE_URL}/api/v1/audio/sessions", params={"limit": 1})
                sessions = r.json().get("sessions", []) if r.status_code == 200 else []
                state.set_server_audio(r.status_code == 200, sessions[0] if sessions else None)
                m = http.get(f"{VOICE_URL}/api/v1/model")
                state.set_voice_model(m.json() if m.status_code == 200 else None)
            except (httpx.HTTPError, ValueError):
                state.set_server_audio(False, None)
                state.set_voice_model(None)
            stop.wait(1.0 if state.any_listening() else 3.0)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _mqtt
    stop = threading.Event()
    if os.getenv("CONTROLLER_DISABLE_IO") != "1":
        _mqtt = _start_mqtt()
        threading.Thread(target=_poll_voice, args=(stop,), daemon=True).start()
    yield
    stop.set()
    if _mqtt:
        _mqtt.loop_stop()


app = FastAPI(title="BedLight AI Controller", version=__version__, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"])


class LightRequest(BaseModel):
    on: bool


class OccupantRequest(BaseModel):
    in_bed: bool


@app.get("/healthz")
def healthz():
    return {"status": "ok", "mqtt_connected": bool(_mqtt and _mqtt.is_connected())}


@app.get("/api/v1/state")
def get_state():
    return state.snapshot()


@app.post("/api/v1/light")
def set_light(req: LightRequest):
    """Simulator wall switch (button sensor)."""
    state.set_light(req.on, "simulator", "wall switch pressed in simulator")
    publish_light()
    return state.snapshot()["light"]


@app.post("/api/v1/occupant")
def set_occupant(req: OccupantRequest):
    """Simulator 취침/기상 buttons: place / remove the virtual person on the bed (display only)."""
    return state.set_occupant(req.in_bed)


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(state.snapshot())
            await asyncio.sleep(0.25)
    except (WebSocketDisconnect, RuntimeError):
        return
