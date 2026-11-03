#!/usr/bin/env python3
"""Inspect WAV files: format, duration, RMS, peak, clipping, silence hints.

Usage:
  python scripts/check_wav.py datasets/voice/raw/off                 # directory (recursive)
  python scripts/check_wav.py a.wav b.wav --json                     # machine readable
  python scripts/check_wav.py datasets/voice/raw --strict            # exit 1 unless every file is 16 kHz/mono/16-bit PCM

The silence/clipping columns are heuristics. They cannot tell whether the word "off" was
actually spoken — listen to the files to confirm (see datasets/voice/README.md).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from _voice_common import wav_utils as wu


def collect(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files.extend(sorted(p.rglob("*.wav")))
        elif p.exists():
            files.append(p)
        else:
            print(f"warning: {p} does not exist", file=sys.stderr)
    return files


def inspect(path: Path, sample_rate: int) -> dict:
    row: dict = {"file": str(path)}
    try:
        data, info = wu.read_wav_file(path)
    except (OSError, wu.WavFormatError) as e:
        row.update(ok=False, error=str(e))
        return row
    row.update(
        sample_rate=info.sample_rate,
        channels=info.channels,
        bits_per_sample=info.bits_per_sample,
        audio_format=info.audio_format,
        duration_ms=round(info.duration_ms, 1),
        num_samples=info.num_frames,
        file_size=len(data),
    )
    errors = wu.format_errors(info, sample_rate=sample_rate)
    row["format_errors"] = errors
    row["ok"] = not errors
    if info.audio_format == wu.WAVE_FORMAT_PCM and info.bits_per_sample == 16:
        samples = wu.pcm16_samples(data, info)
        if info.channels > 1:
            samples = samples[:: info.channels]  # first channel only for metrics
        q = wu.analyze_pcm16(samples)
        row.update(q.to_dict())
    return row


def fmt_db(v) -> str:
    return "  -inf" if v is None or (isinstance(v, float) and math.isinf(v)) else f"{v:6.1f}"


def print_table(rows: list[dict]) -> None:
    header = f"{'file':<48} {'Hz':>6} {'ch':>2} {'bit':>3} {'dur ms':>7} {'samples':>7} {'RMS dBFS':>8} {'peak':>6} {'clip':>5} {'silence':>8}  result"
    print(header)
    print("-" * len(header))
    for r in rows:
        name = r["file"] if len(r["file"]) <= 48 else "…" + r["file"][-47:]
        if "error" in r:
            print(f"{name:<48} {'':>6} {'':>2} {'':>3} {'':>7} {'':>7} {'':>8} {'':>6} {'':>5} {'':>8}  INVALID: {r['error']}")
            continue
        clip = "YES" if r.get("clipping") else "no"
        silence = "all-zero" if r.get("all_zero") else ("likely" if r.get("likely_silent") else "no")
        result = "OK" if r["ok"] else "FORMAT: " + "; ".join(r["format_errors"])
        print(
            f"{name:<48} {r['sample_rate']:>6} {r['channels']:>2} {r['bits_per_sample']:>3} "
            f"{r['duration_ms']:>7.0f} {r['num_samples']:>7} {fmt_db(r.get('rms_dbfs')):>8} "
            f"{r.get('peak', 0):>6} {clip:>5} {silence:>8}  {result}"
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="WAV files or directories")
    ap.add_argument("--json", action="store_true", help="print JSON lines instead of a table")
    ap.add_argument("--strict", action="store_true", help="exit 1 if any file is invalid or not 16 kHz/mono/16-bit PCM")
    ap.add_argument("--sample-rate", type=int, default=16000)
    args = ap.parse_args(argv)

    files = collect(args.paths)
    if not files:
        print("no WAV files found", file=sys.stderr)
        return 1
    rows = [inspect(f, args.sample_rate) for f in files]
    if args.json:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False, default=lambda x: None))
    else:
        print_table(rows)
        bad = sum(1 for r in rows if not r["ok"])
        warn = sum(1 for r in rows if r.get("warnings"))
        print(f"\n{len(rows)} file(s), {bad} invalid/format mismatch, {warn} with quality warnings")
        print("Reminder: these metrics cannot confirm the spoken word. Listen to the recordings.")
    if args.strict and any(not r["ok"] for r in rows):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
