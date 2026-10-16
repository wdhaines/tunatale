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
from datetime import UTC, datetime

import pytest

from app.generation import publishing
from app.generation.grammar_lesson import contrast_cards
from app.generation.publishing import CurriculumDayTarget, publish_lesson
from app.generation.story import PROMPT_TOKEN_BUDGET, build_story_prompts
from app.generation.story_pattern import add_closing_drill, add_day_drill, named_pattern, pattern_block, pattern_for_day
from app.languages import get_a1_morphology, get_language
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import KeyPhraseInfo, SectionType
from app.models.srs_item import Direction, RevlogRow, SRSState
from app.models.strategy import ContentStrategy
from app.srs.contrast_card import contrast_card
from app.storage.store import ContentStore
from tests._helpers.lemmatizer import StubLemmatizer
from tests.test_grammar_lesson import _BURIAL, _WAKE, _bury, _card, _story_lesson, owner  # noqa: F401

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


def _key(curriculum: Curriculum, day: CurriculumDay, **learner) -> str | None:
    pattern = pattern_for_day(curriculum, day, **learner)
    return pattern.key if pattern is not None else None


# --- which pattern -----------------------------------------------------------
#
# The rotation is gone (tunatale-ve4p.26): which pattern a day gets is the
# gate's call (``app.generation.pattern_gate``), tested there. What is tested
# here is what the day itself contributes.


def test_with_no_deck_to_read_every_thematic_day_is_asked_for_the_first_pattern():
    days = [_day(n) for n in (1, 2, 3, 4)]
    curriculum = _curriculum(days)
    assert [_key(curriculum, day) for day in days] == ["mo-mi"] * 4


def test_a_grammar_day_is_not_planned_here():
    days = [_day(1), _day(2, kind="grammar", pattern="mo-mi"), _day(3)]
    curriculum = _curriculum(days)
    assert [_key(curriculum, day) for day in days] == ["mo-mi", None, "mo-mi"]


def test_a_day_that_names_its_pattern_keeps_it():
    days = [_day(1, pattern="ma-na"), _day(2)]
    assert [_key(_curriculum(days), day) for day in days] == ["ma-na", "mo-mi"]


def test_a_name_the_language_does_not_have_falls_back_to_the_gate():
    days = [_day(1), _day(2, pattern="um-in")]
    assert _key(_curriculum(days), days[1]) == "mo-mi"


def test_a_language_with_no_patterns_plans_none():
    days = [_day(1), _day(2)]
    assert [_key(_curriculum(days, "no"), day) for day in days] == [None, None]


def _mo_mi_passed(srs_db, store) -> None:
    """mo- / mi- heard on two days, four of its cards past a first review."""
    passed = _story_lesson("Affix drill", [])
    passed.generation_metadata["affix_drill"] = {"pattern": "mo-mi", "roots": [], "new_roots": []}
    store.save_lesson("drill-mo-mi", "elsewhere", 1, passed)
    srs_db.record_listen("drill-mo-mi", at=datetime(2026, 10, 9, 12, tzinfo=UTC))
    srs_db.record_listen("drill-mo-mi", at=datetime(2026, 10, 13, 12, tzinfo=UTC))
    for n, (root, blank) in enumerate((("uban", 1), ("inom", 0), ("adto", 1), ("anhi", 0))):
        unit = contrast_card("ceb", _PATTERNS["mo-mi"], root, blank).unit()
        srs_db.add_collocation(unit, language_code="ceb")
        srs_db.append_revlog(
            RevlogRow(
                id=1_791_600_000_000 + n * 60_000,
                collocation_id=srs_db.get_collocation_id_by_guid(srs_db.get_collocation(unit.text).guid),
                direction=Direction.PRODUCTION,
                button_chosen=3,
                interval=5,
                last_interval=1,
                factor=2500,
                taken_millis=5000,
                review_kind=1,
            )
        )


def test_a_day_with_a_deck_takes_the_gates_pattern(srs_db):
    store = ContentStore(":memory:")
    curriculum = _curriculum([_day(1), _day(2)])
    store.save_curriculum(curriculum.id, curriculum)
    assert _key(curriculum, curriculum.days[1], srs_db=srs_db, store=store) == "mo-mi"

    _mo_mi_passed(srs_db, store)
    assert _key(curriculum, curriculum.days[1], srs_db=srs_db, store=store) == "mag-nag"


def test_a_day_that_already_holds_a_drilled_lesson_keeps_its_pattern(srs_db):
    """A lesson made again, or rebuilt from its story, drills what it drilled,
    though the learner has passed that pattern since."""
    store = ContentStore(":memory:")
    curriculum = _curriculum([_day(1), _day(2)])
    store.save_curriculum(curriculum.id, curriculum)
    made = _story_lesson("An Evening Wake", _WAKE)
    made.generation_metadata["affix_drill"] = {"pattern": "mo-mi", "roots": [], "new_roots": []}
    store.save_lesson("wake", curriculum.id, 2, made)
    _mo_mi_passed(srs_db, store)

    assert _key(curriculum, curriculum.days[1], srs_db=srs_db, store=store) == "mo-mi"
    assert _key(curriculum, curriculum.days[0], srs_db=srs_db, store=store) == "mag-nag"


def test_a_day_whose_lesson_has_no_drill_takes_the_gates_pattern(srs_db):
    store = ContentStore(":memory:")
    curriculum = _curriculum([_day(1)])
    store.save_curriculum(curriculum.id, curriculum)
    store.save_lesson("wake", curriculum.id, 1, _story_lesson("An Evening Wake", _WAKE))
    _mo_mi_passed(srs_db, store)
    assert _key(curriculum, curriculum.days[0], srs_db=srs_db, store=store) == "mag-nag"


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
        ("ceb", "WIDER", "7c620668755e39bc"),
        ("ceb", "DEEPER", "92e6b5c600dce2a3"),
        ("tl", "WIDER", "9238bf15b1a03090"),
    ],
)
def test_a_prompt_with_no_pattern_is_byte_identical_to_before(code, strategy, recorded):
    """Measured on main at 4e1d381, before the block existed. Every cassette is keyed on these bytes.

    The two ceb values were re-measured 2026-10-16 when style.md gained the
    everyday-short-forms rule (the owner's call); no cassette was recorded
    against the Cebuano story prompt, so none went stale."""
    assert _fingerprint(code, strategy) == recorded


def test_a_language_with_no_patterns_is_unmoved_even_inside_a_curriculum(owner):  # noqa: F811
    srs_db, store = owner
    store.save_curriculum("no-plan", _curriculum([_day(1)], "no"))
    assert _fingerprint("no", "DEEPER", content_store=store, curriculum_id="no-plan") == "0d965d4571180cd8"


@pytest.mark.parametrize("strategy", ["WIDER", "DEEPER"])
def test_a_story_in_a_curriculum_is_asked_for_its_days_pattern(owner, strategy):  # noqa: F811
    srs_db, store = owner
    language = get_language("ceb")
    day = store.get_curriculum("funeral").days[1]  # with no deck to read, nothing has passed: mo-mi

    plain = build_story_prompts(day, language, ContentStrategy[strategy], "A1")
    planned = build_story_prompts(
        day, language, ContentStrategy[strategy], "A1", srs_db=None, content_store=store, curriculum_id="funeral"
    )

    block = pattern_block("ceb", _PATTERNS["mo-mi"])
    rules = f"**{strategy} STRATEGY RULES**"
    assert planned.system_prompt == plain.system_prompt
    assert f"{block}{rules}" in planned.user_prompt
    # Nothing else moved: take the block out and it is the plain prompt again.
    # (DEEPER also gains day 1's transcript as its source, which is not this test's subject.)
    if strategy == "WIDER":
        assert planned.user_prompt.replace(block, "") == plain.user_prompt
    assert (len(planned.system_prompt) + len(planned.user_prompt)) // 4 <= PROMPT_TOKEN_BUDGET


def test_a_story_with_a_deck_is_asked_for_the_gates_pattern(owner):  # noqa: F811
    srs_db, store = owner
    _mo_mi_passed(srs_db, store)
    day = store.get_curriculum("funeral").days[1]
    prompts = build_story_prompts(
        day,
        get_language("ceb"),
        ContentStrategy.WIDER,
        "A1",
        srs_db=srs_db,
        content_store=store,
        curriculum_id="funeral",
    )
    assert pattern_block("ceb", _PATTERNS["mag-nag"], srs_db=srs_db) in prompts.user_prompt


_MARKET = [
    ("Naglakaw mi sa merkado.", "We walked to the market."),
    ("Nagdala ko og bugas.", "I brought rice."),
    ("Magluto ko karon.", "I will cook now."),
]


def test_a_new_days_closing_drill_follows_the_gate(srs_db):
    store = ContentStore(":memory:")
    curriculum = _curriculum([_day(1)])
    store.save_curriculum(curriculum.id, curriculum)
    _mo_mi_passed(srs_db, store)

    lesson = _story_lesson("At the Market", _MARKET)
    add_day_drill(lesson, get_language("ceb"), curriculum, 1, srs_db=srs_db, lemmatizer=None, store=store)
    assert lesson.generation_metadata["affix_drill"]["pattern"] == "mag-nag"


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
        "note": False,
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


def test_a_closing_drill_asks_for_every_form_twice_and_ends_on_the_line(srs_db):
    for word in ("ko", "ugma"):
        _card(srs_db, word, recognition=SRSState.REVIEW)
    lesson = _closed(_WAKE, srs_db)
    # Three roots, each named once, each of its two forms built up once
    # (tunatale-ve4p.16). Then the six forms are asked for, and asked for once
    # more in another order (the owner, 2026-10-13: closing drills only, so the
    # closing drill is given what the grammar lesson had). Last, the one whole
    # line of the story that says a drilled form in a single sentence.
    answers = _answers(lesson)
    forms = answers[:12]
    assert sorted(forms[:6]) == sorted(forms[6:]) == sorted(set(forms))
    # Then the three lines the drill itself brings, and last the story's.
    assert answers[12:] == ["Wala ko moadto.", "Moanhi ko kada adlaw.", "Gusto ko moadto.", "Mouban ko ugma."]


# --- aspect, not tense (tunatale-ve4p.25) --------------------------------------
#
# The owner, 2026-10-13, chose all four: reworded prompts (the gloss table), a
# sentence from the narrator when a pattern is first met, lines for the habitual
# and for what did not happen, and "begun / not begun" over the page's columns.

# The lines the wake's drill brings: adto is its first root and anhi its second.
# uban has none, so the third comes round to adto again (tunatale-ve4p.31: gusto).
_FRAMED = [
    ("Wala ko moadto.", "I didn't go."),
    ("Moanhi ko kada adlaw.", "I come every day."),
    ("Gusto ko moadto.", "I want to go."),
]


def test_the_drill_teaches_then_asks_for_what_did_not_happen_what_usually_does_and_what_is_wanted():
    """Rule-built, on the roots the drill models, a different root for each. They
    come before the story's own line and need no deck: the drill teaches them
    the way it teaches any line, the whole of it first."""
    lesson = _closed(_WAKE, None)
    phrases = lesson.sections[-1].phrases

    assert _answers(lesson)[-len(_FRAMED) :] == [text for text, _ in _FRAMED]
    for text, english in _FRAMED:
        said = [i for i, p in enumerate(phrases) if p.text == text]
        taught, asked = said[0], said[-1]
        assert (phrases[taught].role, phrases[asked].role) == ("model", "answer")
        assert phrases[taught - 1].text == english
        assert taught < asked
    prompts = [p.text for p in phrases if p.role == "prompt"]
    assert prompts[-3:] == ['How do you say "I didn\'t go"?', "I come every day.", "I want to go."]


def test_a_drill_whose_roots_have_no_line_written_ends_as_it_did():
    """ma- / na- has none: 'I didn't happy' is what a rule would make of lipay."""
    lines = [("Natulog siya.", "He is asleep."), ("Matulog ko.", "I will sleep."), ("Nalipay ko.", "I am happy.")]
    lesson = _story_lesson("A Day at the Beach", lines)
    add_closing_drill(lesson, get_language("ceb"), _PATTERNS["ma-na"], srs_db=None, lemmatizer=None)

    assert lesson.generation_metadata["affix_drill"]["roots"] == ["tulog", "lipay"]
    assert not [a for a in _answers(lesson) if " " in a]


def _noted(lesson) -> list[str]:
    notes = _PATTERNS[lesson.generation_metadata["affix_drill"]["pattern"]].notes
    return [p.text for p in lesson.sections[-1].phrases if p.text in notes]


def _stored(store, curriculum, day: int, lesson) -> None:
    store.save_curriculum(curriculum.id, curriculum)
    store.save_lesson(f"lesson-{day}", curriculum.id, day, lesson)


def _drilled(curriculum, day: int, store, title: str = "An Evening Wake"):
    lesson = _story_lesson(title, _WAKE)
    add_day_drill(lesson, get_language("ceb"), curriculum, day, srs_db=None, lemmatizer=None, store=store)
    return lesson


def test_the_narrator_says_what_the_pair_is_for_the_first_time_a_pattern_is_met():
    store = ContentStore(":memory:")
    curriculum = _curriculum([_day(1, pattern="mo-mi"), _day(2, pattern="mo-mi"), _day(3, pattern="mo-mi")])

    first = _drilled(curriculum, 1, store)
    assert _noted(first) == list(_PATTERNS["mo-mi"].notes)
    assert first.generation_metadata["affix_drill"]["note"] is True
    assert {p.role for p in first.sections[-1].phrases if p.text in _noted(first)} == {"narrator"}

    _stored(store, curriculum, 1, first)
    second = _drilled(curriculum, 2, store, "The Burial Morning")
    assert _noted(second) == []
    assert second.generation_metadata["affix_drill"]["note"] is False


def test_a_day_made_again_says_its_note_again():
    """What an earlier DAY said is what counts. The lesson a day already holds
    is the one being replaced, and the new one must not lose the note to it."""
    store = ContentStore(":memory:")
    curriculum = _curriculum([_day(1, pattern="mo-mi")])
    _stored(store, curriculum, 1, _drilled(curriculum, 1, store))

    assert _noted(_drilled(curriculum, 1, store)) == list(_PATTERNS["mo-mi"].notes)


def test_a_pattern_drilled_before_the_note_existed_is_still_owed_it():
    """The owner's day 3 and day 4 drilled mo- / mi- and ma- / na- and said
    nothing about what the forms are for. The next lesson of either says it."""
    store = ContentStore(":memory:")
    curriculum = _curriculum([_day(1, pattern="mo-mi"), _day(2, pattern="mo-mi")])
    silent = _story_lesson("An Evening Wake", _WAKE)
    silent.generation_metadata["affix_drill"] = {"pattern": "mo-mi", "roots": ["adto", "anhi"], "new_roots": []}
    _stored(store, curriculum, 1, silent)

    assert _noted(_drilled(curriculum, 2, store, "The Burial Morning")) == list(_PATTERNS["mo-mi"].notes)


def test_another_patterns_note_does_not_stand_in_for_this_ones():
    store = ContentStore(":memory:")
    curriculum = _curriculum([_day(1, pattern="mag-nag"), _day(2, pattern="mo-mi")])
    other = _story_lesson("An Evening Wake", _WAKE)
    other.generation_metadata["affix_drill"] = {"pattern": "mag-nag", "roots": [], "new_roots": [], "note": True}
    _stored(store, curriculum, 1, other)

    assert _noted(_drilled(curriculum, 2, store, "The Burial Morning")) == list(_PATTERNS["mo-mi"].notes)


def test_with_no_store_to_ask_the_note_is_said():
    """Nothing says the learner has heard it, and hearing it twice costs ten seconds."""
    assert _noted(_drilled(_curriculum([_day(1, pattern="mo-mi")]), 1, None)) == list(_PATTERNS["mo-mi"].notes)


# --- roots the story did not use (the owner, 2026-10-13: closing drills only) ---
#
# The grammar lesson asked for roots it never modelled, and getting those right
# can only be the affix. With no new grammar lessons, the closing drill asks
# for up to two: roots the learner understands, whose forms mean what the root
# means, and which this story never said.


def _understands(srs_db, *roots: str) -> None:
    for root in roots:
        _card(srs_db, root, recognition=SRSState.REVIEW)


def _new_roots(lesson) -> list[str]:
    return lesson.generation_metadata["affix_drill"]["new_roots"]


def test_the_drill_asks_for_two_roots_the_story_did_not_use_without_modelling_them(srs_db):
    _understands(srs_db, "lakaw", "balik", "kaon", "sulti")

    lesson = _closed(_WAKE, srs_db)

    assert lesson.generation_metadata["affix_drill"]["roots"] == ["adto", "anhi", "uban"]
    new = _new_roots(lesson)
    assert len(new) == 2
    assert set(new) < {"lakaw", "balik", "kaon", "sulti"}
    phrases = lesson.sections[-1].phrases
    modelled = [p.text for p in phrases if p.role == "model"]
    for root in new:
        future, past = f"mo{root}", f"mi{root}"
        # Named, so the learner knows which root is meant; its forms are theirs to build.
        assert root in modelled
        assert future not in modelled
        assert past not in modelled
        # Once among the new roots, once more among all of them.
        assert _answers(lesson).count(future) == _answers(lesson).count(past) == 2


def test_a_root_the_story_said_is_not_asked_for_as_new(srs_db):
    """The wake says `miinom`. It is the fourth root, so it is not drilled; it
    was heard a minute ago, so asking for it proves nothing either."""
    _understands(srs_db, "inom", "lakaw")

    assert _new_roots(_closed(_WAKE, srs_db)) == ["lakaw"]


def test_a_root_whose_forms_have_to_be_shown_is_not_asked_for_as_new(srs_db):
    """`sulod` is "inside" and `mosulod` is "will enter": nothing in the prompt leads there."""
    _understands(srs_db, "sulod", "lakaw")

    assert _new_roots(_closed(_WAKE, srs_db)) == ["lakaw"]


def test_a_root_the_learner_does_not_understand_is_not_asked_for_as_new(srs_db):
    _understands(srs_db, "lakaw")
    _card(srs_db, "balik", recognition=SRSState.NEW)

    assert _new_roots(_closed(_WAKE, srs_db)) == ["lakaw"]


def test_a_root_buried_for_the_day_can_still_be_asked_for_as_new(srs_db):
    """The owner's day 5: the one eligible root had been reviewed that morning."""
    _understands(srs_db, "lakaw")
    _bury(srs_db, "lakaw", reps=5)

    assert _new_roots(_closed(_WAKE, srs_db)) == ["lakaw"]


def test_one_lesson_always_asks_for_the_same_new_roots_and_another_lesson_for_others(srs_db):
    """Drawn from the lesson, not from the clock: a lesson rebuilt from its
    story is the lesson it was. And not the table's first two every time, or
    after one lesson they would be remembered words and not built ones."""
    _understands(srs_db, "lakaw", "balik", "kaon", "sulti", "tan-aw")

    def new(title: str) -> tuple[str, ...]:
        lesson = _story_lesson(title, _WAKE)
        add_closing_drill(lesson, get_language("ceb"), _PATTERNS["mo-mi"], srs_db=srs_db, lemmatizer=None)
        return tuple(_new_roots(lesson))

    titles = [f"An Evening Wake, part {n}" for n in range(8)]
    assert [new(title) for title in titles] == [new(title) for title in titles]
    assert len({frozenset(new(title)) for title in titles}) > 1


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

    assert len(answers) == 3 * 2 * 2 + len(_FRAMED)
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

    lesson = _closed(_WAKE, None)

    assert len(_answers(lesson)) == 3 * 2 * 2 + len(_FRAMED)
    assert _new_roots(lesson) == []


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
