"""Object focus in the affix drill: a pattern taught as whole sentences (tunatale-ve4p.33).

Stage 6 of the Cebuano curriculum, gi- / -on, cannot be asked for one verb at a
time: "ate" is both ``mikaon`` and ``gikaon``. Only the object's article says
which (He ate bread / He ate THE bread). So the pattern's cells are sentences,
read from the checked rows of ``ceb/focus_sentences.py``, and the drill, the
grammar lesson, the story's closing drill and the lesson page all take them
from there.

A root's four cells are the actor forms it is contrasted with (mo-, mi-),
which the learner has, then the pattern's own (-on, gi-). The first two are
said whole; the last two are built up the way a new form is.

Design: .beads-tasks/briefs/design-ve4p-stage6-object-focus-2026-10.md.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from app.generation.affix_drill import DrillRoot, build_affix_drill, drill_root
from app.generation.grammar_lesson import (
    build_grammar_lesson,
    drill_table,
    pattern_title,
    plan_grammar_lesson,
)
from app.generation.section_builder import build_word_breakdown_spans
from app.generation.story_pattern import add_closing_drill, pattern_block
from app.languages import get_a1_morphology, get_language
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.languages.ceb.affix_patterns import (
    PATTERN_AFFIXES,
    PATTERNS,
    pattern_forms,
    pattern_glosses,
    pattern_lines,
    pattern_roots,
)
from app.srs.contrast_card import contrast_card
from app.storage.store import ContentStore
from tests.test_grammar_lesson import _BURIAL, _WAKE, _story_lesson

_PATTERNS = {p.key: p for p in get_a1_morphology("ceb").patterns}
_ON_GI = _PATTERNS["on-gi"]

_INOM = (
    ("Moinom ko og tubig.", "I'll drink water."),
    ("Miinom ko og tubig.", "I drank water."),
    ("Imnon ko ang tubig.", "I'll drink the water."),
    ("Giinom ko ang tubig.", "I drank the water."),
)
_KAON = (
    ("Mokaon siya og pan.", "He'll eat bread."),
    ("Mikaon siya og pan.", "He ate bread."),
    ("Kan-on niya ang pan.", "He'll eat the bread."),
    ("Gikaon niya ang pan.", "He ate the bread."),
)
_DALA = (
    ("Modala ko og pagkaon.", "I'll bring food."),
    ("Midala ko og pagkaon.", "I brought food."),
    ("Dad-on ko ang pagkaon.", "I'll bring the food."),
    ("Gidala ko ang pagkaon.", "I brought the food."),
)
_TAN_AW = (
    ("Motan-aw siya og balay.", "He'll look at a house."),
    ("Mitan-aw siya og balay.", "He looked at a house."),
    ("Tan-awon niya ang balay.", "He'll look at the house."),
    ("Gitan-aw niya ang balay.", "He looked at the house."),
)
_CELLS = {"inom": _INOM, "kaon": _KAON, "dala": _DALA, "tan-aw": _TAN_AW}
# What the narrator stresses in each cell's English: the doer in actor focus,
# the thing in object focus.
_FOCUS = {
    "inom": ("I'll", "I", "the water", "the water"),
    "kaon": ("He'll", "He", "the bread", "the bread"),
    "dala": ("I'll", "I", "the food", "the food"),
    "tan-aw": ("He'll", "He", "the house", "the house"),
}
_ROOT_OF = {text: root for root, cells in _CELLS.items() for text, _ in cells}
_ENGLISH_OF = {english: (root, i) for root, cells in _CELLS.items() for i, (_, english) in enumerate(cells)}


# ── the pattern ──────────────────────────────────────────────────────────────


def test_object_focus_is_taught_last_and_as_sentences():
    assert (_ON_GI.key, _ON_GI.features, _ON_GI.labels, _ON_GI.sentences, _ON_GI.review_only) == (
        "on-gi",
        ("verb:on", "verb:gi"),
        ("not begun", "begun"),
        True,
        False,
    )
    assert [p.key for p in PATTERNS] == ["mo-mi", "mag-nag", "ma-na", "maka-naka", "on-gi"]
    assert [p.key for p in PATTERNS if p.sentences] == ["on-gi"]


def test_the_note_says_what_ang_does_to_the_verb():
    assert _ON_GI.notes == (
        "When it is the thing you mean, the verb takes -on or gi-, and the thing takes ang.",
        "Gi- is for what has begun. -on is for what has not begun yet.",
    )


def test_the_count_builder_still_counts_only_the_prefix_patterns():
    """gi- and -on are counted by spelling (build_cebuano_focus_counts.py):
    prefix + root would count 'oninom'."""
    assert PATTERN_AFFIXES == ("mo", "mi", "mag", "nag", "ma", "na", "maka", "naka")


def test_the_title_spells_a_suffix_as_a_suffix():
    assert pattern_title(_ON_GI) == "Affix drill: -on / gi-"
    assert pattern_title(_PATTERNS["mo-mi"]) == "Affix drill: mo- / mi-"


def test_the_word_level_tables_say_nothing_of_a_sentence_pattern():
    assert pattern_forms("inom", _ON_GI) is None
    assert pattern_glosses("inom", _ON_GI) is None
    assert pattern_lines("inom", _ON_GI) == ()
    assert pattern_roots(_ON_GI) == ("inom", "kaon", "dala", "tan-aw")


# ── a root ───────────────────────────────────────────────────────────────────


def test_a_root_is_four_sentences_with_one_doer_and_one_thing():
    root = drill_root("ceb", "inom", _ON_GI)
    assert (root.root, root.english, root.cells) == ("inom", "drink", _INOM)
    assert root.forms == ("moinom", "miinom", "imnon", "giinom")
    assert root.contrast == 2  # the first two cells are the actor forms it is contrasted with
    assert root.shown == _INOM[2:]


@pytest.mark.parametrize("root", ["inom", "kaon", "dala", "tan-aw"])
def test_the_doer_alternates_down_the_table_so_both_are_drilled(root):
    assert drill_root("ceb", root, _ON_GI).cells == _CELLS[root]


@pytest.mark.parametrize("root", ["inom", "kaon", "dala", "tan-aw"])
def test_each_cell_knows_its_focus(root):
    found = drill_root("ceb", root, _ON_GI)
    assert (
        tuple(english[slice(*span)] for (_, english), span in zip(found.cells, found.stressed, strict=True))
        == (_FOCUS[root])
    )


@pytest.mark.parametrize(("root", "transparent"), [("inom", False), ("kaon", False), ("dala", False), ("tan-aw", True)])
def test_a_root_whose_on_form_drops_a_vowel_has_to_be_modelled(root, transparent):
    """imnon, kan-on, dad-on: nothing leads from the root to them. tan-awon is regular."""
    assert drill_root("ceb", root, _ON_GI).transparent is transparent


def test_a_root_with_no_checked_row_says_why():
    assert drill_root("ceb", "lakaw", _ON_GI) == "no checked sentences for this root"


def test_a_word_pattern_root_has_its_forms_as_its_cells():
    found = drill_root("ceb", "lakaw", _PATTERNS["mag-nag"])
    assert found == DrillRoot("lakaw", "walk", (("maglakaw", "will be walking"), ("naglakaw", "is walking")))
    assert (found.forms, found.contrast, found.shown, found.stressed) == (
        ("maglakaw", "naglakaw"),
        0,
        found.cells,
        (),
    )


# ── the script ───────────────────────────────────────────────────────────────


def _drill(roots=("inom", "kaon"), new_roots=("tan-aw",), note=_ON_GI.notes):
    return build_affix_drill("ceb", _ON_GI, roots=list(roots), new_roots=list(new_roots), note=note)


def _whole(text: str) -> list[str]:
    chunks = build_word_breakdown_spans(text, "ceb")
    return [chunks[0].text, chunks[-1].text]


def test_the_forms_the_learner_has_are_said_whole_and_the_new_ones_built_up():
    steps = _drill().steps
    assert [(s.kind, s.language_code, s.text) for s in steps[:7]] == [
        ("model", "en", "Listen and repeat."),
        ("model", "en", "drink"),
        ("model", "ceb", "inom"),
        ("model", "en", "I'll drink water."),
        ("model", "ceb", "Moinom ko og tubig."),
        ("model", "en", "I drank water."),
        ("model", "ceb", "Miinom ko og tubig."),
    ]
    assert (steps[7].language_code, steps[7].text) == ("en", "I'll drink the water.")
    built = []
    for step in steps[8:]:
        if step.language_code != "ceb":
            break
        built.append(step.text)
    # Whole, the pieces from the end, whole again: it is new.
    assert built[0] == built[-1] == "Imnon ko ang tubig."
    assert len(built) > 2


def test_the_note_follows_the_first_roots_four_sentences():
    texts = [s.text for s in _drill().steps]
    note = texts.index(_ON_GI.notes[0])
    assert texts[note : note + 2] == list(_ON_GI.notes)
    assert texts[note - 1] == "Giinom ko ang tubig."  # the last piece of the last built-up cell is the whole
    assert texts[note + 2 : note + 4] == ["eat", "kaon"]


def _rounds(drill) -> list[list[str]]:
    rounds: list[list[str]] = []
    for step in drill.steps:
        if step.kind == "prompt" and step.text.startswith("How do you say"):
            rounds.append([])
        if step.kind == "answer":
            rounds[-1].append(step.text)
    return rounds


def test_every_round_asks_every_sentence_of_its_roots_once():
    modelled, new, everything = _rounds(_drill())
    assert sorted(modelled) == sorted(text for root in ("inom", "kaon") for text, _ in _CELLS[root])
    assert sorted(new) == sorted(text for text, _ in _TAN_AW)
    assert sorted(everything) == sorted(modelled + new)


def test_no_two_neighbours_in_a_round_share_a_root():
    for answers in _rounds(_drill())[::2]:  # the single-root round has nobody to stand between
        roots = [_ROOT_OF[text] for text in answers]
        assert all(a != b for a, b in zip(roots, roots[1:], strict=False)), roots


def test_a_sentence_drill_ends_on_its_last_round_with_no_lines():
    steps = _drill().steps
    assert steps[-1].kind == "answer"
    assert [s.text for s in steps].count("Listen and repeat.") == 1


def test_a_sentence_prompt_is_its_english_exactly():
    """A word cell's English is 'will walk' and the prompt adds the full stop; a
    sentence brings its own, and 'I'll drink water..' would be read oddly."""
    prompts = [s.text for s in _drill().steps if s.kind == "prompt"]
    asked = [p for p in prompts if not p.startswith("How do you say")]
    assert asked and all(p in _ENGLISH_OF for p in asked), asked
    assert all(p.endswith('"?') and '."' not in p for p in prompts if p.startswith("How do you say"))


def _focus_of(text: str) -> str | None:
    """The focus words of a step that says a cell's English, bare or asked as a question."""
    bare = text.removeprefix('How do you say "').removesuffix('"?')
    if bare + ("." if text.startswith("How do you say") else "") in _ENGLISH_OF:
        root, cell = _ENGLISH_OF[bare + ("." if text.startswith("How do you say") else "")]
        return _FOCUS[root][cell]
    return None


def test_every_english_sentence_carries_its_focus_and_nothing_else_does():
    """What the narrator stresses (tunatale-ve4p.34) rides on the step that says it."""
    for step in _drill().steps:
        focus = _focus_of(step.text) if step.language_code == "en" else None
        if focus is None:
            assert step.stressed is None, step
        else:
            assert step.text[slice(*step.stressed)] == focus, step


def test_a_word_drill_stresses_nothing():
    drill = build_affix_drill("ceb", _PATTERNS["mo-mi"], roots=["inom", "adto"])
    assert {step.stressed for step in drill.steps} == {None}


# ── the grammar lesson ───────────────────────────────────────────────────────


def _card(db, root: str, state: SRSState) -> None:
    db.add_collocation(
        SyntacticUnit(text=root, translation=root, word_count=1, difficulty=1, source="test"), language_code="ceb"
    )
    guid = db.get_collocation(root).guid
    due = datetime.combine(date.today(), time(4, 0), tzinfo=UTC)
    for direction in (Direction.RECOGNITION, Direction.PRODUCTION):
        db.update_direction(
            guid,
            direction,
            DirectionState(direction=direction, due_at=due, stability=5.0, difficulty=4.0, reps=5, state=state),
        )


@pytest.fixture
def learner(srs_db):
    """The owner on 2026-10-15: inom, kaon, dala understood; tan-aw still new."""
    for root in ("inom", "kaon", "dala"):
        _card(srs_db, root, SRSState.REVIEW)
    _card(srs_db, "tan-aw", SRSState.NEW)
    for word in ("ko", "ang", "kape", "og", "sila", "ug"):
        _card(srs_db, word, SRSState.REVIEW)
    store = ContentStore(":memory:")
    store.save_curriculum(
        "funeral",
        Curriculum(
            id="funeral",
            topic="Attending a funeral",
            language_code="ceb",
            cefr_level="A1",
            days=[
                CurriculumDay(day=1, title="A wake", focus="f", collocations=["haya"], learning_objective="o"),
                CurriculumDay(day=2, title="A burial", focus="f", collocations=["lubong"], learning_objective="o"),
            ],
        ),
    )
    wake = [*_WAKE, ("Giinom ko ang kape.", "I drank the coffee.")]
    store.save_lesson("wake-1", "funeral", 1, _story_lesson("An Evening Wake", wake))
    store.save_lesson("burial-1", "funeral", 2, _story_lesson("The Burial Morning", _BURIAL))
    return srs_db, store


def _plan(learner):
    srs_db, store = learner
    return plan_grammar_lesson("ceb", _ON_GI, srs_db=srs_db, store=store, curriculum_id="funeral", lemmatizer=None)


def test_today_every_root_is_modelled_because_every_on_form_is_irregular(learner):
    plan = _plan(learner)
    assert (plan.roots, plan.new_roots) == (("inom", "kaon", "dala"), ())
    assert plan.forms == (
        "moinom", "miinom", "imnon", "giinom",
        "mokaon", "mikaon", "kan-on", "gikaon",
        "modala", "midala", "dad-on", "gidala",
    )  # fmt: skip


def test_tan_aw_is_asked_cold_once_its_card_is_in_review(learner):
    srs_db, _ = learner
    _card(srs_db, "tan-aw", SRSState.REVIEW)
    assert _plan(learner).new_roots == ("tan-aw",)


def test_the_lesson_ends_on_a_story_line_that_says_an_object_focus_form(learner):
    """'Miinom sila og kape ug nag-istorya sila.' says an actor form the drill also
    asks for, but the stage is about gi- and -on; it also has a word the learner lacks."""
    assert _plan(learner).lines == (("Giinom ko ang kape.", "I drank the coffee."),)


def test_a_sentence_pattern_has_no_contrast_card_its_cells_are_focus_cards():
    """Blanking one cell of a sentence is the focus card's job (``test_focus_card.py``)."""
    assert contrast_card("ceb", _ON_GI, "inom", 1) is None


def test_the_page_shows_each_roots_two_object_focus_sentences(learner):
    lesson = build_grammar_lesson(get_language("ceb"), _plan(learner))
    table = drill_table(lesson)
    assert (table["pattern"], table["title"], table["labels"]) == (
        "on-gi",
        "Affix drill: -on / gi-",
        ["not begun", "begun"],
    )
    assert table["roots"][0] == {
        "root": "inom",
        "english": "drink",
        "new": False,
        "forms": [
            {"form": "Imnon ko ang tubig.", "english": "I'll drink the water."},
            {"form": "Giinom ko ang tubig.", "english": "I drank the water."},
        ],
    }


# ── a story lesson ───────────────────────────────────────────────────────────


def test_the_story_is_asked_for_the_object_focus_sentences():
    assert pattern_block("ceb", _ON_GI) == (
        "**Verb Pattern for This Lesson: -on / gi-**\n"
        "Use each of these verbs in BOTH forms somewhere in the dialogue, each in a line where it is natural:\n"
        "- inom: Imnon ko ang tubig. (I'll drink the water.), Giinom ko ang tubig. (I drank the water.)\n"
        "- kaon: Kan-on niya ang pan. (He'll eat the bread.), Gikaon niya ang pan. (He ate the bread.)\n"
        "- dala: Dad-on ko ang pagkaon. (I'll bring the food.), Gidala ko ang pagkaon. (I brought the food.)\n"
        "- tan-aw: Tan-awon niya ang balay. (He'll look at the house.), Gitan-aw niya ang balay. "
        "(He looked at the house.)\n"
        "\n"
    )


def test_a_closing_drill_of_sentences_does_not_wait_for_the_story_to_use_them(learner):
    """A story seldom says gi- or -on unasked, and the sentences need no story: the
    owner chose closing drills over grammar lessons (2026-10-13), so a focus day
    whose story missed the pattern would otherwise never drill it."""
    srs_db, _ = learner
    lesson = _story_lesson("An Evening Wake", _WAKE)
    add_closing_drill(lesson, get_language("ceb"), _ON_GI, srs_db=srs_db, lemmatizer=None)
    # inom first: the story said miinom. Then the table's order.
    assert lesson.generation_metadata["affix_drill"] == {
        "pattern": "on-gi",
        "roots": ["inom", "kaon", "dala"],
        "new_roots": [],
        "note": False,
    }


def test_with_no_deck_a_closing_drill_of_sentences_has_only_what_the_story_said():
    lesson = _story_lesson("An Evening Wake", _WAKE)
    add_closing_drill(lesson, get_language("ceb"), _ON_GI, srs_db=None, lemmatizer=None)
    assert "affix_drill" not in lesson.generation_metadata  # inom alone is a word, not a pattern
