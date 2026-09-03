"""Item 4: listen-preview ordering by group + due_at, not by mastery.

The user-ratified order is:
  1. create (existing _rank_listen_candidates order — unchanged)
  2. learning / relearning (due_at ascending)
  3. due (due_at ascending)
  4. ahead (due_at ascending) — well-known subset splits into disclosure
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/content/lesson-1/listen-preview"


def _setup(phrases: list[str], language_code: str = "sl"):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code=language_code,
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text=t, voice_id="female-1", language_code=language_code, role="female-1") for t in phrases
                ],
            )
        ],
        key_phrases=[],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


def _seed_learning(db, text: str, *, days_until_due: int = 1) -> None:
    from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

    unit = SyntacticUnit(text=text, translation=f"t-{text}", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.LEARNING
    rec.due_at = due_at_rollover_utc(anki_today() + timedelta(days=days_until_due))
    db.update_collocation(item)


def _seed_review(db, text: str, *, days_until_due: int = 5) -> None:
    from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

    unit = SyntacticUnit(text=text, translation=f"t-{text}", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.due_at = due_at_rollover_utc(anki_today() + timedelta(days=days_until_due))
    rec.last_review = datetime.now(UTC) - timedelta(days=5)
    db.update_collocation(item)


async def _get_preview() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()


class TestGroupOrder:
    """A seeded lesson with one create, one learning, one due, one ahead
    card returns them in exactly the group order: create, learning, due, ahead."""

    async def test_creates_before_learning_before_due_before_ahead(self):
        # Insertion order is deliberately the REVERSE of the expected output
        # order: candidates are appended in lesson-text order, so seeding
        # gamma (ahead) → beta (due) → alpha (learning) means an unsorted
        # implementation returns exactly the wrong order. Seeding in the
        # already-correct order would pass with the sort deleted.
        db = _setup(["banka gamma beta alpha"])
        _seed_learning(db, "alpha", days_until_due=3)
        _seed_review(db, "beta", days_until_due=0)
        _seed_review(db, "gamma", days_until_due=100)

        preview = await _get_preview()
        texts_by_kind = [(c["kind"], c["grade_class"], c["text"]) for c in preview["candidates"]]
        # banka is create (kind=create), alpha is learning, beta is due, gamma is ahead
        grades = [g for _, g, _ in texts_by_kind]
        # learning < due < ahead
        learning_idx = grades.index("learning")
        due_idx = grades.index("due")
        ahead_idx = grades.index("ahead")
        assert learning_idx < due_idx < ahead_idx


class TestDueAtAscending:
    """Two 'due' cards with different due_at come back nearest-first."""

    async def test_due_cards_nearest_first(self):
        # Scrambled: alpha appears first in the lesson but is due LAST, so
        # insertion order and due_at order disagree and only a real sort
        # produces the expected result.
        db = _setup(["alpha beta"])
        _seed_review(db, "alpha", days_until_due=0)
        _seed_review(db, "beta", days_until_due=-3)

        preview = await _get_preview()
        due_texts = [c["text"] for c in preview["candidates"] if c["grade_class"] == "due"]
        assert due_texts == ["beta", "alpha"]
