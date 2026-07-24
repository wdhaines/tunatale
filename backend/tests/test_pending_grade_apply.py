"""Stage 3: reviewing a card RELEASES its pending listen grade.

A pending row is provisional. Grading the card for real — from "Check your work"
or from the main queue — applies through the ordinary grade path (schedule →
revlog → dirty_fsrs, so a normal sync pushes it) and clears the pending row, which
returns the card to the review pool it was hidden from.

The clear is unconditional on any real grade, not just the Check-your-work path:
a stale pending row must never outlive a genuine grade, or the card stays hidden
from the main queue forever.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

LESSON_ID = "lesson-1"


@pytest.fixture
def db() -> SRSDatabase:
    d = SRSDatabase(":memory:")
    app.state.srs_db = d
    try:
        yield d
    finally:
        d.close()


def _seed(db: SRSDatabase, text: str = "banka", *, ahead: bool = False) -> int:
    """A tracked REVIEW card, due today (or 5 days out when *ahead*)."""
    unit = SyntacticUnit(text=text, translation="t", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    assert item is not None
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.last_review = datetime.now(UTC) - timedelta(days=10)
    # 04:00-UTC convention for the due case; see count_review_due_collocations.
    rec.due_at = datetime.now(UTC) + timedelta(days=5) if ahead else due_at_rollover_utc(anki_today())
    rec.reps = 5
    db.update_collocation(item)
    cid = db.get_collocation_id_by_guid(item.guid)
    assert cid is not None
    return cid


async def _grade(cid: int, rating: str = "good", *, lesson_review: bool = True) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/srs/items/{cid}/direction/recognition/feedback",
            json={"rating": rating, "lesson_review": lesson_review},
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _revlog(db: SRSDatabase, cid: int) -> list:
    with db._get_conn() as conn:
        return conn.execute(
            "SELECT review_kind, button_chosen FROM tt_revlog WHERE collocation_id = ? ORDER BY id",
            (cid,),
        ).fetchall()


class TestApplyReleasesPending:
    async def test_grading_clears_the_pending_row(self, db):
        cid = _seed(db)
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")

        await _grade(cid)

        assert db.get_pending_grade(cid, Direction.RECOGNITION.value) is None

    async def test_grading_applies_through_the_normal_path(self, db):
        cid = _seed(db)
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")

        await _grade(cid)

        rows = _revlog(db, cid)
        assert len(rows) == 1
        rec = db.get_collocation("banka").directions[Direction.RECOGNITION]
        assert rec.reps == 6
        assert rec.dirty_fsrs is True, "a released grade must be dirty so a normal sync pushes it"

    async def test_released_card_returns_to_the_review_pool(self, db):
        cid = _seed(db, ahead=True)
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "ahead")
        assert db.count_review_due_collocations(anki_today()) == 0

        await _grade(cid)

        assert db.get_pending_grade(cid, Direction.RECOGNITION.value) is None
        assert db.pending_grade_ids() == set()

    async def test_the_corrected_rating_wins_not_the_provisional_one(self, db):
        """The provisional Good was never real, so an Again here is just a first
        grade with the right value — not an overwrite of a Good."""
        cid = _seed(db)
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")

        await _grade(cid, "again")

        rows = _revlog(db, cid)
        assert len(rows) == 1, "exactly one revlog row — nothing to overwrite or delete"
        assert rows[0]["button_chosen"] == 1
        assert db.get_collocation("banka").directions[Direction.RECOGNITION].state == SRSState.RELEARNING


class TestReviewKindOnRelease:
    async def test_releasing_an_ahead_card_records_kind_3(self, db):
        cid = _seed(db, ahead=True)
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "ahead")

        await _grade(cid)

        assert _revlog(db, cid)[0]["review_kind"] == 3

    async def test_releasing_a_due_card_records_the_ordinary_kind(self, db):
        cid = _seed(db)
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")

        await _grade(cid)

        assert _revlog(db, cid)[0]["review_kind"] == 1

    async def test_class_is_recomputed_at_apply_time_not_read_off_the_row(self, db):
        """Dueness can shift between staging and release (a sync pulls a new
        due_at, or the day rolls over). The stored grade_class is a record of
        what was true at stage time, never the input to the revlog kind."""
        cid = _seed(db, ahead=True)
        # Deliberately stale: staged as "due", but the card is not due now.
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")

        await _grade(cid)

        assert _revlog(db, cid)[0]["review_kind"] == 3

    async def test_a_grade_with_no_pending_row_is_untouched(self, db):
        """Main-queue grading of a not-due card keeps its existing kind — the
        kind-3 override belongs to the release path only."""
        cid = _seed(db, ahead=True)

        await _grade(cid, lesson_review=False)

        assert _revlog(db, cid)[0]["review_kind"] != 3


class TestStalePendingRowsCannotSurviveARealGrade:
    async def test_a_main_queue_grade_also_clears_pending(self, db):
        """Not just the Check-your-work path — otherwise a card graded in the
        main queue keeps its pending row and stays hidden from that queue."""
        cid = _seed(db)
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")

        await _grade(cid, lesson_review=False)

        assert db.get_pending_grade(cid, Direction.RECOGNITION.value) is None
        assert db.count_review_due_collocations(anki_today()) == 0  # graded today, so buried

    async def test_clearing_is_direction_scoped(self, db):
        cid = _seed(db)
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")
        db.stage_pending_grade(LESSON_ID, cid, Direction.PRODUCTION.value, "good", "due")

        await _grade(cid)

        assert db.get_pending_grade(cid, Direction.RECOGNITION.value) is None
        assert db.get_pending_grade(cid, Direction.PRODUCTION.value) is not None
