"""A grade on a not-due REVIEW card is Anki's review-ahead: kind 3, uncharged, from every path.

Decided by the owner 2026-11-13 ("Match Anki"), measured on the Anki 26.9.4
engine first (`.beads-tasks/briefs/design-grade-page-2026-11.md` § P1 measured):
Anki cannot grade a not-due card in its home deck at all; its one early-review
path, a rescheduling filtered deck, logs revlog type 3 and leaves the home deck's
``revToday`` alone. The memory state after the grade is the same either way, so
the kind changes bookkeeping only: the review budget, Anki's statistics, and
anything that filters on kind.

The listen popup's confirmed grades (`_apply_grade_now`) already logged 3. The
card-review route (`drill_feedback`, which the Read popover and the review screen
call) logged 3 only when releasing a staged listen grade and otherwise logged 1,
charging today's review allowance for a card Anki would not charge. These pin
the route to dueness alone. Oracle cases D1-D4 of the brief; D5 (the push) is in
``test_parity_review_ahead_push.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.queue_stats import (
    build_live_load_balancer,
    resolve_col_crt,
    resolve_fsrs_params,
    resolve_learning_steps,
    resolve_relearning_steps,
)
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase
from app.srs.fsrs import Rating, schedule
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

REC = Direction.RECOGNITION


@pytest.fixture
def db() -> SRSDatabase:
    d = SRSDatabase(":memory:")
    app.state.srs_db = d
    try:
        yield d
    finally:
        d.close()


def _seed(
    db: SRSDatabase,
    text: str = "hus",
    *,
    state: SRSState = SRSState.REVIEW,
    due_in_days: int | None = 2,
    last_review: datetime | None = None,
) -> int:
    """One Norwegian card. *due_in_days* None = due today (04:00-UTC convention of the due count)."""
    unit = SyntacticUnit(text=text, translation="house", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="no")
    item = db.get_collocation(text)
    assert item is not None
    rec = item.directions[REC]
    rec.state = state
    if state is not SRSState.NEW:
        rec.last_review = last_review or datetime.now(UTC) - timedelta(days=5)
        rec.stability = 10.0
        rec.difficulty = 5.0
        rec.reps = 5
        rec.due_at = (
            due_at_rollover_utc(anki_today())
            if due_in_days is None
            else datetime.now(UTC) + timedelta(days=due_in_days)
        )
        if state in (SRSState.LEARNING, SRSState.RELEARNING):
            rec.left = 1
            rec.due_at = datetime.now(UTC) + timedelta(minutes=5)
    db.update_collocation(item)
    cid = db.get_collocation_id_by_guid(item.guid)
    assert cid is not None
    return cid


async def _grade(cid: int, rating: str = "good", *, lesson_review: bool = False) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/srs/items/{cid}/direction/recognition/feedback",
            json={"rating": rating, "lesson_review": lesson_review},
        )
    assert resp.status_code == 200, resp.text


def _kinds(db: SRSDatabase, cid: int) -> list[tuple[int, int]]:
    with db._get_conn() as conn:
        return [
            (r["review_kind"], r["budget_neutral"])
            for r in conn.execute(
                "SELECT review_kind, budget_neutral FROM tt_revlog WHERE collocation_id = ? ORDER BY id", (cid,)
            )
        ]


class TestNotDueReviewIsReviewAhead:
    """D1, with its control."""

    async def test_not_due_review_logs_kind_3_and_is_not_charged(self, db):
        cid = _seed(db, due_in_days=2)
        before = db.count_reviews_completed_today(anki_today())

        await _grade(cid)

        assert _kinds(db, cid) == [(3, 0)]
        assert db.count_reviews_completed_today(anki_today()) == before

    async def test_control_due_review_logs_kind_1_and_is_charged(self, db):
        cid = _seed(db, due_in_days=None)
        before = db.count_reviews_completed_today(anki_today())

        await _grade(cid)

        assert _kinds(db, cid) == [(1, 0)]
        assert db.count_reviews_completed_today(anki_today()) == before + 1

    async def test_not_due_again_is_kind_3_too(self, db):
        """A lapse taken early is still review-ahead in Anki; D5 pins what the push makes of it."""
        cid = _seed(db, due_in_days=2)

        await _grade(cid, "again")

        assert _kinds(db, cid) == [(3, 0)]
        assert db.get_collocation("hus").directions[REC].state == SRSState.RELEARNING


class TestDirectionRowUnchanged:
    """D2: only the revlog kind moves. The direction row is what ``schedule`` makes at the grade's time."""

    async def test_direction_row_equals_schedule_at_the_grade_time(self, db):
        cid = _seed(db, due_in_days=2)
        twin = SRSDatabase(":memory:")
        try:
            twin_cid = _seed(twin, due_in_days=2)
            prev_item = db.get_collocation("hus")
            # Same pre-grade state in both, so the replay starts where the route started.
            twin.update_direction_by_id(twin_cid, REC, prev_item.directions[REC])
            prev_item = twin.get_collocation("hus")

            await _grade(cid)

            graded_at = db.get_collocation("hus").directions[REC].last_review
            assert graded_at is not None
            col_crt = resolve_col_crt(db)
            expected = schedule(
                prev_item,
                Rating.GOOD,
                direction=REC,
                params=resolve_fsrs_params(db)[0],
                time_ms=0,  # DrillRequest's default, which _grade sends
                now=graded_at,
                col_crt=col_crt,
                load_balancer=build_live_load_balancer(db, now=graded_at, col_crt=col_crt),
                learn_steps=resolve_learning_steps(db)[0],
                relearn_steps=resolve_relearning_steps(db)[0],
            )
            twin.update_direction_by_id(twin_cid, REC, expected.directions[REC])

            def row(d: SRSDatabase) -> dict:
                with d._get_conn() as conn:
                    r = conn.execute("SELECT * FROM collocation_directions WHERE direction = 'recognition'").fetchone()
                return {k: v for k, v in dict(r).items() if k not in ("id", "collocation_id")}

            assert row(db) == row(twin)
        finally:
            twin.close()


class TestOtherStatesUnchanged:
    """D3: regression guards, green before and after. Only REVIEW cards can be ahead."""

    @pytest.mark.parametrize(
        ("state", "kind"),
        [(SRSState.LEARNING, 0), (SRSState.RELEARNING, 2), (SRSState.NEW, 0)],
    )
    async def test_kind_is_the_state_kind(self, db, state, kind):
        cid = _seed(db, state=state)

        await _grade(cid)

        assert [k for k, _ in _kinds(db, cid)] == [kind]


class TestLessonReRegrade:
    """D4: a "Check your work" re-grade of a card already charged today stays budget-neutral,
    and its kind is decided by dueness, not by having been graded today."""

    async def test_regrade_of_a_card_graded_today_is_neutral_and_review_ahead(self, db):
        cid = _seed(db, due_in_days=None, last_review=datetime.now(UTC) - timedelta(days=5))
        await _grade(cid)  # the day's counting review: due today, kind 1, charged
        assert _kinds(db, cid) == [(1, 0)]
        charged = db.count_reviews_completed_today(anki_today())

        # The grade moved it out by days, so it is not due any more.
        await _grade(cid, lesson_review=True)

        assert _kinds(db, cid) == [(1, 0), (3, 1)]
        assert db.count_reviews_completed_today(anki_today()) == charged

    async def test_control_regrade_without_a_charged_review_is_charged_by_dueness(self, db):
        cid = _seed(db, due_in_days=None)

        await _grade(cid, lesson_review=True)

        assert _kinds(db, cid) == [(1, 0)]
