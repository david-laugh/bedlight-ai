#!/usr/bin/env python3
"""Prepare recorded voice WAVs for Edge Impulse (training / testing).

Pipeline
  1. scan   datasets/voice/raw/<label>/*.wav
  2. check  RIFF/PCM 16-bit / mono / 16 kHz, minimum length
  3. cut    1 s windows (default):
              off, unknown -> "peak" : the 1 s window with the highest energy (utterance heuristic)
              noise        -> "split": every non-overlapping 1 s window
  4. review manual overrides (segments.csv) take precedence over the heuristic
  5. split  training / testing by SOURCE RECORDING (stable hash of PCM content) so windows
            from the same recording - or byte-identical copies - never land in both splits
  6. write  prepared/{training,testing}/<label>.<source-stem>.s<start_ms>.wav
            (Edge Impulse infers the label from the text before the first '.')
            prepared/manifest.csv, prepared/review.html (audio players + waveforms)

The peak-energy heuristic can pick a cough, a door slam, or cut the word. It is NOT verified
speech detection: open prepared/review.html and listen before uploading. Fix mistakes in
segments.csv (see --overrides) and run again.

This script never synthesizes audio; it only cuts the user's own recordings.

Usage
  python scripts/prepare_voice_dataset.py                       # defaults: datasets/voice
  python scripts/prepare_voice_dataset.py --dry-run             # report only
  python scripts/prepare_voice_dataset.py --test-ratio 0.2 --window-ms 1000
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from _voice_common import REPO_ROOT, wav_utils as wu

DEFAULT_MODES = {"off": "peak", "unknown": "peak", "noise": "split"}
MANIFEST_FIELDS = [
    "split", "label", "file", "source_file", "source_sha1", "start_ms", "duration_ms",
    "rms_dbfs", "peak_dbfs", "selection", "flags",
]


@dataclass
class Clip:
    label: str
    source: Path
    source_sha1: str
    start: int            # sample index
    samples: list
    selection: str        # peak | split | manual
    flags: list[str] = field(default_factory=list)
    split: str = ""
    out_name: str = ""
    rms_dbfs: float = 0.0
    peak_dbfs: float = 0.0


@dataclass
class Report:
    rejected: list[tuple[str, str]] = field(default_factory=list)   # (file, reason)
    excluded: list[tuple[str, str]] = field(default_factory=list)
    duplicates: list[tuple[str, str]] = field(default_factory=list)  # (file, duplicate_of)
    sources: dict[str, int] = field(default_factory=dict)            # label -> accepted source files


# --------------------------------------------------------------------------- overrides
def load_overrides(path: Path | None) -> dict[str, dict]:
    """segments.csv columns: file,action,start_ms,note

    file    : file name as in raw/<label>/ (e.g. off_20261103T120000_ab12cd34.wav)
    action  : include (use start_ms instead of the heuristic) | exclude (drop the recording)
    start_ms: window start in ms (include only)
    """
    if path is None or not path.exists():
        return {}
    out = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("file") or "").strip()
            action = (row.get("action") or "").strip().lower()
            if not name or name.startswith("#"):
                continue
            if action not in {"include", "exclude"}:
                raise SystemExit(f"{path}: invalid action {action!r} for {name} (use include|exclude)")
            entry = {"action": action, "note": row.get("note", "")}
            if action == "include":
                try:
                    entry["start_ms"] = int(row["start_ms"])
                except (KeyError, TypeError, ValueError):
                    raise SystemExit(f"{path}: include for {name} needs an integer start_ms")
            out[name] = entry
    return out


# --------------------------------------------------------------------------- core
def stable_fraction(key: str, salt: str) -> float:
    h = hashlib.sha256(f"{salt}:{key}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


def window_metrics(samples) -> tuple[float, float]:
    q = wu.analyze_pcm16(samples)
    return q.rms_dbfs, q.peak_dbfs


def cut_clips(label, path, samples, sha1, sr, args, override) -> tuple[list[Clip], str | None]:
    win = sr * args.window_ms // 1000
    n = len(samples)
    if override and override["action"] == "include":
        start = sr * override["start_ms"] // 1000
        if start < 0 or start + win > n:
            return [], f"override start_ms={override['start_ms']} outside recording ({n * 1000 // sr} ms)"
        return [Clip(label, path, sha1, start, samples[start : start + win], "manual")], None

    if n < win:
        return [], f"shorter than window ({n * 1000 // sr} ms < {args.window_ms} ms)"

    mode = args.modes.get(label, "peak")
    if mode == "split":
        return [
            Clip(label, path, sha1, s, samples[s : s + win], "split")
            for s in range(0, n - win + 1, win)
        ], None

    start = wu.find_peak_window(samples, sr, args.window_ms)
    clip = Clip(label, path, sha1, start, samples[start : start + win], "peak")
    edge = sr * args.edge_ms // 1000
    if start <= 0 or start + win >= n:
        clip.flags.append("window_at_recording_edge")
    # energy concentrated at the window border suggests the word may be cut
    head = wu.analyze_pcm16(clip.samples[:edge]).rms_dbfs
    tail = wu.analyze_pcm16(clip.samples[-edge:]).rms_dbfs
    body = wu.analyze_pcm16(clip.samples).rms_dbfs
    if max(head, tail) > body - 3.0:
        clip.flags.append("energy_at_window_border")
    return [clip], None


def prepare(args) -> int:
    data_dir: Path = args.data_dir
    raw = data_dir / "raw"
    prepared = data_dir / "prepared"
    overrides = load_overrides(args.overrides)
    report = Report()
    clips: list[Clip] = []
    seen_sha: dict[str, str] = {}

    for label in args.labels:
        files = sorted((raw / label).glob("*.wav"))
        report.sources[label] = 0
        for path in files:
            ov = overrides.get(path.name)
            if ov and ov["action"] == "exclude":
                report.excluded.append((str(path), ov.get("note") or "excluded in overrides"))
                continue
            try:
                data, info = wu.read_wav_file(path)
            except (OSError, wu.WavFormatError) as e:
                report.rejected.append((str(path), f"invalid WAV: {e}"))
                continue
            errs = wu.format_errors(info, sample_rate=args.sample_rate)
            if errs:
                report.rejected.append((str(path), "; ".join(errs)))
                continue
            pcm = data[info.data_offset : info.data_offset + info.data_size]
            sha1 = hashlib.sha1(pcm).hexdigest()
            if sha1 in seen_sha:
                report.duplicates.append((str(path), seen_sha[sha1]))
                continue
            seen_sha[sha1] = str(path)
            samples = list(wu.pcm16_samples(data, info))
            if wu.analyze_pcm16(samples).all_zero:
                report.rejected.append((str(path), "all samples are zero"))
                continue
            new, reason = cut_clips(label, path, samples, sha1, args.sample_rate, args, ov)
            if reason:
                report.rejected.append((str(path), reason))
                continue
            for c in new:
                c.rms_dbfs, c.peak_dbfs = window_metrics(c.samples)
                if label != "noise" and c.selection != "manual" and c.rms_dbfs < args.min_speech_dbfs:
                    c.flags.append("low_energy_review")
            # A peak window with almost no energy is unlikely to contain a word: exclude unless manual.
            kept = [c for c in new if not ("low_energy_review" in c.flags and args.drop_low_energy)]
            if not kept:
                report.rejected.append((str(path), f"no window above {args.min_speech_dbfs} dBFS (add an include override after listening)"))
                continue
            clips.extend(kept)
            report.sources[label] += 1

    # ---- split by source recording (content hash) => no leakage between splits
    for c in clips:
        c.split = "testing" if stable_fraction(c.source_sha1, args.salt) < args.test_ratio else "training"
        c.out_name = f"{c.label}.{c.source.stem.replace('.', '_')}.s{c.start * 1000 // args.sample_rate:05d}.wav"

    leaks = {c.source_sha1 for c in clips if c.split == "training"} & {c.source_sha1 for c in clips if c.split == "testing"}
    assert not leaks, "internal error: a source recording is in both splits"

    print_summary(args, clips, report)
    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0

    for split in ("training", "testing"):
        d = prepared / split
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("*.wav"):
            if old.name.split(".", 1)[0] in args.labels:
                old.unlink()
    for c in clips:
        wu.write_pcm16_wav(prepared / c.split / c.out_name, c.samples, args.sample_rate)

    write_manifest(prepared / "manifest.csv", clips, args.sample_rate, args.window_ms)
    write_review(prepared / "review.html", clips, report, data_dir, args.sample_rate)
    (prepared / "report.json").write_text(
        json.dumps(
            {
                "rejected": report.rejected,
                "excluded": report.excluded,
                "duplicates": report.duplicates,
                "counts": counts(clips),
                "settings": {"window_ms": args.window_ms, "test_ratio": args.test_ratio, "salt": args.salt},
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {len(clips)} clip(s) to {prepared}")
    print(f"Review before uploading: {prepared / 'review.html'}")
    return 0


def counts(clips) -> dict:
    out: dict = {}
    for c in clips:
        out.setdefault(c.label, {"training": 0, "testing": 0})[c.split] += 1
    return out


def print_summary(args, clips, report) -> None:
    cnt = counts(clips)
    print(f"{'label':<10} {'sources':>7} {'training':>9} {'testing':>8} {'flagged':>8}")
    for label in args.labels:
        c = cnt.get(label, {"training": 0, "testing": 0})
        flagged = sum(1 for x in clips if x.label == label and x.flags)
        print(f"{label:<10} {report.sources.get(label, 0):>7} {c['training']:>9} {c['testing']:>8} {flagged:>8}")
        if report.sources.get(label, 0) and (c["training"] == 0 or c["testing"] == 0):
            print(f"  warning: '{label}' has an empty split - record more samples (hash split is per recording)")
    for title, items in (("rejected", report.rejected), ("excluded", report.excluded), ("duplicate content", report.duplicates)):
        if items:
            print(f"\n{title}: {len(items)}")
            for f, why in items:
                print(f"  - {f}: {why}")


def write_manifest(path: Path, clips, sr: int, window_ms: int) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        w.writeheader()
        for c in sorted(clips, key=lambda c: (c.split, c.label, c.out_name)):
            w.writerow(
                {
                    "split": c.split,
                    "label": c.label,
                    "file": f"{c.split}/{c.out_name}",
                    "source_file": c.source.name,
                    "source_sha1": c.source_sha1,
                    "start_ms": c.start * 1000 // sr,
                    "duration_ms": window_ms,
                    "rms_dbfs": round(c.rms_dbfs, 1),
                    "peak_dbfs": round(c.peak_dbfs, 1),
                    "selection": c.selection,
                    "flags": "|".join(c.flags),
                }
            )


def _svg_wave(samples, width=320, height=48, marks=None) -> str:
    n = len(samples) or 1
    bins = width
    step = max(1, n // bins)
    parts = []
    mid = height / 2
    for x in range(bins):
        seg = samples[x * step : (x + 1) * step]
        if not seg:
            break
        lo, hi = min(seg), max(seg)
        parts.append(f"M{x} {mid - hi / 32768 * mid:.1f}V{mid - lo / 32768 * mid:.1f}")
    rect = ""
    if marks:
        x0 = marks[0] / n * width
        x1 = marks[1] / n * width
        rect = f'<rect x="{x0:.1f}" y="0" width="{x1 - x0:.1f}" height="{height}" class="sel"/>'
    return f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}">{rect}<path d="{"".join(parts)}" class="w"/></svg>'


def write_review(path: Path, clips, report, data_dir: Path, sr: int) -> None:
    rows = []
    cache: dict[Path, list] = {}
    for c in sorted(clips, key=lambda c: (c.label, c.source.name, c.start)):
        if c.source not in cache:
            data, info = wu.read_wav_file(c.source)
            cache[c.source] = list(wu.pcm16_samples(data, info))
        full = cache[c.source]
        raw_rel = Path("..") / c.source.relative_to(data_dir)
        flags = ", ".join(c.flags) or "-"
        rows.append(
            f"<tr class='{'flag' if c.flags else ''}'><td>{html.escape(c.label)}</td><td>{c.split}</td>"
            f"<td>{html.escape(c.source.name)}<br>{_svg_wave(full, marks=(c.start, c.start + len(c.samples)))}"
            f"<br><audio controls preload='none' src='{html.escape(raw_rel.as_posix())}'></audio></td>"
            f"<td>{c.start * 1000 // sr} ms ({c.selection})<br>{_svg_wave(c.samples, width=160)}"
            f"<br><audio controls preload='none' src='{c.split}/{html.escape(c.out_name)}'></audio></td>"
            f"<td>{c.rms_dbfs:.1f}</td><td>{html.escape(flags)}</td></tr>"
        )
    rejected = "".join(f"<li>{html.escape(f)}: {html.escape(r)}</li>" for f, r in report.rejected + report.excluded)
    doc = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>BedLight voice dataset review</title>
<style>
body{{font-family:system-ui,sans-serif;margin:16px;}} table{{border-collapse:collapse;}} td,th{{border:1px solid #8884;padding:6px;vertical-align:top;font-size:13px}}
tr.flag{{background:#f59e0b22}} .w{{stroke:#2563eb;stroke-width:1;fill:none}} .sel{{fill:#16a34a33}} audio{{height:28px;width:260px}}
</style></head><body>
<h1>Voice dataset review</h1>
<p><b>모든 클립을 직접 들어보고</b> "off" 발화가 온전히 들어 있는지 확인하세요. 자동 구간 선택(peak)은 에너지 기반 추정일 뿐입니다.
문제가 있으면 <code>datasets/voice/segments.csv</code> 에 <code>include,start_ms</code> 또는 <code>exclude</code> 를 기록하고 스크립트를 다시 실행하세요.
녹색 영역 = 선택된 1초 구간. 노란 행 = 검토 필요 플래그.</p>
<table><tr><th>label</th><th>split</th><th>원본 (전체)</th><th>선택 구간</th><th>RMS dBFS</th><th>flags</th></tr>
{''.join(rows)}</table>
<h2>제외/거부된 파일</h2><ul>{rejected or '<li>없음</li>'}</ul>
</body></html>"""
    path.write_text(doc, encoding="utf-8")


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", type=Path, default=REPO_ROOT / "datasets" / "voice")
    ap.add_argument("--labels", nargs="+", default=["off", "unknown", "noise"])
    ap.add_argument("--window-ms", type=int, default=1000)
    ap.add_argument("--test-ratio", type=float, default=0.2)
    ap.add_argument("--salt", default="bedlight-v1", help="change to reshuffle the split (affects ALL files)")
    ap.add_argument("--sample-rate", type=int, default=16000)
    ap.add_argument("--overrides", type=Path, default=None, help="segments.csv (default: <data-dir>/segments.csv)")
    ap.add_argument("--min-speech-dbfs", type=float, default=-45.0, help="peak window below this is flagged low_energy_review")
    ap.add_argument("--drop-low-energy", action="store_true", help="drop flagged low-energy windows instead of keeping them")
    ap.add_argument("--edge-ms", type=int, default=50)
    ap.add_argument("--mode", action="append", default=[], metavar="LABEL=peak|split")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not 0.0 <= args.test_ratio < 1.0:
        ap.error("--test-ratio must be in [0, 1)")
    if args.overrides is None:
        args.overrides = args.data_dir / "segments.csv"
    args.modes = dict(DEFAULT_MODES)
    for m in args.mode:
        k, _, v = m.partition("=")
        if v not in {"peak", "split"}:
            ap.error(f"--mode {m}: use LABEL=peak or LABEL=split")
        args.modes[k] = v
    return args


def main(argv=None) -> int:
    return prepare(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
