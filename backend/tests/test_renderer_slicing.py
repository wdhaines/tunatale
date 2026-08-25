"""Renderer + slicer integration: fallback identity and pacing.

These are the two behaviours the whole feature is gated on. Slicing may change
how a chunk *sounds*; it may not change anything else — not the section's
rhythm, and not one byte of output when it is unavailable.
"""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest
import soundfile as sf

from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.renderer import LessonRenderer
from app.audio.slicer import SliceSpec
from app.generation.section_builder import build_key_phrases_section
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.plugins.languages.no.preprocessor import NorwegianPreprocessor

_RATE = 24_000
_TTS_MS = 600.0
_SLICED_MS = 150.0
_L2_VOICE = "nb-NO-PernilleNeural"
_NARRATOR = "en-US-JennyNeural"
# "politiet" is po|li|ti|et, whose buildup offers seven contiguous spans.
_WORD = "politiet"
_TRANSLATION = "the police"


def _wav_bytes(duration_ms: float, marker: float) -> bytes:
    buf = BytesIO()
    frames = round(duration_ms / 1000 * _RATE)
    sf.write(buf, np.full((frames, 1), marker, dtype="float32"), _RATE, format="WAV", subtype="PCM_16")
    return buf.getvalue()


class FakeTTS:
    """Every utterance is the same length — so any duration change is the slicer's."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def synthesize(self, text, voice_id, output_path, rate="+0%", phonemes=None) -> None:
        self.calls.append(text)
        output_path.write_bytes(_wav_bytes(_TTS_MS, marker=0.25))


class FakeSlicer:
    """Writes a short, distinctively-valued buffer for whichever spans it accepts."""

    def __init__(self, accept: bool = True) -> None:
        self.accept = accept
        self.requests: list[SliceSpec] = []

    async def slice_to_file(self, spec: SliceSpec, out_path) -> bool:
        self.requests.append(spec)
        if not self.accept:
            return False
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(_wav_bytes(_SLICED_MS, marker=0.75))
        return True


def _lesson(phrases: list[Phrase]) -> Lesson:
    """A bare NATURAL_SPEED lesson — no key-phrase cue scaffolding required."""
    return Lesson(
        title="Test",
        language_code="no",
        sections=[Section(section_type=SectionType.NATURAL_SPEED, phrases=phrases)],
    )


def _key_phrase_lesson() -> Lesson:
    """A real key-phrases section, built the way the pipeline builds one.

    Going through ``build_key_phrases_section`` rather than hand-writing phrases
    means the provenance under test is the provenance production actually
    produces, and the cue manifest's phrase-count contract holds by construction.
    """
    section = build_key_phrases_section(
        [{"phrase": _WORD, "translation": _TRANSLATION}],
        {"female-1": _L2_VOICE},
        _NARRATOR,
        "no",
    )
    return Lesson(
        title="Test",
        language_code="no",
        sections=[section],
        key_phrases=[KeyPhraseInfo(phrase=_WORD, translation=_TRANSLATION)],
    )


def _sliceable_count(lesson: Lesson) -> int:
    return sum(1 for p in lesson.sections[0].phrases if p.syllable_span is not None)


def _renderer(tts, slicer=None):
    return LessonRenderer(
        tts=tts,
        preprocessors={"no": NorwegianPreprocessor()},
        pause_calculator=NaturalPauseCalculator(),
        slicers={"no": slicer} if slicer is not None else None,
    )


async def _render(tmp_path, lesson, slicer=None, name="out.wav"):
    tts = FakeTTS()
    out = tmp_path / name
    await _renderer(tts, slicer).render(lesson, out)
    return out.read_bytes(), tts


class TestFallbackIdentity:
    """With slicing off or failing, output must be byte-identical to today."""

    async def test_no_slicer_matches_baseline(self, tmp_path):
        lesson = _key_phrase_lesson()
        baseline, _ = await _render(tmp_path, lesson, slicer=None, name="a.wav")
        again, _ = await _render(tmp_path, lesson, slicer=None, name="b.wav")
        assert baseline == again

    async def test_slicer_that_declines_every_span_is_byte_identical(self, tmp_path):
        """The aligner raising, an unsupported word, or ``flat_syllables`` →
        ``None`` all arrive here as a ``False`` return."""
        lesson = _key_phrase_lesson()
        baseline, _ = await _render(tmp_path, lesson, slicer=None, name="base.wav")
        declining = FakeSlicer(accept=False)
        sliced, _ = await _render(tmp_path, lesson, slicer=declining, name="declined.wav")
        assert declining.requests, "the slicer was never consulted — the test proves nothing"
        assert sliced == baseline

    async def test_only_provenance_carrying_phrases_are_offered_to_the_slicer(self, tmp_path):
        lesson = _key_phrase_lesson()
        slicer = FakeSlicer()
        await _render(tmp_path, lesson, slicer=slicer, name="c.wav")
        assert len(slicer.requests) == _sliceable_count(lesson)
        assert all(r.word == _WORD for r in slicer.requests)
        # The English title/translation and the standalone key-phrase line are
        # not among them.
        assert len(slicer.requests) < len(lesson.sections[0].phrases)

    async def test_slicer_receives_the_phrase_voice(self, tmp_path):
        slicer = FakeSlicer()
        await _render(tmp_path, _key_phrase_lesson(), slicer=slicer, name="d.wav")
        assert {r.voice_id for r in slicer.requests} == {_L2_VOICE}

    async def test_a_lesson_with_no_provenance_at_all_is_untouched(self, tmp_path):
        lesson = _lesson([Phrase(text="god dag", voice_id=_L2_VOICE, language_code="no")])
        baseline, _ = await _render(tmp_path, lesson, slicer=None, name="e.wav")
        slicer = FakeSlicer()
        sliced, _ = await _render(tmp_path, lesson, slicer=slicer, name="f.wav")
        assert sliced == baseline
        assert slicer.requests == []


class TestSlicersAreKeyedByLanguage:
    async def test_a_slicer_for_another_language_is_not_used(self, tmp_path):
        """Multi-language mode renders both languages through one renderer, so a
        Slovene slicer must never be handed Norwegian words (or vice versa) —
        its aligner and syllabifier are the wrong ones for them."""
        lesson = _key_phrase_lesson()  # language_code="no"
        wrong_language = FakeSlicer()
        tts = FakeTTS()
        renderer = LessonRenderer(
            tts=tts,
            preprocessors={"no": NorwegianPreprocessor()},
            pause_calculator=NaturalPauseCalculator(),
            slicers={"sl": wrong_language},
        )
        out = tmp_path / "n.wav"
        await renderer.render(lesson, out)
        assert wrong_language.requests == []
        baseline, _ = await _render(tmp_path, lesson, slicer=None, name="o.wav")
        assert out.read_bytes() == baseline


class TestSlicingChangesTheAudio:
    async def test_accepted_spans_replace_the_chunk_audio(self, tmp_path):
        lesson = _key_phrase_lesson()
        baseline, _ = await _render(tmp_path, lesson, slicer=None, name="g.wav")
        sliced, _ = await _render(tmp_path, lesson, slicer=FakeSlicer(), name="h.wav")
        assert sliced != baseline

    async def test_the_fallback_render_still_happens(self, tmp_path):
        """The isolated TTS is still needed — it is what the pause is measured
        from, and what a declined span falls back to."""
        _, tts = await _render(tmp_path, _key_phrase_lesson(), slicer=FakeSlicer(), name="i.wav")
        assert "po" in tts.calls
        assert "tiet" in tts.calls


class TestPacingComesFromTheFallbackDuration:
    """A key-phrase L2 pause equals the chunk's own duration.

    Letting the shorter sliced chunk drive it silently rewrote a real section's
    rhythm from 6.5 to 5.1 minutes. The user asked for the original pacing, so
    the pause is measured from the fallback TTS render — a behaviour decision,
    not an implementation detail.
    """

    async def _total_ms(self, tmp_path, slicer, name):
        data, _ = await _render(tmp_path, _key_phrase_lesson(), slicer=slicer, name=name)
        samples, rate = sf.read(BytesIO(data), dtype="float32", always_2d=True)
        return len(samples) / rate * 1000.0

    async def test_output_shrinks_by_exactly_the_audio_that_was_replaced(self, tmp_path):
        n = _sliceable_count(_key_phrase_lesson())
        baseline = await self._total_ms(tmp_path, None, "j.wav")
        sliced = await self._total_ms(tmp_path, FakeSlicer(), "k.wav")
        # Each sliced chunk is (600 - 150) ms shorter. If the pauses had followed
        # the sliced durations, each would ALSO have shrunk by that much.
        expected = baseline - n * (_TTS_MS - _SLICED_MS)
        assert sliced == pytest.approx(expected, abs=2.0)

    async def test_pacing_from_the_slice_would_shrink_it_twice_as_much(self, tmp_path):
        """Guards the assertion above against being trivially true: pacing off
        the sliced duration is a materially different, and much smaller, number."""
        n = _sliceable_count(_key_phrase_lesson())
        baseline = await self._total_ms(tmp_path, None, "l.wav")
        sliced = await self._total_ms(tmp_path, FakeSlicer(), "m.wav")
        if_pause_followed_slice = baseline - 2 * n * (_TTS_MS - _SLICED_MS)
        assert sliced > if_pause_followed_slice + 100.0
