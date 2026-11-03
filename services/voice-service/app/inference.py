"""Keyword-spotting inference (Edge Impulse Linux `.eim`).

The model is loaded from <model_dir>/*.eim (models/voice/deploy on the host). Without a model nothing is
classified and nothing is published — the service never fabricates results.

Note: in this project the `off` class was recorded as a DOUBLE HAND CLAP (label name kept as "off").
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .eim_runner import EimError, EimRunner

log = logging.getLogger("voice-service.inference")


@dataclass
class InferenceResult:
    label: str                     # top label
    confidence: float              # score of `label`
    scores: dict[str, float]       # all class scores
    inference_ms: float
    model_name: str
    model_version: str


class KeywordSpotter(Protocol):
    name: str
    version: str
    window_samples: int
    sample_rate: int
    labels: list[str]

    def classify(self, samples: list[int]) -> InferenceResult: ...


@dataclass
class ModelStatus:
    loaded: bool
    reason: str
    model_dir: str
    model_files: list[str]
    info: dict = field(default_factory=dict)


class EimSpotter:
    def __init__(self, path: Path):
        self.runner = EimRunner(path)
        info = self.runner.start()
        mp = info["model_parameters"]
        proj = info.get("project", {})
        self.name = str(proj.get("name") or path.stem)[:64]
        self.version = f"ei-{proj.get('id', '?')}-v{proj.get('deploy_version', '?')}"[:32]
        self.window_samples = int(mp["input_features_count"])
        self.sample_rate = int(round(mp.get("frequency", 16000)))
        self.labels = list(mp.get("labels", []))
        self.sensor = mp.get("sensor")
        self.path = path

    def classify(self, samples: list[int]) -> InferenceResult:
        t0 = time.perf_counter()
        resp = self.runner.classify(list(samples))
        ms = (time.perf_counter() - t0) * 1000
        scores = {k: float(v) for k, v in resp["result"]["classification"].items()}
        top = max(scores, key=scores.get)
        timing = resp.get("timing", {})
        model_ms = timing.get("dsp", 0) + timing.get("classification", 0)
        return InferenceResult(top, scores[top], scores, float(model_ms or ms), self.name, self.version)

    def close(self) -> None:
        self.runner.stop()


def _eim_files(model_dir: Path) -> list[Path]:
    model_dir = Path(model_dir)
    return sorted(model_dir.glob("*.eim")) if model_dir.is_dir() else []


def load_spotter(model_dir: Path, sample_rate: int = 16000, keyword: str = "off"):
    """Return (spotter | None, reason)."""
    files = _eim_files(model_dir)
    if not files:
        return None, f"no Edge Impulse .eim model in {model_dir}"
    path = files[0]
    try:
        sp = EimSpotter(path)
    except (EimError, OSError, KeyError, ValueError) as e:
        log.error("failed to load %s: %s", path.name, e)
        return None, f"failed to load {path.name}: {e}"
    if sp.sample_rate != sample_rate:
        sp.close()
        return None, f"{path.name}: model frequency {sp.sample_rate} Hz != {sample_rate} Hz"
    if keyword not in sp.labels:
        sp.close()
        return None, f"{path.name}: label '{keyword}' not in model labels {sp.labels}"
    log.info("loaded %s (%s) window=%d labels=%s", sp.name, sp.version, sp.window_samples, sp.labels)
    return sp, f"loaded {path.name}"


def model_status(model_dir: Path, spotter=None, reason: str = "") -> ModelStatus:
    files = [p.name for p in _eim_files(model_dir)]
    info = {}
    if spotter is not None:
        info = {"name": spotter.name, "version": spotter.version, "labels": spotter.labels,
                "window_samples": spotter.window_samples, "sample_rate": spotter.sample_rate}
    if not reason:
        reason = "model loaded" if spotter else (f"no Edge Impulse .eim model in {model_dir}" if not files
                                                 else "model file present but not loaded")
    return ModelStatus(loaded=spotter is not None, reason=reason, model_dir=str(model_dir), model_files=files, info=info)
