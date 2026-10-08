"""A story lesson's planned affix pattern, and the closing drill built from it (tunatale-ve4p.7).

The owner, 2026-10-07: "Lessons should have an affix pattern." Three pieces,
none of which calls a model:

* **which pattern** a thematic day gets: its own, if it names one; otherwise
  the language's patterns in rotation over the curriculum's thematic days;
* **what the story is asked for**: a short block in the story prompt naming
  roots to use in BOTH forms. The acceptance target is three roots in both
  cells, which no unplanned lesson meets (``report_affix_exposure.py``);
* **the closing drill**: after the story exists, an affix drill on the roots
  the story actually used, appended as the lesson's last section.

Cebuano throughout: an affix pattern is a facet only its plugin registers.
Slovene and Norwegian appear as the languages whose prompts must not move by
a byte, because every story cassette is keyed on sha256(system + user).
"""

from __future__ import annotations

import hashlib

import pytest

from app.generation.grammar_lesson import contrast_cards
from app.generation.story import PROMPT_TOKEN_BUDGET, build_story_prompts
from app.generation.story_pattern import add_closing_drill, add_day_drill, named_pattern, pattern_block, pattern_for_day
from app.languages import get_a1_morphology, get_language
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import SectionType
from app.models.strategy import ContentStrategy
from tests.test_grammar_lesson import _BURIAL, _WAKE, _story_lesson, owner  # noqa: F401

_PATTERNS = {p.key: p for p in get_a1_morphology("ceb").patterns}


def _day(n: int, *, kind: str = "thematic", pattern: str = "") -> CurriculumDay:
    return CurriculumDay(
        day=n,
        title="A wake",
        focus="Attending a wake",
        collocations=["haya", "kape"],
        learning_objective="Greet the family",
        story_guidance="Paul visits Liza",
        kind=kind,
        pattern=pattern,
    )


def _curriculum(days: list[CurriculumDay], language_code: str = "ceb") -> Curriculum:
    return Curriculum(id="funeral", topic="A funeral", language_code=language_code, cefr_level="A1", days=days)


def _key(curriculum: Curriculum, day: CurriculumDay) -> str | None:
    pattern = pattern_for_day(curriculum, day)
    return pattern.key if pattern is not None else None


# --- which pattern -----------------------------------------------------------


def test_thematic_days_take_the_languages_patterns_in_turn():
    days = [_day(n) for n in (1, 2, 3, 4)]
    curriculum = _curriculum(days)
    assert [_key(curriculum, day) for day in days] == ["mo-mi", "mag-nag", "ma-na", "mo-mi"]


def test_a_grammar_day_is_not_a_turn_and_is_not_planned_here():
    # Day 2 is a drill lesson with its own pattern; the story after it carries on the rotation.
    days = [_day(1), _day(2, kind="grammar", pattern="mo-mi"), _day(3)]
    curriculum = _curriculum(days)
    assert [_key(curriculum, day) for day in days] == ["mo-mi", None, "mag-nag"]


def test_a_day_that_names_its_pattern_keeps_it():
    days = [_day(1, pattern="ma-na"), _day(2)]
    curriculum = _curriculum(days)
    # ...and still counts as a turn for the days after it.
    assert [_key(curriculum, day) for day in days] == ["ma-na", "mag-nag"]


def test_a_name_the_language_does_not_have_falls_back_to_the_rotation():
    days = [_day(1), _day(2, pattern="um-in")]
    assert _key(_curriculum(days), days[1]) == "mag-nag"


def test_a_language_with_no_patterns_plans_none():
    days = [_day(1), _day(2)]
    assert [_key(_curriculum(days, "no"), day) for day in days] == [None, None]


# --- what the story is asked for ---------------------------------------------


def test_the_block_names_four_roots_in_both_forms_with_their_english():
    assert pattern_block("ceb", _PATTERNS["mo-mi"]) == (
        "**Verb Pattern for This Lesson: mo- / mi-**\n"
        "Use each of these verbs in BOTH forms somewhere in the dialogue, each in a line where it is natural:\n"
        "- lakaw: molakaw (will walk), milakaw (walked)\n"
        "- inom: moinom (will drink), miinom (drank)\n"
        "- adto: moadto (will go), miadto (went)\n"
        "- anhi: moanhi (will come), mianhi (came)\n"
        "\n"
    )


def test_roots_the_learner_understands_are_asked_for_first(owner):  # noqa: F811
    srs_db, _ = owner
    block = pattern_block("ceb", _PATTERNS["mag-nag"], srs_db=srs_db)
    # The table's order is lakaw ampo dala kita ...; this learner understands
    # lakaw and uban, and a card can only be made for an understood root.
    assert [line.split(":")[0] for line in block.splitlines()[2:6]] == ["- lakaw", "- uban", "- ampo", "- dala"]


def test_a_pattern_with_three_worded_roots_asks_for_all_three():
    block = pattern_block("ceb", _PATTERNS["ma-na"])
    assert [line.split(":")[0] for line in block.splitlines()[2:]] == ["- tulog", "- lipay", "- hurot", ""]


# --- the prompt ---------------------------------------------------------------


def _fingerprint(code: str, strategy: str, **kwargs) -> str:
    prompts = build_story_prompts(_day(1), get_language(code), ContentStrategy[strategy], "A1", **kwargs)
    return hashlib.sha256((prompts.system_prompt + "\x1f" + prompts.user_prompt).encode()).hexdigest()[:16]


@pytest.mark.parametrize(
    ("code", "strategy", "recorded"),
    [
        ("sl", "WIDER", "1a4db6536708be24"),
        ("no", "DEEPER", "0d965d4571180cd8"),
        ("ceb", "WIDER", "dd5bde475e52c2cc"),
        ("ceb", "DEEPER", "982ae2e86704c1db"),
        ("tl", "WIDER", "9238bf15b1a03090"),
    ],
)
def test_a_prompt_with_no_pattern_is_byte_identical_to_before(code, strategy, recorded):
    """Measured on main at 4e1d381, before the block existed. Every cassette is keyed on these bytes."""
    assert _fingerprint(code, strategy) == recorded


def test_a_language_with_no_patterns_is_unmoved_even_inside_a_curriculum(owner):  # noqa: F811
    srs_db, store = owner
    store.save_curriculum("no-plan", _curriculum([_day(1)], "no"))
    assert _fingerprint("no", "DEEPER", content_store=store, curriculum_id="no-plan") == "0d965d4571180cd8"


@pytest.mark.parametrize("strategy", ["WIDER", "DEEPER"])
def test_a_story_in_a_curriculum_is_asked_for_its_days_pattern(owner, strategy):  # noqa: F811
    srs_db, store = owner
    language = get_language("ceb")
    day = store.get_curriculum("funeral").days[1]  # the second thematic day: mag-nag

    plain = build_story_prompts(day, language, ContentStrategy[strategy], "A1")
    planned = build_story_prompts(
        day, language, ContentStrategy[strategy], "A1", srs_db=None, content_store=store, curriculum_id="funeral"
    )

    block = pattern_block("ceb", _PATTERNS["mag-nag"])
    rules = f"**{strategy} STRATEGY RULES**"
    assert planned.system_prompt == plain.system_prompt
    assert f"{block}{rules}" in planned.user_prompt
    # Nothing else moved: take the block out and it is the plain prompt again.
    # (DEEPER also gains day 1's transcript as its source, which is not this test's subject.)
    if strategy == "WIDER":
        assert planned.user_prompt.replace(block, "") == plain.user_prompt
    assert (len(planned.system_prompt) + len(planned.user_prompt)) // 4 <= PROMPT_TOKEN_BUDGET


def test_a_day_that_names_its_pattern_is_asked_for_it_with_no_curriculum_to_read():
    prompts = build_story_prompts(_day(1, pattern="ma-na"), get_language("ceb"), ContentStrategy.WIDER, "A1")
    assert pattern_block("ceb", _PATTERNS["ma-na"]) in prompts.user_prompt


# --- the closing drill ----------------------------------------------------------


def test_the_drill_is_built_from_the_roots_the_story_used():
    lesson = _story_lesson("An Evening Wake", _WAKE)
    sections_before = len(lesson.sections)

    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"])

    assert len(lesson.sections) == sections_before + 1
    assert lesson.sections[-1].section_type is SectionType.AFFIX_DRILL
    # The first three the story says, in its order: moadto, mianhi, mouban. (miinom is the fourth.)
    assert lesson.generation_metadata["affix_drill"] == {
        "pattern": "mo-mi",
        "roots": ["adto", "anhi", "uban"],
        "new_roots": [],
    }
    assert lesson.kind == "thematic"


def test_a_closing_drill_is_short():
    lesson = _story_lesson("An Evening Wake", _WAKE)
    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"])
    spoken = [p for p in lesson.sections[-1].phrases if p.language_code == "ceb"]
    # Three roots: each named once, each of its two forms modelled once and
    # answered once; then the one whole line of the story that says a drilled
    # form in a single sentence. No second, mixed round.
    assert len(spoken) == 3 * (1 + 2 + 2) + 1


def test_a_root_the_story_used_in_both_forms_leads():
    lines = [*_WAKE, ("Miuban siya.", "She came along.")]
    lesson = _story_lesson("An Evening Wake", lines)
    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"])
    assert lesson.generation_metadata["affix_drill"]["roots"] == ["uban", "adto", "anhi"]


def test_at_most_three_roots_are_drilled():
    # The wake says four roots of the pattern; a closing drill is not a grammar lesson.
    lesson = _story_lesson("An Evening Wake", _WAKE)
    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"])
    assert "inom" not in lesson.generation_metadata["affix_drill"]["roots"]
    assert len(lesson.generation_metadata["affix_drill"]["roots"]) == 3


def test_a_story_that_used_one_root_of_the_pattern_gets_no_drill():
    # The burial says naglakaw and nothing else in mag-/nag-: one root is a word, not a pattern.
    lesson = _story_lesson("The Burial Morning", _BURIAL)
    sections_before = len(lesson.sections)

    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mag-nag"])

    assert len(lesson.sections) == sections_before
    assert "affix_drill" not in lesson.generation_metadata


def test_adding_it_twice_adds_it_once():
    lesson = _story_lesson("An Evening Wake", _WAKE)
    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"])
    sections = len(lesson.sections)
    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"])
    assert len(lesson.sections) == sections


def test_the_story_lesson_then_adds_contrast_cards_like_a_grammar_lesson(owner):  # noqa: F811
    """The listen path reads ``affix_drill`` and nothing else, so a story lesson needs no code of its own."""
    srs_db, store = owner
    lesson = _story_lesson("An Evening Wake", _WAKE)
    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"])

    cards = contrast_cards(lesson, srs_db=srs_db, store=store, curriculum_id="funeral")

    assert [(card.root, card.form) for card in cards] == [
        ("adto", "miadto"),
        ("anhi", "moanhi"),
        ("uban", "miuban"),
    ]


# --- the one call a writer makes ------------------------------------------------


def test_a_lesson_published_into_its_day_gets_that_days_drill():
    curriculum = _curriculum([_day(1), _day(2)])
    lesson = _story_lesson("An Evening Wake", _WAKE)
    add_day_drill(lesson, get_language("ceb"), curriculum, 1)
    assert lesson.generation_metadata["affix_drill"]["pattern"] == "mo-mi"


@pytest.mark.parametrize(
    ("curriculum", "day"),
    [
        (None, 1),  # no curriculum to read a pattern from
        (_curriculum([_day(1)]), 7),  # a day the curriculum does not hold
        (_curriculum([_day(1, kind="grammar", pattern="mo-mi")]), 1),  # a grammar day
        (_curriculum([_day(1)], "no"), 1),  # a language with no patterns
    ],
)
def test_a_lesson_with_no_planned_pattern_is_left_as_it_was(curriculum, day):
    lesson = _story_lesson("An Evening Wake", _WAKE)
    before = len(lesson.sections)
    add_day_drill(lesson, get_language("ceb"), curriculum, day)
    assert len(lesson.sections) == before
    assert "affix_drill" not in lesson.generation_metadata


def test_a_grammar_day_names_no_story_pattern_even_with_no_curriculum():
    assert named_pattern("ceb", _day(1, kind="grammar", pattern="mo-mi")) is None
    assert named_pattern("ceb", _day(1, pattern="mo-mi")).key == "mo-mi"
