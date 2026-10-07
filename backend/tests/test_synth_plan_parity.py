"""The cost report names exactly the clips the renderer sends (tunatale-u9ce).

The report decides hit-or-miss by asking the cache for the file each request
would be written to, so it has to arrive at the SAME requests the renderer
does. It used to carry its own copy of the renderer's rules, and two rules were
added to the renderer after the copy was made. Measured on the live lessons,
2026-10-06: 57 of a Tagalog lesson's 227 clips and 18 of a Cebuano lesson's 232
were files no render would ever write, and the report quoted them as uncached.

So this renders a lesson through the real renderer, with a stand-in that
records what it is asked for and synthesizes nothing, and compares that with
what the report collects. Per language, because the rules that drifted were
each one language's: Norwegian agreed all along and proved nothing about the
others.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from app.audio.render_cost import collect_keys
from app.audio.renderer import build_lesson_renderer
from app.audio.slicer import PARENT_RATE
from app.config import settings
from app.generation.affix_drill import build_affix_drill
from app.generation.section_builder import (
    build_affix_drill_section,
    build_en_translated_section,
    build_key_phrases_section,
    build_natural_speed_section,
    build_slow_en_translated_section,
    build_slow_speed_section,
    build_slow_translated_section,
    build_translated_section,
)
from app.languages import (
    get_a1_morphology,
    get_language,
    get_phoneme_planner,
    get_preprocessor,
    get_slow_word,
    get_tts_locale,
)
from app.models.lesson import Lesson, Section
from tests.test_renderer import _make_wav_bytes

# One small story per language, built by the real section builders so the
# phrases carry what generated lessons carry: breakdown chunks with provenance,
# multi-word drill steps, punctuation on a chunk, a long word, a compound, a
# name, and English beside all of it.
_STORIES = {
    "tl": (
        [("Saan ang libing?", "Where is the burial?"), ("Nakikiramay po ako", "My condolences")],
        [("Magandang gabi po. Nakikiramay po ako.", "Good evening. My condolences."), ("Opo.", "Yes.")],
    ),
    "ceb": (
        [("Kanus-a ang lubong?", "When is the burial?"), ("ilubong ugma", "bury tomorrow")],
        [("Buntag sa Jimenez. Naglakaw si Paul.", "Morning in Jimenez. Paul walked."), ("Oo.", "Yes.")],
    ),
    "no": (
        [("Hvor er flyplassen?", "Where is the airport?"), ("ta hensyn", "be considerate")],
        [("Jeg venter på flyplassen.", "I am waiting at the airport."), ("Ja.", "Yes.")],
    ),
    "sl": (
        [("Dober dan!", "Good day!"), ("Prosim kavo", "A coffee, please")],
        [("Dober dan! Prosim kavo.", "Good day! A coffee, please."), ("Ja.", "Yes.")],
    ),
}


# The affix drill is the one section not made from a story, for the languages
# that register affix patterns: one-word forms, prompts, and a whole line.
_DRILLS = {
    "ceb": ("mo-mi", ["inom", "lakaw"], ("Milakaw si Paul sa dalan.", "Paul walked on the road.")),
}


def _lesson(code: str) -> Lesson:
    key_phrases, lines = _STORIES[code]
    language = get_language(code)
    voices, narrator = language.tts_voice_map, language.tts_voice_map["narrator"]
    scenes = [
        {
            "label": "A scene",
            "lines": [
                {"speaker": speaker, "text": text, "translation": translation}
                for speaker, (text, translation) in zip(("female-1", "male-1"), lines, strict=True)
            ],
        }
    ]
    spoken = {"en_voice_map": language.tts_en_voice_map}
    sections: list[Section] = [
        build_key_phrases_section(
            [{"phrase": phrase, "translation": translation} for phrase, translation in key_phrases],
            voices,
            narrator,
            code,
        ),
        build_natural_speed_section(scenes, voices, narrator, code),
        build_slow_speed_section(scenes, voices, narrator, code),
        build_translated_section(scenes, voices, narrator, code, **spoken),
        build_slow_translated_section(scenes, voices, narrator, code, **spoken),
        build_en_translated_section(scenes, voices, narrator, code, **spoken),
        build_slow_en_translated_section(scenes, voices, narrator, code, **spoken),
    ]
    if code in _DRILLS:
        pattern_key, roots, line = _DRILLS[code]
        pattern = next(p for p in get_a1_morphology(code).patterns if p.key == pattern_key)
        drill = build_affix_drill(code, pattern, roots=roots, line=line)
        sections.append(build_affix_drill_section(drill, narrator_voice=narrator, l2_voice=voices["female-1"]))
    return Lesson(title="A lesson", language_code=code, sections=sections, narrator_voice=narrator)


async def _rendered(lesson: Lesson) -> set[tuple]:
    """Every distinct request the real renderer makes for *lesson*'s sections."""
    requests: set[tuple] = set()
    audio = _make_wav_bytes()

    class _Recorder:
        async def synthesize(
            self, text, voice_id, output_path, rate="+0%", phonemes=None, speak_locale=None, enunciation=None
        ):
            requests.add(
                (text, voice_id, rate, tuple(sorted(phonemes.items())) if phonemes else None, speak_locale, enunciation)
            )
            output_path.write_bytes(audio)

        async def list_voices(self, language_code=None):
            return []

    renderer = build_lesson_renderer(_Recorder(), [lesson.language_code], settings)
    memo: dict = {}
    lock = asyncio.Lock()
    with tempfile.TemporaryDirectory() as tmp:
        for index, section in enumerate(lesson.sections):
            await renderer._synthesize_section(section, Path(tmp), index, lesson.language_code, memo, lock)
    return requests


def _reported(lesson: Lesson) -> set[tuple]:
    """Every distinct request the cost report collects for *lesson*, the app's way (no slicer)."""
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
    return {
        (text, voice_id, rate, tuple(sorted(phonemes.items())) if phonemes else None, speak_locale, enunciation)
        for text, voice_id, rate, phonemes, speak_locale, enunciation in (
            *keys.phrase.values(),
            *keys.gemini_phrase.values(),
        )
    }


@pytest.mark.parametrize("code", sorted(_STORIES))
async def test_the_report_collects_exactly_the_requests_the_renderer_makes(code):
    lesson = _lesson(code)

    rendered = await _rendered(lesson)
    reported = _reported(lesson)

    assert rendered, "the renderer made no request: the lesson exercised nothing"
    assert reported - rendered == set(), "the report names a clip no render sends"
    assert rendered - reported == set(), "the renderer sends a clip the report does not price"


@pytest.mark.parametrize(
    ("code", "what"),
    [
        ("tl", "an IPA chunk with no locale wrapper"),
        ("ceb", "a multi-word drill step with a per-word reading"),
        ("ceb", "an Enunciated line with its reading"),
        ("no", "an Enunciated line with a cut inside a word"),
    ],
)
async def test_the_story_really_reaches_the_rule_it_is_there_for(code, what):
    """A parity test passes vacuously on a lesson that never triggers the rule.

    These are the rules that drifted (and the two this work added), each
    checked to be PRESENT in what the renderer sends for that language's story.
    """
    rendered = await _rendered(_lesson(code))
    target = get_tts_locale(code)

    found = {
        "an IPA chunk with no locale wrapper": any(ph and locale is None for _, _, _, ph, locale, _ in rendered),
        "a multi-word drill step with a per-word reading": any(
            ph and len(ph) >= 2 and say is None and locale == target for _, _, _, ph, locale, say in rendered
        ),
        "an Enunciated line with its reading": any(ph and say for _, _, _, ph, _, say in rendered),
        "an Enunciated line with a cut inside a word": any(
            say and any(len(parts) > 1 for parts in say) for _, _, _, _, _, say in rendered
        ),
    }[what]

    assert found, f"{code}: the story no longer produces {what}"
