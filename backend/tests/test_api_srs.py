"""Tests for /api/srs/queue-stats endpoint."""

from __future__ import annotations

import json
from unittest.mock import patch

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

    async def test_queue_stats_splits_learning_and_review(self, api_app_state):
        """Test that queue-stats returns learning and review fields, not due."""
        from datetime import date

        db = api_app_state
        # Seed a collocation with LEARNING and REVIEW states
        from app.models.syntactic_unit import SyntacticUnit

        unit1 = SyntacticUnit(text="word1", translation="w1", word_count=2, difficulty=1, source="test")
        unit2 = SyntacticUnit(text="word2", translation="w2", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit1, language_code="sl")
        db.add_collocation(unit2, language_code="sl")

        item1 = db.get_collocation("word1")
        item2 = db.get_collocation("word2")
        today = date.today()

        # word1: both directions LEARNING (should count as learning)
        for direction in [Direction.RECOGNITION, Direction.PRODUCTION]:
            ds = DirectionState(
                direction=direction,
                due_date=today,
                stability=1.0,
                difficulty=5.0,
                reps=1,
                lapses=1,
                state=SRSState.LEARNING,
            )
            db.update_direction(item1.guid, direction, ds)

        # word2: both directions REVIEW (should count as review)
        for direction in [Direction.RECOGNITION, Direction.PRODUCTION]:
            ds = DirectionState(
                direction=direction,
                due_date=today,
                stability=1.0,
                difficulty=5.0,
                reps=5,
                lapses=0,
                state=SRSState.REVIEW,
            )
            db.update_direction(item2.guid, direction, ds)

        with patch("app.api.srs.count_anki_review_remaining_today", return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/srs/queue-stats")

        assert resp.status_code == 200
        data = resp.json()
        assert "learning" in data
        assert "review" in data
        assert "due" not in data
        assert data["learning"] == 2  # word1: 2 directions in LEARNING
        assert data["review"] == 2  # word2: 2 directions in REVIEW
        assert data["new"] == 0

    async def test_queue_stats_new_decrements_via_anki_revlog(self, monkeypatch, tmp_path, api_app_state):
        """Anki-parity "new today": badge subtracts the Anki-revlog count of
        cards whose first revlog entry is today. Without this, TunaTale shows
        `min(cap, pool)` which never moves while Anki ticks 27 → 26 → 25.
        """
        import sqlite3 as _sqlite3
        from datetime import date, datetime, time

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()

        # Cap=2, pool=5 — pool > cap so `min(cap, pool)` never moves on its own.
        db.set_anki_state_cache("daily_new_cap", "2")
        for i in range(5):
            unit = SyntacticUnit(text=f"new_{i}", translation="t", word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")

        # Stub Anki collection: empty revlog → 0 introduced.
        anki_path = tmp_path / "collection.anki2"
        conn = _sqlite3.connect(str(anki_path))
        conn.execute(
            "CREATE TABLE revlog (id INTEGER, cid INTEGER, usn INTEGER, ease INTEGER, "
            "ivl INTEGER, lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER)"
        )
        conn.commit()
        conn.close()
        from app.config import settings

        monkeypatch.setattr(settings, "anki_collection_path", anki_path)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.json()["new"] == 2  # cap - 0

        # Now simulate "user introduced 1 new card today" by writing a revlog row.
        today_ms = int(datetime.combine(today, time(14, 0)).timestamp() * 1000)
        conn = _sqlite3.connect(str(anki_path))
        conn.execute(
            "INSERT INTO revlog VALUES (?, ?, 0, 3, 1, 1, 0, 0, 0)",
            (today_ms, 4242),
        )
        conn.commit()
        conn.close()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.json()["new"] == 1, "Anki revlog drives the badge — first-entry-today counts"

    async def test_queue_stats_learning_count_includes_future_due_date(self, api_app_state):
        """Anki parity: queue=1 cards stay in the Learning badge regardless of
        due_date. After Good on a learning card the FSRS engine schedules a 10-min
        step, which can roll due_date past UTC midnight to tomorrow. Anki still
        counts that card; TunaTale must too.
        """
        from datetime import date, timedelta

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()
        unit = SyntacticUnit(text="future_learn", translation="t", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("future_learn")

        # FSRS-engine output after Good late at night: due_date jumped to tomorrow.
        ds = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            due_date=today + timedelta(days=1),
            stability=0.5,
            difficulty=5.0,
            reps=2,
            lapses=0,
            left=1002,
        )
        db.update_direction(item.guid, Direction.RECOGNITION, ds)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        assert resp.json()["learning"] == 1, "future-due learning card must still count"

    async def test_queue_stats_learning_includes_relearning(self, api_app_state):
        """Test that RELEARNING states are counted in learning bucket."""
        from datetime import date

        db = api_app_state
        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="relearn_word", translation="rw", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("relearn_word")
        today = date.today()

        # Both directions in RELEARNING (should count as learning)
        for direction in [Direction.RECOGNITION, Direction.PRODUCTION]:
            ds = DirectionState(
                direction=direction,
                due_date=today,
                stability=1.0,
                difficulty=5.0,
                reps=5,
                lapses=2,
                state=SRSState.RELEARNING,
            )
            db.update_direction(item.guid, direction, ds)

        with patch("app.api.srs.count_anki_review_remaining_today", return_value=None):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                resp = await client.get("/api/srs/queue-stats")

        assert resp.status_code == 200
        data = resp.json()
        assert data["learning"] == 2  # RELEARNING counts as learning
        assert data["review"] == 0


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

    async def test_proactive_sibling_bury_drops_lower_priority_review(self, api_app_state):
        """Two due-review siblings: the lower-priority one is buried, matching
        Anki's gather_cards proactive sibling bury (rslib/.../gathering.rs).
        """
        from datetime import date

        from app.models.srs_item import DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()
        # Both directions in REVIEW state, both due today, but recognition is
        # the more urgent (lower stability → lower retrievability).
        unit = SyntacticUnit(text="oblačilo", translation="garment", word_count=1, difficulty=1, source="t")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="oblačilo", limit=1)
        row_id, _, _ = rows[0]
        db.update_direction_by_id(
            row_id,
            Direction.RECOGNITION,
            DirectionState(direction=Direction.RECOGNITION, due_date=today, state=SRSState.REVIEW, stability=0.05),
        )
        db.update_direction_by_id(
            row_id,
            Direction.PRODUCTION,
            DirectionState(direction=Direction.PRODUCTION, due_date=today, state=SRSState.REVIEW, stability=10.0),
        )
        db.set_anki_state_cache("bury_review", "True")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        # Only one direction of oblačilo should be present.
        oblacilo = [q for q in queue if q["text"] == "oblačilo"]
        assert len(oblacilo) == 1
        # The more urgent (recognition) survives; production sibling is buried.
        assert oblacilo[0]["direction"] == "recognition"

    async def test_proactive_sibling_bury_drops_new_when_review_sibling_present(self, api_app_state):
        """A review's sibling new card is buried under bury_new=true.

        Mirrors Anki: bury_new=true buries new cards whose note already has a
        higher-priority card in the queue. govedina-like case.
        """
        from datetime import date

        from app.models.srs_item import DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()
        unit = SyntacticUnit(text="govedina_t", translation="beef", word_count=1, difficulty=1, source="t")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="govedina_t", limit=1)
        row_id, _, _ = rows[0]
        # Recognition is REVIEW (in queue today); production is NEW (would be
        # in the new pool but should be buried as a sibling).
        db.update_direction_by_id(
            row_id,
            Direction.RECOGNITION,
            DirectionState(direction=Direction.RECOGNITION, due_date=today, state=SRSState.REVIEW, stability=0.025),
        )
        db.update_direction_by_id(
            row_id,
            Direction.PRODUCTION,
            DirectionState(direction=Direction.PRODUCTION, due_date=today, state=SRSState.NEW),
        )
        db.set_anki_state_cache("bury_new", "True")
        db.set_anki_state_cache("bury_review", "True")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        govedina = [q for q in queue if q["text"] == "govedina_t"]
        assert len(govedina) == 1
        assert govedina[0]["direction"] == "recognition"
        assert govedina[0]["state"] == "review"

    async def test_proactive_sibling_bury_disabled_keeps_dup_review(self, api_app_state):
        """With bury_review=false, both review siblings stay in the queue."""
        from datetime import date

        from app.models.srs_item import DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()
        unit = SyntacticUnit(text="majica_t", translation="shirt", word_count=1, difficulty=1, source="t")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="majica_t", limit=1)
        row_id, _, _ = rows[0]
        db.update_direction_by_id(
            row_id,
            Direction.RECOGNITION,
            DirectionState(direction=Direction.RECOGNITION, due_date=today, state=SRSState.REVIEW, stability=0.1),
        )
        db.update_direction_by_id(
            row_id,
            Direction.PRODUCTION,
            DirectionState(direction=Direction.PRODUCTION, due_date=today, state=SRSState.REVIEW, stability=0.2),
        )
        db.set_anki_state_cache("bury_review", "False")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        majica = [q for q in queue if q["text"] == "majica_t"]
        assert len(majica) == 2
        directions = {q["direction"] for q in majica}
        assert directions == {"recognition", "production"}

    async def test_review_queue_does_not_duplicate_relearning_cards(self, api_app_state):
        """Relearning cards must appear exactly once in the queue (not in both learning and nonlearning)."""
        from datetime import UTC, date, datetime

        db = api_app_state
        today = date.today()
        past_due_at = datetime(2020, 1, 1, tzinfo=UTC)

        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="relearn_dup_test", translation="rdt", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")

        rows, _ = db.list_collocations(search="relearn_dup_test", limit=1)
        row_id, item, _ = rows[0]

        # Set RECOGNITION to RELEARNING with past due_at
        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.RELEARNING,
            due_date=today,
            due_at=past_due_at,
            stability=1.0,
            difficulty=5.0,
            reps=5,
            lapses=2,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)

        # Set PRODUCTION to RELEARNING with past due_at
        prod_dir = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.RELEARNING,
            due_date=today,
            due_at=past_due_at,
            stability=1.0,
            difficulty=5.0,
            reps=5,
            lapses=2,
        )
        db.update_direction_by_id(row_id, Direction.PRODUCTION, prod_dir)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        data = resp.json()
        queue = data["queue"]

        # Each (id, direction) pair must appear exactly once
        pairs = [(q["id"], q["direction"]) for q in queue]
        from collections import Counter

        counts = Counter(pairs)
        duplicates = {k: v for k, v in counts.items() if v > 1}
        assert duplicates == {}, f"Relearning cards duplicated: {duplicates}"

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

    async def test_spread_mix_matches_anki_intersperser_3_3(self, api_app_state):
        """Anki Intersperser([1,2,3], [11,22,33]) yields [1,11,2,22,3,33]."""
        from app.api.srs import _spread_mix

        reviews = [(i, "R", "sl", Direction.RECOGNITION) for i in range(3)]
        news = [(i, "N", "sl", Direction.PRODUCTION) for i in range(3)]
        tags = [t[1] for t in _spread_mix(reviews, news)]
        assert tags == ["R", "N", "R", "N", "R", "N"]

    async def test_spread_mix_matches_anki_intersperser_3_2(self, api_app_state):
        """Anki Intersperser([1,2,3], [11,22]) yields [1,11,2,22,3]."""
        from app.api.srs import _spread_mix

        reviews = [(i, "R", "sl", Direction.RECOGNITION) for i in range(3)]
        news = [(i, "N", "sl", Direction.PRODUCTION) for i in range(2)]
        tags = [t[1] for t in _spread_mix(reviews, news)]
        assert tags == ["R", "N", "R", "N", "R"]

    async def test_spread_mix_starts_with_longer_iter(self, api_app_state):
        """When news outnumber reviews, Anki Intersperser starts from the news side.

        Intersperser([1,2,3], [11,22,33,44,55,66]) = [11,1,22,33,2,44,55,3,66].
        """
        from app.api.srs import _spread_mix

        reviews = [(i, "R", "sl", Direction.RECOGNITION) for i in range(3)]
        news = [(i, "N", "sl", Direction.PRODUCTION) for i in range(6)]
        tags = [t[1] for t in _spread_mix(reviews, news)]
        assert tags == ["N", "R", "N", "N", "R", "N", "N", "R", "N"]

    async def test_fnv1a_64_matches_anki_for_known_pair(self, api_app_state):
        """Anki's fnvhash(id, mod) over revija/vadba production cards.

        Both cards share mod=1778078218; their hashes determine Anki's tiebreak
        when retrievability is identical. Anki shows vadba first because its
        hash is the smaller signed i64. Verifies the FNV-1a-64 port exactly.
        """
        from app.api.srs import _fnv1a_64_i64

        revija = _fnv1a_64_i64(1775264032053, 1778078218)
        vadba = _fnv1a_64_i64(1775264032065, 1778078218)
        assert revija == 2230325772437798989
        assert vadba == -542033352715009175
        assert vadba < revija

    async def test_fnv1a_64_zero_args_returns_offset_basis(self, api_app_state):
        """Empty input returns FNV-1a's 64-bit offset basis, cast to signed i64."""
        from app.api.srs import _fnv1a_64_i64

        # 0xcbf29ce484222325 - 2**64
        assert _fnv1a_64_i64() == -3750763034362895579

    async def test_retrievability_tie_breaks_by_fnvhash_anki_parity(self, api_app_state):
        """When two due directions tie on retrievability, the one with the
        smaller fnvhash(anki_card_id, anki_card_mod) sorts first — matching
        Anki's `ORDER BY ... retrievability asc, fnvhash(id, mod)`.
        """
        from datetime import date

        from app.api.srs import _merge_by_retrievability_ascending
        from app.models.srs_item import DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        today = date.today()

        def _make(text: str, cid: int, mod: int) -> tuple[int, SRSItem, str]:
            unit = SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="t")
            ds = DirectionState(
                direction=Direction.PRODUCTION,
                due_date=today,
                stability=0.021,
                difficulty=9.421,
                reps=8,
                lapses=0,
                state=SRSState.REVIEW,
                last_review=None,
                anki_card_id=cid,
                anki_card_mod=mod,
            )
            item = SRSItem(syntactic_unit=unit, directions={Direction.PRODUCTION: ds})
            return (1 if text == "revija" else 2, item, "sl")

        rec: list = []
        prod = [_make("revija", 1775264032053, 1778078218), _make("vadba", 1775264032065, 1778078218)]
        result = _merge_by_retrievability_ascending(rec, prod, today)
        # vadba has the smaller fnvhash → first under ASC (matches Anki)
        assert result[0][1].syntactic_unit.text == "vadba"
        assert result[1][1].syntactic_unit.text == "revija"

    async def test_spread_mix_injects_news_early_when_reviews_dominate(self, api_app_state):
        """With 10 reviews and 2 news, Anki injects the first new card at position 3
        (0-indexed), not position 5 like TT's old floor-ratio algorithm.

        Anki Intersperser uses a continuous (one_len+1)/(two_len+1) ratio, so the
        first new appears earlier in long review queues — matching what the user
        observes in Anki when a new card surfaces while TT is still on a review.
        """
        from app.api.srs import _spread_mix

        reviews = [(i, "R", "sl", Direction.RECOGNITION) for i in range(10)]
        news = [(i, "N", "sl", Direction.PRODUCTION) for i in range(2)]
        tags = [t[1] for t in _spread_mix(reviews, news)]
        assert tags == ["R", "R", "R", "N", "R", "R", "R", "R", "N", "R", "R", "R"]

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

    # --- Tests for _merge_by_retrievability_ascending ---
    async def test_merge_retrievability_empty_inputs(self, api_app_state):
        from datetime import date

        from app.api.srs import _merge_by_retrievability_ascending

        result = _merge_by_retrievability_ascending([], [], date.today())
        assert result == []

    async def test_merge_retrievability_orders_by_retrievability_alone(self, api_app_state):
        """Sort key is R only; due_date does not influence position. Mirrors Anki's
        SortOrder::RetrievabilityAscending — the daily pool is one flat list keyed on R.
        """
        from datetime import date

        from app.api.srs import _merge_by_retrievability_ascending

        today = date(2026, 5, 2)

        # prašič recognition: s=0.4, due=2026-05-01
        prasic_rec = self._make_item(
            date(2026, 5, 1),
            1766,
            Direction.RECOGNITION,
            stability=0.4,
            last_review=date(2026, 5, 1),
        )
        # vlak production: s=0.086, due=2026-05-01
        vlak_prod = self._make_item(
            date(2026, 5, 1),
            1777,
            Direction.PRODUCTION,
            stability=0.086,
            last_review=date(2026, 5, 1),
        )
        # prašič production: s=0.5, due=2026-05-02 (today, but highest stability → highest R)
        prasic_prod = self._make_item(
            date(2026, 5, 2),
            1767,
            Direction.PRODUCTION,
            stability=0.5,
            last_review=date(2026, 5, 1),
        )

        rec = [(1, prasic_rec, "sl")]
        prod = [(2, vlak_prod, "sl"), (3, prasic_prod, "sl")]

        result = _merge_by_retrievability_ascending(rec, prod, today)
        # Lowest R first regardless of due_date
        assert result[0][1].directions[Direction.PRODUCTION].stability == 0.086  # vlak-prod
        assert result[1][1].directions[Direction.RECOGNITION].stability == 0.4  # prašič-rec
        assert result[2][1].directions[Direction.PRODUCTION].stability == 0.5  # prašič-prod

    async def test_merge_retrievability_overdue_high_R_loses_to_today_low_R(self, api_app_state):
        """Regression: rama (overdue 2 days, R=0.84) must NOT come before navijač
        (due today, R=0.07). TunaTale used to bucket by due_date first and surfaced
        the well-remembered overdue card, while Anki's retrievability-ascending
        order picked the nearly-forgotten today card.
        """
        from datetime import date

        from app.api.srs import _merge_by_retrievability_ascending

        today = date(2026, 5, 6)

        # rama recognition: due 2 days ago, well-remembered (R≈0.84)
        rama_rec = self._make_item(
            date(2026, 5, 4),
            1775264032302,
            Direction.RECOGNITION,
            stability=3.368,
            last_review=date(2026, 4, 30),
        )
        # navijač production: due today, nearly forgotten (R≈0.07)
        navijac_prod = self._make_item(
            date(2026, 5, 6),
            1775264031967,
            Direction.PRODUCTION,
            stability=0.001,
            last_review=date(2026, 5, 5),
        )

        rec = [(1, rama_rec, "hr")]
        prod = [(2, navijac_prod, "hr")]

        result = _merge_by_retrievability_ascending(rec, prod, today)
        # navijač (R≈0.07) must come first, even though rama is more overdue
        assert result[0][3] == Direction.PRODUCTION
        assert result[0][0] == 2  # navijač row
        assert result[1][3] == Direction.RECOGNITION
        assert result[1][0] == 1  # rama row

    async def test_merge_retrievability_null_stability_sorts_last(self, api_app_state):
        """Directions with null stability (R=1.0) sort after those with real stability."""
        from datetime import date

        from app.api.srs import _merge_by_retrievability_ascending

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

        result = _merge_by_retrievability_ascending(rec, prod, today)
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
        # Anki parity: when news outnumber reviews, Intersperser starts from
        # the longer iter (news). For 2 reviews + 5 news → [N, R, N, N, R, N, N].
        from app.api.srs import _spread_mix

        reviews = [(i, None, "sl", Direction.RECOGNITION) for i in range(2)]
        news = [(i, None, "sl", Direction.PRODUCTION) for i in range(5)]
        result = _spread_mix(reviews, news)
        assert len(result) == 7
        directions = [t[3] for t in result]
        assert directions == [
            Direction.PRODUCTION,
            Direction.RECOGNITION,
            Direction.PRODUCTION,
            Direction.PRODUCTION,
            Direction.RECOGNITION,
            Direction.PRODUCTION,
            Direction.PRODUCTION,
        ]

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

        # Set llm on app.state (matching production wiring in main.py), then mock translate_term
        mock_client = AsyncMock(spec=LLMClient)
        app.state.llm = mock_client

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

        # Pure retrievability-ascending order — due_date does not bucket the pool.
        # sreda  R≈0.065 (s=0.001, 1d elapsed)
        # vozov. R≈0.092 (s=0.002, 1d elapsed)
        # izgled R≈0.833 (s=2.131, 4d elapsed) — overdue but well-remembered, sorts last
        assert len(queue) >= 3
        sreda_idx = next(i for i, q in enumerate(queue) if q["text"] == "sreda")
        voz_idx = next(i for i, q in enumerate(queue) if q["text"] == "vozovnica")
        izgled_idx = next(i for i, q in enumerate(queue) if q["text"] == "izgled")
        assert sreda_idx < voz_idx < izgled_idx


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

    async def test_learning_bucket_bypasses_bury_review(self, api_app_state):
        """Anki parity: queue=1 cards are NOT subject to sibling-bury within
        the same day. A production-LEARNING card must surface in TunaTale's
        queue even when its recognition sibling was reviewed today (which
        would normally bury the collocation under bury_review=True).
        """
        from datetime import date

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()

        unit = SyntacticUnit(text="bury_test", translation="test", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="bury_test", limit=1)
        row_id, item, _ = rows[0]

        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            last_review=today,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)

        prod_dir = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.LEARNING,
            due_date=today,
            stability=0.01,
        )
        db.update_direction_by_id(row_id, Direction.PRODUCTION, prod_dir)

        db.set_anki_state_cache("bury_review", "True")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]

        prod_in_queue = [
            q for q in queue if q["direction"] == "production" and q["directions"]["production"]["state"] == "learning"
        ]
        assert len(prod_in_queue) == 1, (
            "production-LEARNING must remain visible — Anki's queue=1 dispatcher does not honour sibling-bury"
        )

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
            ("zenska", 1775264031927, 0.036, 1777834178),  # higher stability, earlier sub-day due
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


class TestLearningStepFeedback:
    """Tests for feedback returning learning state with due_at, and queue filtering."""

    async def test_good_on_learning_card_returns_learning_state_with_due_at(self, api_app_state):
        """Rating Good on a 2-step learning card returns new_state=learning with future due_at."""
        from datetime import UTC, datetime, timedelta

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state

        # Create a learning card with 2 steps
        unit = SyntacticUnit(text="test_learning", translation="test", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="test_learning", limit=1)
        row_id, item, _ = rows[0]

        # Set up learning state with 2 steps remaining (left=2002)
        from app.models.srs_item import Direction, DirectionState, SRSState

        now = datetime.now(UTC)
        dstate = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            due_date=now.date(),
            stability=1.0,
            left=2002,
            due_at=now + timedelta(minutes=1),  # Step 0: 1 minute
            dirty_fsrs=True,
            last_rating=3,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, dstate)

        # Rate Good (should advance to step 1, left=1002)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/api/srs/items/{row_id}/direction/recognition/feedback", json={"rating": "good"})
        assert resp.status_code == 200
        data = resp.json()

        # Should return learning state with due_at
        assert data["new_state"] == "learning"
        assert "due_at" in data, "Response should include due_at for learning cards"
        assert "left" in data, "Response should include left for learning cards"
        assert data["left"] == 1002, f"Expected left=1002 after GOOD, got {data.get('left')}"

        # Parse due_at and verify it's in the future
        from datetime import datetime

        due_at = datetime.fromisoformat(data["due_at"])
        assert due_at > datetime.now(UTC), "due_at should be in the future"

    async def test_learning_card_appears_in_queue_when_due_at_future(self, api_app_state):
        """Learning card with future due_at must still appear in queue (Anki parity).

        Anki's deck-overview "Learning" count includes every queue=1 card regardless
        of when its next step is due — the dispatcher orders them but they never
        disappear from the bucket. TunaTale used to filter them out, which is why
        rating Good on every learning card emptied TunaTale's queue while Anki
        still showed N remaining.
        """
        from datetime import UTC, datetime, timedelta

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state

        unit = SyntacticUnit(text="test_future", translation="test", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="test_future", limit=1)
        row_id, item, _ = rows[0]

        now = datetime.now(UTC)
        dstate = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            due_date=now.date(),
            stability=1.0,
            left=1002,
            due_at=now + timedelta(minutes=10),
            dirty_fsrs=True,
            last_rating=3,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, dstate)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]

        assert any(q["text"] == "test_future" for q in queue), (
            "Learning card with future due_at must remain in the queue (Anki parity)"
        )

    async def test_future_due_learning_sorts_after_reviews(self, api_app_state):
        """Anki parity: when a learning card's next step is in the future,
        Anki serves review cards while the timer ticks. TunaTale must do the
        same — pending-step learning cards sit *behind* due reviews, not in
        front of them. Past-due learning still gets priority.
        """
        from datetime import UTC, date, datetime, timedelta

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()
        now = datetime.now(UTC)

        # 1) past-due learning (priority — must come first)
        db.add_collocation(
            SyntacticUnit(text="past_learn", translation="t", word_count=1, difficulty=1, source="test"),
            language_code="sl",
        )
        past_id = db.list_collocations(search="past_learn", limit=1)[0][0][0]
        db.update_direction_by_id(
            past_id,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.LEARNING,
                due_date=today,
                stability=0.5,
                left=1002,
                due_at=now - timedelta(minutes=1),
            ),
        )

        # 2) due review (must come before any future-due learning)
        db.add_collocation(
            SyntacticUnit(text="due_review", translation="t", word_count=1, difficulty=1, source="test"),
            language_code="sl",
        )
        review_id = db.list_collocations(search="due_review", limit=1)[0][0][0]
        db.update_direction_by_id(
            review_id,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_date=today,
                stability=5.0,
                difficulty=5.0,
                reps=4,
                lapses=0,
                last_review=now - timedelta(days=1),
            ),
        )

        # 3) future-due learning (must come last — serve only after the timer elapses)
        db.add_collocation(
            SyntacticUnit(text="future_learn", translation="t", word_count=1, difficulty=1, source="test"),
            language_code="sl",
        )
        future_id = db.list_collocations(search="future_learn", limit=1)[0][0][0]
        db.update_direction_by_id(
            future_id,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.LEARNING,
                due_date=today,
                stability=0.5,
                left=1002,
                due_at=now + timedelta(minutes=10),
            ),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        queue = resp.json()["queue"]

        idx_past = next(i for i, q in enumerate(queue) if q["text"] == "past_learn")
        idx_review = next(i for i, q in enumerate(queue) if q["text"] == "due_review")
        idx_future = next(i for i, q in enumerate(queue) if q["text"] == "future_learn")
        assert idx_past < idx_review < idx_future, (
            f"expected past-learning < review < future-learning, got "
            f"past={idx_past}, review={idx_review}, future={idx_future}"
        )

    async def test_learning_card_with_due_date_in_future_still_in_queue(self, api_app_state):
        """Regression: a LEARNING card with `due_date > today` (e.g. UTC midnight
        crossed when FSRS added a 10-minute step late at night local time) must
        still appear in the queue.

        get_due_items filters by due_date <= today, which is right for REVIEW
        cards but wrong for queue=1 — Anki's learning dispatcher uses the per-
        card due_at timestamp, not the daily due_date bucket.
        """
        from datetime import UTC, date, datetime, timedelta

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()
        now = datetime.now(UTC)

        unit = SyntacticUnit(text="glasbilo_t", translation="instrument", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="glasbilo_t", limit=1)
        row_id, _, _ = rows[0]

        db.update_direction_by_id(
            row_id,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                state=SRSState.LEARNING,
                due_date=today + timedelta(days=1),  # FSRS rolled past local midnight
                stability=0.2,
                left=1002,
                due_at=now + timedelta(minutes=10),
                last_review=now,
                reps=3,
                lapses=0,
                dirty_fsrs=True,
                last_rating=3,
            ),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        queue = resp.json()["queue"]

        assert any(q.get("text") == "glasbilo_t" and q.get("state") == "learning" for q in queue), (
            "LEARNING card with due_date>today must remain visible — the daily-bucket "
            "filter is for REVIEW cards, not queue=1"
        )

    async def test_learning_card_survives_sibling_bury_after_grade(self, api_app_state):
        """Regression: Anki's queue=1 dispatcher ignores sibling-bury, so a card
        in LEARNING with a future due_at stays in the bucket even when its own
        last_review=today places its collocation_id in buried.

        Concretely: user grades a card "Good" → it advances to step 2 (future due_at)
        and last_review is set to today. list_collocations_reviewed_today now
        contains this collocation. The bury_review filter must NOT remove the
        learning card from the queue.
        """
        from datetime import UTC, date, datetime, timedelta

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()
        now = datetime.now(UTC)

        unit = SyntacticUnit(text="glasbilo_t", translation="instrument", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="glasbilo_t", limit=1)
        row_id, _, _ = rows[0]

        # Production: just graded → LEARNING, future due_at, last_review=today.
        db.update_direction_by_id(
            row_id,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                state=SRSState.LEARNING,
                due_date=today,
                stability=0.2,
                left=1002,
                due_at=now + timedelta(minutes=10),
                last_review=now,
                reps=3,
                lapses=0,
                dirty_fsrs=True,
                last_rating=3,
            ),
        )
        # Recognition: anything reviewable so the collocation makes it into `due`.
        db.update_direction_by_id(
            row_id,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_date=today,
                stability=5.0,
                difficulty=5.0,
                reps=4,
                lapses=0,
                last_review=now - timedelta(days=1),
            ),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        queue = resp.json()["queue"]

        learning = [q for q in queue if q.get("text") == "glasbilo_t" and q.get("state") == "learning"]
        assert learning, (
            "production-LEARNING with future due_at must survive sibling-bury "
            "even when last_review=today puts the collocation in the buried set"
        )

    async def test_learning_card_past_due_at_sorts_before_future_due_at(self, api_app_state):
        """Within the learning bucket, soonest-due (or already-due) cards come first."""
        from datetime import UTC, datetime, timedelta

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        now = datetime.now(UTC)

        # past-due card
        db.add_collocation(
            SyntacticUnit(text="learn_past", translation="t", word_count=1, difficulty=1, source="test"),
            language_code="sl",
        )
        past_id = db.list_collocations(search="learn_past", limit=1)[0][0][0]
        db.update_direction_by_id(
            past_id,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.LEARNING,
                due_date=now.date(),
                stability=1.0,
                left=1002,
                due_at=now - timedelta(minutes=1),
                dirty_fsrs=True,
                last_rating=3,
            ),
        )

        # future-due card
        db.add_collocation(
            SyntacticUnit(text="learn_future", translation="t", word_count=1, difficulty=1, source="test"),
            language_code="sl",
        )
        future_id = db.list_collocations(search="learn_future", limit=1)[0][0][0]
        db.update_direction_by_id(
            future_id,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.LEARNING,
                due_date=now.date(),
                stability=1.0,
                left=1002,
                due_at=now + timedelta(minutes=10),
                dirty_fsrs=True,
                last_rating=3,
            ),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        queue = resp.json()["queue"]

        idx_past = next(i for i, q in enumerate(queue) if q["text"] == "learn_past")
        idx_future = next(i for i, q in enumerate(queue) if q["text"] == "learn_future")
        assert idx_past < idx_future, "past-due learning card must sort before future-due"

    async def test_learning_card_appears_in_queue_when_due_at_past(self, api_app_state):
        """Learning card with past due_at should appear in review queue."""
        from datetime import UTC, datetime, timedelta

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state

        # Create a learning card with past due_at
        unit = SyntacticUnit(text="test_past", translation="test", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="test_past", limit=1)
        row_id, item, _ = rows[0]

        now = datetime.now(UTC)
        dstate = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            due_date=now.date(),
            stability=1.0,
            left=1002,
            due_at=now - timedelta(minutes=1),  # Past due_at
            dirty_fsrs=True,
            last_rating=3,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, dstate)

        # Check that the card IS in the queue
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]

        # This card should be in the queue (due_at is in the past)
        card_in_queue = any(q["text"] == "test_past" for q in queue)
        assert card_in_queue, "Learning card with past due_at should be in queue"

    async def test_easy_on_new_card_graduates_to_review(self, api_app_state):
        """Rating Easy on NEW card graduates immediately to REVIEW (left=None, due_at=None)."""
        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state

        # Create a new card
        unit = SyntacticUnit(text="test_graduate", translation="test", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="test_graduate", limit=1)
        row_id, item, _ = rows[0]

        # Rate Easy (should graduate to REVIEW immediately)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/api/srs/items/{row_id}/direction/recognition/feedback", json={"rating": "easy"})
        assert resp.status_code == 200
        data = resp.json()

        # Should return review state without left or due_at
        assert data["new_state"] == "review"
        assert "left" not in data, "Response should not include left for review cards"
        assert "due_at" not in data, "Response should not include due_at for review cards"
