"""B1: the listen-preview must mirror mark_lesson_listened's ranked, budget-
truncated creation list — not emit every untracked lemma unconditionally.

Kept in a separate file from `test_api_listen_preview.py` on purpose: that
file's own docstring pins it as an untouched Stage-4 guard-test contract
("Do NOT edit these tests — git diff on this file must be empty at
delivery. Add separate tests for any extra coverage you need."). These tests
exercise the NEW budget/ranking behavior this task adds on top of that
contract.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/lesson/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"


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


def _seed_review_due(db, text: str, *, days_overdue: int = 1) -> None:
    from datetime import UTC, datetime, timedelta

    from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

    unit = SyntacticUnit(text=text, translation=f"t-{text}", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.last_review = datetime.now(UTC) - timedelta(days=5)
    rec.due_at = due_at_rollover_utc(anki_today() - timedelta(days=days_overdue))
    rec.reps = 5
    db.update_collocation(item)


async def _get_preview() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()


async def _post_listen(payload: dict) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(LISTEN_URL, json=payload)
    assert resp.status_code == 200
    return resp.json()


class TestPreviewMatchesListenCreation:
    async def test_preview_create_set_equals_actual_listen_creations(self):
        """The point of B1: what the preview SHOWS as 'create' must be exactly
        what a listen against the same state WOULD create — not every
        untracked lemma in the lesson."""
        lemmas = ["banka", "center", "hotel", "kava", "mesto"]
        db = _setup([" ".join(lemmas)])
        db.set_anki_state_cache("daily_new_cap", "2")

        preview = await _get_preview()
        preview_creates = {c["text"] for c in preview["candidates"] if c["kind"] == "create"}

        listen = await _post_listen({"lesson_id": "lesson-1"})
        assert listen["created"] == 2

        created_texts = {lemma for lemma in lemmas if db.get_collocation_by_lemma(lemma) is not None}
        assert preview_creates == created_texts
        assert len(preview_creates) == 2


class TestPreviewBudgetTruncation:
    async def test_truncates_to_budget(self):
        lemmas = ["banka", "center", "hotel", "kava", "mesto"]
        db = _setup([" ".join(lemmas)])
        db.set_anki_state_cache("daily_new_cap", "3")

        preview = await _get_preview()
        creates = [c for c in preview["candidates"] if c["kind"] == "create"]
        assert len(creates) == 3


class TestPreviewBudgetExhausted:
    async def test_zero_budget_yields_zero_creates_but_keeps_tracked(self):
        db = _setup(["banka riba"])
        _seed_review_due(db, "riba")
        db.set_anki_state_cache("daily_new_cap", "0")

        preview = await _get_preview()
        creates = [c for c in preview["candidates"] if c["kind"] == "create"]
        tracked = [c for c in preview["candidates"] if c["kind"] != "create"]
        assert creates == []
        assert any(c["text"] == "riba" for c in tracked)

    async def test_same_day_relisten_state_yields_zero_creates(self):
        """Same-day-re-listen case: count_new_created_today already at cap."""
        lemmas = ["banka", "center", "hotel"]
        db = _setup([" ".join(lemmas)])
        db.set_anki_state_cache("daily_new_cap", "1")

        first = await _post_listen({"lesson_id": "lesson-1"})
        assert first["created"] == 1

        preview = await _get_preview()
        creates = [c for c in preview["candidates"] if c["kind"] == "create"]
        assert creates == [], "budget already spent today by the first listen's creation"


class TestPreviewCreateOrder:
    async def test_create_rows_are_in_rank_order_not_analysis_order(self):
        """occurrences: hotel=1, kava=2, banka=3 (first-appearance order is
        hotel, kava, banka). Ranked (occurrence desc) order is banka, kava,
        hotel. Budget=2 must keep the two HIGHEST-occurrence lemmas, in ranked
        order — not the first two lemmas encountered by the lemmatizer."""
        db = _setup(["hotel kava banka kava banka banka"])
        db.set_anki_state_cache("daily_new_cap", "2")

        preview = await _get_preview()
        creates = [c["text"] for c in preview["candidates"] if c["kind"] == "create"]
        assert creates == ["banka", "kava"], f"expected ranked order, got {creates}"
