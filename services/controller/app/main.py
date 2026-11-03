"""BedLight AI Controller (FastAPI).

- MQTT subscribe bedlight/v1/devices/+/events and /status -> aggregated state
- Polls voice-service /api/v1/audio/sessions for server-side audio reception status
- WebSocket /ws pushes the state snapshot to simulator-web (4 Hz)
- POST /api/v1/light : manual light control from the simulator (source=simulator)
Automatic light-off is NOT implemented: the server keyword model is not deployed yet.
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
from .state import SystemState, light_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("controller")

ROOM_ID = os.getenv("ROOM_ID", "bedroom-01")
VOICE_URL = os.getenv("VOICE_SERVICE_URL", "http://voice-service:8001")
state = SystemState(ROOM_ID)
_mqtt: mqtt.Client | None = None


def _start_mqtt() -> mqtt.Client:
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="bedlight-controller")

    def on_connect(client, _u, _f, rc, _p=None):
        log.info("MQTT connected rc=%s", rc)
        client.subscribe("bedlight/v1/devices/+/events", qos=1)
        client.subscribe("bedlight/v1/devices/+/status", qos=1)

    def on_message(_c, _u, msg):
        try:
            payload = json.loads(msg.payload)
        except ValueError:
            log.warning("non-JSON message on %s", msg.topic)
            return
        parts = msg.topic.split("/")
        if len(parts) >= 5 and parts[4] == "status":
            state.on_status(parts[3], payload)
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
            except (httpx.HTTPError, ValueError):
                state.set_server_audio(False, None)
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


@app.get("/healthz")
def healthz():
    return {"status": "ok", "mqtt_connected": bool(_mqtt and _mqtt.is_connected())}


@app.get("/api/v1/state")
def get_state():
    return state.snapshot()


@app.post("/api/v1/light")
def set_light(req: LightRequest):
    light = state.set_light(req.on, "simulator")
    ev = light_event(ROOM_ID, req.on, "simulator", "manual toggle in simulator")
    if _mqtt:
        _mqtt.publish(f"bedlight/v1/simulator/{ROOM_ID}/events", json.dumps(ev), qos=1)
    state.on_event(ev)
    return light


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(state.snapshot())
            await asyncio.sleep(0.25)
    except (WebSocketDisconnect, RuntimeError):
        return
