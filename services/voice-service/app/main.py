"""BedLight AI Voice Service (FastAPI).

Implemented:
  GET  /health
  POST /api/v1/recordings          ESP32 WAV upload -> validate -> datasets/voice/raw/<label>/
  GET  /api/v1/recordings/stats
  GET  /api/v1/model               model deployment status (no model yet)
  POST /api/v1/recognize           returns 503 until a KWS model is deployed (no dummy results)
"""

from __future__ import annotations

import hmac
import logging
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Annotated

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from . import __version__
from .inference import load_spotter, model_status
from .settings import Settings
from .storage import DuplicateConflict, RecordingStore
from .wav_utils import WavFormatError, analyze_pcm16, format_errors, parse_wav, pcm16_samples

log = logging.getLogger("voice-service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

DEVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
CAPTURE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
UPLOAD_PATH = "/api/v1/recordings"
MULTIPART_OVERHEAD = 64 * 1024


def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message, **extra})


def _ms(t0: float, t1: float) -> float:
    return round((t1 - t0) * 1000.0, 3)


def create_app(settings: Settings | None = None, now: Callable[[], datetime] | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    clock = now or (lambda: datetime.now(timezone.utc))
    store = RecordingStore(settings.data_dir, settings.allowed_labels)
    spotter = load_spotter(settings.model_dir)

    app = FastAPI(title="BedLight AI Voice Service", version=__version__)
    app.state.settings = settings
    app.state.store = store

    @app.middleware("http")
    async def timing_and_size_guard(request: Request, call_next):
        request.state.t_arrival = time.perf_counter()
        if request.url.path == UPLOAD_PATH and request.method == "POST":
            cl = request.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > settings.max_upload_bytes + MULTIPART_OVERHEAD:
                return JSONResponse(
                    status_code=413,
                    content={"detail": {"error": "too_large", "message": f"request exceeds {settings.max_upload_bytes} bytes"}},
                )
        return await call_next(request)

    @app.get("/health")
    def health():
        writable = store.is_writable()
        body = {
            "status": "ok" if writable else "degraded",
            "service": "voice-service",
            "version": __version__,
            "data_dir_writable": writable,
            "model_loaded": spotter is not None,
        }
        return JSONResponse(status_code=200 if writable else 503, content=body)

    @app.post(UPLOAD_PATH, status_code=201)
    async def upload_recording(
        request: Request,
        audio: Annotated[UploadFile, File()],
        label: Annotated[str, Form()],
        device_id: Annotated[str, Form()],
        capture_id: Annotated[str, Form()],
        sample_rate: Annotated[int, Form()],
        record_ms: Annotated[int | None, Form()] = None,
        free_heap: Annotated[int | None, Form()] = None,
        free_psram: Annotated[int | None, Form()] = None,
        device_uptime_ms: Annotated[int | None, Form()] = None,
        firmware_version: Annotated[str | None, Form(max_length=32)] = None,
        x_device_token: Annotated[str | None, Header()] = None,
    ):
        t_handler = time.perf_counter()
        t_arrival = getattr(request.state, "t_arrival", t_handler)

        if settings.upload_token and not hmac.compare_digest(x_device_token or "", settings.upload_token):
            raise _err(401, "unauthorized", "missing or invalid X-Device-Token")
        if label not in settings.allowed_labels:
            raise _err(422, "invalid_label", f"label must be one of {list(settings.allowed_labels)}")
        if not DEVICE_ID_RE.fullmatch(device_id):
            raise _err(422, "invalid_device_id", "device_id must match ^[a-z0-9][a-z0-9-]{2,63}$")
        if not CAPTURE_ID_RE.fullmatch(capture_id):
            raise _err(422, "invalid_capture_id", "capture_id must match ^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
        if sample_rate != settings.sample_rate:
            raise _err(422, "invalid_sample_rate", f"sample_rate must be {settings.sample_rate}")

        data = await audio.read(settings.max_upload_bytes + 1)
        t_received = time.perf_counter()
        if not data:
            raise _err(400, "empty_file", "audio file is empty")
        if len(data) > settings.max_upload_bytes:
            raise _err(413, "too_large", f"audio exceeds {settings.max_upload_bytes} bytes")

        # ---------------- validation
        try:
            info = parse_wav(data)
        except WavFormatError as e:
            raise _err(422, "invalid_wav", str(e))
        errors = format_errors(info, settings.sample_rate, settings.channels, settings.bits_per_sample)
        if errors:
            raise _err(422, "unsupported_format", "; ".join(errors), errors=errors)
        if info.sample_rate != sample_rate:
            raise _err(422, "sample_rate_mismatch", "form sample_rate does not match WAV header")
        duration_ms = info.duration_ms
        if duration_ms < settings.min_duration_ms or duration_ms > settings.max_duration_ms:
            raise _err(
                422,
                "invalid_duration",
                f"duration {duration_ms:.0f} ms outside [{settings.min_duration_ms}, {settings.max_duration_ms}] ms",
            )
        quality = analyze_pcm16(pcm16_samples(data, info))
        if quality.all_zero:
            raise _err(422, "all_zero_audio", "all samples are zero - microphone produced no signal")
        t_validated = time.perf_counter()

        # ---------------- save
        captured_at = clock()
        record = {
            "device_id": device_id,
            "sample_rate": info.sample_rate,
            "channels": info.channels,
            "bits_per_sample": info.bits_per_sample,
            "num_samples": info.num_frames,
            "duration_ms": round(duration_ms, 3),
            "captured_at": captured_at.isoformat(),
            "validation_status": "valid_with_warnings" if quality.warnings else "valid",
            "warnings": quality.warnings,
            "quality": quality.to_dict(),
            "device": {
                "record_ms": record_ms,
                "free_heap": free_heap,
                "free_psram": free_psram,
                "device_uptime_ms": device_uptime_ms,
                "firmware_version": firmware_version,
            },
        }
        try:
            saved, created = store.save(
                label=label, capture_id=capture_id, wav=data, captured_at=captured_at, record=record
            )
        except DuplicateConflict as e:
            raise _err(409, "capture_id_conflict", str(e))
        t_saved = time.perf_counter()

        timing = {
            "receive_ms": _ms(t_arrival, t_received),
            "validate_ms": _ms(t_received, t_validated),
            "save_ms": _ms(t_validated, t_saved),
            "total_ms": _ms(t_arrival, t_saved),
        }
        body = {
            "capture_id": saved["capture_id"],
            "label": saved["label"],
            "filename": saved["filename"],
            "relative_path": saved["relative_path"],
            "duration_ms": saved["duration_ms"],
            "sample_rate": saved["sample_rate"],
            "channels": saved["channels"],
            "bits_per_sample": saved["bits_per_sample"],
            "num_samples": saved["num_samples"],
            "file_size": saved["file_size"],
            "status": "saved" if created else "duplicate",
            "validation_status": saved["validation_status"],
            "warnings": saved["warnings"],
            "quality": saved["quality"],
            "timing": timing,
        }
        log.info(
            "recording %s capture_id=%s label=%s file=%s status=%s warnings=%s timing=%s",
            "saved" if created else "duplicate", capture_id, label, saved["relative_path"],
            saved["validation_status"], saved["warnings"], timing,
        )
        server_timing = ", ".join(f"{k.removesuffix('_ms')};dur={v}" for k, v in timing.items())
        return JSONResponse(status_code=201 if created else 200, content=body, headers={"Server-Timing": server_timing})

    @app.get("/api/v1/recordings/stats")
    def stats():
        by_name = {r["relative_path"]: r for r in store.records()}
        labels = {}
        total = 0
        for label in settings.allowed_labels:
            files = sorted((store.raw / label).glob("*.wav"))
            duration = 0.0
            warned = 0
            for f in files:
                rel = f.relative_to(store.root).as_posix()
                rec = by_name.get(rel)
                if rec:
                    duration += rec.get("duration_ms", 0.0)
                    warned += 1 if rec.get("warnings") else 0
                else:
                    try:
                        duration += parse_wav(f.read_bytes()).duration_ms
                    except (OSError, WavFormatError):
                        pass
            labels[label] = {"files": len(files), "total_duration_ms": round(duration, 1), "with_warnings": warned}
            total += len(files)
        return {"labels": labels, "total_files": total, "metadata_records": len(by_name)}

    @app.get("/api/v1/model")
    def model():
        st = model_status(settings.model_dir)
        return {"loaded": spotter is not None, "reason": st.reason, "model_dir": st.model_dir, "model_files": st.model_files}

    @app.post("/api/v1/recognize")
    def recognize():
        if spotter is None:
            raise _err(503, "model_not_deployed", model_status(settings.model_dir).reason)
        raise _err(501, "not_implemented", "inference endpoint not implemented")  # pragma: no cover

    return app
# Run with the factory: uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8001
