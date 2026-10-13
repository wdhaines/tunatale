"""``speech_seconds`` — a clip's length with its silences taken out.

Real ffmpeg on real audio: the function IS its ffmpeg call, so a fake would test
nothing. The clips are built here as WAV (tone for speech, digital silence for a
pause) so each test states its own answer; ffmpeg is already required by the
backend suite.
"""

from __future__ import annotations

import io
import math
import struct
import wave

import pytest

from app.audio.speech_length import speech_seconds

_RATE = 16000


def _tone(seconds: float) -> list[int]:
    return [int(12000 * math.sin(2 * math.pi * 440 * i / _RATE)) for i in range(int(seconds * _RATE))]


def _hush(seconds: float) -> list[int]:
    return [0] * int(seconds * _RATE)


def _wav(*pieces: list[int]) -> bytes:
    samples = [s for piece in pieces for s in piece]
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_RATE)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return out.getvalue()


def test_a_pause_between_two_words_is_not_speech():
    clip = _wav(_tone(1.0), _hush(1.0), _tone(0.5))

    assert speech_seconds(clip) == pytest.approx(1.5, abs=0.05)


def test_the_silence_at_either_end_is_not_speech():
    clip = _wav(_hush(0.5), _tone(1.0), _hush(0.5))

    assert speech_seconds(clip) == pytest.approx(1.0, abs=0.05)


def test_a_gap_too_short_to_be_a_pause_stays_in_the_count():
    """A stop closure inside a word is not a pause; 50 ms is under the 120 ms floor."""
    clip = _wav(_tone(0.5), _hush(0.05), _tone(0.5))

    assert speech_seconds(clip) == pytest.approx(1.05, abs=0.03)


def test_said_twice_measures_twice_whatever_the_pause_between():
    """The property the repeat guard rests on: the pauses do not hide a second saying."""
    once = _wav(_tone(0.4), _hush(0.3), _tone(0.4))
    twice = _wav(_tone(0.4), _hush(0.3), _tone(0.4), _hush(1.1), _tone(0.4), _hush(0.3), _tone(0.4))

    assert speech_seconds(twice) == pytest.approx(2 * speech_seconds(once), abs=0.05)


def test_a_silent_clip_has_no_speech():
    assert speech_seconds(_wav(_hush(1.0))) == pytest.approx(0.0, abs=0.01)


def test_bytes_that_are_not_audio_raise_rather_than_measure_zero():
    """Zero would PASS a length check; a clip nobody could measure must not."""
    with pytest.raises(RuntimeError, match="could not measure the clip"):
        speech_seconds(b"ID3-audio")
