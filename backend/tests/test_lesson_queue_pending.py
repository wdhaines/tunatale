"""Stage 3: the "Check your work" queue serves cards with a pending listen grade.

Without this the model has a hole. A listen stages *ahead* cards too (not due,
and — since staging no longer stamps ``last_review`` — not "touched today"
either), so the lesson queue's dueness test would drop them. They would then be
invisible in Check-your-work AND hidden from the main queue by the Stage-3
exclusion: unreachable until the user hit "Sync it". A pending row is itself the
reason to serve the card.

Each served item carries its provisional rating so the UI can pre-fill the grade
the listen staged, which is what makes correcting one a single tap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

LESSON_ID = "lesson-1"


def _setup(phrases: list[str]):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=t, voice_id="female-1", language_code="sl", role="female-1") for t in phrases],
            )
        ],
        key_phrases=[],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson(LESSON_ID, "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    db.set_anki_state_cache("daily_new_cap", "0")
    return db


def _track_ahead(db, text: str) -> int:
    """A REVIEW card due 5 days out: neither due nor touched today."""
    db.add_collocation(
        SyntacticUnit(text=text, translation="x", word_count=1, difficulty=1, source="llm"),
        language_code="sl",
    )
    item = db.get_collocation(text)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.due_at = datetime.now(UTC) + timedelta(days=5)
    rec.last_review = datetime.now(UTC) - timedelta(days=10)
    rec.reps = 5
    db.update_direction(item.guid, Direction.RECOGNITION, rec)
    cid = db.get_collocation_id_by_guid(item.guid)
    assert cid is not None
    return cid


async def _queue() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/srs/lesson/{LESSON_ID}/review-queue")
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestPendingCardsAreServed:
    async def test_an_ahead_card_is_not_served_without_a_pending_row(self, monkeypatch):
        """The baseline this feature changes — pin it so the new inclusion is
        demonstrably caused by the pending row and not by the seeding."""
        db = _setup(["banka"])
        _track_ahead(db, "banka")

        assert [i["text"] for i in (await _queue())["queue"]] == []

    async def test_a_pending_ahead_card_is_served(self):
        db = _setup(["banka"])
        cid = _track_ahead(db, "banka")

        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "ahead")

        assert [i["text"] for i in (await _queue())["queue"]] == ["banka"]

    async def test_the_provisional_rating_is_surfaced(self):
        db = _setup(["banka"])
        cid = _track_ahead(db, "banka")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "again", "ahead")

        item = (await _queue())["queue"][0]

        assert item["pending_rating"] == "again"

    async def test_non_pending_items_report_no_provisional_rating(self):
        db = _setup(["banka"])
        db.add_collocation(
            SyntacticUnit(text="banka", translation="x", word_count=1, difficulty=1, source="llm"),
            language_code="sl",
        )
        item = db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.due_at = datetime.now(UTC) - timedelta(days=1)
        db.update_direction(item.guid, Direction.RECOGNITION, rec)

        served = (await _queue())["queue"]

        assert [i["text"] for i in served] == ["banka"]
        assert served[0]["pending_rating"] is None

    async def test_a_due_card_staged_by_another_lesson_reports_no_rating(self):
        """A card this lesson would serve anyway (genuinely due) still shows no
        provisional rating when the pending row belongs elsewhere — that rating
        is the other lesson's bucket to display.

        Supersedes ``test_only_this_lessons_pending_rows_surface_a_rating``,
        which seeded an *ahead* card and asserted it was served regardless of
        owner. That inclusion was the cross-lesson leak; see
        ``TestPendingRowsAreLessonScoped``. The rating half of the old
        assertion is preserved here on a card whose servability is independent
        of the pending row.
        """
        db = _setup(["banka"])
        cid = _track_ahead(db, "banka")
        item = db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.due_at = datetime.now(UTC) - timedelta(days=1)
        db.update_direction(item.guid, Direction.RECOGNITION, rec)
        db.stage_pending_grade("lesson-2", cid, Direction.RECOGNITION.value, "hard", "ahead")

        served = (await _queue())["queue"]

        assert [i["text"] for i in served] == ["banka"]
        assert served[0]["pending_rating"] is None

    async def test_grading_a_pending_card_drops_it_from_the_queue(self):
        """Release clears the pending row and stamps last_review past the arming
        listen, so the correction pass does not re-serve it forever."""
        db = _setup(["banka"])
        cid = _track_ahead(db, "banka")
        db.record_listen(LESSON_ID)
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "ahead")
        assert [i["text"] for i in (await _queue())["queue"]] == ["banka"]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/srs/items/{cid}/direction/recognition/feedback",
                json={"rating": "good", "lesson_review": True},
            )
        assert resp.status_code == 200

        assert db.get_pending_grade(cid, Direction.RECOGNITION.value) is None
        assert [i["text"] for i in (await _queue())["queue"]] == []


class TestPendingRowsAreLessonScoped:
    """A pending row belongs to the lesson that staged it — and only that lesson serves it.

    Cross-lesson leak found 2026-07-27. ``pending_listen_grades`` is keyed
    ``UNIQUE(collocation_id, direction)`` — globally, not per lesson — and the
    queue's admission check asked ``get_pending_grade(rid, direction)``, i.e.
    "is this staged by *anybody*". Two Norwegian lessons sharing common
    vocabulary put 33 of day-4's 102 staged words into day-1's "Check your
    work", which reported 39 words to review for a listen that staged nothing.

    Ownership is already well-defined on the write side: ``stage_pending_grade``
    UPSERTs ``lesson_id = excluded.lesson_id``, so the most recent listen to
    auto-grade a card owns its pending row. These tests pin the read to agree.
    """

    async def test_a_card_staged_by_another_lesson_is_not_served(self):
        db = _setup(["banka"])
        cid = _track_ahead(db, "banka")

        db.stage_pending_grade("lesson-2", cid, Direction.RECOGNITION.value, "good", "ahead")

        assert [i["text"] for i in (await _queue())["queue"]] == []

    async def test_restaging_from_this_lesson_takes_ownership_and_serves_it(self):
        """The other half of the rule: once THIS lesson's listen auto-grades the
        card, the UPSERT moves the row here and the queue serves it again."""
        db = _setup(["banka"])
        cid = _track_ahead(db, "banka")
        db.stage_pending_grade("lesson-2", cid, Direction.RECOGNITION.value, "good", "ahead")

        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "hard", "ahead")

        served = (await _queue())["queue"]
        assert [i["text"] for i in served] == ["banka"]
        assert served[0]["pending_rating"] == "hard"

    async def test_sync_it_releases_exactly_what_the_queue_serves(self):
        """The invariant the leak broke. ``commit-pending`` iterates
        ``get_pending_grades(lesson_id)`` — lesson-scoped — while queue
        membership was not, so the page offered "Sync it" for cards that button
        could never release: a no-op on a queue claiming 39 words."""
        db = _setup(["banka"])
        cid = _track_ahead(db, "banka")
        db.stage_pending_grade("lesson-2", cid, Direction.RECOGNITION.value, "good", "ahead")

        served = (await _queue())["queue"]
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/api/srs/lesson/{LESSON_ID}/commit-pending")
        assert resp.status_code == 200, resp.text

        assert resp.json()["applied"] == 0
        assert len(served) == 0

    async def test_grading_a_pending_card_drops_it_from_the_queue(self):
        """Release clears the pending row and stamps last_review past the arming
        listen, so the correction pass does not re-serve it forever."""
        db = _setup(["banka"])
        cid = _track_ahead(db, "banka")
        db.record_listen(LESSON_ID)
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "ahead")
        assert [i["text"] for i in (await _queue())["queue"]] == ["banka"]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/srs/items/{cid}/direction/recognition/feedback",
                json={"rating": "good", "lesson_review": True},
            )
        assert resp.status_code == 200

        assert db.get_pending_grade(cid, Direction.RECOGNITION.value) is None
        assert [i["text"] for i in (await _queue())["queue"]] == []
