"""Object focus drilled in two halves, one per story lesson (the owner, 2026-10-15).

The whole gi- / -on drill, four sentences a root, came to 10:47 and about 9
minutes after cut A: "too long for the end of a lesson". It is split by
tense, and each half still turns on the new choice, which noun is the ``ang``
noun, with the tense held still:

* half 1, what has begun: Miinom ko og tubig. / Giinom ko ang tubig.
  "I drank water" against "I drank the water", where only "the" differs;
* half 2, what has not begun: Moinom ko og tubig. / Imnon ko ang tubig.,
  which carries the -on forms no rule spells.

On-gi days take the halves in turn, and a lesson made again keeps its half.

Cebuano throughout: an affix pattern is a facet only its plugin registers.
"""

from __future__ import annotations

from app.generation.affix_drill import build_affix_drill
from app.generation.grammar_lesson import drill_table
from app.generation.story_pattern import add_day_drill
from app.languages import get_a1_morphology, get_language
from app.storage.store import ContentStore
from tests.test_focus_drill import learner  # noqa: F401
from tests.test_grammar_lesson import _WAKE, _story_lesson
from tests.test_story_pattern import _curriculum, _day

_ON_GI = next(p for p in get_a1_morphology("ceb").patterns if p.key == "on-gi")
_BEGUN, _NOT_BEGUN = _ON_GI.halves
_ROOTS = ["inom", "kaon", "dala"]
_GIINOM = ("Giinom ko ang kape.", "I drank the coffee.")


def _ceb(drill) -> list[str]:
    return [s.text for s in drill.steps if s.language_code == "ceb"]


# --- the halves, as the language words them -------------------------------------


def test_the_halves_are_begun_then_not_begun_by_cell():
    """Cells are mo-, mi-, -on, gi-: begun is mi- and gi-, not begun is mo- and -on."""
    assert _ON_GI.halves == ((1, 3), (0, 2))


def test_each_half_has_its_own_note():
    assert _ON_GI.half_notes == (
        ("When it is the thing you mean, the verb takes gi-, and the thing takes ang.",),
        ("For what has not begun yet, the verb takes -on, and the thing still takes ang.",),
    )


def test_a_word_pattern_is_not_split():
    assert [p.key for p in get_a1_morphology("ceb").patterns if p.halves] == ["on-gi"]


# --- one half's drill ------------------------------------------------------------


def test_the_begun_half_says_only_mi_and_gi_sentences():
    drill = build_affix_drill("ceb", _ON_GI, roots=_ROOTS, cells=_BEGUN)
    said = _ceb(drill)
    assert "Miinom ko og tubig." in said
    assert "Giinom ko ang tubig." in said
    assert not [line for line in said if line.split()[0].casefold().startswith(("mo", "imnon", "kan-on", "dad-on"))]
    assert [(s.language_code, s.text) for s in drill.steps[:9]] == [
        ("en", "Listen and repeat."),
        ("en", "drink"),
        ("ceb", "inom"),
        ("en", "I drank water."),
        ("ceb", "Miinom ko og tubig."),
        ("en", "I drank the water."),
        ("ceb", "Giinom ko ang tubig."),
        ("ceb", "gi"),
        ("ceb", "giinom"),
    ]


def test_the_not_begun_half_says_only_mo_and_on_sentences():
    drill = build_affix_drill("ceb", _ON_GI, roots=_ROOTS, cells=_NOT_BEGUN)
    said = _ceb(drill)
    assert "Imnon ko ang tubig." in said
    assert "Kan-on niya ang pan." in said
    assert not [line for line in said if line.split()[0].casefold().startswith(("mi", "gi"))]


def test_each_round_asks_each_root_two_sentences():
    drill = build_affix_drill("ceb", _ON_GI, roots=_ROOTS, cells=_BEGUN)
    answers = [s.text for s in drill.steps if s.kind == "answer"]
    assert len(answers) == 12  # two rounds of 3 roots x 2 sentences
    assert sorted(answers[:6]) == sorted(
        [
            "Miinom ko og tubig.",
            "Giinom ko ang tubig.",
            "Mikaon siya og pan.",
            "Gikaon niya ang pan.",
            "Midala ko og pagkaon.",
            "Gidala ko ang pagkaon.",
        ]
    )


def test_a_half_is_about_half_the_whole():
    whole = build_affix_drill("ceb", _ON_GI, roots=_ROOTS)
    half = build_affix_drill("ceb", _ON_GI, roots=_ROOTS, cells=_BEGUN)
    assert len(_ceb(half)) <= 0.6 * len(_ceb(whole))


# --- which half a story lesson gets ------------------------------------------------


def _drilled(curriculum, day, store, srs_db, lines=_WAKE):
    lesson = _story_lesson(f"Day {day}", lines)
    add_day_drill(lesson, get_language("ceb"), curriculum, day, srs_db=srs_db, lemmatizer=None, store=store, owner=True)
    return lesson


def _stored(store, curriculum, day, lesson) -> None:
    store.save_curriculum(curriculum.id, curriculum)
    store.save_lesson(f"lesson-{day}", curriculum.id, day, lesson)


def _notes(lesson) -> list[str]:
    every = {note for half in _ON_GI.half_notes for note in half} | set(_ON_GI.notes)
    return [p.text for p in lesson.sections[-1].phrases if p.text in every]


def test_on_gi_days_take_the_halves_in_turn_each_told_once(learner):  # noqa: F811
    srs_db, _ = learner
    store = ContentStore(":memory:")
    curriculum = _curriculum([_day(n, pattern="on-gi") for n in (1, 2, 3)])

    first = _drilled(curriculum, 1, store, srs_db)
    assert first.generation_metadata["affix_drill"]["half"] == 0
    assert _notes(first) == list(_ON_GI.half_notes[0])
    _stored(store, curriculum, 1, first)

    second = _drilled(curriculum, 2, store, srs_db)
    assert second.generation_metadata["affix_drill"]["half"] == 1
    assert _notes(second) == list(_ON_GI.half_notes[1])
    _stored(store, curriculum, 2, second)

    third = _drilled(curriculum, 3, store, srs_db)
    assert third.generation_metadata["affix_drill"]["half"] == 0
    assert _notes(third) == []


def test_a_lesson_made_again_keeps_its_half(learner):  # noqa: F811
    srs_db, _ = learner
    store = ContentStore(":memory:")
    curriculum = _curriculum([_day(1, pattern="on-gi")])
    held = _drilled(curriculum, 1, store, srs_db)
    held.generation_metadata["affix_drill"]["half"] = 1
    _stored(store, curriculum, 1, held)

    assert _drilled(curriculum, 1, store, srs_db).generation_metadata["affix_drill"]["half"] == 1


def test_with_no_store_the_first_half_is_drilled(learner):  # noqa: F811
    srs_db, _ = learner
    lesson = _drilled(_curriculum([_day(1, pattern="on-gi")]), 1, None, srs_db)
    assert lesson.generation_metadata["affix_drill"]["half"] == 0


def test_a_story_line_ends_the_drill_only_in_the_half_that_says_its_form(learner):  # noqa: F811
    srs_db, _ = learner
    store = ContentStore(":memory:")
    curriculum = _curriculum([_day(n, pattern="on-gi") for n in (1, 2)])
    lines = [*_WAKE, _GIINOM]

    first = _drilled(curriculum, 1, store, srs_db, lines)
    assert [s.text for s in first.sections[-1].phrases if s.role == "answer"][-1] == _GIINOM[0]
    _stored(store, curriculum, 1, first)

    second = _drilled(curriculum, 2, store, srs_db, lines)
    assert _GIINOM[0] not in [s.text for s in second.sections[-1].phrases]


def test_a_word_pattern_day_records_no_half(learner):  # noqa: F811
    srs_db, _ = learner
    lesson = _drilled(_curriculum([_day(1, pattern="mo-mi")]), 1, None, None)
    assert "half" not in lesson.generation_metadata["affix_drill"]


# --- the lesson page ---------------------------------------------------------------


def test_the_page_shows_only_the_halfs_object_focus_sentence(learner):  # noqa: F811
    srs_db, _ = learner
    lesson = _drilled(_curriculum([_day(1, pattern="on-gi")]), 1, None, srs_db)
    table = drill_table(lesson)
    assert table["labels"] == ["begun"]
    assert table["roots"][0]["forms"] == [{"form": "Giinom ko ang tubig.", "english": "I drank the water."}]
