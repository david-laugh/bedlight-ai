import csv
import json
import shutil

from conftest import tone_with_burst, wu

import prepare_voice_dataset as pvd


def run(root, *extra):
    return pvd.main(["--data-dir", str(root), *extra])


def manifest(root):
    with (root / "prepared" / "manifest.csv").open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_peak_window_contains_burst_and_is_one_second(dataset):
    root, add = dataset
    add("off", "off_a.wav", tone_with_burst(burst=(1200, 400)))
    assert run(root, "--test-ratio", "0") == 0
    rows = manifest(root)
    assert len(rows) == 1
    start = int(rows[0]["start_ms"])
    assert start <= 1200 and start + 1000 >= 1600
    out = root / "prepared" / rows[0]["file"]
    data, info = wu.read_wav_file(out)
    assert wu.format_errors(info) == []
    assert info.num_frames == 16000
    assert out.name.startswith("off.off_a.s")  # Edge Impulse label = text before first '.'


def test_noise_is_split_into_non_overlapping_windows(dataset):
    root, add = dataset
    add("noise", "noise_a.wav", tone_with_burst(duration_ms=3000, burst=None))
    run(root, "--test-ratio", "0")
    rows = manifest(root)
    assert sorted(int(r["start_ms"]) for r in rows) == [0, 1000, 2000]
    assert all(r["selection"] == "split" for r in rows)


def test_no_source_recording_in_both_splits(dataset):
    root, add = dataset
    for i in range(30):
        add("noise", f"noise_{i:02d}.wav", tone_with_burst(duration_ms=3000, burst=None, seed=i))
        add("off", f"off_{i:02d}.wav", tone_with_burst(seed=i))
    run(root, "--test-ratio", "0.3")
    rows = manifest(root)
    by_source = {}
    for r in rows:
        by_source.setdefault(r["source_sha1"], set()).add(r["split"])
    assert all(len(s) == 1 for s in by_source.values())
    splits = {r["split"] for r in rows}
    assert splits == {"training", "testing"}


def test_split_is_stable_when_new_files_are_added(dataset):
    root, add = dataset
    for i in range(10):
        add("off", f"off_{i:02d}.wav", tone_with_burst(seed=i))
    run(root)
    before = {r["source_file"]: r["split"] for r in manifest(root)}
    for i in range(10, 20):
        add("off", f"off_{i:02d}.wav", tone_with_burst(seed=i))
    run(root)
    after = {r["source_file"]: r["split"] for r in manifest(root)}
    assert all(after[k] == v for k, v in before.items())


def test_identical_copies_are_deduplicated(dataset):
    root, add = dataset
    p = add("off", "off_a.wav", tone_with_burst())
    shutil.copy(p, root / "raw" / "off" / "off_b_copy.wav")
    run(root)
    assert len(manifest(root)) == 1
    report = json.loads((root / "prepared" / "report.json").read_text(encoding="utf-8"))
    assert len(report["duplicates"]) == 1


def test_invalid_and_wrong_format_files_are_rejected(dataset):
    root, add = dataset
    add("off", "good.wav", tone_with_burst())
    add("off", "rate8k.wav", tone_with_burst(sr=8000), sr=8000)
    (root / "raw" / "off" / "garbage.wav").write_bytes(b"not a wav")
    add("off", "short.wav", tone_with_burst(duration_ms=500, burst=(100, 200)))
    add("off", "zeros.wav", [0] * 32000)
    run(root)
    assert [r["source_file"] for r in manifest(root)] == ["good.wav"]
    rejected = json.loads((root / "prepared" / "report.json").read_text(encoding="utf-8"))["rejected"]
    names = {r[0].split("/")[-1].split("\\")[-1] for r in rejected}
    assert names == {"rate8k.wav", "garbage.wav", "short.wav", "zeros.wav"}


def test_overrides_include_and_exclude(dataset):
    root, add = dataset
    add("off", "off_a.wav", tone_with_burst(burst=(1500, 300)))
    add("off", "off_b.wav", tone_with_burst(seed=2))
    (root / "segments.csv").write_text(
        "file,action,start_ms,note\noff_a.wav,include,200,manual pick\noff_b.wav,exclude,,cough\n", encoding="utf-8"
    )
    run(root)
    rows = manifest(root)
    assert len(rows) == 1
    assert rows[0]["source_file"] == "off_a.wav"
    assert rows[0]["start_ms"] == "200"
    assert rows[0]["selection"] == "manual"


def test_quiet_off_recording_is_flagged_for_review(dataset):
    root, add = dataset
    add("off", "off_quiet.wav", tone_with_burst(burst=None))  # only faint noise
    run(root)
    rows = manifest(root)
    assert "low_energy_review" in rows[0]["flags"]
    run(root, "--drop-low-energy")
    assert manifest(root) == []


def test_rerun_removes_stale_outputs_and_dry_run_writes_nothing(dataset):
    root, add = dataset
    p = add("off", "off_a.wav", tone_with_burst())
    run(root, "--test-ratio", "0")
    assert len(list((root / "prepared" / "training").glob("*.wav"))) == 1
    p.unlink()
    add("off", "off_b.wav", tone_with_burst(seed=5))
    run(root, "--test-ratio", "0")
    names = [f.name for f in (root / "prepared" / "training").glob("*.wav")]
    assert len(names) == 1 and "off_b" in names[0]

    shutil.rmtree(root / "prepared")
    run(root, "--dry-run")
    assert not (root / "prepared").exists()


def test_review_html_references_audio(dataset):
    root, add = dataset
    add("off", "off_a.wav", tone_with_burst())
    run(root, "--test-ratio", "0")
    html = (root / "prepared" / "review.html").read_text(encoding="utf-8")
    assert "../raw/off/off_a.wav" in html
    assert "training/off.off_a.s" in html
