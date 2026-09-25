"""Render-progress reporting from ``LessonRenderer.render`` (tunatale-hbnd).

The whole synthesis plan is knowable almost immediately: every distinct clip is
one entry of ``synth_memo``, and a section gathers ALL of its phrases at once,
so ``total`` is complete before the first network response returns. What that
buys the pipeline card is a real percentage, not "Rendering audio".

Deliberately NOT marked ``ffmpeg``: the renderer is built with
``delivery_codec="wav"``, so the soundfile writer handles the export and no
binary is needed — the test then also runs in the two hostile-timezone jobs.
"""

import asyncio
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.renderer import LessonRenderer
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.plugins.languages.sl.preprocessor import SlovenePreprocessor

_RATE = 11025

# Five distinct (text, voice, rate) keys, with ONE of them requested twice (in a
# second section, so the dedup is the render-scoped memo's and not an artifact
# of one section's gather).
_TEXTS = ["dober dan", "hvala", "kje si", "vse najboljse", "prosim"]
_VOICE = "sl-SI-PetraNeural"


def _wav_bytes(duration_ms: int = 40) -> bytes:
    buf = BytesIO()
    frames = round(duration_ms / 1000 * _RATE)
    sf.write(buf, np.zeros((frames, 1), dtype="float32"), _RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class FakeTTS:
    """A ``TTSService`` double writing a fixed WAV for every request."""

    def __init__(self, *, fail_after: dict[str, float] | None = None, blocked: set[str] | None = None) -> None:
        self.audio = _wav_bytes()
        self.requested: list[str] = []
        # Per text: seconds to wait before raising.
        self.fail_after = fail_after or {}
        # Texts whose synthesis never returns on its own.
        self.blocked = blocked or set()
        self.released = asyncio.Event()

    async def synthesize(self, text, voice_id, output_path, rate="+0%", phonemes=None, speak_locale=None) -> None:
        self.requested.append(text)
        if text in self.blocked:
            await self.released.wait()
        if text in self.fail_after:
            await asyncio.sleep(self.fail_after[text])
            raise RuntimeError(f"TTS refused {text!r}")
        Path(output_path).write_bytes(self.audio)

    async def list_voices(self, language_code: str | None = None) -> list[dict]:
        return []


def _renderer(tts: FakeTTS) -> LessonRenderer:
    return LessonRenderer(
        tts=tts,
        preprocessors={"sl": SlovenePreprocessor()},
        pause_calculator=NaturalPauseCalculator(),
        delivery_codec="wav",
    )


def _phrase(text: str) -> Phrase:
    return Phrase(text=text, voice_id=_VOICE, language_code="sl")


def _lesson(texts: list[str], sections: int = 1) -> Lesson:
    """One section per *sections*, cycling *texts* across them."""
    per = len(texts) // sections
    return Lesson(
        title="Napredek",
        language_code="sl",
        sections=[
            Section(
                section_type=section_type,
                phrases=[_phrase(t) for t in texts[i * per : (i + 1) * per]],
            )
            for i, section_type in enumerate(
                [SectionType.NATURAL_SPEED, SectionType.TRANSLATED, SectionType.SLOW_SPEED][:sections]
            )
        ],
    )


# Six phrases, five distinct synthesis keys: the first text appears in both
# sections, so the dedup is the render-scoped memo's and not one gather's.
_FIVE_KEY_LESSON_TEXTS = [*_TEXTS, _TEXTS[0]]


async def test_progress_reaches_the_total_and_never_goes_backwards(tmp_path):
    calls: list[tuple[int, int]] = []
    tts = FakeTTS()

    await _renderer(tts).render(
        _lesson(_FIVE_KEY_LESSON_TEXTS, sections=2),
        tmp_path / "out.wav",
        on_progress=lambda done, total: calls.append((done, total)),
    )

    # The last notification is the whole plan finished.
    assert calls[-1] == (5, 5)
    assert max(total for _done, total in calls) == 5
    assert all(done <= total for done, total in calls)
    dones = [done for done, _total in calls]
    assert dones == sorted(dones), f"done went backwards: {dones}"
    # Exactly one done-increment per clip: five, no more.
    assert sum(1 for before, after in zip([0, *dones[:-1]], dones, strict=True) if after > before) == 5
    # The duplicated phrase was synthesized once and reused.
    assert sorted(tts.requested) == sorted(["Napredek", *_TEXTS])


async def test_progress_is_absent_without_a_callback_and_the_audio_is_identical(tmp_path):
    """``on_progress=None`` is today's behaviour: same calls, same bytes."""
    with_progress = tmp_path / "with.wav"
    without_progress = tmp_path / "without.wav"
    tts_a, tts_b = FakeTTS(), FakeTTS()
    calls: list[tuple[int, int]] = []

    await _renderer(tts_a).render(
        _lesson(_FIVE_KEY_LESSON_TEXTS, sections=2),
        with_progress,
        on_progress=lambda done, total: calls.append((done, total)),
    )
    await _renderer(tts_b).render(_lesson(_FIVE_KEY_LESSON_TEXTS, sections=2), without_progress, on_progress=None)

    assert with_progress.read_bytes() == without_progress.read_bytes()
    assert calls, "the callback form reported nothing"
    assert tts_a.requested == tts_b.requested


async def test_a_failed_and_a_cancelled_clip_never_count_as_done(tmp_path):
    """A failed task and a cancelled sibling are both skipped.

    The render raises, so progress ends at ``done=1`` of 3 and stops: counting
    either would report a render that failed as one that finished.
    """
    calls: list[tuple[int, int]] = []
    tts = FakeTTS(fail_after={"boom": 0.02}, blocked={"slow"})

    with pytest.raises(RuntimeError, match="refused"):
        await _renderer(tts).render(
            _lesson(["ok", "boom", "slow"]),
            tmp_path / "out.wav",
            on_progress=lambda done, total: calls.append((done, total)),
        )

    assert calls[-1] == (1, 3)
    assert all(done <= total for done, total in calls)
