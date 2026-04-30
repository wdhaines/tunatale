"""Tests for /api/srs/queue-stats endpoint."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.language import Language
from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore


@pytest.fixture(autouse=True)
def _clean_app_state():
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    app.state.srs_db = db
    app.state.content_store = store
    app.state.language = Language.slovene()
    yield
    db.close()
    store.close()
    for attr in ("srs_db", "content_store", "language", "llm"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def _db() -> SRSDatabase:
    return app.state.srs_db


class TestQueueStats:
    async def test_queue_stats_includes_fsrs_source_default(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        assert resp.json()["fsrs_source"] == "default"

    async def test_queue_stats_includes_fsrs_source_cache(self):
        db = _db()
        db.set_anki_state_cache("fsrs_params", json.dumps({"weights": [0.0] * 19, "desired_retention": 0.9}))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        assert resp.json()["fsrs_source"] == "cache"


class TestReviewQueue:
    async def test_returns_empty_queue_when_nothing_due(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        assert resp.json()["queue"] == []

    async def test_buries_new_when_sibling_reviewed_today(self):
        from datetime import date

        db = _db()
        today = date.today()

        # Create a collocation with both directions
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="test_word", translation="test", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")

        rows, _ = db.list_collocations(search="test_word", limit=1)
        row_id, item, _ = rows[0]

        # Set recognition as reviewed today, production as new
        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            last_review=today,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)

        # Production is new and should be buried
        db.set_anki_state_cache("bury_new", "True")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        # Production should be buried since recognition was reviewed today
        prod_in_queue = [q for q in queue if q["direction"] == "production"]
        assert len(prod_in_queue) == 0

    async def test_no_bury_new_when_disabled(self):
        """Test that new cards appear when bury_new=False."""

        db = _db()

        # Create a new collocation
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="test_word5", translation="test5", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")

        # Explicitly disable bury_new
        db.set_anki_state_cache("bury_new", "False")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        # New items should appear since bury_new is disabled
        new_in_queue = [q for q in queue if q["state"] == "new"]
        assert len(new_in_queue) > 0

    async def test_buries_review_when_sibling_reviewed_today(self):
        from datetime import date

        db = _db()
        today = date.today()

        # Create a collocation with both directions
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="test_word2", translation="test2", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")

        rows, _ = db.list_collocations(search="test_word2", limit=1)
        row_id, item, _ = rows[0]

        # Set recognition as reviewed today, production as due (should be buried)
        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            last_review=today,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)
        prod_dir = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.REVIEW,
            due_date=today,
        )
        db.update_direction_by_id(row_id, Direction.PRODUCTION, prod_dir)

        # Enable bury_review
        db.set_anki_state_cache("bury_review", "True")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        # Production should be buried since recognition was reviewed today
        prod_in_queue = [q for q in queue if q["direction"] == "production" and q["state"] != "new"]
        assert len(prod_in_queue) == 0

    async def test_no_bury_when_sibling_reviewed_and_bury_disabled(self):
        from datetime import date

        db = _db()
        today = date.today()

        # Create a collocation with both directions
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="test_word4", translation="test4", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")

        rows, _ = db.list_collocations(search="test_word4", limit=1)
        row_id, item, _ = rows[0]

        # Set both directions as reviewed today
        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            last_review=today,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)
        prod_dir = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.REVIEW,
            due_date=today,
            last_review=today,
        )
        db.update_direction_by_id(row_id, Direction.PRODUCTION, prod_dir)

        # Explicitly disable bury_review
        db.set_anki_state_cache("bury_review", "False")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        # Both should appear since bury_review is disabled
        prod_in_queue = [q for q in queue if q["direction"] == "production" and q["state"] != "new"]
        assert len(prod_in_queue) > 0

    async def test_caps_new_across_directions(self):
        db = _db()

        # Create multiple new collocations
        from app.models.syntactic_unit import SyntacticUnit

        for i in range(10):
            unit = SyntacticUnit(text=f"word_{i}", translation=f"trans_{i}", word_count=2, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")

        # Set cap to 5
        db.set_anki_state_cache("daily_new_cap", "5")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        new_count = len([q for q in queue if q["state"] == "new"])
        assert new_count <= 5

    async def test_new_spread_after_review(self):
        from datetime import date

        db = _db()
        today = date.today()

        # Create due and new items
        from app.models.syntactic_unit import SyntacticUnit

        unit1 = SyntacticUnit(text="due_word", translation="due", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit1, language_code="sl")
        rows, _ = db.list_collocations(search="due_word", limit=1)
        row_id, item, _ = rows[0]
        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)

        unit2 = SyntacticUnit(text="new_word", translation="new", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit2, language_code="sl")

        # Set spread=1 (reviews first)
        db.set_anki_state_cache("new_spread", "1")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        if len(queue) > 1:
            # All due should come before new
            due_indices = [i for i, q in enumerate(queue) if q["state"] != "new"]
            new_indices = [i for i, q in enumerate(queue) if q["state"] == "new"]
            if due_indices and new_indices:
                assert max(due_indices) < min(new_indices)

    async def test_new_spread_mix(self):
        from datetime import date

        db = _db()
        today = date.today()

        # Create several due and new items
        from app.models.syntactic_unit import SyntacticUnit

        for i in range(5):
            unit = SyntacticUnit(text=f"due_{i}", translation=f"d_{i}", word_count=2, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            rows, _ = db.list_collocations(search=f"due_{i}", limit=1)
            row_id, item, _ = rows[0]
            rec_dir = DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_date=today,
            )
            db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)

        for i in range(5):
            unit = SyntacticUnit(text=f"new_{i}", translation=f"n_{i}", word_count=2, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")

        # Set spread=0 (mix)
        db.set_anki_state_cache("new_spread", "0")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        # Should have both due and new interleaved
        states = [q["state"] for q in queue]
        assert "new" in states
        assert any(s != "new" for s in states)

    async def test_new_spread_before_review(self):
        from datetime import date

        db = _db()
        today = date.today()

        # Create due and new items
        from app.models.syntactic_unit import SyntacticUnit

        unit1 = SyntacticUnit(text="due_word2", translation="due2", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit1, language_code="sl")
        rows, _ = db.list_collocations(search="due_word2", limit=1)
        row_id, item, _ = rows[0]
        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)

        unit2 = SyntacticUnit(text="new_word2", translation="new2", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit2, language_code="sl")

        # Set spread=2 (new before review)
        db.set_anki_state_cache("new_spread", "2")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        # Verify the endpoint works with spread=2
        assert isinstance(queue, list)

    async def test_bury_review_enabled(self):
        """Test that bury_review=True removes sibling reviews from queue."""
        from datetime import date

        db = _db()
        today = date.today()

        # Create a collocation with both directions in REVIEW state
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="review_bury_test", translation="test", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")

        rows, _ = db.list_collocations(search="review_bury_test", limit=1)
        row_id, item, _ = rows[0]

        # Set both directions as reviewed today
        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            last_review=today,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)
        prod_dir = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.REVIEW,
            due_date=today,
        )
        db.update_direction_by_id(row_id, Direction.PRODUCTION, prod_dir)

        # Enable bury_review
        db.set_anki_state_cache("bury_review", "True")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        # Production should be buried since recognition was reviewed today
        prod_reviews = [q for q in queue if q["direction"] == "production" and q["state"] == "review"]
        assert len(prod_reviews) == 0

    async def test_buries_due_when_sibling_reviewed_today(self):
        from datetime import date

        db = _db()
        today = date.today()

        # Create a collocation with both directions
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="test_word2", translation="test2", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")

        rows, _ = db.list_collocations(search="test_word2", limit=1)
        row_id, item, _ = rows[0]

        # Set recognition as reviewed today, production as due (should be buried)
        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            last_review=today,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)
        prod_dir = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.REVIEW,
            due_date=today,
        )
        db.update_direction_by_id(row_id, Direction.PRODUCTION, prod_dir)

        # Enable bury_review
        db.set_anki_state_cache("bury_review", "True")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        # Production should be buried since recognition was reviewed today
        prod_in_queue = [q for q in queue if q["direction"] == "production" and q["state"] != "new"]
        assert len(prod_in_queue) == 0

    async def test_orders_by_anki_card_id(self):
        from datetime import date

        db = _db()

        # Create new items with different anki_card_ids
        from app.models.syntactic_unit import SyntacticUnit

        today = date.today()
        for text, anki_id in [("word_b", 100), ("word_c", 200), ("word_a", None)]:
            unit = SyntacticUnit(text=text, translation=f"trans_{text}", word_count=2, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            # Get the specific collocation by text to avoid duplicates
            item_result = db.get_collocation(text)
            assert item_result is not None
            # Update the recognition direction with the desired anki_card_id
            orig = item_result.directions[Direction.RECOGNITION]
            new_dir = DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.NEW,
                due_date=today,
                stability=orig.stability,
                difficulty=orig.difficulty,
                reps=orig.reps,
                lapses=orig.lapses,
                anki_card_id=anki_id,
            )
            db.update_direction(item_result.guid, Direction.RECOGNITION, new_dir)

        # Set cap high enough to include all items
        db.set_anki_state_cache("daily_new_cap", "10")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        # Should be ordered by anki_card_id (NULLS LAST)
        # Extract unique anki_card_ids in order (one per collocation)
        seen = set()
        ordered_ids = []
        for q in queue:
            aid = q["directions"]["recognition"]["anki_card_id"]
            if aid not in seen:
                seen.add(aid)
                ordered_ids.append(aid)
        # word_b (100), word_c (200), word_a (None)
        non_null = [x for x in ordered_ids if x is not None]
        assert non_null == sorted(non_null)  # 100, 200
        # None should be at the end
        if None in ordered_ids:
            assert ordered_ids[-1] is None

    async def test_spread_mix_interleaves(self):
        """Test _spread_mix interleaves news into reviews."""
        from app.api.srs import _spread_mix

        # Create fake queue items
        reviews = [(i, None, None, Direction.RECOGNITION) for i in range(10)]
        news = [(i, None, None, Direction.PRODUCTION) for i in range(3)]

        result = _spread_mix(reviews, news)

        # Should have reviews + news
        assert len(result) == 13
        # Reviews should generally come before news (ratio = 10//3 = 3)
        review_count = sum(1 for t in result if t[3] == Direction.RECOGNITION)
        assert review_count == 10

    async def test_spread_mix_direct(self):
        """Test _spread_mix directly."""
        from app.api.srs import _spread_mix

        reviews = [(i, None, "sl", Direction.RECOGNITION) for i in range(5)]
        news = [(i, None, "sl", Direction.PRODUCTION) for i in range(3)]
        result = _spread_mix(reviews, news)
        assert len(result) == 8

    # --- Helper for merge tests ---
    def _make_item(self, due, anki_id, direction):
        """Build a minimal SRSItem with one direction populated for merge testing."""
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="x", translation="x", word_count=1, difficulty=1, source="t")
        state = DirectionState(
            direction=direction,
            due_date=due,
            state=SRSState.REVIEW,
            anki_card_id=anki_id,
        )
        return SRSItem(syntactic_unit=unit, directions={direction: state}, guid="g", anki_note_id=1)

    # --- Tests for _merge_by_due_then_anki_id ---
    async def test_merge_due_empty_inputs(self):

        from app.api.srs import _merge_by_due_then_anki_id

        result = _merge_by_due_then_anki_id([], [])
        assert result == []

    async def test_merge_due_only_recognition(self):
        from datetime import date

        from app.api.srs import _merge_by_due_then_anki_id

        item = self._make_item(date(2026, 1, 1), 100, Direction.RECOGNITION)
        rec = [(1, item, "sl")]
        result = _merge_by_due_then_anki_id(rec, [])
        assert len(result) == 1
        assert result[0][3] == Direction.RECOGNITION

    async def test_merge_due_only_production(self):
        from datetime import date

        from app.api.srs import _merge_by_due_then_anki_id

        item = self._make_item(date(2026, 1, 1), 100, Direction.PRODUCTION)
        prod = [(2, item, "sl")]
        result = _merge_by_due_then_anki_id([], prod)
        assert len(result) == 1
        assert result[0][3] == Direction.PRODUCTION

    async def test_merge_due_earlier_date_wins(self):
        from datetime import date

        from app.api.srs import _merge_by_due_then_anki_id

        rec_item = self._make_item(date(2026, 1, 1), 100, Direction.RECOGNITION)
        prod_item = self._make_item(date(2026, 1, 2), 200, Direction.PRODUCTION)
        rec = [(1, rec_item, "sl")]
        prod = [(2, prod_item, "sl")]
        result = _merge_by_due_then_anki_id(rec, prod)
        assert result[0][3] == Direction.RECOGNITION

    async def test_merge_due_later_date_wins(self):
        from datetime import date

        from app.api.srs import _merge_by_due_then_anki_id

        rec_item = self._make_item(date(2026, 1, 2), 100, Direction.RECOGNITION)
        prod_item = self._make_item(date(2026, 1, 1), 200, Direction.PRODUCTION)
        rec = [(1, rec_item, "sl")]
        prod = [(2, prod_item, "sl")]
        result = _merge_by_due_then_anki_id(rec, prod)
        assert result[0][3] == Direction.PRODUCTION

    async def test_merge_due_tiebreak_anki_id_nulls_last(self):
        from datetime import date

        from app.api.srs import _merge_by_due_then_anki_id

        rec_item = self._make_item(date(2026, 1, 1), None, Direction.RECOGNITION)
        prod_item = self._make_item(date(2026, 1, 1), 100, Direction.PRODUCTION)
        rec = [(1, rec_item, "sl")]
        prod = [(2, prod_item, "sl")]
        result = _merge_by_due_then_anki_id(rec, prod)
        assert result[0][3] == Direction.PRODUCTION

    async def test_merge_due_tiebreak_anki_id_nulls_last_reverse(self):
        from datetime import date

        from app.api.srs import _merge_by_due_then_anki_id

        rec_item = self._make_item(date(2026, 1, 1), 100, Direction.RECOGNITION)
        prod_item = self._make_item(date(2026, 1, 1), None, Direction.PRODUCTION)
        rec = [(1, rec_item, "sl")]
        prod = [(2, prod_item, "sl")]
        result = _merge_by_due_then_anki_id(rec, prod)
        assert result[0][3] == Direction.RECOGNITION

    async def test_merge_due_tiebreak_anki_id_numeric(self):
        from datetime import date

        from app.api.srs import _merge_by_due_then_anki_id

        rec_item = self._make_item(date(2026, 1, 1), 100, Direction.RECOGNITION)
        prod_item = self._make_item(date(2026, 1, 1), 200, Direction.PRODUCTION)
        rec = [(1, rec_item, "sl")]
        prod = [(2, prod_item, "sl")]
        result = _merge_by_due_then_anki_id(rec, prod)
        assert result[0][3] == Direction.RECOGNITION

    async def test_merge_due_tiebreak_anki_id_numeric_reverse(self):
        from datetime import date

        from app.api.srs import _merge_by_due_then_anki_id

        rec_item = self._make_item(date(2026, 1, 1), 200, Direction.RECOGNITION)
        prod_item = self._make_item(date(2026, 1, 1), 100, Direction.PRODUCTION)
        rec = [(1, rec_item, "sl")]
        prod = [(2, prod_item, "sl")]
        result = _merge_by_due_then_anki_id(rec, prod)
        assert result[0][3] == Direction.PRODUCTION

    async def test_merge_due_tiebreak_both_null_uses_row_id(self):
        from datetime import date

        from app.api.srs import _merge_by_due_then_anki_id

        rec_item = self._make_item(date(2026, 1, 1), None, Direction.RECOGNITION)
        prod_item = self._make_item(date(2026, 1, 1), None, Direction.PRODUCTION)
        rec = [(5, rec_item, "sl")]
        prod = [(3, prod_item, "sl")]
        result = _merge_by_due_then_anki_id(rec, prod)
        assert result[0][3] == Direction.PRODUCTION

    async def test_merge_due_tiebreak_both_null_rid_r_smaller(self):
        from datetime import date

        from app.api.srs import _merge_by_due_then_anki_id

        rec_item = self._make_item(date(2026, 1, 1), None, Direction.RECOGNITION)
        prod_item = self._make_item(date(2026, 1, 1), None, Direction.PRODUCTION)
        rec = [(3, rec_item, "sl")]
        prod = [(5, prod_item, "sl")]
        result = _merge_by_due_then_anki_id(rec, prod)
        assert result[0][3] == Direction.RECOGNITION

    # --- Tests for _merge_by_anki_id ---
    async def test_merge_anki_id_empty_inputs(self):
        from app.api.srs import _merge_by_anki_id

        result = _merge_by_anki_id([], [])
        assert result == []

    async def test_merge_anki_id_orders_nulls_last(self):
        from datetime import date

        from app.api.srs import _merge_by_anki_id

        rec_item = self._make_item(date(2026, 1, 1), None, Direction.RECOGNITION)
        prod_item = self._make_item(date(2026, 1, 1), 100, Direction.PRODUCTION)
        rec = [(1, rec_item, "sl")]
        prod = [(2, prod_item, "sl")]
        result = _merge_by_anki_id(rec, prod)
        assert result[0][3] == Direction.PRODUCTION
        assert result[1][3] == Direction.RECOGNITION

    async def test_merge_anki_id_numeric_order(self):
        from datetime import date

        from app.api.srs import _merge_by_anki_id

        item1 = self._make_item(date(2026, 1, 1), 200, Direction.RECOGNITION)
        item2 = self._make_item(date(2026, 1, 1), 100, Direction.PRODUCTION)
        item3 = self._make_item(date(2026, 1, 1), None, Direction.RECOGNITION)
        rec = [(1, item1, "sl"), (3, item3, "sl")]
        prod = [(2, item2, "sl")]
        result = _merge_by_anki_id(rec, prod)
        assert result[0][1].directions[result[0][3]].anki_card_id == 100
        assert result[1][1].directions[result[1][3]].anki_card_id == 200
        assert result[2][1].directions[result[2][3]].anki_card_id is None

    # --- Additional tests for _spread_mix ---
    async def test_spread_mix_empty_news_returns_reviews(self):
        from app.api.srs import _spread_mix

        reviews = [(i, None, "sl", Direction.RECOGNITION) for i in range(5)]
        result = _spread_mix(reviews, [])
        assert result == reviews

    async def test_spread_mix_empty_reviews_returns_news(self):
        from app.api.srs import _spread_mix

        news = [(i, None, "sl", Direction.PRODUCTION) for i in range(3)]
        result = _spread_mix([], news)
        assert result == news

    async def test_spread_mix_more_news_than_reviews(self):
        from app.api.srs import _spread_mix

        reviews = [(i, None, "sl", Direction.RECOGNITION) for i in range(2)]
        news = [(i, None, "sl", Direction.PRODUCTION) for i in range(5)]
        result = _spread_mix(reviews, news)
        assert len(result) == 7
        assert result[0][3] == Direction.RECOGNITION
        assert result[1][3] == Direction.PRODUCTION
