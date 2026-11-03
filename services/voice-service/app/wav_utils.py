"""WAV parsing, format validation and signal-quality analysis (standard library only).

Shared by:
  - voice-service upload validation (app/main.py)
  - scripts/check_wav.py
  - scripts/prepare_voice_dataset.py
"""

from __future__ import annotations

import math
import struct
import sys
import wave
from array import array
from dataclasses import asdict, dataclass, field
from pathlib import Path

WAVE_FORMAT_PCM = 0x0001
WAVE_FORMAT_IEEE_FLOAT = 0x0003
WAVE_FORMAT_EXTENSIBLE = 0xFFFE

# Reference thresholds (informational, not ground truth about speech presence).
SILENCE_RMS_DBFS = -50.0
DC_OFFSET_WARN = 2000
CLIP_LEVEL = 32767


class WavFormatError(ValueError):
    """The bytes are not a well-formed RIFF/WAVE file."""


@dataclass(frozen=True)
class WavInfo:
    audio_format: int
    channels: int
    sample_rate: int
    bits_per_sample: int
    block_align: int
    byte_rate: int
    data_offset: int
    data_size: int

    @property
    def num_frames(self) -> int:
        return self.data_size // self.block_align

    @property
    def duration_ms(self) -> float:
        return 1000.0 * self.num_frames / self.sample_rate if self.sample_rate else 0.0


@dataclass
class QualityMetrics:
    num_samples: int
    rms: float          # 0..1 relative to full scale
    rms_dbfs: float
    peak: int           # absolute sample value 0..32768
    peak_dbfs: float
    clipped_samples: int
    clipping: bool
    dc_offset: float
    all_zero: bool
    likely_silent: bool
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rms"] = round(self.rms, 6)
        d["rms_dbfs"] = round(self.rms_dbfs, 2)
        d["peak_dbfs"] = round(self.peak_dbfs, 2)
        d["dc_offset"] = round(self.dc_offset, 2)
        return d


def parse_wav(data: bytes) -> WavInfo:
    """Parse RIFF/WAVE chunks. Raises WavFormatError on any structural problem."""
    if len(data) < 12:
        raise WavFormatError("file too small to be a WAV file")
    riff, _riff_size, wave_id = struct.unpack_from("<4sI4s", data, 0)
    if riff != b"RIFF" or wave_id != b"WAVE":
        raise WavFormatError("not a RIFF/WAVE file")

    fmt = None
    data_chunk = None
    pos = 12
    while pos + 8 <= len(data):
        chunk_id, size = struct.unpack_from("<4sI", data, pos)
        body = pos + 8
        if chunk_id == b"fmt ":
            if size < 16 or body + size > len(data):
                raise WavFormatError("fmt chunk truncated")
            audio_format, channels, sample_rate, byte_rate, block_align, bits = struct.unpack_from(
                "<HHIIHH", data, body
            )
            if audio_format == WAVE_FORMAT_EXTENSIBLE and size >= 40:
                # SubFormat GUID: first two bytes carry the real format tag.
                (audio_format,) = struct.unpack_from("<H", data, body + 24)
            fmt = (audio_format, channels, sample_rate, byte_rate, block_align, bits)
        elif chunk_id == b"data":
            if body + size > len(data):
                raise WavFormatError(
                    f"data chunk truncated: header declares {size} bytes, file has {len(data) - body}"
                )
            data_chunk = (body, size)
            break
        pos = body + size + (size & 1)  # chunks are word aligned

    if fmt is None:
        raise WavFormatError("missing fmt chunk")
    if data_chunk is None:
        raise WavFormatError("missing data chunk")
    audio_format, channels, sample_rate, byte_rate, block_align, bits = fmt
    if channels == 0 or block_align == 0 or sample_rate == 0:
        raise WavFormatError("invalid fmt chunk (zero channels/block_align/sample_rate)")
    if data_chunk[1] % block_align != 0:
        raise WavFormatError("data size is not a whole number of sample frames")
    return WavInfo(
        audio_format=audio_format,
        channels=channels,
        sample_rate=sample_rate,
        bits_per_sample=bits,
        block_align=block_align,
        byte_rate=byte_rate,
        data_offset=data_chunk[0],
        data_size=data_chunk[1],
    )


def format_errors(info: WavInfo, sample_rate: int = 16000, channels: int = 1, bits: int = 16) -> list[str]:
    """Return a list of human readable mismatches against the required capture format."""
    errors = []
    if info.audio_format != WAVE_FORMAT_PCM:
        errors.append(f"audio format must be PCM (1), got {info.audio_format}")
    if info.channels != channels:
        errors.append(f"channels must be {channels} (mono), got {info.channels}")
    if info.sample_rate != sample_rate:
        errors.append(f"sample rate must be {sample_rate} Hz, got {info.sample_rate}")
    if info.bits_per_sample != bits:
        errors.append(f"bits per sample must be {bits}, got {info.bits_per_sample}")
    expected_align = channels * bits // 8
    if info.block_align != expected_align:
        errors.append(f"block align must be {expected_align}, got {info.block_align}")
    if info.byte_rate != sample_rate * expected_align:
        errors.append(f"byte rate must be {sample_rate * expected_align}, got {info.byte_rate}")
    return errors


def pcm16_samples(data: bytes, info: WavInfo) -> array:
    """Little-endian int16 samples of the data chunk (interleaved if multi-channel)."""
    if info.bits_per_sample != 16 or info.audio_format != WAVE_FORMAT_PCM:
        raise WavFormatError("only 16-bit PCM can be decoded")
    samples = array("h")
    samples.frombytes(data[info.data_offset : info.data_offset + info.data_size])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def _dbfs(x: float) -> float:
    return 20.0 * math.log10(x) if x > 0 else float("-inf")


def analyze_pcm16(samples) -> QualityMetrics:
    n = len(samples)
    if n == 0:
        return QualityMetrics(0, 0.0, float("-inf"), 0, float("-inf"), 0, False, 0.0, True, True, ["empty"])
    total = 0
    sq = 0
    peak = 0
    clipped = 0
    for s in samples:
        total += s
        sq += s * s
        a = -s if s < 0 else s
        if a > peak:
            peak = a
        if a >= CLIP_LEVEL:
            clipped += 1
    rms = math.sqrt(sq / n) / 32768.0
    m = QualityMetrics(
        num_samples=n,
        rms=rms,
        rms_dbfs=_dbfs(rms),
        peak=peak,
        peak_dbfs=_dbfs(peak / 32768.0),
        clipped_samples=clipped,
        clipping=clipped > max(3, n // 1000),
        dc_offset=total / n,
        all_zero=peak == 0,
        likely_silent=_dbfs(rms) < SILENCE_RMS_DBFS,
    )
    if m.all_zero:
        m.warnings.append("all_zero")
    elif m.likely_silent:
        m.warnings.append("likely_silent")
    if m.clipping:
        m.warnings.append("clipping")
    if abs(m.dc_offset) > DC_OFFSET_WARN:
        m.warnings.append("high_dc_offset")
    return m


def find_peak_window(samples, sample_rate: int, window_ms: int, hop_ms: int = 10) -> int:
    """Start index of the window with the highest energy (mono samples).

    A heuristic for locating an utterance — it does NOT guarantee the window contains the keyword.
    """
    win = sample_rate * window_ms // 1000
    hop = max(1, sample_rate * hop_ms // 1000)
    n = len(samples)
    if n <= win:
        return 0
    prefix = [0] * (n + 1)
    acc = 0
    for i, s in enumerate(samples):
        acc += s * s
        prefix[i + 1] = acc
    best_start, best_energy = 0, -1
    for start in range(0, n - win + 1, hop):
        e = prefix[start + win] - prefix[start]
        if e > best_energy:
            best_start, best_energy = start, e
    # also consider the window flush with the end
    last = n - win
    if prefix[n] - prefix[last] > best_energy:
        best_start = last
    return best_start


def read_wav_file(path: Path) -> tuple[bytes, WavInfo]:
    data = Path(path).read_bytes()
    return data, parse_wav(data)


def pcm16_wav_bytes(samples, sample_rate: int) -> bytes:
    """Mono 16-bit PCM WAV as bytes (canonical 44-byte header, little-endian)."""
    out = array("h", samples)
    if sys.byteorder == "big":
        out.byteswap()
    pcm = out.tobytes()
    header = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ", 16, WAVE_FORMAT_PCM, 1,
                         sample_rate, sample_rate * 2, 2, 16, b"data", len(pcm))
    return header + pcm


def write_pcm16_wav(path: Path, samples, sample_rate: int) -> None:
    """Write mono 16-bit PCM WAV using the standard `wave` module (little-endian)."""
    out = array("h", samples)
    if sys.byteorder == "big":
        out.byteswap()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(out.tobytes())
