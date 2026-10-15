"""Which focus cards a listen of an object-focus lesson adds (tunatale-ve4p.9).

The contrast-card rule, kept: only roots the drill took and the learner
understands, and at most one card per root at a time. The root's next card
waits until the cards it has are in review, so two cards of one root are
never new together. The cards come in the order gi-, mi-, -on, mo-.

The learner is the owner on 2026-10-15 (``test_focus_drill.learner``):
inom, kaon and dala understood, tan-aw still new.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from app.common.guid import compute_guid
from app.generation.grammar_lesson import build_grammar_lesson, contrast_cards, plan_grammar_lesson
from app.languages import get_a1_morphology, get_language
from app.models.srs_item import Direction, DirectionState, SRSState
from app.srs.focus_card import FocusCard
from tests.test_focus_drill import _card, learner  # noqa: F401

_ON_GI = next(p for p in get_a1_morphology("ceb").patterns if p.key == "on-gi")


def _lesson(learner):  # noqa: F811
    srs_db, store = learner
    plan = plan_grammar_lesson("ceb", _ON_GI, srs_db=srs_db, store=store, curriculum_id="funeral", lemmatizer=None)
    return build_grammar_lesson(get_language("ceb"), plan)


def _offered(lesson, learner) -> list[tuple[str, str]]:  # noqa: F811
    srs_db, store = learner
    return [(c.root, c.text) for c in contrast_cards(lesson, srs_db=srs_db, store=store, curriculum_id="funeral")]


def _add_all(lesson, learner, state: SRSState = SRSState.NEW) -> None:  # noqa: F811
    srs_db, store = learner
    for card in contrast_cards(lesson, srs_db=srs_db, store=store, curriculum_id="funeral"):
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


def test_the_first_listen_adds_each_roots_object_focus_card(learner):  # noqa: F811
    srs_db, store = learner
    lesson = _lesson(learner)
    cards = contrast_cards(lesson, srs_db=srs_db, store=store, curriculum_id="funeral")
    assert all(isinstance(card, FocusCard) for card in cards)
    assert [(c.root, c.text, c.english) for c in cards] == [
        ("inom", "Giinom ko ang tubig.", "I drank the water."),
        ("kaon", "Gikaon ko ang pan.", "I ate the bread."),
        ("dala", "Gidala ko ang pagkaon.", "I brought the food."),
    ]


def test_a_roots_next_card_waits_while_its_card_is_new(learner):  # noqa: F811
    lesson = _lesson(learner)
    _add_all(lesson, learner)
    assert _offered(lesson, learner) == []


def test_a_roots_next_card_waits_while_its_card_is_learning(learner):  # noqa: F811
    lesson = _lesson(learner)
    _add_all(lesson, learner, SRSState.LEARNING)
    assert _offered(lesson, learner) == []


def test_once_in_review_the_next_card_is_actor_focus_with_the_next_object_and_doer(learner):  # noqa: F811
    lesson = _lesson(learner)
    _add_all(lesson, learner, SRSState.REVIEW)
    assert _offered(lesson, learner) == [
        ("inom", "Miinom siya og kape."),
        ("kaon", "Mikaon siya og pagkaon."),
        ("dala", "Midala siya og pan."),
    ]


def test_after_four_cards_a_root_adds_no_more(learner):  # noqa: F811
    lesson = _lesson(learner)
    texts = []
    for _ in range(4):
        texts.append(_offered(lesson, learner)[0][1])
        _add_all(lesson, learner, SRSState.REVIEW)
    assert texts == ["Giinom ko ang tubig.", "Miinom siya og kape.", "Imnon ko ang gatas.", "Moinom siya og tubig."]
    assert _offered(lesson, learner) == []


def test_a_root_no_longer_understood_is_skipped(learner):  # noqa: F811
    srs_db, _ = learner
    lesson = _lesson(learner)
    srs_db.update_direction(
        srs_db.get_collocation("kaon").guid,
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=1.0,
            difficulty=8.0,
            reps=6,
            state=SRSState.RELEARNING,
        ),
    )
    assert [root for root, _ in _offered(lesson, learner)] == ["inom", "dala"]


def test_tan_aw_joins_once_its_recognition_card_is_in_review(learner):  # noqa: F811
    srs_db, _ = learner
    _card(srs_db, "tan-aw", SRSState.REVIEW)
    lesson = _lesson(learner)
    assert _offered(lesson, learner)[-1] == ("tan-aw", "Gitan-aw ko ang balay.")
