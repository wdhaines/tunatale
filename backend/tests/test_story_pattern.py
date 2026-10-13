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

import asyncio
import hashlib

import pytest

from app.generation import publishing
from app.generation.grammar_lesson import contrast_cards
from app.generation.publishing import CurriculumDayTarget, publish_lesson
from app.generation.story import PROMPT_TOKEN_BUDGET, build_story_prompts
from app.generation.story_pattern import add_closing_drill, add_day_drill, named_pattern, pattern_block, pattern_for_day
from app.languages import get_a1_morphology, get_language
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import KeyPhraseInfo, SectionType
from app.models.srs_item import SRSState
from app.models.strategy import ContentStrategy
from app.storage.store import ContentStore
from tests._helpers.lemmatizer import StubLemmatizer
from tests.test_grammar_lesson import _BURIAL, _WAKE, _card, _story_lesson, owner  # noqa: F401

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

    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"], srs_db=None, lemmatizer=None)

    assert len(lesson.sections) == sections_before + 1
    assert lesson.sections[-1].section_type is SectionType.AFFIX_DRILL
    # The first three the story says, in its order: moadto, mianhi, mouban. (miinom is the fourth.)
    assert lesson.generation_metadata["affix_drill"] == {
        "pattern": "mo-mi",
        "roots": ["adto", "anhi", "uban"],
        "new_roots": [],
    }
    assert lesson.kind == "thematic"


def _answers(lesson) -> list[str]:
    return [p.text for p in lesson.sections[-1].phrases if p.role == "answer"]


def _closed(lines, srs_db, *, key_phrases=()):
    """The wake, with its closing mo-/mi- drill chosen against *srs_db*."""
    lesson = _story_lesson("An Evening Wake", lines)
    lesson.key_phrases = [KeyPhraseInfo(phrase=phrase, translation="") for phrase in key_phrases]
    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"], srs_db=srs_db, lemmatizer=None)
    return lesson


def test_a_closing_drill_is_short(srs_db):
    for word in ("ko", "ugma"):
        _card(srs_db, word, recognition=SRSState.REVIEW)
    lesson = _closed(_WAKE, srs_db)
    # Three roots, each named once, each of its two forms built up once
    # (tunatale-ve4p.16) and answered once; then the one whole line of the
    # story that says a drilled form in a single sentence, taught and then
    # asked. No second, mixed round: every answer is asked exactly once.
    answers = _answers(lesson)
    assert len(answers) == 3 * 2 + 1
    assert len(set(answers)) == len(answers)
    assert answers[-1] == "Mouban ko ugma."


# --- which whole lines it may end on (tunatale-ve4p.20) -------------------------
#
# The owner, 2026-10-09: "The full sentences at the end are too hard right now
# because we're introducing things I don't know." A line is asked for only when
# the learner has every word of it, or the lesson teaches it. The rule itself is
# pinned in test_drill_lines.py; these pin what a closing drill hands it.


def test_a_line_with_a_word_the_learner_has_not_met_is_not_asked_for(srs_db):
    """`ugma` has no card: the drill keeps its forms and ends without the line."""
    _card(srs_db, "ko", recognition=SRSState.REVIEW)

    answers = _answers(_closed(_WAKE, srs_db))

    assert len(answers) == 3 * 2
    assert "Mouban ko ugma." not in answers


def test_the_drilled_form_needs_no_card_of_its_own(srs_db):
    """`mouban` is what the drill teaches. Nothing in the deck is spelled that way
    (the card is `uban`, and the suite's lemmatizer does not strip an affix), and
    the line is still asked for once its OTHER words are understood."""
    for word in ("ko", "ugma"):
        _card(srs_db, word, recognition=SRSState.REVIEW)
    assert srs_db.get_collocation("mouban") is None

    assert _answers(_closed(_WAKE, srs_db))[-1] == "Mouban ko ugma."


def test_a_word_of_this_lessons_key_phrases_counts_as_taught(srs_db):
    """The lesson's new words are not in the deck yet; its key phrases are what it teaches."""
    _card(srs_db, "ko", recognition=SRSState.REVIEW)

    assert "Mouban ko ugma." not in _answers(_closed(_WAKE, srs_db))
    assert _answers(_closed(_WAKE, srs_db, key_phrases=["Ugma na lang"]))[-1] == "Mouban ko ugma."


def test_with_no_deck_to_ask_the_drill_ends_on_its_forms(srs_db):
    for word in ("ko", "ugma"):
        _card(srs_db, word, recognition=SRSState.REVIEW)

    assert len(_answers(_closed(_WAKE, None))) == 3 * 2


def test_the_lemmatizer_handed_in_is_the_one_that_reads_the_line(srs_db):
    """`ko` has no card; `ako` does. Only a lemmatizer that knows `ko` is `ako` admits the line."""
    for word in ("ako", "ugma"):
        _card(srs_db, word, recognition=SRSState.REVIEW)
    stub = StubLemmatizer()
    stub.set_lemma("ko", "ako")

    def answers(lemmatizer):
        lesson = _story_lesson("An Evening Wake", _WAKE)
        add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"], srs_db=srs_db, lemmatizer=lemmatizer)
        return _answers(lesson)

    assert "Mouban ko ugma." not in answers(None)
    assert answers(stub)[-1] == "Mouban ko ugma."


def test_a_curriculum_day_target_hands_its_drill_the_deck_and_the_lemmatizer(srs_db):
    """The publish seam's half: ``prepare`` is where a published lesson gets its drill."""
    for word in ("ako", "ugma"):
        _card(srs_db, word, recognition=SRSState.REVIEW)
    stub = StubLemmatizer()
    stub.set_lemma("ko", "ako")
    store = ContentStore(":memory:")
    store.save_curriculum("funeral", _curriculum([_day(1, pattern="mo-mi")]))
    target = CurriculumDayTarget(store, "ceb", "funeral", 1, None, user_id=None)

    def answers(**prepare):
        lesson = _story_lesson("An Evening Wake", _WAKE)
        target.prepare(lesson, **prepare)
        return _answers(lesson)

    assert answers(srs_db=srs_db, lemmatizer=stub)[-1] == "Mouban ko ugma."
    assert "Mouban ko ugma." not in answers(srs_db=srs_db, lemmatizer=None)
    assert "Mouban ko ugma." not in answers(srs_db=None, lemmatizer=stub)


async def test_publishing_hands_the_target_the_publishs_own_deck_and_lemmatizer(srs_db):
    """``publish_lesson`` already holds both for the tagging steps; ``prepare`` runs
    before them and used to be handed neither."""
    seen: dict[str, object] = {}

    class _Target:
        def prepare(self, lesson, *, srs_db, lemmatizer) -> None:
            seen.update(srs_db=srs_db, lemmatizer=lemmatizer)

        def write(self, lesson) -> str:
            return "written"

        def invalidate_audio(self, content_id) -> None:
            raise AssertionError("nothing is being replaced")

        async def schedule_render(self, content_id, lesson) -> None:
            return None

    stub = StubLemmatizer()
    lesson = _story_lesson("An Evening Wake", _WAKE)

    await publish_lesson(
        lesson,
        target=_Target(),
        srs_db=srs_db,
        lemmatizer_kwargs={"lemmatizer": stub, "model_version": "stub-1"},
        replace=False,
        llm=None,
    )
    await asyncio.gather(*publishing._background_tasks)

    assert seen == {"srs_db": srs_db, "lemmatizer": stub}

    seen.clear()
    await publish_lesson(lesson, target=_Target(), srs_db=None, lemmatizer_kwargs={}, replace=False, llm=None)
    assert seen == {"srs_db": None, "lemmatizer": None}


def test_the_day_drill_asks_the_same_deck(srs_db):
    """``add_day_drill`` is the call every writer makes; it must not drop the deck on the way."""
    for word in ("ko", "ugma"):
        _card(srs_db, word, recognition=SRSState.REVIEW)
    curriculum = _curriculum([_day(1, pattern="mo-mi")])
    lesson = _story_lesson("An Evening Wake", _WAKE)

    add_day_drill(lesson, get_language("ceb"), curriculum, 1, srs_db=srs_db, lemmatizer=None)

    assert _answers(lesson)[-1] == "Mouban ko ugma."


def test_a_root_the_story_used_in_both_forms_leads():
    lines = [*_WAKE, ("Miuban siya.", "She came along.")]
    lesson = _story_lesson("An Evening Wake", lines)
    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"], srs_db=None, lemmatizer=None)
    assert lesson.generation_metadata["affix_drill"]["roots"] == ["uban", "adto", "anhi"]


def test_at_most_three_roots_are_drilled():
    # The wake says four roots of the pattern; a closing drill is not a grammar lesson.
    lesson = _story_lesson("An Evening Wake", _WAKE)
    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"], srs_db=None, lemmatizer=None)
    assert "inom" not in lesson.generation_metadata["affix_drill"]["roots"]
    assert len(lesson.generation_metadata["affix_drill"]["roots"]) == 3


def test_a_story_that_used_one_root_of_the_pattern_gets_no_drill():
    # The burial says naglakaw and nothing else in mag-/nag-: one root is a word, not a pattern.
    lesson = _story_lesson("The Burial Morning", _BURIAL)
    sections_before = len(lesson.sections)

    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mag-nag"], srs_db=None, lemmatizer=None)

    assert len(lesson.sections) == sections_before
    assert "affix_drill" not in lesson.generation_metadata


def test_adding_it_twice_adds_it_once():
    lesson = _story_lesson("An Evening Wake", _WAKE)
    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"], srs_db=None, lemmatizer=None)
    sections = len(lesson.sections)
    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"], srs_db=None, lemmatizer=None)
    assert len(lesson.sections) == sections


def test_the_story_lesson_then_adds_contrast_cards_like_a_grammar_lesson(owner):  # noqa: F811
    """The listen path reads ``affix_drill`` and nothing else, so a story lesson needs no code of its own."""
    srs_db, store = owner
    lesson = _story_lesson("An Evening Wake", _WAKE)
    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"], srs_db=None, lemmatizer=None)

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
    add_day_drill(lesson, get_language("ceb"), curriculum, 1, srs_db=None, lemmatizer=None)
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
    add_day_drill(lesson, get_language("ceb"), curriculum, day, srs_db=None, lemmatizer=None)
    assert len(lesson.sections) == before
    assert "affix_drill" not in lesson.generation_metadata


def test_a_grammar_day_names_no_story_pattern_even_with_no_curriculum():
    assert named_pattern("ceb", _day(1, kind="grammar", pattern="mo-mi")) is None
    assert named_pattern("ceb", _day(1, pattern="mo-mi")).key == "mo-mi"
