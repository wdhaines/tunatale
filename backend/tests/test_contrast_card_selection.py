"""Which contrast cards a lesson's listen adds (tunatale-ve4p.6).

A lesson adds cards only for the ONE pattern its affix drill is on, only for
roots the learner understands, and one card per root at a time:

* the first card for a root asks for the form the learner's lessons have NOT
  used, beside the one they have. That is the transfer test: the owner's
  baseline, 2026-10-07, missed exactly these;
* the root's other card waits until the first is in review. The two cards
  show each other's answer as the model and are separate Anki notes, so Anki
  would not keep them apart on the day they are both new.

The oracle is the owner's deck and lessons as ``test_grammar_lesson.py``
records them: five understood roots take mo-/mi-, the lessons say ``moadto``,
``mianhi``, ``mouban`` and ``miinom``, and neither form of ``lakaw``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from app.common.guid import compute_guid
from app.generation.grammar_lesson import build_grammar_lesson, contrast_cards, plan_grammar_lesson
from app.languages import get_a1_morphology, get_language
from app.models.lesson import Lesson
from app.models.srs_item import Direction, DirectionState, SRSState
from tests.test_grammar_lesson import _BURIAL, _WAKE, _card, _story_lesson, owner  # noqa: F401

_MO_MI = next(p for p in get_a1_morphology("ceb").patterns if p.key == "mo-mi")


def _drill(srs_db, store) -> Lesson:
    """The owner's mo-/mi- lesson, built the way the pipeline builds it."""
    plan = plan_grammar_lesson("ceb", _MO_MI, srs_db=srs_db, store=store, curriculum_id="funeral")
    return build_grammar_lesson(get_language("ceb"), plan)


def _offered(lesson, srs_db, store) -> list[str]:
    return [card.form for card in contrast_cards(lesson, srs_db=srs_db, store=store, curriculum_id="funeral")]


def _add(srs_db, card, state: SRSState = SRSState.NEW) -> None:
    unit = card.unit()
    srs_db.add_collocation(unit, language_code="ceb")
    if state is not SRSState.NEW:
        srs_db.update_direction(
            compute_guid(unit.text, "ceb", unit.disambig_key),
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.0,
                reps=5,
                state=state,
            ),
        )


def _add_all(lesson, srs_db, store, state: SRSState = SRSState.NEW) -> None:
    for card in contrast_cards(lesson, srs_db=srs_db, store=store, curriculum_id="funeral"):
        _add(srs_db, card, state)


def test_the_first_listen_asks_for_the_form_the_lessons_have_not_used(owner):  # noqa: F811
    srs_db, store = owner
    lesson = _drill(srs_db, store)

    cards = contrast_cards(lesson, srs_db=srs_db, store=store, curriculum_id="funeral")

    # Modelled roots first, then the ones left to build: the drill's own order.
    assert [(card.root, card.form, card.prompt) for card in cards] == [
        ("uban", "miuban", "went along"),
        ("inom", "moinom", "will drink"),
        ("adto", "miadto", "went"),
        ("anhi", "moanhi", "will come"),
        ("lakaw", "milakaw", "walked"),  # the lessons say neither form: the last cell
    ]
    # A form no lesson says has no lesson line to stand in.
    assert [card.line for card in cards] == [""] * 5


def test_a_root_whose_forms_were_both_heard_is_asked_for_the_last(owner):  # noqa: F811
    srs_db, store = owner
    store.save_lesson("burial-2", "funeral", 2, _story_lesson("The Burial", [*_BURIAL, ("Miadto sila.", "They went.")]))

    lesson = _drill(srs_db, store)
    cards = contrast_cards(lesson, srs_db=srs_db, store=store, curriculum_id="funeral")

    assert {card.root: card.form for card in cards}["adto"] == "miadto"


def test_nothing_more_is_offered_while_the_first_cards_are_new(owner):  # noqa: F811
    srs_db, store = owner
    lesson = _drill(srs_db, store)
    _add_all(lesson, srs_db, store)

    assert _offered(lesson, srs_db, store) == []


def test_a_roots_other_form_follows_once_its_first_card_is_in_review(owner):  # noqa: F811
    srs_db, store = owner
    lesson = _drill(srs_db, store)
    first = contrast_cards(lesson, srs_db=srs_db, store=store, curriculum_id="funeral")
    for card in first:
        # uban and anhi have been learned; the other three are still new.
        _add(srs_db, card, SRSState.REVIEW if card.root in ("uban", "anhi") else SRSState.NEW)

    second = contrast_cards(lesson, srs_db=srs_db, store=store, curriculum_id="funeral")

    assert [card.form for card in second] == ["mouban", "mianhi"]
    # mouban is said in one sentence, so that sentence comes with it. mianhi is
    # said only in the second sentence of a two-sentence line, which a card
    # does not carry, any more than the drill does.
    assert [(card.line, card.line_english) for card in second] == [
        ("Mouban ko ugma.", "I will come along tomorrow."),
        ("", ""),
    ]


def test_a_root_with_both_cards_is_finished(owner):  # noqa: F811
    srs_db, store = owner
    lesson = _drill(srs_db, store)
    _add_all(lesson, srs_db, store, SRSState.REVIEW)
    _add_all(lesson, srs_db, store, SRSState.KNOWN)

    assert _offered(lesson, srs_db, store) == []


def test_a_root_no_longer_understood_is_skipped(owner):  # noqa: F811
    srs_db, store = owner
    lesson = _drill(srs_db, store)
    _card(srs_db, "lakaw", recognition=SRSState.RELEARNING)

    assert _offered(lesson, srs_db, store) == ["miuban", "moinom", "miadto", "moanhi"]


def test_a_lesson_with_no_affix_drill_adds_none(owner):  # noqa: F811
    srs_db, store = owner
    story = _story_lesson("An Evening Wake", _WAKE)

    assert contrast_cards(story, srs_db=srs_db, store=store, curriculum_id="funeral") == []


@pytest.mark.parametrize("pattern", ["um-in", ""])
def test_a_drill_on_a_pattern_the_language_no_longer_has_adds_none(owner, pattern):  # noqa: F811
    srs_db, store = owner
    lesson = _drill(srs_db, store)
    lesson.generation_metadata["affix_drill"]["pattern"] = pattern

    assert contrast_cards(lesson, srs_db=srs_db, store=store, curriculum_id="funeral") == []
