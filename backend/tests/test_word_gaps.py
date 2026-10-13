"""``word_gaps`` — an Enunciated take with its pauses rewritten.

Real ffmpeg on real audio, as ``test_speech_length`` is and for the same reason:
the module IS its ffmpeg calls. Each clip is built here as WAV, a tone for speech
and digital silence for a pause, so each test states its own answer.
"""

from __future__ import annotations

import io
import math
import struct
import wave

import pytest

from app.audio import gemini_tts
from app.audio.speech_length import speech_stretches
from app.audio.word_gaps import rewrite_word_gaps

# Every test here shells out to a real ffmpeg. The two hostile-clock CI jobs run
# `-m "not ffmpeg"` on runners that do not install it (ci.yml says why).
pytestmark = pytest.mark.ffmpeg

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


def _spans(audio: bytes) -> list[tuple[float, float]]:
    return [(round(a, 2), round(b, 2)) for a, b in speech_stretches(audio)]


def _lengths(audio: bytes) -> tuple[list[float], list[float]]:
    """How long each stretch of speech runs, and each pause between them."""
    spans = speech_stretches(audio)
    return [b - a for a, b in spans], [spans[i + 1][0] - spans[i][1] for i in range(len(spans) - 1)]


def test_the_stretches_of_speech_are_found_between_the_pauses():
    clip = _wav(_hush(0.3), _tone(0.5), _hush(0.4), _tone(0.2), _hush(0.3))

    assert _spans(clip) == [pytest.approx((0.3, 0.8), abs=0.03), pytest.approx((1.2, 1.4), abs=0.03)]


def test_a_clip_with_no_pause_is_one_stretch():
    assert _spans(_wav(_tone(0.6))) == [pytest.approx((0.0, 0.6), abs=0.03)]


def test_every_word_gap_comes_out_the_length_asked_for():
    """Three words, two pauses of very different lengths: both become the one gap."""
    clip = _wav(_tone(0.4), _hush(0.9), _tone(0.4), _hush(0.2), _tone(0.4))

    out = rewrite_word_gaps(clip, 3, gap_seconds=0.45, shut_seconds=0.03, speed=1.0)

    speech, pauses = _lengths(out)
    assert len(speech) == 3
    assert pauses == [pytest.approx(0.45, abs=0.06), pytest.approx(0.45, abs=0.06)]


def test_a_pause_inside_a_word_is_shut_and_the_longest_pauses_are_the_word_gaps():
    """Two words, three stretches: the model broke the first word. The longer
    pause is the word gap; the shorter one is closed to under what counts as a
    pause, so the broken word is one stretch again."""
    clip = _wav(_tone(0.3), _hush(0.2), _tone(0.3), _hush(0.6), _tone(0.5))

    out = rewrite_word_gaps(clip, 2, gap_seconds=0.45, shut_seconds=0.03, speed=1.0)

    speech, pauses = _lengths(out)
    assert len(speech) == 2
    assert pauses == [pytest.approx(0.45, abs=0.06)]
    # The mended word holds both of its halves (each cut with 20 ms kept either
    # side, and 30 ms left of the pause between them); the second word is untouched.
    assert speech == [pytest.approx(0.71, abs=0.06), pytest.approx(0.5, abs=0.08)]


def test_the_speech_is_sped_and_the_gap_is_not():
    clip = _wav(_tone(0.9), _hush(0.5), _tone(0.6))

    out = rewrite_word_gaps(clip, 2, gap_seconds=0.45, shut_seconds=0.03, speed=1.5)

    speech, pauses = _lengths(out)
    assert speech == [pytest.approx(0.6, abs=0.06), pytest.approx(0.4, abs=0.06)]
    assert pauses == [pytest.approx(0.45, abs=0.06)]


def test_the_silence_at_either_end_is_taken_off():
    clip = _wav(_hush(0.6), _tone(0.5), _hush(0.4), _tone(0.5), _hush(0.7))

    out = rewrite_word_gaps(clip, 2, gap_seconds=0.45, shut_seconds=0.03, speed=1.0)

    spans = speech_stretches(out)
    # 0.6 s of lead and 0.7 s of tail are gone: what is left is two words, the
    # 20 ms kept either side of each, and the one gap.
    assert spans[0][0] < 0.1
    assert spans[-1][1] == pytest.approx(0.5 + 0.45 + 0.5 + 0.08, abs=0.1)


def test_a_take_that_ran_two_words_together_is_refused():
    """Three words and one pause: there is no second gap to set, and inventing
    one would cut a word in half. ``None`` says take it again."""
    clip = _wav(_tone(0.8), _hush(0.4), _tone(0.4))

    assert rewrite_word_gaps(clip, 3, gap_seconds=0.45, shut_seconds=0.03, speed=1.0) is None


def test_the_result_is_an_mp3_like_the_take_it_replaces():
    out = rewrite_word_gaps(_wav(_tone(0.4), _hush(0.4), _tone(0.4)), 2, gap_seconds=0.45, shut_seconds=0.03, speed=1.5)

    assert out is not None
    assert out[:3] == b"ID3" or out[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")


def test_audio_ffmpeg_cannot_read_is_an_error_not_a_refusal():
    """``None`` means "take the line again". Bytes that are not audio are a
    different failure and must not be answered by spending another take."""
    with pytest.raises(RuntimeError, match="ffmpeg"):
        rewrite_word_gaps(b"not audio at all", 2, gap_seconds=0.45, shut_seconds=0.03, speed=1.0)


def test_the_adapters_own_rewrite_uses_the_gap_and_the_speed_it_declares():
    """The wiring: ``gemini_tts.rewrite_enunciated_take`` is this module with the
    adapter's three numbers, and a take with a missing pause comes back ``None``."""
    clip = _wav(_tone(0.9), _hush(0.3), _tone(0.6))

    out = gemini_tts.rewrite_enunciated_take(clip, 2)

    speech, pauses = _lengths(out)
    assert speech == [
        pytest.approx(0.9 / gemini_tts.ENUNCIATED_SPEECH_SPEED, abs=0.06),
        pytest.approx(0.6 / gemini_tts.ENUNCIATED_SPEECH_SPEED, abs=0.06),
    ]
    assert pauses == [pytest.approx(gemini_tts.WORD_GAP_SECONDS, abs=0.06)]
    assert gemini_tts.rewrite_enunciated_take(clip, 3) is None


def test_a_speed_ffmpeg_will_not_take_is_an_error_not_a_silent_clip():
    """The detector read the clip; it is the rewrite that fails (ffmpeg's tempo
    filter stops at half speed). Loud, because the alternative is an empty file
    written into the cache as a line."""
    clip = _wav(_tone(0.4), _hush(0.4), _tone(0.4))

    with pytest.raises(RuntimeError, match="could not rewrite"):
        rewrite_word_gaps(clip, 2, gap_seconds=0.45, shut_seconds=0.03, speed=0.1)
