"""Fake Edge Impulse `.eim` model process for tests (speaks the same Unix-socket JSON protocol).

Classifies by loudness only: RMS > 3000 -> "off" 0.95, otherwise "noise" 0.9. NOT a real model.
"""

import json
import math
import os
import socket
import sys

path = sys.argv[1]
if os.path.exists(path):
    os.unlink(path)
srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
srv.bind(path)
srv.listen(1)
conn, _ = srv.accept()
dec = json.JSONDecoder()
buf = ""
while True:
    chunk = conn.recv(1 << 20)
    if not chunk:
        break
    buf += chunk.decode()
    while buf:
        try:
            msg, end = dec.raw_decode(buf)
        except ValueError:
            break
        buf = buf[end:]
        if "hello" in msg:
            resp = {"success": True, "id": msg["id"],
                    "project": {"name": "fake-kws", "id": 1, "deploy_version": 1, "owner": "test"},
                    "model_parameters": {"input_features_count": 16000, "frequency": 16000, "sensor": 1,
                                         "labels": ["noise", "off", "unknown"], "interval_ms": 0.0625}}
        elif "classify" in msg:
            f = msg["classify"]
            rms = math.sqrt(sum(x * x for x in f) / len(f))
            off = 0.95 if rms > 3000 else 0.02
            resp = {"success": True, "id": msg["id"], "timing": {"dsp": 2, "classification": 3},
                    "result": {"classification": {"off": off, "noise": round(1 - off - 0.01, 2), "unknown": 0.01}}}
        else:
            resp = {"success": False, "id": msg.get("id"), "error": "unknown message"}
        conn.sendall(json.dumps(resp).encode() + b"\x00")
