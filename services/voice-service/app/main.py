"""BedLight AI Voice Service (FastAPI).

Implemented:
  GET  /health
  POST /api/v1/recordings          ESP32 WAV upload -> validate -> datasets/voice/raw/<label>/
  GET  /api/v1/recordings/stats
  POST /api/v1/audio/chunks        RUNTIME_AUDIO: Fall-triggered 1 s chunks (session/sequence/sample_start)
  GET  /api/v1/audio/sessions[/{id}[/audio.wav|/window]]  integrity summary + reconstruction
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
from fastapi.responses import JSONResponse, Response

from . import __version__
from .chunks import ChunkConflict, ChunkInfo, ChunkStore, sha256
from .inference import load_spotter, model_status
from .settings import Settings
from .storage import DuplicateConflict, RecordingStore
from .wav_utils import WavFormatError, analyze_pcm16, format_errors, parse_wav, pcm16_samples, pcm16_wav_bytes

log = logging.getLogger("voice-service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

DEVICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
CAPTURE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
ROOM_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
UPLOAD_PATH = "/api/v1/recordings"
CHUNK_PATH = "/api/v1/audio/chunks"
MAX_SEQUENCE = 100_000
MULTIPART_OVERHEAD = 64 * 1024


def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": code, "message": message, **extra})


def _ms(t0: float, t1: float) -> float:
    return round((t1 - t0) * 1000.0, 3)


def create_app(settings: Settings | None = None, now: Callable[[], datetime] | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    clock = now or (lambda: datetime.now(timezone.utc))
    store = RecordingStore(settings.data_dir, settings.allowed_labels)
    chunk_store = ChunkStore(settings.runtime_dir)
    spotter = load_spotter(settings.model_dir)

    app = FastAPI(title="BedLight AI Voice Service", version=__version__)
    app.state.settings = settings
    app.state.store = store
    app.state.chunk_store = chunk_store

    @app.middleware("http")
    async def timing_and_size_guard(request: Request, call_next):
        request.state.t_arrival = time.perf_counter()
        if request.url.path in (UPLOAD_PATH, CHUNK_PATH) and request.method == "POST":
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

    # ------------------------------------------------------------------ RUNTIME_AUDIO chunks
    def _parse_bool(v: str) -> bool:
        if v.lower() in ("true", "1"):
            return True
        if v.lower() in ("false", "0"):
            return False
        raise _err(422, "invalid_is_final", "is_final must be true/false")

    def _opt_int(v: str | None) -> int | None:
        if v is None or v == "":
            return None
        try:
            return int(v)
        except ValueError:
            raise _err(422, "invalid_field", f"not an integer: {v!r}")

    @app.post(CHUNK_PATH, status_code=201)
    async def upload_chunk(
        request: Request,
        audio: Annotated[UploadFile, File()],
        device_id: Annotated[str, Form()],
        session_id: Annotated[str, Form()],
        sequence: Annotated[int, Form()],
        sample_rate: Annotated[int, Form()],
        channels: Annotated[int, Form()],
        bits_per_sample: Annotated[int, Form()],
        duration_ms: Annotated[int, Form()],
        sample_start: Annotated[int, Form()],
        capture_timestamp_ms: Annotated[int, Form()],
        is_final: Annotated[str, Form()],
        capture_epoch_ms: Annotated[str | None, Form()] = None,
        dma_overflows: Annotated[int, Form()] = 0,
        dropped_chunks_before: Annotated[int, Form()] = 0,
        room_id: Annotated[str | None, Form()] = None,
        x_device_token: Annotated[str | None, Header()] = None,
    ):
        t_handler = time.perf_counter()
        t_arrival = getattr(request.state, "t_arrival", t_handler)
        if settings.upload_token and not hmac.compare_digest(x_device_token or "", settings.upload_token):
            raise _err(401, "unauthorized", "missing or invalid X-Device-Token")
        if not DEVICE_ID_RE.fullmatch(device_id):
            raise _err(422, "invalid_device_id", "device_id must match ^[a-z0-9][a-z0-9-]{2,63}$")
        if not CAPTURE_ID_RE.fullmatch(session_id):
            raise _err(422, "invalid_session_id", "session_id must match ^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
        if room_id and not ROOM_ID_RE.fullmatch(room_id):
            raise _err(422, "invalid_room_id", "room_id must match ^[a-z0-9][a-z0-9-]{1,63}$")
        if not 0 <= sequence <= MAX_SEQUENCE:
            raise _err(422, "invalid_sequence", f"sequence must be in [0, {MAX_SEQUENCE}]")
        if sample_start < 0 or dma_overflows < 0 or dropped_chunks_before < 0:
            raise _err(422, "invalid_field", "negative counters are not allowed")
        if (sample_rate, channels, bits_per_sample) != (settings.sample_rate, settings.channels, settings.bits_per_sample):
            raise _err(422, "unsupported_format",
                       f"chunks must be {settings.sample_rate} Hz / {settings.channels} ch / {settings.bits_per_sample}-bit")
        final = _parse_bool(is_final)
        epoch = _opt_int(capture_epoch_ms)

        max_bytes = 44 + settings.sample_rate * 2 * settings.max_chunk_ms // 1000 + 4096
        data = await audio.read(max_bytes + 1)
        t_received = time.perf_counter()
        if not data:
            raise _err(400, "empty_file", "audio part is empty")
        if len(data) > max_bytes:
            raise _err(413, "too_large", f"chunk exceeds {settings.max_chunk_ms} ms")
        try:
            info = parse_wav(data)
        except WavFormatError as e:
            raise _err(422, "invalid_wav", str(e))
        errors = format_errors(info, settings.sample_rate, settings.channels, settings.bits_per_sample)
        if errors:
            raise _err(422, "unsupported_format", "; ".join(errors), errors=errors)
        samples = info.num_frames
        if samples == 0 and not final:
            raise _err(422, "empty_chunk", "only the final chunk may contain 0 samples")
        if abs(round(1000 * samples / settings.sample_rate) - duration_ms) > 1:
            raise _err(422, "duration_mismatch", f"duration_ms={duration_ms} but WAV has {samples} samples")
        t_validated = time.perf_counter()

        chunk = ChunkInfo(
            sequence=sequence, sample_start=sample_start, samples=samples, sha256=sha256(data), file="",
            received_at=clock().isoformat(), capture_timestamp_ms=capture_timestamp_ms, capture_epoch_ms=epoch,
            dma_overflows=dma_overflows, dropped_chunks_before=dropped_chunks_before, is_final=final,
        )
        try:
            sess, created = chunk_store.add(device_id=device_id, session_id=session_id, room_id=room_id or None,
                                            sample_rate=sample_rate, info=chunk, wav=data, now=clock())
        except ChunkConflict as e:
            raise _err(409, "chunk_conflict", str(e))
        t_saved = time.perf_counter()
        timing = {
            "receive_ms": _ms(t_arrival, t_received),
            "validate_ms": _ms(t_received, t_validated),
            "save_ms": _ms(t_validated, t_saved),
            "total_ms": _ms(t_arrival, t_saved),
        }
        if created:
            chunk_store.set_timing(sess, sequence, timing)
        summary = sess.summary()
        body = {
            "status": "saved" if created else "duplicate",
            "session_id": session_id,
            "device_id": device_id,
            "sequence": sequence,
            "sample_start": sample_start,
            "samples": samples,
            "duration_ms": duration_ms,
            "is_final": final,
            "session": summary,
            "timing": timing,
            "inference": {"performed": False, "reason": model_status(settings.model_dir).reason},
        }
        log.info("chunk %s session=%s seq=%d start=%d samples=%d final=%s missing=%s disc=%d timing=%s",
                 body["status"], session_id, sequence, sample_start, samples, final, summary["missing_sequences"],
                 len(summary["discontinuities"]), timing)
        return JSONResponse(status_code=201 if created else 200, content=body)

    @app.get("/api/v1/audio/sessions")
    def list_sessions(device_id: str | None = None, limit: int = 20):
        return {"sessions": [s.summary() for s in chunk_store.list(device_id, max(1, min(limit, 200)))]}

    def _session_or_404(session_id: str):
        s = chunk_store.get(session_id)
        if s is None:
            raise _err(404, "not_found", "unknown session_id")
        return s

    @app.get("/api/v1/audio/sessions/{session_id}")
    def get_session(session_id: str):
        s = _session_or_404(session_id)
        d = s.summary()
        d["chunks"] = [
            {k: v for k, v in c.__dict__.items() if k != "file"}
            for c in sorted(s.chunks.values(), key=lambda c: c.sequence)
        ]
        return d

    @app.get("/api/v1/audio/sessions/{session_id}/audio.wav")
    def session_audio(session_id: str, fill_gaps: bool = True):
        s = _session_or_404(session_id)
        pcm, gaps = chunk_store.assemble(s)
        if gaps and not fill_gaps:
            raise _err(409, "gaps_present", "session has missing audio", gaps=gaps)
        return Response(content=pcm16_wav_bytes(pcm, s.sample_rate), media_type="audio/wav",
                        headers={"X-Gap-Count": str(len(gaps)), "X-Gap-Samples": str(sum(g[1] for g in gaps))})

    @app.get("/api/v1/audio/sessions/{session_id}/window")
    def session_window(session_id: str, start_sample: int = 0, duration_ms: int = 2000, allow_gaps: bool = False):
        """Contiguous window (e.g. 2 s for a clap/keyword model) that may span chunk boundaries."""
        s = _session_or_404(session_id)
        if start_sample < 0 or not 1 <= duration_ms <= 10000:
            raise _err(422, "invalid_window", "start_sample >= 0 and 1 <= duration_ms <= 10000")
        n = s.sample_rate * duration_ms // 1000
        if start_sample + n > s.span_samples():
            raise _err(416, "window_out_of_range", f"window ends after received audio ({s.span_samples()} samples)")
        pcm, gaps = chunk_store.assemble(s, start_sample, n)
        if gaps and not allow_gaps:
            raise _err(409, "gaps_present", "window overlaps missing audio", gaps=gaps)
        return Response(content=pcm16_wav_bytes(pcm, s.sample_rate), media_type="audio/wav",
                        headers={"X-Gap-Count": str(len(gaps))})

    return app
# Run with the factory: uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8001
