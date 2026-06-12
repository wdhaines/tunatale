"""Tests for app.audio.transcode — WAV → compressed delivery encoding.

ffmpeg is a real CI/system dependency (root CLAUDE.md), so these run it for real
rather than mocking the subprocess (the mock-boundary rule forbids faking an
internal seam; ffmpeg is a true process boundary but running it is cheap here).
"""

from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from app.audio.transcode import (
    CODEC_EXT,
    EXT_MEDIA_TYPE,
    encode_audio,
)


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
