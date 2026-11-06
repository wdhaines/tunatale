"""A lesson line's own take, for its sentence card's audio (tunatale-h0q3.42).

The card sounds like the lesson: its audio is the clip the lesson's Natural
Speed section already rendered, found in the TTS cache, never a new synthesis.
The oracle is the cost instrument's own addressing (``collect_keys`` plus each
adapter's ``_cache_path``, exactly as ``price_lessons`` reads it), so the take
and the bill name the same file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.audio.azure_tts import AzureTTSService
from app.audio.gemini_tts import GeminiTTSService
from app.audio.render_cost import collect_keys, gemini_cache_path, natural_take
from app.audio.slicer import PARENT_RATE
from app.audio.tts_router import provider_for
from app.languages import get_phoneme_planner, get_preprocessor, get_slow_word, get_tts_locale
from app.models.lesson import Lesson, Phrase, Section, SectionType

NB_A = "nb-NO-FinnNeural"
NB_B = "nb-NO-PernilleNeural"


def _lesson(code: str, lines: list[tuple[str, str]]) -> Lesson:
    natural = Section(
        SectionType.NATURAL_SPEED,
        [Phrase("Natural Speed", "en-US-AndrewMultilingualNeural", "en", role="narrator")]
        + [Phrase(text, voice, code) for text, voice in lines],
    )
    slow = Section(SectionType.SLOW_SPEED, [Phrase(text, voice, code) for text, voice in lines])
    return Lesson(title="t", language_code=code, sections=[natural, slow])


def _instrument_paths(lesson: Lesson, cache_dir: Path) -> dict[tuple[str, str, bool], Path]:
    """(text, voice, enunciated) -> the file the cost report would price for it."""
    code = lesson.language_code
    keys = collect_keys(
        [lesson],
        language_code=code,
        preprocessor=get_preprocessor(code),
        planner=get_phoneme_planner(code),
        target_locale=get_tts_locale(code),
        syllabify_fn=None,
        slicer_enabled=False,
        parent_rate=PARENT_RATE,
        slow_word_fn=get_slow_word(code),
    )
    out = {}
    for v in [*keys.azure_values, *keys.gemini_values]:
        text, voice, rate, phonemes, locale, enunciation, _line, stressed = v
        if provider_for(voice) == "gemini":
            path = gemini_cache_path(GeminiTTSService(cache_dir=cache_dir), v)
        else:
            path = AzureTTSService(cache_dir=cache_dir)._cache_path(
                text, voice, rate, phonemes, locale, enunciation, stressed
            )
        out[(text, voice, enunciation is not None)] = path
    return out


def test_finds_the_natural_speed_clip_the_cost_report_addresses(tmp_path):
    lesson = _lesson("no", [("Hvor bor du?", NB_A), ("Jeg bor i Oslo.", NB_B)])
    path = _instrument_paths(lesson, tmp_path)[("Jeg bor i Oslo.", NB_B, False)]
    path.write_bytes(b"take")

    assert natural_take(lesson, "Jeg bor i Oslo.", cache_dir=tmp_path) == path


def test_not_the_enunciated_clip_of_the_same_line(tmp_path):
    lesson = _lesson("no", [("Jeg bor i Oslo.", NB_B)])
    paths = _instrument_paths(lesson, tmp_path)
    paths[("Jeg bor i Oslo.", NB_B, True)].write_bytes(b"slow")  # only the Enunciated take is cached

    assert natural_take(lesson, "Jeg bor i Oslo.", cache_dir=tmp_path) is None


def test_uncached_line_has_no_take(tmp_path):
    lesson = _lesson("no", [("Jeg bor i Oslo.", NB_B)])
    assert natural_take(lesson, "Jeg bor i Oslo.", cache_dir=tmp_path) is None


def test_a_line_the_lesson_never_says_has_no_take(tmp_path):
    lesson = _lesson("no", [("Jeg bor i Oslo.", NB_B)])
    for path in _instrument_paths(lesson, tmp_path).values():
        path.write_bytes(b"x")
    assert natural_take(lesson, "Jeg bor i Bergen.", cache_dir=tmp_path) is None


def test_voice_picks_between_two_speakers_saying_the_same_line(tmp_path):
    lesson = _lesson("no", [("Ja.", NB_A), ("Ja.", NB_B)])
    paths = _instrument_paths(lesson, tmp_path)
    for path in paths.values():
        path.write_bytes(b"x")

    assert natural_take(lesson, "Ja.", cache_dir=tmp_path, voice_id=NB_B) == paths[("Ja.", NB_B, False)]
    assert natural_take(lesson, "Ja.", cache_dir=tmp_path, voice_id=NB_A) == paths[("Ja.", NB_A, False)]


def test_gemini_voiced_line(tmp_path):
    lesson = _lesson("ceb", [("Maayo man.", "ceb-PH-CharonGemini")])
    path = _instrument_paths(lesson, tmp_path)[("Maayo man.", "ceb-PH-CharonGemini", False)]
    path.write_bytes(b"take")

    assert natural_take(lesson, "Maayo man.", cache_dir=tmp_path) == path


@pytest.mark.parametrize("sections", [[], [Section(SectionType.SLOW_SPEED, [])]])
def test_a_lesson_with_no_natural_section_has_no_take(tmp_path, sections):
    lesson = Lesson(title="t", language_code="no", sections=sections)
    assert natural_take(lesson, "Ja.", cache_dir=tmp_path) is None
