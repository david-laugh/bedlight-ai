import math
import struct

import pytest
from conftest import make_wav, synth_samples

from app.wav_utils import (
    WavFormatError,
    analyze_pcm16,
    find_peak_window,
    format_errors,
    parse_wav,
    pcm16_samples,
    write_pcm16_wav,
)


def test_parse_valid_16k_mono():
    info = parse_wav(make_wav(duration_ms=2000))
    assert (info.audio_format, info.channels, info.sample_rate, info.bits_per_sample) == (1, 1, 16000, 16)
    assert info.num_frames == 32000
    assert info.duration_ms == pytest.approx(2000.0)
    assert format_errors(info) == []


@pytest.mark.parametrize(
    "kwargs, fragment",
    [
        ({"channels": 2}, "channels"),
        ({"sample_rate": 8000}, "sample rate"),
        ({"sample_rate": 44100}, "sample rate"),
        ({"bits": 8}, "bits per sample"),
        ({"bits": 32, "audio_format": 3}, "PCM"),
    ],
)
def test_format_errors_detect_wrong_format(kwargs, fragment):
    info = parse_wav(make_wav(duration_ms=200, **kwargs))
    errs = format_errors(info)
    assert any(fragment in e for e in errs), errs


@pytest.mark.parametrize(
    "data, fragment",
    [
        (b"", "too small"),
        (b"hello world, not audio", "RIFF"),
        (b"RIFF\x00\x00\x00\x00WAVE", "missing fmt"),
    ],
)
def test_parse_rejects_garbage(data, fragment):
    with pytest.raises(WavFormatError, match=fragment):
        parse_wav(data)


def test_parse_rejects_truncated_data_chunk():
    wav = make_wav(duration_ms=1000)
    with pytest.raises(WavFormatError, match="truncated"):
        parse_wav(wav[:-100])


def test_parse_rejects_missing_data_chunk():
    wav = make_wav(duration_ms=100)
    with pytest.raises(WavFormatError, match="missing data"):
        parse_wav(wav[:36])


def test_parse_rejects_partial_frame():
    wav = bytearray(make_wav(duration_ms=100))
    struct.pack_into("<I", wav, 40, 3)  # 3 bytes is not a whole 16-bit frame
    with pytest.raises(WavFormatError, match="whole number"):
        parse_wav(bytes(wav))


def test_parse_skips_unknown_chunks():
    wav = make_wav(duration_ms=100)
    extra = b"LIST" + struct.pack("<I", 5) + b"abcde" + b"\x00"  # odd size -> pad byte
    rebuilt = wav[:36] + extra + wav[36:]
    rebuilt = rebuilt[:4] + struct.pack("<I", len(rebuilt) - 8) + rebuilt[8:]
    assert parse_wav(rebuilt).num_frames == 1600


def test_analyze_sine_rms_and_peak():
    wav = make_wav(duration_ms=1000, amp=0.5)
    m = analyze_pcm16(pcm16_samples(wav, parse_wav(wav)))
    assert m.rms_dbfs == pytest.approx(20 * math.log10(0.5 / math.sqrt(2)), abs=0.3)
    assert m.peak == pytest.approx(0.5 * 32767, rel=0.02)
    assert not m.clipping and not m.likely_silent and not m.all_zero
    assert m.warnings == []


def test_analyze_all_zero_and_silence_and_clipping():
    zero = analyze_pcm16([0] * 1600)
    assert zero.all_zero and "all_zero" in zero.warnings

    quiet = analyze_pcm16([1, -1] * 800)
    assert quiet.likely_silent and not quiet.all_zero

    loud = analyze_pcm16([32767, -32768] * 800)
    assert loud.clipping and "clipping" in loud.warnings


def test_find_peak_window_locates_burst():
    samples = synth_samples(duration_ms=2000, burst=(1200, 300))
    start = find_peak_window(samples, 16000, window_ms=1000)
    start_ms = start * 1000 / 16000
    assert start_ms <= 1200 and start_ms + 1000 >= 1500


def test_write_and_reparse_roundtrip(tmp_path):
    samples = synth_samples(duration_ms=500)
    p = tmp_path / "x.wav"
    write_pcm16_wav(p, samples, 16000)
    data = p.read_bytes()
    info = parse_wav(data)
    assert format_errors(info) == []
    assert list(pcm16_samples(data, info)) == samples
