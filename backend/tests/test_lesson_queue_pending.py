"""Stage 3: the "Check your work" queue serves cards with a pending listen grade.

Without this the model has a hole. A listen stages *ahead* cards too (not due,
and — since staging no longer stamps ``last_review`` — not "touched today"
either), so the lesson queue's dueness test would drop them. They would then be
invisible in Check-your-work AND hidden from the main queue by the Stage-3
exclusion: unreachable until the user hit "Sync it". A pending row is itself the
reason to serve the card.

Each served item carries its provisional rating so the UI can pre-fill the grade
the listen staged, which is what makes correcting one a single tap.

As of 2026-07-27 a pending row is the ONLY reason to serve a card — see
``TestOnlyAutogradedCardsAreServed`` for the scope narrowing and why. Two tests
here were retired by it, both of which seeded a card with no pending row for
this lesson and asserted it was served anyway:
``test_non_pending_items_report_no_provisional_rating`` (a due card, null
rating) and ``test_a_due_card_staged_by_another_lesson_reports_no_rating`` (a
due card owned by another lesson). Neither premise exists now — such cards are
not served at all, which
``TestOnlyAutogradedCardsAreServed.test_a_due_vocab_card_without_a_pending_row_is_not_served``
and ``TestPendingRowsAreLessonScoped.test_a_card_staged_by_another_lesson_is_not_served``
assert directly. The "rating is this lesson's to show" property they guarded is
now structural: the rating comes from the same row that admits the card.
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
        resp = await client.get(f"/api/srs/content/{LESSON_ID}/review-queue")
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

    async def test_queue_is_exactly_this_lessons_pending_set(self):
        """The whole contract in one assertion: served keys == pending keys."""
        db = _setup(["banka"])
        cid = _track_ahead(db, "banka")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "ahead")

        served = {(i["id"], i["direction"]) for i in (await _queue())["queue"]}
        expected = {(p["collocation_id"], p["direction"]) for p in db.get_pending_grades(LESSON_ID)}

        assert served == expected

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
            resp = await client.post(f"/api/srs/content/{LESSON_ID}/commit-pending")
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


class TestOnlyAutogradedCardsAreServed:
    """ "Check your work" is exactly the autograde-verification pass — nothing else.

    Decided 2026-07-27. The endpoint used to be a lesson-scoped *study* queue
    (D6 buckets: learning, tracked NEW in D2 rank order, REVIEW touched-today or
    due). After the confirmed/staged split that scope produced two visible
    wrongs on day-4: the two words the user confirmed in the preview came back
    (applied immediately, so no pending row, but admitted by "touched today" —
    the exact double-question the split set out to remove), and two due *cloze*
    cards appeared that a listen can never autograde, because staging is
    RECOGNITION-only and clozes are production-only.

    Everything dropped here is still reachable from the main review queue: with
    no pending row, the Layer 81 exclusion does not hold it back.
    """

    def _track(self, db, text: str, *, card_type: str = "vocab") -> int:
        db.add_collocation(
            SyntacticUnit(text=text, translation="x", word_count=1, difficulty=1, source="llm", card_type=card_type),
            language_code="sl",
        )
        item = db.get_collocation(text)
        cid = db.get_collocation_id_by_guid(item.guid)
        assert cid is not None
        return cid

    def _set(self, db, text: str, direction: Direction, state: SRSState, *, due_at=None, last_review=None) -> None:
        item = db.get_collocation(text)
        ds = item.directions[direction]
        ds.state = state
        if due_at is not None:
            ds.due_at = due_at
        if last_review is not None:
            ds.last_review = last_review
        db.update_direction(item.guid, direction, ds)

    async def test_a_review_card_graded_at_listen_time_is_not_re_served(self):
        """The om/vite case: confirmed in the preview, applied immediately, so it
        holds no pending row. "Touched today" used to re-admit it."""
        db = _setup(["banka"])
        self._track(db, "banka")
        db.record_listen(LESSON_ID)
        self._set(
            db,
            "banka",
            Direction.RECOGNITION,
            SRSState.REVIEW,
            due_at=datetime.now(UTC) + timedelta(days=3),
            last_review=datetime.now(UTC),
        )

        assert [i["text"] for i in (await _queue())["queue"]] == []

    async def test_a_due_cloze_is_not_served(self):
        """The noe/fra case. A listen stages RECOGNITION only and skips cloze
        rows outright, so a due cloze can never carry a pending grade — serving
        it here asked for production work the listen never assessed."""
        db = _setup(["banka"])
        self._track(db, "banka", card_type="cloze")
        self._set(db, "banka", Direction.PRODUCTION, SRSState.REVIEW, due_at=datetime.now(UTC) - timedelta(days=1))

        assert [i["text"] for i in (await _queue())["queue"]] == []

    async def test_a_due_vocab_card_without_a_pending_row_is_not_served(self):
        db = _setup(["banka"])
        self._track(db, "banka")
        self._set(db, "banka", Direction.RECOGNITION, SRSState.REVIEW, due_at=datetime.now(UTC) - timedelta(days=1))

        assert [i["text"] for i in (await _queue())["queue"]] == []

    async def test_a_learning_card_without_a_pending_row_is_not_served(self):
        db = _setup(["banka"])
        self._track(db, "banka")
        self._set(db, "banka", Direction.RECOGNITION, SRSState.LEARNING, due_at=datetime.now(UTC))

        assert [i["text"] for i in (await _queue())["queue"]] == []

    async def test_a_new_card_is_not_served(self):
        """NEW cards can never be staged (_listen_grade_class returns None for
        them), so under pending-only scope the D2 tap-to-introduce bucket goes."""
        db = _setup(["banka"])
        self._track(db, "banka")

        assert [i["text"] for i in (await _queue())["queue"]] == []

    async def test_a_pending_learning_card_sorts_before_a_pending_review_card(self):
        db = _setup(["banka", "hisa"])
        learning_id = self._track(db, "banka")
        review_id = self._track(db, "hisa")
        self._set(db, "banka", Direction.RECOGNITION, SRSState.LEARNING, due_at=datetime.now(UTC))
        self._set(db, "hisa", Direction.RECOGNITION, SRSState.REVIEW, due_at=datetime.now(UTC) - timedelta(days=1))
        db.stage_pending_grade(LESSON_ID, learning_id, Direction.RECOGNITION.value, "again", "learning")
        db.stage_pending_grade(LESSON_ID, review_id, Direction.RECOGNITION.value, "good", "due")

        assert [i["text"] for i in (await _queue())["queue"]] == ["banka", "hisa"]

    async def test_a_pending_row_for_a_deleted_card_is_skipped(self):
        """commit-pending tolerates orphans; the queue must not 500 on them."""
        db = _setup(["banka"])
        cid = self._track(db, "banka")
        db.stage_pending_grade(LESSON_ID, cid, Direction.RECOGNITION.value, "good", "due")
        db.delete_collocation(cid)

        assert (await _queue())["queue"] == []
