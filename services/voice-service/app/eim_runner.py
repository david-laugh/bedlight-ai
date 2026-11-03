"""Minimal client for Edge Impulse Linux `.eim` model executables (no SDK dependency).

Protocol (same as edge_impulse_linux.runner.ImpulseRunner):
  1. start `<model.eim> <socket_path>`; the model creates a Unix domain socket at socket_path
  2. connect, send JSON messages with an increasing "id"; each response is JSON terminated by b"\\x00"
     {"hello": 1}            -> model info: {"project": {...}, "model_parameters": {...}}
     {"classify": [f0, f1...]} -> {"result": {"classification": {label: score}}, "timing": {...}}
For audio models the features are the raw int16 sample values of one window.

Not verified against a real `.eim` from this project yet (no model deployed at the time of writing);
the unit tests use a fake model process that speaks the same protocol.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


class EimError(RuntimeError):
    pass


class EimRunner:
    def __init__(self, model_path: Path, start_timeout_s: float = 15.0):
        self.model_path = Path(model_path)
        self.start_timeout_s = start_timeout_s
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._tmpdir: str | None = None
        self._id = 0
        self._lock = threading.Lock()
        self.info: dict = {}

    def _command(self, socket_path: str) -> list[str]:
        if self.model_path.suffix == ".py":  # test double
            return [sys.executable, str(self.model_path), socket_path]
        return [str(self.model_path), socket_path]

    def start(self) -> dict:
        if not hasattr(socket, "AF_UNIX"):
            raise EimError("Unix domain sockets are not available on this platform")
        if self.model_path.suffix != ".py" and not os.access(self.model_path, os.X_OK):
            # bind mounts from Windows may drop the exec bit: run from a private executable copy
            self._tmpdir = tempfile.mkdtemp(prefix="eim-")
            copy = Path(self._tmpdir) / self.model_path.name
            shutil.copy2(self.model_path, copy)
            copy.chmod(0o755)
            self.model_path = copy
        self._tmpdir = self._tmpdir or tempfile.mkdtemp(prefix="eim-")
        sock_path = os.path.join(self._tmpdir, "runner.sock")
        self._proc = subprocess.Popen(self._command(sock_path), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        deadline = time.monotonic() + self.start_timeout_s
        while not os.path.exists(sock_path):
            if self._proc.poll() is not None:
                err = (self._proc.stderr.read() or b"").decode(errors="replace")[:300]
                raise EimError(f"model process exited with {self._proc.returncode}: {err}")
            if time.monotonic() > deadline:
                self.stop()
                raise EimError("model socket did not appear in time")
            time.sleep(0.05)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(sock_path)
        self.info = self._send({"hello": 1})
        if not self.info.get("success", True) or "model_parameters" not in self.info:
            raise EimError(f"unexpected hello response: {str(self.info)[:200]}")
        return self.info

    def _send(self, msg: dict) -> dict:
        with self._lock:
            if not self._sock:
                raise EimError("runner not started")
            self._id += 1
            msg = dict(msg, id=self._id)
            self._sock.sendall(json.dumps(msg).encode())
            data = b""
            while True:
                chunk = self._sock.recv(65536)
                if not chunk:
                    raise EimError("model closed the connection")
                data += chunk
                if data.endswith(b"\x00"):
                    break
            return json.loads(data[:-1].decode())

    def classify(self, features: list[int]) -> dict:
        resp = self._send({"classify": features})
        if not resp.get("success", True):
            raise EimError(f"classify failed: {str(resp)[:200]}")
        return resp

    def stop(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None
