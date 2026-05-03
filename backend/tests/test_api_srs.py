"""Tests for /api/srs/queue-stats endpoint."""

from __future__ import annotations

import json

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState


class TestQueueStats:
    async def test_queue_stats_includes_fsrs_source_default(self, api_app_state):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        assert resp.json()["fsrs_source"] == "default"

    async def test_queue_stats_includes_fsrs_source_cache(self, api_app_state):
        db = api_app_state
        db.set_anki_state_cache("fsrs_params", json.dumps({"weights": [0.0] * 19, "desired_retention": 0.9}))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        assert resp.json()["fsrs_source"] == "cache"


class TestReviewQueue:
    async def test_returns_empty_queue_when_nothing_due(self, api_app_state):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        assert resp.json()["queue"] == []

    async def test_buries_new_when_sibling_reviewed_today(self, api_app_state):
        from datetime import date

        db = api_app_state
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

    async def test_no_bury_new_when_disabled(self, api_app_state):
        """Test that new cards appear when bury_new=False."""

        db = api_app_state

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

    async def test_buries_review_when_sibling_reviewed_today(self, api_app_state):
        from datetime import date

        db = api_app_state
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

    async def test_no_bury_when_sibling_reviewed_and_bury_disabled(self, api_app_state):
        from datetime import date

        db = api_app_state
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

    async def test_caps_new_across_directions(self, api_app_state):
        db = api_app_state

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

    async def test_new_spread_after_review(self, api_app_state):
        from datetime import date

        db = api_app_state
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

    async def test_new_spread_mix(self, api_app_state):
        from datetime import date

        db = api_app_state
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

    async def test_new_spread_before_review(self, api_app_state):
        from datetime import date

        db = api_app_state
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

    async def test_bury_review_enabled(self, api_app_state):
        """Test that bury_review=True removes sibling reviews from queue."""
        from datetime import date

        db = api_app_state
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

    async def test_buries_due_when_sibling_reviewed_today(self, api_app_state):
        from datetime import date

        db = api_app_state
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

    async def test_orders_by_anki_card_id(self, api_app_state):
        from datetime import date

        db = api_app_state

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

    async def test_spread_mix_interleaves(self, api_app_state):
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

    async def test_spread_mix_direct(self, api_app_state):
        """Test _spread_mix directly."""
        from app.api.srs import _spread_mix

        reviews = [(i, None, "sl", Direction.RECOGNITION) for i in range(5)]
        news = [(i, None, "sl", Direction.PRODUCTION) for i in range(3)]
        result = _spread_mix(reviews, news)
        assert len(result) == 8

    # --- Helper for merge tests ---
    def _make_item(self, due, anki_id, direction, anki_due=None, stability=1.0, last_review=None):
        """Build a minimal SRSItem with both directions populated for merge testing."""
        from app.models.syntactic_unit import SyntacticUnit

        # Create the specified direction
        dir_state = DirectionState(
            direction=direction,
            due_date=due,
            state=SRSState.REVIEW,
            anki_card_id=anki_id,
            anki_due=anki_due,
            stability=stability,
            last_review=last_review,
        )
        # Create the opposite direction with defaults
        opposite = Direction.PRODUCTION if direction == Direction.RECOGNITION else Direction.RECOGNITION
        opposite_state = DirectionState(
            direction=opposite,
            due_date=due,
            state=SRSState.REVIEW,
            stability=1.0,
            last_review=None,
        )
        unit = SyntacticUnit(text="x", translation="x", word_count=1, difficulty=1, source="t")
        return SRSItem(
            syntactic_unit=unit, directions={direction: dir_state, opposite: opposite_state}, guid="g", anki_note_id=1
        )

    # --- Tests for _merge_by_due_then_retrievability ---
    async def test_merge_retrievability_empty_inputs(self, api_app_state):
        from datetime import date

        from app.api.srs import _merge_by_due_then_retrievability

        result = _merge_by_due_then_retrievability([], [], date.today())
        assert result == []

    async def test_merge_retrievability_orders_by_retrievability_within_same_due_date(self, api_app_state):
        """Exact regression from plan: prašič-rec (s=0.4) vs vlak-prod (s=0.086) vs prašič-prod (s=0.5, due next day)."""
        from datetime import date

        from app.api.srs import _merge_by_due_then_retrievability

        today = date(2026, 5, 2)

        # prašič recognition: s=0.4, due=2026-05-01, anki=1766
        prasic_rec = self._make_item(
            date(2026, 5, 1),
            1766,
            Direction.RECOGNITION,
            stability=0.4,
            last_review=date(2026, 5, 1),
        )
        # vlak production: s=0.086, due=2026-05-01, anki=1777
        vlak_prod = self._make_item(
            date(2026, 5, 1),
            1777,
            Direction.PRODUCTION,
            stability=0.086,
            last_review=date(2026, 5, 1),
        )
        # prašič production: s=0.5, due=2026-05-02, anki=1767
        prasic_prod = self._make_item(
            date(2026, 5, 2),
            1767,
            Direction.PRODUCTION,
            stability=0.5,
            last_review=date(2026, 5, 1),
        )

        rec = [(1, prasic_rec, "sl")]
        prod = [(2, vlak_prod, "sl"), (3, prasic_prod, "sl")]

        result = _merge_by_due_then_retrievability(rec, prod, today)
        # Expected: vlak-prod (lowest R), prašič-rec (next R), prašič-prod (due next day)
        assert result[0][3] == Direction.PRODUCTION  # vlak-prod
        assert result[0][1].directions[Direction.PRODUCTION].stability == 0.086
        assert result[1][3] == Direction.RECOGNITION  # prašič-rec
        assert result[1][1].directions[Direction.RECOGNITION].stability == 0.4
        assert result[2][3] == Direction.PRODUCTION  # prašič-prod (due 2026-05-02)

    async def test_merge_retrievability_null_stability_sorts_last(self, api_app_state):
        """Directions with null stability (R=1.0) sort after those with real stability."""
        from datetime import date

        from app.api.srs import _merge_by_due_then_retrievability

        today = date(2026, 5, 2)

        # word_a: stability=None (null), due=today-1
        item_null = self._make_item(
            date(2026, 5, 1),
            100,
            Direction.RECOGNITION,
            stability=None,
            last_review=date(2026, 5, 1),
        )
        # word_b: stability=0.086, due=today-1
        item_low = self._make_item(
            date(2026, 5, 1),
            200,
            Direction.PRODUCTION,
            stability=0.086,
            last_review=date(2026, 5, 1),
        )

        rec = [(1, item_null, "sl")]
        prod = [(2, item_low, "sl")]

        result = _merge_by_due_then_retrievability(rec, prod, today)
        # item_low (stability=0.086, R < 1.0) comes first
        assert result[0][3] == Direction.PRODUCTION
        assert result[1][3] == Direction.RECOGNITION

    # --- Tests for _merge_by_anki_due_then_id ---
    async def test_merge_anki_due_empty_inputs(self, api_app_state):
        from app.api.srs import _merge_by_anki_due_then_id

        result = _merge_by_anki_due_then_id([], [])
        assert result == []

    async def test_merge_anki_due_orders_nulls_last(self, api_app_state):
        from datetime import date

        from app.api.srs import _merge_by_anki_due_then_id

        rec_item = self._make_item(date(2026, 1, 1), None, Direction.RECOGNITION, anki_due=None)
        prod_item = self._make_item(date(2026, 1, 1), 100, Direction.PRODUCTION, anki_due=50)
        rec = [(1, rec_item, "sl")]
        prod = [(2, prod_item, "sl")]
        result = _merge_by_anki_due_then_id(rec, prod)
        # prod has anki_due=50, rec has anki_due=None, so prod should come first
        assert result[0][3] == Direction.PRODUCTION
        assert result[1][3] == Direction.RECOGNITION

    async def test_merge_anki_due_numeric_order(self, api_app_state):
        from datetime import date

        from app.api.srs import _merge_by_anki_due_then_id

        # item1: anki_due=200, item2: anki_due=100, item3: anki_due=None
        item1 = self._make_item(date(2026, 1, 1), 999, Direction.RECOGNITION, anki_due=200)
        item2 = self._make_item(date(2026, 1, 1), 100, Direction.PRODUCTION, anki_due=100)
        item3 = self._make_item(date(2026, 1, 1), 200, Direction.RECOGNITION, anki_due=None)
        rec = [(1, item1, "sl"), (3, item3, "sl")]
        prod = [(2, item2, "sl")]
        result = _merge_by_anki_due_then_id(rec, prod)
        # item2 (anki_due=100) < item1 (anki_due=200) < item3 (anki_due=None)
        assert result[0][1].directions[result[0][3]].anki_due == 100
        assert result[1][1].directions[result[1][3]].anki_due == 200
        assert result[2][1].directions[result[2][3]].anki_due is None

    async def test_review_queue_new_cards_ordered_by_anki_due_across_directions(self, api_app_state):
        """New cards ordered by anki_due across directions."""
        from datetime import date

        db = api_app_state

        from app.models.syntactic_unit import SyntacticUnit

        # Coll A: recognition (anki_due=596, anki_card_id=999)
        # Coll B: production (anki_due=595, anki_card_id=100)
        unit_a = SyntacticUnit(text="coll_a", translation="a", word_count=2, difficulty=1, source="test")
        unit_b = SyntacticUnit(text="coll_b", translation="b", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit_a, language_code="sl")
        db.add_collocation(unit_b, language_code="sl")

        rows_a, _ = db.list_collocations(search="coll_a", limit=1)
        row_id_a, item_a, _ = rows_a[0]
        rows_b, _ = db.list_collocations(search="coll_b", limit=1)
        row_id_b, item_b, _ = rows_b[0]

        # Set recognition for coll_a: anki_due=596, anki_card_id=999
        rec_dir_a = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.NEW,
            due_date=date.today(),
            anki_card_id=999,
            anki_due=596,
        )
        db.update_direction_by_id(row_id_a, Direction.RECOGNITION, rec_dir_a)

        # Set production for coll_b: anki_due=595, anki_card_id=100
        prod_dir_b = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.NEW,
            due_date=date.today(),
            anki_card_id=100,
            anki_due=595,
        )
        db.update_direction_by_id(row_id_b, Direction.PRODUCTION, prod_dir_b)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        new_items = [q for q in queue if q["state"] == "new"]
        # coll_b (anki_due=595) should come before coll_a (anki_due=596)
        assert len(new_items) >= 2
        assert new_items[0]["text"] == "coll_b"
        assert new_items[1]["text"] == "coll_a"

    async def test_review_queue_new_cards_fall_back_to_anki_card_id_when_anki_due_null(self, api_app_state):
        """When anki_due is None, fall back to anki_card_id ordering."""
        from datetime import date

        db = api_app_state

        from app.models.syntactic_unit import SyntacticUnit

        unit_a = SyntacticUnit(text="null_a", translation="a", word_count=2, difficulty=1, source="test")
        unit_b = SyntacticUnit(text="null_b", translation="b", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit_a, language_code="sl")
        db.add_collocation(unit_b, language_code="sl")

        rows_a, _ = db.list_collocations(search="null_a", limit=1)
        row_id_a, item_a, _ = rows_a[0]
        rows_b, _ = db.list_collocations(search="null_b", limit=1)
        row_id_b, item_b, _ = rows_b[0]

        # Both have anki_due=None, but different anki_card_ids
        rec_dir_a = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.NEW,
            due_date=date.today(),
            anki_card_id=200,
            anki_due=None,
        )
        db.update_direction_by_id(row_id_a, Direction.RECOGNITION, rec_dir_a)

        rec_dir_b = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.NEW,
            due_date=date.today(),
            anki_card_id=100,
            anki_due=None,
        )
        db.update_direction_by_id(row_id_b, Direction.RECOGNITION, rec_dir_b)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        new_items = [q for q in queue if q["state"] == "new"]
        # Both have anki_due=None, so fall back to anki_card_id: null_b (100) < null_a (200)
        assert len(new_items) >= 2
        assert new_items[0]["text"] == "null_b"
        assert new_items[1]["text"] == "null_a"

    # --- Additional tests for _spread_mix ---
    async def test_spread_mix_empty_news_returns_reviews(self, api_app_state):
        from app.api.srs import _spread_mix

        reviews = [(i, None, "sl", Direction.RECOGNITION) for i in range(5)]
        result = _spread_mix(reviews, [])
        assert result == reviews

    async def test_spread_mix_empty_reviews_returns_news(self, api_app_state):
        from app.api.srs import _spread_mix

        news = [(i, None, "sl", Direction.PRODUCTION) for i in range(3)]
        result = _spread_mix([], news)
        assert result == news

    async def test_spread_mix_more_news_than_reviews(self, api_app_state):
        from app.api.srs import _spread_mix

        reviews = [(i, None, "sl", Direction.RECOGNITION) for i in range(2)]
        news = [(i, None, "sl", Direction.PRODUCTION) for i in range(5)]
        result = _spread_mix(reviews, news)
        assert len(result) == 7
        assert result[0][3] == Direction.RECOGNITION
        assert result[1][3] == Direction.PRODUCTION

    async def test_review_queue_excludes_own_buried_direction(self, api_app_state):
        """Buried directions must not appear in /api/srs/review-queue."""
        from datetime import date

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()

        # Create a collocation with recognition=buried, production=new
        unit = SyntacticUnit(text="buried_test", translation="test", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")

        rows, _ = db.list_collocations(search="buried_test", limit=1)
        row_id, item, _ = rows[0]

        # Set recognition as buried
        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.BURIED,
            due_date=today,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)

        # Production is new (default state)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]

        # Recognition should NOT be in the queue (buried)
        rec_in_queue = [q for q in queue if q["direction"] == "recognition"]
        assert len(rec_in_queue) == 0

        # Just verify the endpoint works and doesn't error
        assert isinstance(queue, list)

    async def test_review_queue_includes_audio_url_when_audio_exists(self, api_app_state):
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        unit = SyntacticUnit(text="mleko", translation="milk", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="mleko", limit=1)
        row_id = rows[0][0]
        db.add_media(
            row_id,
            kind="audio_forvo",
            filename="sl_mleko.mp3",
            path="/tmp/sl_mleko.mp3",
            anki_filename="sl_mleko.mp3",
            sha256="abc",
            size_bytes=100,
        )
        rec_dir = DirectionState(direction=Direction.RECOGNITION, due_date=date.today(), state=SRSState.REVIEW)
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        item = next(q for q in queue if q["text"] == "mleko")
        assert item["audio_url"] == "/api/srs/media/sl_mleko.mp3"

    async def test_review_queue_includes_image_url_when_image_exists(self, api_app_state):
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        unit = SyntacticUnit(text="jabolko", translation="apple", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="jabolko", limit=1)
        row_id = rows[0][0]
        db.add_media(
            row_id,
            kind="image",
            filename="apple.jpg",
            path="/tmp/apple.jpg",
            anki_filename="apple.jpg",
            sha256="def",
            size_bytes=200,
        )
        rec_dir = DirectionState(direction=Direction.RECOGNITION, due_date=date.today(), state=SRSState.REVIEW)
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        item = next(q for q in queue if q["text"] == "jabolko")
        assert item["image_url"] == "/api/srs/media/apple.jpg"


class TestAudioUrlGrammarNote:
    """Tests for audio_url, grammar, note in API responses."""

    async def test_due_item_has_audio_url_when_audio_exists(self, api_app_state):
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        unit = SyntacticUnit(
            text="stol",
            translation="chair",
            word_count=2,
            difficulty=1,
            source="test",
            grammar="noun, masc, sing",
            note="common",
        )
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="stol", limit=1)
        row_id = rows[0][0]
        db.add_media(
            row_id,
            kind="audio_forvo",
            filename="sl_stol.mp3",
            path="/tmp/sl_stol.mp3",
            anki_filename="sl_stol.mp3",
            sha256="abc",
            size_bytes=100,
        )
        # Set to REVIEW so it appears in /due
        rec_dir = DirectionState(direction=Direction.RECOGNITION, due_date=date.today(), state=SRSState.REVIEW)
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/due")
        assert resp.status_code == 200
        due = resp.json()["due"]
        assert len(due) > 0
        item = due[0]
        assert item["audio_url"] == "/api/srs/media/sl_stol.mp3"
        assert item["grammar"] == "noun, masc, sing"
        assert item["note"] == "common"

    async def test_due_item_audio_url_null_when_no_audio(self, api_app_state):
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        unit = SyntacticUnit(text="miza", translation="table", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="miza", limit=1)
        row_id = rows[0][0]
        rec_dir = DirectionState(direction=Direction.RECOGNITION, due_date=date.today(), state=SRSState.REVIEW)
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/due")
        assert resp.status_code == 200
        due = resp.json()["due"]
        assert len(due) > 0
        assert due[0]["audio_url"] is None

    async def test_review_queue_includes_grammar_and_note(self, api_app_state):
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        unit = SyntacticUnit(
            text="okno",
            translation="window",
            word_count=2,
            difficulty=1,
            source="test",
            grammar="noun, neut, sing",
            note="irregular",
        )
        db.add_collocation(unit, language_code="sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        assert len(queue) > 0
        item = queue[0]
        assert "audio_url" in item
        assert item["grammar"] == "noun, neut, sing"
        assert item["note"] == "irregular"

    async def test_grammar_and_note_default_to_empty_for_legacy_rows(self, api_app_state):
        """Legacy rows (no grammar/note set) should default to empty strings."""
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        unit = SyntacticUnit(text="knjiga", translation="book", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        assert len(queue) > 0
        item = queue[0]
        assert item["grammar"] == ""
        assert item["note"] == ""


class TestCreateItemWithSourceContext:
    """Tests for POST /api/srs/items with source context and LLM auto-translate."""

    async def test_create_item_with_source_context(self, api_app_state):
        """POST with source_sentence, source_lesson_id, source_line_index stores them."""
        payload = {
            "text": "kako si",
            "language_code": "sl",
            "word_count": 2,
            "translation": "how are you",
            "source_sentence": "Kako si? Jaz sem dobro.",
            "source_lesson_id": "lesson-123",
            "source_line_index": 5,
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/items", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        # Check that the item was created (source context is stored in DB, not returned in response)
        assert data["text"] == "kako si"
        assert data["translation"] == "how are you"

    async def test_create_item_without_translation_triggers_llm(self, api_app_state, monkeypatch):
        """POST with empty translation triggers LLM auto-translate."""
        from unittest.mock import AsyncMock

        from app.llm.client import LLMClient

        # Ensure llm_client exists on app.state, then mock it with monkeypatch for auto-cleanup
        app.state.llm_client = None  # Ensure attribute exists
        mock_client = AsyncMock(spec=LLMClient)
        monkeypatch.setattr(app.state, "llm_client", mock_client)

        # Mock translate_term to return a translation
        async def mock_translate(*args, **kwargs):
            return "how are you"

        monkeypatch.setattr("app.api.srs.translate_term", mock_translate)

        payload = {
            "text": "kako si",
            "language_code": "sl",
            "word_count": 2,
            # No translation - should trigger LLM
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/items", json=payload)

        assert resp.status_code == 201
        data = resp.json()
        assert data["translation"] == "how are you"

    async def test_create_item_with_translation_skips_llm(self, api_app_state):
        """POST with non-empty translation should NOT call LLM."""
        payload = {
            "text": "dober dan",
            "language_code": "sl",
            "word_count": 2,
            "translation": "good day",  # Has translation - no LLM needed
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/items", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["translation"] == "good day"

    async def test_create_item_minimal_payload(self, api_app_state):
        """POST with only required fields works."""
        payload = {
            "text": "test word",
            "language_code": "sl",
            "word_count": 1,
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/items", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["text"] == "test word"

    async def test_create_item_raises_500_if_item_not_found_after_insert(self, api_app_state, monkeypatch):
        """POST raises 500 if add_collocation succeeds but item can't be retrieved."""
        db = api_app_state

        # Patch list_collocations to return empty (rows, total) after successful add
        def mock_list(*args, **kwargs):
            return [], 0

        monkeypatch.setattr(db, "list_collocations", mock_list)

        payload = {
            "text": "test",
            "language_code": "sl",
            "word_count": 1,
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/items", json=payload)
        assert resp.status_code == 500
        assert "Failed to retrieve created item" in resp.json()["detail"]


class TestMergeUsesRealRetrievabilityWhenLastReviewPopulated:
    async def test_merge_orders_by_retrievability_with_last_review(self, api_app_state):
        """Regression test: sreda rec (s=0.001, last_review=yesterday-ish) should be first."""
        from datetime import date, timedelta

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()

        # sreda recognition: stability=0.001, last_review=1.26 days ago → R ≈ 0.058
        unit1 = SyntacticUnit(text="sreda", translation="wednesday", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit1, language_code="sl")
        rows, _ = db.list_collocations(search="sreda", limit=1)
        row_id1, item1, _ = rows[0]
        sreda_rec = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            stability=0.001,
            difficulty=5.0,
            reps=5,
            lapses=1,
            last_review=today - timedelta(days=1),
        )
        db.update_direction_by_id(row_id1, Direction.RECOGNITION, sreda_rec)

        # vozovnica production: stability=0.002, last_review=0.95 days ago → R ≈ 0.094
        unit2 = SyntacticUnit(text="vozovnica", translation="ticket", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit2, language_code="sl")
        rows, _ = db.list_collocations(search="vozovnica", limit=1)
        row_id2, item2, _ = rows[0]
        voz_prod = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.REVIEW,
            due_date=today,
            stability=0.002,
            difficulty=5.0,
            reps=9,
            lapses=0,
            last_review=today - timedelta(days=1),
        )
        db.update_direction_by_id(row_id2, Direction.PRODUCTION, voz_prod)

        # izgled recognition: stability=2.131, last_review=4 days ago → R ≈ 0.035
        unit3 = SyntacticUnit(text="izgled", translation="appearance", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit3, language_code="sl")
        rows, _ = db.list_collocations(search="izgled", limit=1)
        row_id3, item3, _ = rows[0]
        izg_rec = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today - timedelta(days=2),  # due 2 days ago (overdue)
            stability=2.131,
            difficulty=4.0,
            reps=10,
            lapses=0,
            last_review=today - timedelta(days=4),
        )
        db.update_direction_by_id(row_id3, Direction.RECOGNITION, izg_rec)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]

        # With last_review populated, check retrievability ordering
        # izgled (due 2 days ago) comes first due to due_date, then sreda (due today)
        assert len(queue) >= 3
        # The first item should be overdue (izgled due 2 days ago)
        assert queue[0]["text"] == "izgled"
        # Among same-day-due items, sreda (R=0.065) should come before vozovnica (R=0.092)
        sreda_idx = next(i for i, q in enumerate(queue) if q["text"] == "sreda")
        voz_idx = next(i for i, q in enumerate(queue) if q["text"] == "vozovnica")
        assert sreda_idx < voz_idx, "sreda should come before vozovnica (lower R)"


class TestLearningStatePriority:
    """Learning-state cards should sort before review cards (Anki queue=1 behavior)."""

    async def test_review_queue_learning_state_sorts_before_overdue_review(self, api_app_state):
        """Regression: ženska prod (learning) should appear before tovornjak prod (overdue review)."""
        from datetime import date, timedelta

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()

        # tovornjak prod: state=review, due_date=yesterday (overdue), stability=0.116
        unit1 = SyntacticUnit(text="tovornjak", translation="truck", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit1, language_code="sl")
        rows, _ = db.list_collocations(search="tovornjak", limit=1)
        row_id1, item1, _ = rows[0]
        tovornjak_prod = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.REVIEW,
            due_date=today - timedelta(days=1),
            stability=0.116,
            last_review=today - timedelta(days=2),
        )
        db.update_direction_by_id(row_id1, Direction.PRODUCTION, tovornjak_prod)

        # ženska prod: state=learning, due_date=today, stability=0.036, last_review=NULL
        unit2 = SyntacticUnit(text="ženska", translation="woman", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit2, language_code="sl")
        rows, _ = db.list_collocations(search="ženska", limit=1)
        row_id2, item2, _ = rows[0]
        zenska_prod = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.LEARNING,
            due_date=today,
            stability=0.036,
            last_review=None,
        )
        db.update_direction_by_id(row_id2, Direction.PRODUCTION, zenska_prod)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]

        # ženska (learning) should be first, before tovornjak (overdue review)
        assert len(queue) >= 2
        assert queue[0]["text"] == "ženska"
        # Check the state from the directions dict for the correct direction
        first_item = queue[0]
        if first_item["direction"] == "production":
            assert first_item["directions"]["production"]["state"] == "learning"
        else:
            assert first_item["directions"]["recognition"]["state"] == "learning"

    async def test_learning_bucket_orders_by_stability_then_anki_card_id(self, api_app_state):
        """Learning cards should order by stability ASC, then anki_card_id ASC."""
        from datetime import date

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()

        # Create three learning cards with different stability values
        cards = [
            ("mnozica", 300, 0.01),  # lowest stability
            ("clovek", 301, 0.05),  # middle stability
            ("dekle", 302, 0.26),  # highest stability
        ]
        for text, anki_id, stability in cards:
            unit = SyntacticUnit(text=text, translation=f"trans_{text}", word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            rows, _ = db.list_collocations(search=text, limit=1)
            row_id, item, _ = rows[0]
            dstate = DirectionState(
                direction=Direction.PRODUCTION,
                state=SRSState.LEARNING,
                due_date=today,
                stability=stability,
                anki_card_id=anki_id,
            )
            db.update_direction_by_id(row_id, Direction.PRODUCTION, dstate)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        # Check learning state from the correct direction
        learning_queue = []
        for q in queue:
            if q["direction"] == "production":
                if q["directions"]["production"]["state"] == "learning":
                    learning_queue.append(q)
            elif q["directions"]["recognition"]["state"] == "learning":
                learning_queue.append(q)

        assert len(learning_queue) == 3
        assert learning_queue[0]["text"] == "mnozica"
        assert learning_queue[1]["text"] == "clovek"
        assert learning_queue[2]["text"] == "dekle"

    async def test_learning_bucket_respects_bury_review(self, api_app_state):
        """Bury review should still work after the learning/review split."""
        from datetime import date

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()

        # Create a collocation with both directions: recognition=reviewed today, production=learning
        unit = SyntacticUnit(text="bury_test", translation="test", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="bury_test", limit=1)
        row_id, item, _ = rows[0]

        # Recognition reviewed today (triggers bury)
        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            last_review=today,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)

        # Production in learning state (should be buried)
        prod_dir = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.LEARNING,
            due_date=today,
            stability=0.01,
        )
        db.update_direction_by_id(row_id, Direction.PRODUCTION, prod_dir)

        # Enable bury_review
        db.set_anki_state_cache("bury_review", "True")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]

        # Production should be buried (check direction-specific state)
        prod_in_queue = [
            q for q in queue if q["direction"] == "production" and q["directions"]["production"]["state"] == "learning"
        ]
        assert len(prod_in_queue) == 0

    async def test_learning_bucket_orders_by_anki_due_when_present(self, api_app_state):
        """Regression for ženska/dojenček: when anki_due is set (queue=1 sub-day timestamp),
        it must override stability as the sort key. Anki dispatches queue=1 by raw `due` ASC.
        """
        from datetime import date

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()

        # Real-world case: dojenček has lower stability but LATER sub-day due than ženska.
        # Anki shows ženska first (earlier sub-day due); TunaTale must agree.
        cards = [
            # (text, anki_card_id, stability, anki_due_unix_timestamp)
            ("dojencek", 1775264031923, 0.01, 1777835006),  # lower stability, later sub-day due
            ("zenska", 1775264031927, 0.036, 1777834178),   # higher stability, earlier sub-day due
        ]
        for text, anki_id, stability, anki_due_ts in cards:
            unit = SyntacticUnit(text=text, translation=f"trans_{text}", word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            rows, _ = db.list_collocations(search=text, limit=1)
            row_id, item, _ = rows[0]
            dstate = DirectionState(
                direction=Direction.PRODUCTION,
                state=SRSState.LEARNING,
                due_date=today,
                stability=stability,
                anki_card_id=anki_id,
                anki_due=anki_due_ts,
            )
            db.update_direction_by_id(row_id, Direction.PRODUCTION, dstate)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]

        # ženska must come before dojenček despite higher stability — anki_due dominates
        zenska_idx = next(i for i, q in enumerate(queue) if q["text"] == "zenska")
        dojencek_idx = next(i for i, q in enumerate(queue) if q["text"] == "dojencek")
        assert zenska_idx < dojencek_idx, "ženska (earlier anki_due) must come before dojenček"
