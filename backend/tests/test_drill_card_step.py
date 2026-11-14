"""A drill card moves to its next step when it lands in review, not on every answer (tunatale-5i1p.10).

Brief: ``.beads-tasks/briefs/brief-5i1p10-drill-step-advances-on-review-2026-11.md`` (oracle S1-S11).
Owner, 2026-11-14: "My expectation would have been that it stays the same until it graduates
from learning to review, then rotates."

The step is the number of the card's answers that left it in REVIEW, mod its steps, read from
``tt_revlog``: an answer that left the card in review has a positive ``interval`` (days), a
learning or relearning one a negative ``interval`` (seconds), in TT's own rows
(``fsrs._compute_revlog_interval``) and in the ones pulled from Anki. Rows that are not answers
(manual, Forget, reschedule: button 0) never move it; a budget-neutral lesson re-grade is an answer.

"Shows" means every reader agrees: the review payload (``_queue_item_to_dict``), the item payload
(``_item_to_dict``) and the note's fields (``_drill_line_plan``). The Grade page's reader is
pinned in ``test_api_grade_page.py::TestPlanDrillCards``. ``reps`` is set to the number of
answers in each case, so a reader left on ``reps`` fails.
"""

from __future__ import annotations

import itertools

import pytest

from app.api import srs as srs_api
from app.models.srs_item import Direction, RevlogRow
from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
from app.srs.database import SRSDatabase
from app.srs.drill_card import drill_card
from app.srs.lesson_card_mint import mint_lesson_cards
from tests._helpers.anki_sync_create_new import FakeReader
from tests.test_anki_sentence_note_sync import _conn
from tests.test_drill_card import DRILLS
from tests.test_lesson_drill_cards import _cards

LANG = "no"
PROD = Direction.PRODUCTION
SUB = "sent:test-01:drill-1"  # substitution, 4 steps
QA = "sent:test-01:drill-4:2"  # a question-and-answer step card: its own step, always

# (review_kind, interval, button): Anki's revlog shapes, which TT's own rows share.
LEARN_GOOD = (0, -600, 3)
LEARN_AGAIN = (0, -60, 1)
GRADUATE_GOOD = (0, 2, 3)
GRADUATE_EASY = (0, 4, 4)
REVIEW_HARD = (1, 8, 2)
REVIEW_GOOD = (1, 10, 3)
REVIEW_EASY = (1, 20, 4)
REVIEW_AGAIN = (1, -600, 1)  # a lapse into relearning
RELEARN_GOOD = (2, 3, 3)  # graduates again
RELEARN_AGAIN = (2, -600, 1)
LAPSE_NO_RELEARN_STEPS = (1, 1, 1)  # Again with no relearning steps: Anki keeps it in review


def _rows() -> tuple[SRSDatabase, dict[str, int]]:
    db = SRSDatabase(":memory:")
    minted = mint_lesson_cards(db, LANG, _cards(), drills=DRILLS)
    return db, {card.unit.disambig_key: cid for cid, card in minted}


_ids = itertools.count(1_700_000_000_000, 1000)


def _answer(db: SRSDatabase, cid: int, shape: tuple[int, int, int], *, budget_neutral: bool = False, factor=5000):
    kind, interval, button = shape
    db.append_revlog(
        RevlogRow(
            id=next(_ids),
            collocation_id=cid,
            direction=PROD,
            button_chosen=button,
            interval=interval,
            last_interval=0,
            factor=factor,
            taken_millis=1000,
            review_kind=kind,
            anki_card_id=None,
            budget_neutral=budget_neutral,
        )
    )


def _set_reps(db: SRSDatabase, cid: int, reps: int) -> None:
    with db._get_conn() as conn:
        conn.execute(
            "UPDATE collocation_directions SET reps = ? WHERE collocation_id = ? AND direction = 'production'",
            (reps, cid),
        )


def _history(db: SRSDatabase, cid: int, shapes: list[tuple[int, int, int]]) -> None:
    for shape in shapes:
        _answer(db, cid, shape)
    _set_reps(db, cid, len(shapes))


def _steps(db: SRSDatabase, cid: int) -> dict[str, int]:
    """The 0-based step each reader shows for row *cid*."""
    _, item, _lang = db.get_collocation_by_id(cid)
    guid = item.guid
    card = drill_card(item.syntactic_unit)
    queue = srs_api._queue_item_to_dict(cid, item, LANG, PROD, db)["drill"]["step"] - 1
    listed = srs_api._item_to_dict(cid, item, LANG, db=db)["drill"]["step"] - 1
    sync = AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(_conn()), language_code=LANG)
    fields, _media, _ords = sync._drill_line_plan((guid, item, cid), card)
    note = next(card.view(k).step for k in range(len(card.steps)) if fields[0] == f"{{{{c1::{card.view(k).answer}}}}}")
    return {"queue": queue, "item": listed, "note": note}


def _shows(db: SRSDatabase, cid: int, step: int) -> None:
    assert _steps(db, cid) == {"queue": step, "item": step, "note": step}


class TestTheRule:
    def test_s1_a_new_card_shows_step_0(self):
        db, rows = _rows()
        _shows(db, rows[SUB], 0)

    def test_s2_a_learning_answer_does_not_move_it(self):
        db, rows = _rows()
        _history(db, rows[SUB], [LEARN_GOOD])
        _shows(db, rows[SUB], 0)

    def test_s3_again_again_good_still_learning_stays_at_step_0(self):
        db, rows = _rows()
        _history(db, rows[SUB], [LEARN_AGAIN, LEARN_AGAIN, LEARN_GOOD])
        _shows(db, rows[SUB], 0)

    @pytest.mark.parametrize("history", [[LEARN_GOOD, GRADUATE_GOOD], [GRADUATE_EASY]], ids=["good-good", "easy"])
    def test_s4_graduating_moves_it_to_step_1(self, history):
        db, rows = _rows()
        _history(db, rows[SUB], history)
        _shows(db, rows[SUB], 1)

    def test_s5_each_review_answered_hard_good_or_easy_moves_it_on(self):
        db, rows = _rows()
        cid = rows[SUB]
        _history(db, cid, [GRADUATE_GOOD, REVIEW_GOOD])
        _shows(db, cid, 2)
        _history(db, cid, [REVIEW_HARD])
        _shows(db, cid, 3)
        _history(db, cid, [REVIEW_EASY])
        _shows(db, cid, 0)  # 4 steps: wraps

    def test_s6_a_lapse_relearns_the_same_step_and_moves_on_when_it_graduates_again(self):
        db, rows = _rows()
        cid = rows[SUB]
        _history(db, cid, [GRADUATE_GOOD, REVIEW_GOOD])
        _shows(db, cid, 2)
        _history(db, cid, [REVIEW_AGAIN])
        _shows(db, cid, 2)
        _history(db, cid, [RELEARN_AGAIN])
        _shows(db, cid, 2)
        _history(db, cid, [RELEARN_GOOD])
        _shows(db, cid, 3)

    def test_s6_a_lapse_with_no_relearning_steps_moves_it_on(self):
        """Anki keeps the card in review (positive interval): it landed in review, so it advances."""
        db, rows = _rows()
        _history(db, rows[SUB], [GRADUATE_GOOD, LAPSE_NO_RELEARN_STEPS])
        _shows(db, rows[SUB], 2)

    def test_s7_as_many_review_landings_as_steps_wraps_to_step_0(self):
        db, rows = _rows()
        _history(db, rows[SUB], [GRADUATE_GOOD, REVIEW_GOOD, REVIEW_GOOD, REVIEW_GOOD])
        _shows(db, rows[SUB], 0)
        _history(db, rows[SUB], [REVIEW_GOOD])
        _shows(db, rows[SUB], 1)


class TestNotAnswers:
    @pytest.mark.parametrize(
        ("shape", "kw"),
        [
            ((4, 0, 0), {}),  # manual (promote_to_learning and the like)
            ((4, 0, 0), {"factor": 0}),  # Anki's Forget
            ((5, 12, 0), {}),  # a reschedule
        ],
        ids=["manual", "forget", "reschedule"],
    )
    def test_s10_a_row_that_is_not_an_answer_does_not_move_it(self, shape, kw):
        db, rows = _rows()
        cid = rows[SUB]
        _history(db, cid, [GRADUATE_GOOD])
        _answer(db, cid, shape, **kw)
        _set_reps(db, cid, 2)
        _shows(db, cid, 1)


class TestLessonRegrade:
    def test_a_budget_neutral_lesson_regrade_that_lands_in_review_moves_it_on(self):
        """Graded in the queue, then again on the Grade page the same evening: two real answers.

        The re-grade keeps its FSRS state change and only skips the daily-budget charge
        (``api/srs.py``), so it counts; otherwise the learner is asked the step just answered.
        """
        db, rows = _rows()
        cid = rows[SUB]
        _history(db, cid, [GRADUATE_GOOD, REVIEW_GOOD])
        _answer(db, cid, REVIEW_GOOD, budget_neutral=True)
        _set_reps(db, cid, 3)
        _shows(db, cid, 3)


class TestControls:
    def test_s11_a_question_and_answer_step_card_shows_its_own_step_whatever_happens(self):
        db, rows = _rows()
        cid = rows[QA]
        _shows(db, cid, 1)
        _history(db, cid, [GRADUATE_GOOD, REVIEW_GOOD, REVIEW_GOOD])
        _shows(db, cid, 1)

    def test_s11_another_cards_answers_do_not_move_this_one(self):
        db, rows = _rows()
        _history(db, rows["sent:test-01:drill-2"], [GRADUATE_GOOD, REVIEW_GOOD])
        _shows(db, rows[SUB], 0)
