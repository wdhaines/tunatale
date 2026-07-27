"""A listen is THE current assessment of its lesson, not an addition to the last one.

Regression 2026-07-27. Nothing in the listen path ever *removed* a pending row:
``"skip"`` is a bare ``continue`` that neither stages nor clears, and rows only
left the bucket via a release path (per-card grade, "Accept all", or an
Anki-side grade arriving through sync). So the bucket accumulated across
listens. Re-listening to a lesson and skipping every row left the previous
listen's autogrades sitting in "Check your work" — the user had just said "don't
grade these" and the queue still offered 101 of them.

The fix: each listen resets its own lesson's bucket, then stages fresh. Skip ⇒
the row is gone; confirm ⇒ applied, no row; auto-good ⇒ a fresh row.

Consequence, deliberate: a re-listen DISCARDS the previous listen's un-accepted
autogrades rather than leaving them queued. They are provisional by definition,
and the new listen just re-assessed the same words — the newer assessment wins,
the same way ``stage_pending_grade``'s UPSERT already lets the newer listen take
ownership of a row.

The reset is lesson-scoped: another lesson's rows are not this listen's to
discard (see ``test_a_listen_does_not_touch_another_lessons_bucket``).
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


def _setup(phrase_text: str):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=phrase_text, voice_id="female-1", language_code="sl", role="female-1")],
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


def _seed_review_due(db, text: str) -> int:
    from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

    db.add_collocation(
        SyntacticUnit(text=text, translation=f"t-{text}", word_count=1, difficulty=1, source="test"),
        language_code="sl",
    )
    item = db.get_collocation(text)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.last_review = datetime.now(UTC) - timedelta(days=5)
    rec.due_at = due_at_rollover_utc(anki_today() - timedelta(days=1))
    rec.reps = 5
    db.update_collocation(item)
    cid = db.get_collocation_id_by_guid(item.guid)
    assert cid is not None
    return cid


async def _listen(payload: dict) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/srs/listen", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestARelistenSupersedesThePreviousBucket:
    async def test_skipping_everything_empties_the_bucket(self):
        """The reported case: re-listen with every row skipped ⇒ nothing pending."""
        db = _setup("Banka riba")
        _seed_review_due(db, "banka")
        _seed_review_due(db, "riba")

        first = await _listen({"lesson_id": LESSON_ID})
        assert first["staged"] == 2

        await _listen({"lesson_id": LESSON_ID, "word_ratings": {"banka": "skip", "riba": "skip"}})

        assert db.get_pending_grades(LESSON_ID) == []

    async def test_skipping_all_but_one_leaves_exactly_that_one(self):
        db = _setup("Banka riba")
        _seed_review_due(db, "banka")
        _seed_review_due(db, "riba")
        await _listen({"lesson_id": LESSON_ID})

        await _listen({"lesson_id": LESSON_ID, "word_ratings": {"banka": "skip"}})

        pending = db.get_pending_grades(LESSON_ID)
        assert [p["collocation_id"] for p in pending] == [
            db.get_collocation_id_by_guid(db.get_collocation("riba").guid)
        ]

    async def test_a_confirmed_word_leaves_no_row_behind(self):
        """Confirm ⇒ applied now, and nothing queued for re-review."""
        db = _setup("Banka riba")
        _seed_review_due(db, "banka")
        _seed_review_due(db, "riba")
        await _listen({"lesson_id": LESSON_ID})

        result = await _listen(
            {
                "lesson_id": LESSON_ID,
                "word_ratings": {"banka": "easy", "riba": "skip"},
                "confirmed_words": ["banka"],
            }
        )

        assert result["applied"] == 1
        assert db.get_pending_grades(LESSON_ID) == []

    async def test_a_listen_does_not_touch_another_lessons_bucket(self):
        """The reset is this lesson's assessment, not a global wipe."""
        db = _setup("Banka riba")
        cid = _seed_review_due(db, "banka")
        db.stage_pending_grade("lesson-2", cid, Direction.RECOGNITION.value, "hard", "due")

        await _listen({"lesson_id": LESSON_ID, "word_ratings": {"banka": "skip", "riba": "skip"}})

        surviving = db.get_pending_grade(cid, Direction.RECOGNITION.value)
        assert surviving is not None
        assert surviving["lesson_id"] == "lesson-2"
        assert surviving["rating"] == "hard"

    async def test_a_re_listen_still_leaves_exactly_one_row_per_eligible_card(self):
        """The staging contract survives the reset: reset-then-stage, not append."""
        db = _setup("Banka riba")
        _seed_review_due(db, "banka")
        _seed_review_due(db, "riba")

        await _listen({"lesson_id": LESSON_ID})
        second = await _listen({"lesson_id": LESSON_ID})

        assert second["staged"] == 2
        assert len(db.get_pending_grades(LESSON_ID)) == 2
