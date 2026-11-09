"""Authored drills in Story JSON (tunatale-h0q3.3).

A story may carry ``drills``: substitution (Wolff's Pattern Practices), question
and answer, and true or false. They become ONE drill section (the section type
the affix drill already uses, so the player's Drill phase and the hands-free run
pick it up). Every step has one shape: a cue, a pause to answer, the answer
(always given), a pause to repeat it.

Settled by the owner, 2026-11-09:
* one drill section whose steps carry their own kind, several drills back to back;
* voices are the lesson's characters: one character gives a substitution or
  true/false drill's cue and answer; a question-and-answer drill puts the question
  in one character's voice and the answer in the other's; a drill may name its own;
* the narrator says one short English line at the start of each drill, nothing
  between steps.

Sentences here are invented.
"""

from __future__ import annotations

import pytest

from app.audio.cues import CueTiming, build_cue_manifest
from app.audio.pause_calculator import NaturalPauseCalculator
from app.generation.story import build_lesson_from_story
from app.languages import get_language
from app.models.lesson import Lesson, SectionType
from app.storage.lesson_io import validate_story

_NO = get_language("no")
_NARRATOR = _NO.tts_voice_map["narrator"]
_F1 = _NO.tts_voice_map["female-1"]
_F2 = _NO.tts_voice_map["female-2"]
_M1 = _NO.tts_voice_map["male-1"]


def _drills() -> list[dict]:
    return [
        {
            "kind": "substitution",
            "model": "Jeg skal hjem.",
            "steps": [{"cue": "hun", "answer": "Hun skal hjem."}, {"cue": "vi", "answer": "Vi skal hjem."}],
        },
        {"kind": "question_answer", "steps": [{"cue": "Hvor skal du?", "answer": "Jeg skal hjem."}]},
        {"kind": "true_false", "steps": [{"cue": "Hun skal hjem.", "answer": "Nei."}]},
    ]


def _story(drills: list[dict] | None = None, *, scenes: bool = True) -> dict:
    story: dict = {
        "title": "Going home",
        "key_phrases": [{"phrase": "Jeg skal hjem.", "translation": "I'm going home."}],
        "scenes": [
            {
                "label": "At the shop",
                "lines": [
                    {"speaker": "narration", "text": "En butikk.", "translation": "A shop."},
                    {"speaker": "female-1", "text": "Hvor skal du?", "translation": "Where are you going?"},
                    {"speaker": "male-1", "text": "Jeg skal hjem.", "translation": "I'm going home."},
                ],
            }
        ]
        if scenes
        else [],
    }
    if drills is not None:
        story["drills"] = drills
    return story


def _drill_section(story: dict):
    lesson = build_lesson_from_story(story, _NO)
    found = [s for s in lesson.sections if s.section_type is SectionType.AFFIX_DRILL]
    assert len(found) == 1
    return lesson, found[0]


def _rows(section) -> list[tuple[str, str, str, str]]:
    return [(p.text, p.voice_id, p.language_code, p.role) for p in section.phrases]


# --- the section -------------------------------------------------------------


def test_a_story_without_drills_has_no_drill_section():
    lesson = build_lesson_from_story(_story(), _NO)

    assert len(lesson.sections) == 7
    assert all(s.section_type is not SectionType.AFFIX_DRILL for s in lesson.sections)


def test_a_storys_drills_are_one_section_after_the_dialogue_sections():
    lesson, section = _drill_section(_story(_drills()))

    assert len(lesson.sections) == 8
    assert lesson.sections[-1] is section


def test_every_drill_is_its_instruction_then_its_steps_back_to_back():
    _, section = _drill_section(_story(_drills()))

    assert _rows(section) == [
        ("Drill", _NARRATOR, "en", "narrator"),
        # substitution: the model to repeat, then cue and changed sentence, one voice
        ("Change the sentence.", _NARRATOR, "en", "narrator"),
        ("Jeg skal hjem.", _F1, "no", "model"),
        ("hun", _F1, "no", "prompt"),
        ("Hun skal hjem.", _F1, "no", "answer"),
        ("vi", _F1, "no", "prompt"),
        ("Vi skal hjem.", _F1, "no", "answer"),
        # question and answer: two characters
        ("Answer the question.", _NARRATOR, "en", "narrator"),
        ("Hvor skal du?", _F1, "no", "prompt"),
        ("Jeg skal hjem.", _M1, "no", "answer"),
        # true or false: the statement, then the authored verdict, one voice
        ("True or false?", _NARRATOR, "en", "narrator"),
        ("Hun skal hjem.", _F1, "no", "prompt"),
        ("Nei.", _F1, "no", "answer"),
    ]


def test_the_default_voices_are_the_dialogues_first_two_characters_not_its_narration():
    """female-1 speaks first in the dialogue (after a narration line), male-1 second."""
    _, section = _drill_section(_story(_drills()[1:2]))

    question, answer = section.phrases[2:]
    assert (question.voice_id, answer.voice_id) == (_F1, _M1)


def test_a_drill_names_its_own_voices():
    drills = [
        {
            "kind": "substitution",
            "speaker": "male-1",
            "model": "Jeg skal hjem.",
            "steps": [{"cue": "hun", "answer": "Hun skal hjem."}],
        },
        {
            "kind": "question_answer",
            "speaker": "male-1",
            "answer_speaker": "female-2",
            "steps": [{"cue": "Hvor skal du?", "answer": "Jeg skal hjem."}],
        },
    ]
    _, section = _drill_section(_story(drills))

    voices = [p.voice_id for p in section.phrases if p.language_code == "no"]
    assert voices == [_M1, _M1, _M1, _M1, _F2]


def test_with_one_character_a_question_is_answered_in_the_same_voice():
    story = _story(_drills()[1:2])
    story["scenes"][0]["lines"] = story["scenes"][0]["lines"][:2]  # narration + female-1 only
    _, section = _drill_section(story)

    assert [p.voice_id for p in section.phrases[2:]] == [_F1, _F1]


def test_a_story_with_no_dialogue_drills_in_female_1():
    _, section = _drill_section(_story(_drills()[1:2], scenes=False))

    assert [p.voice_id for p in section.phrases[2:]] == [_F1, _F1]


def test_an_unknown_drill_speaker_is_refused_at_build():
    drills = [{"kind": "true_false", "speaker": "ghost", "steps": [{"cue": "Hun skal hjem.", "answer": "Nei."}]}]

    with pytest.raises(ValueError, match="ghost"):
        build_lesson_from_story(_story(drills), _NO)


def test_the_drills_target_language_text_goes_through_the_story_normaliser():
    """Tagalog's affix-hyphen cleanup (U+2011 in mag‑kape) reaches a drill's model, cues and answers."""
    tl = get_language("tl")
    story = {
        "title": "Kape",
        "key_phrases": [{"phrase": "Magkape tayo.", "translation": "Let's have coffee."}],
        "scenes": [],
        "drills": [
            {
                "kind": "substitution",
                "model": "Mag‑kape tayo.",
                "steps": [{"cue": "mag‑kape", "answer": "Mag‑kape kami."}],
            }
        ],
    }
    lesson = build_lesson_from_story(story, tl)
    section = next(s for s in lesson.sections if s.section_type is SectionType.AFFIX_DRILL)

    assert [p.text for p in section.phrases if p.language_code == "tl"] == ["Magkape tayo.", "magkape", "Magkape kami."]
    assert lesson.generation_metadata["story"]["drills"][0]["model"] == "Magkape tayo."


def test_the_drills_survive_the_stored_form():
    lesson, section = _drill_section(_story(_drills()))

    again = Lesson.from_json(lesson.to_json())
    assert _rows(again.sections[-1]) == _rows(section)


# --- the pauses --------------------------------------------------------------


def test_a_target_language_cue_waits_thinking_time_plus_its_answer():
    """The affix drill's pause to answer, unchanged: 2 s plus the answer's length."""
    pause = NaturalPauseCalculator().get_phrase_pause(
        audio_duration_s=0.4,
        word_count=1,
        section_type=SectionType.AFFIX_DRILL,
        language_code="no",
        role="prompt",
        next_duration_s=1.3,
    )
    assert pause == 3300


# --- the cues ----------------------------------------------------------------


def _cue_refs(lesson: Lesson, section_index: int) -> list[dict]:
    section = lesson.sections[section_index]
    timing = [
        CueTiming(section_index=0, phrase_index=i, start_frame=i * 1000, end_frame=i * 1000 + 500)
        for i in range(len(section.phrases))
    ]
    only = Lesson(title=lesson.title, language_code=lesson.language_code, sections=[section])
    return [cue.ref for cue in build_cue_manifest(only, timing, rate=1000)]


def test_each_step_is_one_item_and_an_instruction_before_a_cue_is_narration():
    """The player's sentence step and repeat latch move by item: a cue and its
    answer are one, and a model is one with the instruction that introduces it."""
    lesson, _ = _drill_section(_story(_drills()))

    assert _cue_refs(lesson, len(lesson.sections) - 1) == [
        {"kind": "narration"},  # Drill
        {"kind": "drill", "target_index": 0},  # Change the sentence.
        {"kind": "drill", "target_index": 0},  # model
        {"kind": "drill", "target_index": 1},  # hun
        {"kind": "drill", "target_index": 1},  # Hun skal hjem.
        {"kind": "drill", "target_index": 2},  # vi
        {"kind": "drill", "target_index": 2},  # Vi skal hjem.
        {"kind": "narration"},  # Answer the question.
        {"kind": "drill", "target_index": 3},  # question
        {"kind": "drill", "target_index": 3},  # answer
        {"kind": "narration"},  # True or false?
        {"kind": "drill", "target_index": 4},  # statement
        {"kind": "drill", "target_index": 4},  # Nei.
    ]


# --- validation ----------------------------------------------------------------


def test_a_story_with_valid_drills_validates():
    validate_story(_story(_drills()), _NO)


@pytest.mark.parametrize(
    ("drills", "message"),
    [
        ("not a list", "drills must be a list"),
        (["not an object"], "drills[0] must be an object"),
        (
            [{"kind": "recall", "steps": [{"cue": "a", "answer": "b"}]}],
            "drills[0].kind must be one of question_answer, substitution, true_false",
        ),
        ([{"steps": [{"cue": "a", "answer": "b"}]}], "drills[0] is missing required field 'kind'"),
        ([{"kind": "true_false", "steps": []}], "drills[0].steps must be a non-empty list"),
        ([{"kind": "true_false"}], "drills[0].steps must be a non-empty list"),
        (
            [{"kind": "true_false", "steps": [{"cue": "a", "answer": "b"}, {"cue": "c"}]}],
            "drills[0].steps[1] is missing required field 'answer'",
        ),
        (
            [{"kind": "true_false", "steps": [{"cue": " ", "answer": "b"}]}],
            "drills[0].steps[0].cue must be a non-empty string",
        ),
        (
            [{"kind": "substitution", "steps": [{"cue": "a", "answer": "b"}]}],
            "drills[0] is missing required field 'model'",
        ),
        (
            [{"kind": "true_false", "speaker": "ghost", "steps": [{"cue": "a", "answer": "b"}]}],
            "speaker 'ghost' is not in the no voice map",
        ),
        (
            [{"kind": "question_answer", "answer_speaker": "ghost", "steps": [{"cue": "a", "answer": "b"}]}],
            "speaker 'ghost' is not in the no voice map",
        ),
        (
            [{"kind": "true_false", "speaker": "", "steps": [{"cue": "a", "answer": "b"}]}],
            "drills[0].speaker must be a non-empty string",
        ),
    ],
)
def test_malformed_drills_are_refused_by_name(drills, message):
    with pytest.raises(ValueError) as caught:
        validate_story(_story(drills), _NO)
    assert message in str(caught.value)


def test_a_drill_speaker_is_read_case_blind_as_a_dialogue_speaker_is():
    """Validation lower-cases a role before checking it, so the build must too:
    a story that validates never fails at build on a role's case."""
    drills = [
        {
            "kind": "question_answer",
            "speaker": "Male-1",
            "answer_speaker": "FEMALE-2",
            "steps": [{"cue": "Hvor skal du?", "answer": "Jeg skal hjem."}],
        }
    ]
    story = _story(drills)
    validate_story(story, _NO)
    _, section = _drill_section(story)

    assert [p.voice_id for p in section.phrases[2:]] == [_M1, _F2]


def test_the_drill_track_is_titled_drill_but_the_affix_drill_still_says_its_name():
    """The track the player shows holds authored drills as well as affix drills,
    so its title is "Drill"; the affix drill's own spoken opening is unchanged
    ("Affix Drill"), so no rendered audio moves."""
    from app.generation.affix_drill import AffixDrill, DrillStep
    from app.generation.section_builder import SECTION_TITLES, build_affix_drill_section

    assert SECTION_TITLES[SectionType.AFFIX_DRILL] == "Drill"
    section = build_affix_drill_section(
        AffixDrill(steps=[DrillStep("model", "gå", "no")]), narrator_voice=_NARRATOR, l2_voice=_F1
    )
    assert section.phrases[0].text == "Affix Drill"
