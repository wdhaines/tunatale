"""Branches of the authored story drill the oracle file does not reach (tunatale-h0q3.3).

The oracle is ``tests/test_story_drills.py``. This file covers what it does not:
a ``narrator`` speaker in the dialogue (only ``narration`` appears there), an
``answer_speaker`` on a drill kind that ignores it, validation with no language
to check speakers against, and the normaliser meeting a drill with no model —
which only a language that registers a normaliser (Tagalog) can reach.

Norwegian throughout except the normaliser case; sentences invented.
"""

from __future__ import annotations

from app.generation.story import build_lesson_from_story
from app.languages import get_language
from app.models.lesson import SectionType
from app.storage.lesson_io import validate_story

_NO = get_language("no")
_F1 = _NO.tts_voice_map["female-1"]
_M1 = _NO.tts_voice_map["male-1"]


def _lines() -> list[dict]:
    return [
        {"speaker": "narration", "text": "En butikk.", "translation": "A shop."},
        {"speaker": "female-1", "text": "Hvor skal du?", "translation": "Where are you going?"},
        {"speaker": "male-1", "text": "Jeg skal hjem.", "translation": "I'm going home."},
    ]


def _story(drills: list[dict], lines: list[dict] | None = None) -> dict:
    return {
        "title": "Going home",
        "key_phrases": [{"phrase": "Jeg skal hjem.", "translation": "I'm going home."}],
        "scenes": [{"label": "At the shop", "lines": _lines() if lines is None else lines}],
        "drills": drills,
    }


def _drill_section(story: dict):
    lesson = build_lesson_from_story(story, _NO)
    found = [s for s in lesson.sections if s.section_type is SectionType.AFFIX_DRILL]
    assert len(found) == 1
    return found[0]


def test_a_narrator_speaker_is_not_the_default_voice():
    """The default is the dialogue's first character, so ``narrator`` is skipped like ``narration``."""
    lines = [
        {"speaker": "narrator", "text": "En butikk.", "translation": "A shop."},
        {"speaker": "male-1", "text": "Jeg skal hjem.", "translation": "I'm going home."},
    ]
    section = _drill_section(
        _story([{"kind": "true_false", "steps": [{"cue": "Hun skal hjem.", "answer": "Nei."}]}], lines)
    )

    assert [p.voice_id for p in section.phrases[2:]] == [_M1, _M1]


def test_a_scene_with_no_lines_is_skipped_when_the_default_voice_is_chosen():
    """The first character may be in the second scene; an empty scene has none to lend."""
    story = {
        "title": "Going home",
        "key_phrases": [{"phrase": "Jeg skal hjem.", "translation": "I'm going home."}],
        "scenes": [
            {"label": "An empty street", "lines": []},
            {"label": "At the shop", "lines": [{"speaker": "male-1", "text": "Jeg skal hjem.", "translation": "x"}]},
        ],
        "drills": [{"kind": "true_false", "steps": [{"cue": "Hun skal hjem.", "answer": "Nei."}]}],
    }
    section = _drill_section(story)

    assert [p.voice_id for p in section.phrases[2:]] == [_M1, _M1]


def test_an_answer_speaker_on_a_true_false_drill_is_ignored():
    """Only a question and answer drill has two voices; elsewhere the field has no say."""
    drills = [{"kind": "true_false", "answer_speaker": "ghost", "steps": [{"cue": "Hun skal hjem.", "answer": "Nei."}]}]
    section = _drill_section(_story(drills))

    assert [p.voice_id for p in section.phrases[2:]] == [_F1, _F1]


def test_validate_accepts_unknown_drill_speakers_without_a_language():
    """Without a language there is no voice map to check a role against, as for dialogue lines."""
    drills = [
        {
            "kind": "true_false",
            "speaker": "robot-8",
            "answer_speaker": "robot-9",
            "steps": [{"cue": "a", "answer": "b"}],
        }
    ]

    validate_story(_story(drills))


def test_a_drill_with_no_model_survives_the_normaliser():
    """A true or false drill has no ``model``; the Tagalog normaliser must not demand one."""
    tl = get_language("tl")
    story = {
        "title": "Kape",
        "key_phrases": [{"phrase": "Magkape tayo.", "translation": "Let's have coffee."}],
        "scenes": [],
        "drills": [{"kind": "true_false", "steps": [{"cue": "Mag‑kape kami.", "answer": "Oo."}]}],
    }
    lesson = build_lesson_from_story(story, tl)
    section = next(s for s in lesson.sections if s.section_type is SectionType.AFFIX_DRILL)

    assert [p.text for p in section.phrases if p.language_code == "tl"] == ["Magkape kami.", "Oo."]
