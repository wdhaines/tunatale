"""Narration lines are read by the narrator, not by a character (bd tunatale-fx5n).

The story prompt offered only character roles, so the model put narration in a
character's mouth: a Cebuano lesson used male-2 for nothing but narration ("At
seven in the evening, Paul walked along the road in Jimenez."), and a Norwegian
one did the same. A text heuristic cannot sort narration from speech ("The window
is open." is a detective talking), so the generator tags it with its own role.

"narration", not "narrator": ``narrator`` is structural English narration (titles,
scene labels, translations) that cues, the transcript and lesson_io key on.

The user's calls, 2026-09-30: Davis reads narration in BOTH languages, so it
matches the titles; Cebuano keeps its current voice for the Cebuano text.
"""

import pytest

from app.generation.prompts import _l2_roles_line, build_story_system_prompt
from app.generation.section_builder import build_natural_speed_section, build_translated_section
from app.languages import get_language
from app.models.language import NARRATOR_VOICE

_CODES = ["no", "sl", "tl", "ceb"]

_SCENES = [
    {
        "label": "Early Morning on the Street Outside",
        "lines": [
            {
                "speaker": "narration",
                "text": "Det var tidlig morgen, og gaten lå stille under snøen.",
                "translation": "It was early morning, and the street lay quiet under the snow.",
            },
            {"speaker": "female-2", "text": "Sporene er store.", "translation": "The tracks are big."},
        ],
    }
]


@pytest.mark.parametrize("code", _CODES)
def test_davis_reads_the_english_of_narration_in_every_language(code):
    assert get_language(code).tts_en_voice_map["narration"] == NARRATOR_VOICE


@pytest.mark.parametrize("code", ["no", "sl", "tl"])
def test_davis_reads_the_l2_narration_where_azure_speaks_the_language(code):
    assert get_language(code).tts_voice_map["narration"] == NARRATOR_VOICE


def test_cebuano_narration_keeps_the_voice_that_read_it():
    """Cebuano audio is Gemini and Davis has no Cebuano; the user kept the current
    voice — Orus, the male-2 voice the model had been borrowing for narration."""
    assert get_language("ceb").tts_voice_map["narration"] == "ceb-PH-OrusGemini"


@pytest.mark.parametrize("code", _CODES)
def test_every_voice_reading_the_target_language_has_a_measured_gain(code):
    """Davis now reads L2, so he needs an L2 gain: an unmapped voice renders at
    0.0 dB, silently un-normalised against the cast."""
    lang = get_language(code)
    l2_voices = {v for role, v in lang.tts_voice_map.items() if role != "narrator"}
    assert not l2_voices - set(lang.tts_voice_gain_db)


def test_a_narration_line_is_the_narrators_in_both_languages():
    lang = get_language("no")
    section = build_translated_section(
        _SCENES, lang.tts_voice_map, NARRATOR_VOICE, "no", en_voice_map=lang.tts_en_voice_map
    )
    by_text = {p.text: p for p in section.phrases}

    l2 = by_text["Det var tidlig morgen, og gaten lå stille under snøen."]
    assert (l2.voice_id, l2.role, l2.language_code) == (NARRATOR_VOICE, "narration", "no")
    en = by_text["It was early morning, and the street lay quiet under the snow."]
    assert (en.voice_id, en.language_code) == (NARRATOR_VOICE, "en")
    # A character line beside it is untouched.
    assert by_text["Sporene er store."].voice_id == lang.tts_voice_map["female-2"]
    assert by_text["The tracks are big."].voice_id == lang.tts_en_voice_map["female-2"]


def test_narration_stays_a_dialogue_line_in_the_natural_speed_section():
    """It is still target-language input: it plays in the dialogue sections."""
    lang = get_language("no")
    section = build_natural_speed_section(_SCENES, lang.tts_voice_map, NARRATOR_VOICE, "no")
    assert [p.role for p in section.phrases if p.language_code == "no"] == ["narration", "female-2"]


@pytest.mark.parametrize("code", _CODES)
def test_the_story_writer_is_told_to_tag_narration(code):
    prompt = build_story_system_prompt(get_language(code))
    assert '"narration"' in prompt


def test_narration_is_not_offered_as_a_character():
    assert "narration" not in _l2_roles_line(get_language("no"))
