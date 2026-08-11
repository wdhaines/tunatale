"""Stage 3: POST /lesson/{id}/commit-pending — the bulk "Sync it" release.

The escape hatch from reviewing card-by-card: apply every remaining pending row
for a lesson at its provisional rating, in one shot, through the same grade path
a per-card release uses. After this the grades are ordinary dirty grades and the
next normal "Sync to AnkiWeb" pushes them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

LESSON_ID = "lesson-1"


@pytest.fixture
def db() -> SRSDatabase:
    from app.storage.store import ContentStore

    d = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    lesson = Lesson(
        title="Day 1",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
            )
        ],
        key_phrases=[],
    )
    store.save_lesson(LESSON_ID, "curriculum-1", 1, lesson)
    app.state.srs_db = d
    app.state.content_store = store
    try:
        yield d
    finally:
        d.close()


def _seed(db: SRSDatabase, text: str, *, ahead: bool = False) -> int:
    unit = SyntacticUnit(text=text, translation="t", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    assert item is not None
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.last_review = datetime.now(UTC) - timedelta(days=10)
    rec.due_at = datetime.now(UTC) + timedelta(days=5) if ahead else due_at_rollover_utc(anki_today())
    rec.reps = 5
    db.update_collocation(item)
    cid = db.get_collocation_id_by_guid(item.guid)
    assert cid is not None
    return cid


async def _commit(lesson_id: str = LESSON_ID) -> tuple[int, dict]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/srs/lesson/{lesson_id}/commit-pending")
    return resp.status_code, (resp.json() if resp.content else {})


def _revlog(db: SRSDatabase, cid: int) -> list:
    with db._get_conn() as conn:
        return conn.execute(
            "SELECT id, review_kind, button_chosen FROM tt_revlog WHERE collocation_id = ? ORDER BY id",
            (cid,),
        ).fetchall()


class TestCommitPending:
    async def test_applies_every_pending_row_and_reports_the_count(self, db):
        first = _seed(db, "banka")
        second = _seed(db, "center")
        db.stage_pending_grade(LESSON_ID, first, Direction.RECOGNITION.value, "good", "due")
        db.stage_pending_grade(LESSON_ID, second, Direction.RECOGNITION.value, "good", "due")

        status, data = await _commit()

        assert status == 200
        assert data["applied"] == 2
        assert db.get_pending_grades(LESSON_ID) == []
        assert len(_revlog(db, first)) == 1
        assert len(_revlog(db, second)) == 1

    async def test_applies_the_provisional_rating_not_a_blanket_good(self, db):
        cid = _seed(db, "banka")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "again", "due")

        await _commit()

        assert _revlog(db, cid)[0]["button_chosen"] == 1
        assert db.get_collocation("banka").directions[Direction.RECOGNITION].state == SRSState.RELEARNING

    async def test_released_grades_are_dirty_for_the_next_sync(self, db):
        cid = _seed(db, "banka")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")

        await _commit()

        assert db.get_collocation("banka").directions[Direction.RECOGNITION].dirty_fsrs is True
        assert db.count_pending_grades(LESSON_ID) == 0

    async def test_an_ahead_card_records_kind_3(self, db):
        cid = _seed(db, "banka", ahead=True)
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "ahead")

        await _commit()

        assert _revlog(db, cid)[0]["review_kind"] == 3

    async def test_other_lessons_pending_rows_are_untouched(self, db):
        mine = _seed(db, "banka")
        theirs = _seed(db, "center")
        db.stage_pending_grade(LESSON_ID, mine, Direction.RECOGNITION.value, "good", "due")
        db.stage_pending_grade("lesson-2", theirs, Direction.RECOGNITION.value, "good", "due")

        status, data = await _commit()

        assert status == 200
        assert data["applied"] == 1
        assert db.get_pending_grade(theirs, Direction.RECOGNITION.value) is not None
        assert _revlog(db, theirs) == []

    async def test_nothing_pending_is_a_no_op(self, db):
        _seed(db, "banka")

        status, data = await _commit()

        assert status == 200
        assert data["applied"] == 0

    async def test_unknown_lesson_is_404(self, db):
        status, _ = await _commit("no-such-lesson")

        assert status == 404

    async def test_every_applied_row_survives_a_same_millisecond_batch(self, db):
        """tt_revlog.id is a millisecond-epoch PK and append_revlog is INSERT OR
        IGNORE, so a bulk release that stamps two grades in the same millisecond
        would silently drop the second — losing FSRS replay history and
        under-counting the day's reviews. This is what _bump_grade_clock guards.
        """
        cids = [_seed(db, text) for text in ("banka", "center", "hotel", "kava", "mesto")]
        for cid in cids:
            db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")

        status, data = await _commit()

        assert status == 200
        assert data["applied"] == 5
        with db._get_conn() as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM tt_revlog ORDER BY id").fetchall()]
        assert len(ids) == 5, "a dropped row means two grades collided on the same millisecond id"
        assert len(set(ids)) == 5

    async def test_a_row_orphaned_by_a_deleted_card_is_dropped_not_applied(self, db):
        """pending_listen_grades has no FK to collocations, so deleting a card
        after a listen leaves its pending row behind. Committing must discard it
        rather than crash — and must not leave it to linger forever."""
        alive = _seed(db, "banka")
        doomed = _seed(db, "center")
        db.stage_pending_grade(LESSON_ID, alive, Direction.RECOGNITION.value, "good", "due")
        db.stage_pending_grade(LESSON_ID, doomed, Direction.RECOGNITION.value, "good", "due")
        db.delete_collocation(doomed)

        status, data = await _commit()

        assert status == 200
        assert data["applied"] == 1
        assert db.get_pending_grades(LESSON_ID) == []
        assert db.get_pending_grade(doomed, Direction.RECOGNITION.value) is None

    async def test_a_committed_card_can_still_be_regraded_afterwards(self, db):
        """The post-"Sync it" tail: re-reviewing a released card is an ordinary
        re-grade that APPENDS a second revlog row (never an overwrite), and the
        existing budget_neutral guard keeps it from charging the day twice.
        """
        cid = _seed(db, "banka")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")
        await _commit()
        completed_after_commit = db.count_reviews_completed_today(anki_today())

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/srs/items/{cid}/direction/recognition/feedback",
                json={"rating": "again", "lesson_review": True},
            )
        assert resp.status_code == 200

        assert len(_revlog(db, cid)) == 2
        assert db.count_reviews_completed_today(anki_today()) == completed_after_commit
