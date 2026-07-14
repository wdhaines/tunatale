"""Tests for S3.8: EBU R128 loudness normalization."""

from __future__ import annotations

import json
import subprocess

from app.cards.media.normalize import normalize_audio


def _fake_run_factory(stderr_json: dict | None, returncode: int = 0):
    """Return a fake subprocess.run that returns fixed results."""

    def fake_run(cmd, *, capture_output, text):
        if capture_output and text and returncode == 0 and stderr_json is not None:
            stderr = "..." + json.dumps(stderr_json)
        else:
            stderr = "no json here"
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)

    return fake_run


class TestNormalizeAudio:
    def test_returns_bytes_on_success(self, monkeypatch, tmp_path):
        normalized_bytes = b"normalized_mp3"

        call_count = [0]

        def fake_run(cmd, *, capture_output=False, text=False):
            call_count[0] += 1
            stats = {
                "input_i": "-20.0",
                "input_lra": "6.0",
                "input_tp": "-3.0",
                "input_thresh": "-30.0",
                "target_offset": "0.1",
            }
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=json.dumps(stats))

        def fake_write(self, src_bytes):
            pass

        monkeypatch.setattr("subprocess.run", fake_run)
        monkeypatch.setattr(
            "app.cards.media.normalize._apply_normalization",
            lambda src, dst, stats, target_lufs: dst.write_bytes(normalized_bytes),
        )

        result = normalize_audio(b"input_mp3")
        assert result == normalized_bytes
        assert call_count[0] == 1  # one measure pass

    def test_two_pass_calls_apply_with_stats(self, monkeypatch):
        stats_seen: list[dict] = []

        def fake_measure(path):
            return {
                "input_i": "-18.0",
                "input_lra": "5.0",
                "input_tp": "-2.5",
                "input_thresh": "-28.0",
                "target_offset": "0.2",
            }

        def fake_apply(src, dst, stats, target_lufs):
            stats_seen.append(stats)
            dst.write_bytes(b"result")

        monkeypatch.setattr("app.cards.media.normalize._measure_loudness", fake_measure)
        monkeypatch.setattr("app.cards.media.normalize._apply_normalization", fake_apply)

        normalize_audio(b"input")
        assert stats_seen[0]["input_i"] == "-18.0"

    def test_fallback_to_onepass_when_measure_returns_empty(self, monkeypatch):
        stats_seen: list[dict] = []

        monkeypatch.setattr("app.cards.media.normalize._measure_loudness", lambda p: {})
        monkeypatch.setattr(
            "app.cards.media.normalize._apply_normalization",
            lambda src, dst, stats, target_lufs: (stats_seen.append(stats), dst.write_bytes(b"r")),
        )

        result = normalize_audio(b"x")
        assert result == b"r"
        assert stats_seen[0] == {}  # empty stats passed through

    def test_ffmpeg_failure_returns_original_bytes(self, monkeypatch):
        """A failed loudnorm pass must fail soft to the ORIGINAL audio —
        returning the empty/partial temp file's bytes would save corrupt
        (typically zero-byte) pronunciation audio into the Anki media dir."""
        calls = [0]

        def fake_run(cmd, *, capture_output=False, text=False):
            calls[0] += 1
            # First call = measure pass (success, no json → one-pass fallback);
            # second call = apply pass, which fails without writing output.
            rc = 0 if calls[0] == 1 else 1
            return subprocess.CompletedProcess(cmd, rc, stdout="", stderr="boom")

        monkeypatch.setattr("subprocess.run", fake_run)
        result = normalize_audio(b"original_mp3")
        assert result == b"original_mp3"

    def test_empty_ffmpeg_output_returns_original_bytes(self, monkeypatch):
        """ffmpeg exiting 0 but writing nothing (or an empty file) must also
        fall back to the original bytes."""

        def fake_run(cmd, *, capture_output=False, text=False):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="no json here")

        monkeypatch.setattr("subprocess.run", fake_run)
        result = normalize_audio(b"original_mp3")
        assert result == b"original_mp3"

    def test_cleans_up_temp_files(self, monkeypatch, tmp_path):
        created: list[str] = []

        original_ntf = __import__("tempfile").NamedTemporaryFile

        class TrackingNTF:
            def __init__(self, **kw):
                self._f = original_ntf(**kw)
                created.append(self._f.name)

            def __enter__(self):
                return self._f.__enter__()

            def __exit__(self, *a):
                return self._f.__exit__(*a)

        monkeypatch.setattr("tempfile.NamedTemporaryFile", TrackingNTF)
        monkeypatch.setattr("app.cards.media.normalize._measure_loudness", lambda p: {})
        monkeypatch.setattr(
            "app.cards.media.normalize._apply_normalization",
            lambda src, dst, stats, tl: dst.write_bytes(b"ok"),
        )

        normalize_audio(b"data")
        # All created temp files should no longer exist
        from pathlib import Path

        for p in created:
            assert not Path(p).exists(), f"{p} was not cleaned up"

    def test_measure_loudness_parses_json_from_stderr(self, monkeypatch):
        stats = {
            "input_i": "-22.0",
            "input_lra": "7.0",
            "input_tp": "-1.0",
            "input_thresh": "-32.0",
            "target_offset": "0.0",
        }

        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr="prefix " + json.dumps(stats)),
        )

        from pathlib import Path

        from app.cards.media.normalize import _measure_loudness

        result = _measure_loudness(Path("/fake.mp3"))
        assert result["input_i"] == "-22.0"

    def test_measure_loudness_returns_empty_on_no_braces(self, monkeypatch):
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr="no json here"),
        )

        from pathlib import Path

        from app.cards.media.normalize import _measure_loudness

        result = _measure_loudness(Path("/fake.mp3"))
        assert result == {}

    def test_measure_loudness_returns_empty_on_invalid_json(self, monkeypatch):
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="", stderr="{ invalid json: true }"),
        )

        from pathlib import Path

        from app.cards.media.normalize import _measure_loudness

        result = _measure_loudness(Path("/fake.mp3"))
        assert result == {}

    def test_apply_normalization_with_empty_stats(self, monkeypatch, tmp_path):
        cmds: list[list[str]] = []
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: (cmds.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1],
        )

        from app.cards.media.normalize import _apply_normalization

        src = tmp_path / "src.mp3"
        dst = tmp_path / "dst.mp3"
        src.write_bytes(b"x")
        _apply_normalization(src, dst, {}, -23.0)
        assert len(cmds) == 1
        # one-pass loudnorm: no measured_I in af
        af_arg = cmds[0][cmds[0].index("-af") + 1]
        assert "measured_I" not in af_arg

    def test_apply_normalization_with_stats(self, monkeypatch, tmp_path):
        cmds: list[list[str]] = []
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: (cmds.append(cmd), subprocess.CompletedProcess(cmd, 0, "", ""))[1],
        )

        from app.cards.media.normalize import _apply_normalization

        stats = {
            "input_i": "-20.0",
            "input_lra": "6.0",
            "input_tp": "-3.0",
            "input_thresh": "-30.0",
            "target_offset": "0.1",
        }
        src = tmp_path / "src.mp3"
        dst = tmp_path / "dst.mp3"
        src.write_bytes(b"x")
        _apply_normalization(src, dst, stats, -23.0)
        assert len(cmds) == 1
        af_arg = cmds[0][cmds[0].index("-af") + 1]
        assert "measured_I=-20.0" in af_arg
