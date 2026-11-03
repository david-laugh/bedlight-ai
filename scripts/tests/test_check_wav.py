import json

from conftest import tone_with_burst, wu

import check_wav


def test_reports_metrics_for_valid_file(tmp_path, capsys):
    p = tmp_path / "a.wav"
    wu.write_pcm16_wav(p, tone_with_burst(), 16000)
    assert check_wav.main([str(p), "--json", "--strict"]) == 0
    row = json.loads(capsys.readouterr().out.strip())
    assert row["ok"] is True
    assert (row["sample_rate"], row["channels"], row["bits_per_sample"]) == (16000, 1, 16)
    assert row["num_samples"] == 32000
    assert row["duration_ms"] == 2000.0
    for key in ("rms", "rms_dbfs", "peak", "clipping", "likely_silent"):
        assert key in row


def test_strict_fails_on_wrong_rate_and_garbage(tmp_path, capsys):
    good = tmp_path / "good.wav"
    wu.write_pcm16_wav(good, tone_with_burst(), 16000)
    bad = tmp_path / "bad8k.wav"
    wu.write_pcm16_wav(bad, tone_with_burst(sr=8000), 8000)
    junk = tmp_path / "junk.wav"
    junk.write_bytes(b"xxxx")
    assert check_wav.main([str(tmp_path), "--strict"]) == 1
    out = capsys.readouterr().out
    assert "INVALID" in out and "sample rate must be 16000" in out
    assert "Listen to the recordings" in out


def test_silence_and_clipping_flags(tmp_path, capsys):
    silent = tmp_path / "silent.wav"
    wu.write_pcm16_wav(silent, [0] * 16000, 16000)
    clipped = tmp_path / "clip.wav"
    wu.write_pcm16_wav(clipped, [32767, -32768] * 8000, 16000)
    check_wav.main([str(silent), str(clipped), "--json"])
    rows = {json.loads(l)["file"].split("/")[-1].split("\\")[-1]: json.loads(l) for l in capsys.readouterr().out.splitlines()}
    assert rows["silent.wav"]["all_zero"] is True
    assert rows["clip.wav"]["clipping"] is True
