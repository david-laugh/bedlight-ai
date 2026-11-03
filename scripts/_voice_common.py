"""Make services/voice-service/app/wav_utils.py importable from host scripts.

The WAV parser lives in the voice-service package so that the server and the
offline tools validate files with exactly the same code (stdlib only, no install needed).
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SERVICE = REPO_ROOT / "services" / "voice-service"
if str(_SERVICE) not in sys.path:
    sys.path.insert(0, str(_SERVICE))

from app import wav_utils  # noqa: E402

__all__ = ["REPO_ROOT", "wav_utils"]
