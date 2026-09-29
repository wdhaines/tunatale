"""Tests for app.audio.transcode — WAV → compressed delivery encoding.

ffmpeg is a real CI/system dependency (root CLAUDE.md), so these run it for real
rather than mocking the subprocess (the mock-boundary rule forbids faking an
internal seam; ffmpeg is a true process boundary but running it is cheap here).
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest
import soundfile as sf

from app.audio.transcode import (
    CODEC_EXT,
    EXT_MEDIA_TYPE,
    encode_audio,
)

# Shells out to a real ffmpeg binary. CI's two hostile-timezone jobs deselect
# these with -m "not ffmpeg" so they need no ffmpeg install; see
# pyproject.toml [tool.pytest.ini_options] markers.
pytestmark = pytest.mark.ffmpeg


def _silence(duration_ms: int = 500, rate: int = 24000) -> tuple[np.ndarray, int]:
    frames = round(duration_ms / 1000 * rate)
    return np.zeros((frames, 1), dtype="float32"), rate


def _wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    from io import BytesIO

    buf = BytesIO()
    sf.write(buf, samples, rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class TestEncodeAudio:
    def test_opus_output_is_ogg_container(self):
        """Opus encoding returns Ogg-framed bytes (magic 'OggS')."""
        samples, rate = _silence()
        out = encode_audio(samples, rate, "opus", "28k")
        assert out[:4] == b"OggS"
        assert len(out) > 0

    def test_opus_is_smaller_than_wav(self):
        """The whole point: compressed output is far smaller than the WAV."""
        samples, rate = _silence(duration_ms=2000)
        wav = _wav_bytes(samples, rate)
        out = encode_audio(samples, rate, "opus", "28k")
        assert len(out) < len(wav)

    def test_mp3_output_is_decodable(self):
        """A second codec (mp3) also produces non-empty bytes through the same path."""
        samples, rate = _silence()
        out = encode_audio(samples, rate, "mp3", "64k")
        assert len(out) > 0

    def test_ffmpeg_failure_raises_runtimeerror(self):
        """A bad bitrate makes ffmpeg exit non-zero → RuntimeError, not a silent empty file."""
        samples, rate = _silence()
        with pytest.raises(RuntimeError, match="ffmpeg"):
            encode_audio(samples, rate, "opus", "not-a-bitrate")


def _speechlike(duration_ms: int = 2000, rate: int = 24000) -> tuple[np.ndarray, int]:
    """A deterministic, non-silent signal: silence gives every effort level the same bits."""
    t = np.arange(round(duration_ms / 1000 * rate)) / rate
    tone = 0.3 * np.sin(2 * np.pi * 220 * t) * (1 + np.sin(2 * np.pi * 3 * t)) / 2
    noise = 0.05 * np.random.default_rng(7).standard_normal(t.size)
    return (tone + noise).astype("float32").reshape(-1, 1), rate


def _ffmpeg(args: list[str], stdin: bytes) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *args], input=stdin, capture_output=True, check=True
    ).stdout


def _decoded(opus: bytes) -> bytes:
    """PCM of an Ogg Opus stream. Compared instead of the file, whose Ogg serial number is random."""
    return _ffmpeg(["-i", "pipe:0", "-f", "s16le", "pipe:1"], opus)


def _reference_opus(samples: np.ndarray, rate: int, level: int) -> bytes:
    args = ["-i", "pipe:0", "-c:a", "libopus", "-f", "ogg", "-b:a", "28k", "-compression_level", str(level), "pipe:1"]
    return _ffmpeg(args, _wav_bytes(samples, rate))


class TestOpusEffort:
    def test_opus_encodes_at_effort_five(self):
        """Level 5, the user's pick from a blind ear test (tunatale-guzo.4, 2026-09-29).

        libopus defaults to 10, its slowest; 5 took ~3.2x less encode CPU and
        was indistinguishable blind. The level is read off the AUDIO: the app's
        encode must decode to the same samples as a direct level-5 encode, and
        the level-10 control proves this signal separates the two at all.
        """
        samples, rate = _speechlike()
        got = _decoded(encode_audio(samples, rate, "opus", "28k"))
        at_five = _decoded(_reference_opus(samples, rate, 5))
        at_ten = _decoded(_reference_opus(samples, rate, 10))
        assert at_five != at_ten, "control: this signal must encode differently at 5 and 10"
        assert got == at_five


class TestCodecMaps:
    def test_codec_ext_covers_supported_codecs(self):
        assert CODEC_EXT["opus"] == "opus"
        assert CODEC_EXT["mp3"] == "mp3"
        assert CODEC_EXT["aac"] == "m4a"
        assert CODEC_EXT["wav"] == "wav"

    def test_ext_media_type_maps_back_for_serving(self):
        assert EXT_MEDIA_TYPE[".opus"] == "audio/ogg"
        assert EXT_MEDIA_TYPE[".wav"] == "audio/wav"
        assert EXT_MEDIA_TYPE[".mp3"] == "audio/mpeg"
        assert EXT_MEDIA_TYPE[".m4a"] == "audio/mp4"
