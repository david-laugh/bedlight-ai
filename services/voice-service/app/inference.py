"""Keyword-spotting inference interface (NOT IMPLEMENTED YET).

Planned runtime: Edge Impulse "Linux (x86_64)" deployment (.eim model file) executed with the
`edge_impulse_linux` Python SDK (ImpulseRunner) inside this container. The ESP32 Arduino library
export is NOT usable here. See docs/voice-inference-plan.md.

Until a model is deployed and integrated, this module never fabricates results:
`load_spotter()` returns None with a reason, and the API answers 503.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class InferenceResult:
    label: str                     # "off" | "unknown" | "noise"
    confidence: float              # 0..1 for `label`
    scores: dict[str, float]       # all class scores
    inference_ms: float
    model_name: str
    model_version: str


class KeywordSpotter(Protocol):
    name: str
    version: str

    def classify(self, pcm16_mono: bytes, sample_rate: int) -> InferenceResult: ...


@dataclass
class ModelStatus:
    loaded: bool
    reason: str
    model_dir: str
    model_files: list[str]


def model_status(model_dir: Path) -> ModelStatus:
    model_dir = Path(model_dir)
    files = sorted(p.name for p in model_dir.glob("*.eim")) if model_dir.is_dir() else []
    if not files:
        reason = f"no Edge Impulse .eim model in {model_dir}"
    else:
        reason = "model file present but runtime integration is not implemented yet"
    return ModelStatus(loaded=False, reason=reason, model_dir=str(model_dir), model_files=files)


def load_spotter(model_dir: Path) -> KeywordSpotter | None:
    """Return a ready KeywordSpotter, or None. Currently always None (not implemented)."""
    return None
