"""Tests for /api/srs/queue-stats endpoint."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState
from tests.conftest import anki_day_anchor, seed_direction


def _add_review_due_collocation(db, text: str, today: date):
    """Add a collocation with both directions in REVIEW state, due today."""
    from app.models.syntactic_unit import SyntacticUnit

    unit = SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    for direction in [Direction.RECOGNITION, Direction.PRODUCTION]:
        ds = DirectionState(
            direction=direction,
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.0,
            reps=5,
            lapses=0,
            state=SRSState.REVIEW,
        )
        db.update_direction(item.guid, direction, ds)


def _add_new_with_graduated_sibling(db, text: str, today: date):
    """Add a dual note: recognition still NEW, production graduated to REVIEW due today.

    This is the shape Anki buries out of the new pool (the production sibling is
    gathered into today's review queue, so the new recognition card is buried).
    """
    from app.models.syntactic_unit import SyntacticUnit

    unit = SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    db.update_direction(
        item.guid,
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
            state=SRSState.NEW,
        ),
    )
    db.update_direction(
        item.guid,
        Direction.PRODUCTION,
        DirectionState(
            direction=Direction.PRODUCTION,
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.0,
            reps=5,
            state=SRSState.REVIEW,
        ),
    )


def _stamp_reviews_completed_today(db, today: date, count: int):
    """Simulate `count` reviews done today by appending interday-review tt_revlog
    rows (Layer 73: `count_reviews_completed_today` counts tt_revlog, not state)."""
    from app.models.srs_item import Direction, RevlogRow

    base_ms = int(anki_day_anchor(today).timestamp() * 1000)
    conn = db._get_conn().__enter__()
    rows = conn.execute(
        "SELECT collocation_id, direction FROM collocation_directions WHERE state = 'review' LIMIT ?",
        (count,),
    ).fetchall()
    for i, row in enumerate(rows):
        db.append_revlog(
            RevlogRow(
                id=base_ms + i,
                collocation_id=row["collocation_id"],
                direction=Direction(row["direction"]),
                button_chosen=3,
                interval=30,
                last_interval=30,  # interday footing → counts
                factor=0,
                taken_millis=1500,
                review_kind=1,
            )
        )


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
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
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
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                stability=1.0,
                difficulty=5.0,
                reps=5,
                lapses=0,
                state=SRSState.REVIEW,
            )
            db.update_direction(item2.guid, direction, ds)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")

        assert resp.status_code == 200
        data = resp.json()
        assert "learning" in data
        assert "review" in data
        assert "due" not in data
        assert data["learning"] == 2  # word1: 2 directions in LEARNING (Anki counts queue=1 per card)
        assert data["review"] == 1, (  # word2: 2 directions in REVIEW collapse to 1 collocation (sibling bury)
            f"review badge must mirror Anki's COUNT(DISTINCT nid) with bury_reviews; got {data['review']}"
        )
        assert data["new"] == 0

    async def test_queue_stats_new_badge_bounded_by_review_limit(self, api_app_state):
        """Mirror Anki's 'new cards ignore review limit'=OFF (default): when the
        review budget is consumed by due reviews, the new badge is 0 even with new
        quota + available cards. Review cap 2 + 3 reviews due → 0 new."""
        from datetime import date

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        due = datetime.combine(date.today(), time(4, 0), tzinfo=UTC)

        # 3 review-due collocations (> the review cap of 2).
        for i in range(3):
            db.add_collocation(
                SyntacticUnit(text=f"rev{i}", translation=f"r{i}", word_count=1, difficulty=1, source="test"),
                language_code="sl",
            )
            item = db.get_collocation(f"rev{i}")
            for direction in (Direction.RECOGNITION, Direction.PRODUCTION):
                db.update_direction(
                    item.guid,
                    direction,
                    DirectionState(
                        direction=direction,
                        due_at=due,
                        stability=5.0,
                        difficulty=5.0,
                        reps=5,
                        lapses=0,
                        state=SRSState.REVIEW,
                    ),
                )
        # An available NEW collocation that should still be suppressed by the review cap.
        db.add_collocation(
            SyntacticUnit(text="newword", translation="nw", word_count=1, difficulty=1, source="test"),
            language_code="sl",
        )
        db.set_anki_state_cache("daily_review_cap", "2")
        db.set_anki_state_cache("daily_new_cap", "5")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            data = (await client.get("/api/srs/queue-stats")).json()

        assert data["review"] == 2  # capped at the review limit
        assert data["new"] == 0  # review budget fully consumed → no new, despite quota 5 + 1 available

    async def test_queue_stats_new_badge_buries_new_with_review_due_sibling(self, api_app_state):
        """New badge mirrors Anki's new-sibling bury (bury_new default True).

        Two dual notes whose production sibling graduated to REVIEW (due today):
        Anki buries each new recognition card because its sibling is gathered
        into today's review queue. The badge must read 0, not the raw 2. This
        reproduces the live 2-vs-0 divergence; the served queue already buried
        these (`_compute_live_main`), so this aligns the badge with the queue.
        """
        db = api_app_state
        today = date.today()
        _add_new_with_graduated_sibling(db, "soglasnik", today)
        _add_new_with_graduated_sibling(db, "taliti", today)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")

        assert resp.status_code == 200
        assert resp.json()["new"] == 0, (
            f"new badge must bury new cards whose review sibling is due today; got {resp.json()['new']}"
        )

    async def test_queue_stats_new_badge_falls_back_to_raw_when_bury_new_off(self, api_app_state):
        """With bury_new disabled, the badge falls back to the raw NEW-direction
        count (no new-sibling bury) — no regression for non-default decks."""
        db = api_app_state
        db.set_anki_state_cache("bury_new", "False")
        today = date.today()
        _add_new_with_graduated_sibling(db, "soglasnik", today)
        _add_new_with_graduated_sibling(db, "taliti", today)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")

        assert resp.status_code == 200
        # Two lone NEW recognition directions, raw count, cap=20, none introduced.
        assert resp.json()["new"] == 2, f"bury_new=False must use the raw count_new_available; got {resp.json()['new']}"

    async def test_queue_stats_new_does_not_rebound_after_grading_introduced_card(self, api_app_state):
        """Regression: after sync introduces a card (sets `prior_state='new'`),
        the user grading that learning card must NOT bump the new-card badge
        back up. Earlier, `_schedule_with_steps` clobbered `prior_state` from
        'new' to 'learning' on a step-advance grade, so the card dropped out of
        `count_new_introduced_today` and `cap - introduced_today` rebounded by 1.
        """
        from datetime import UTC, date, datetime, timedelta

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()
        db.set_anki_state_cache("daily_new_cap", "30")
        # Pool > cap so the badge is gated by remaining_quota (cap - introduced),
        # not by count_new_available — otherwise the assertion can't distinguish
        # the regression from a small-pool floor.
        for i in range(50):
            db.add_collocation(
                SyntacticUnit(text=f"pool_{i}", translation="t", word_count=1, difficulty=1, source="test"),
                language_code="sl",
            )
        # Seed one collocation that's already been introduced today: sync would have
        # set state=LEARNING, prior_state=NEW, last_review=today.
        graded_at = anki_day_anchor(today)
        row_id = seed_direction(
            db,
            text="intro_card",
            translation="t",
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            due_date=today + timedelta(days=1),
            stability=0.5,
            difficulty=8.0,
            reps=1,
            lapses=0,
            anki_card_id=4242,
            left=1002,
            due_at=datetime.now(UTC) + timedelta(minutes=1),
            last_review=graded_at,
            introduced_at=graded_at,
            prior_state=SRSState.NEW,
        )

        # Baseline: badge subtracts 1 introduction (cap 30, introduced 1 → 29).
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            before = (await client.get("/api/srs/queue-stats")).json()
        assert before["new"] == 29, before

        # Grade Good on the learning card → advances step, still LEARNING.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                f"/api/srs/items/{row_id}/direction/recognition/feedback",
                json={"rating": "good", "time_ms": 1500},
            )
            assert resp.status_code == 200
            after = (await client.get("/api/srs/queue-stats")).json()

        assert after["new"] == 29, (
            f"badge must not rebound after grading an introduced-today learning card; "
            f"before={before['new']}, after={after['new']} — prior_state='new' must be sticky"
        )

    async def test_queue_stats_learning_count_includes_future_due_date(self, api_app_state):
        """Anki parity: queue=1 cards stay in the Learning badge regardless of
        due_date. After Good on a learning card the FSRS engine schedules a 10-min
        step, which can roll due_date past UTC midnight to tomorrow. Anki still
        counts that card; TunaTale must too.
        """
        from datetime import date, timedelta

        db = api_app_state
        today = date.today()
        seed_direction(
            db,
            text="future_learn",
            translation="t",
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            due_date=today + timedelta(days=1),
            stability=0.5,
            reps=2,
            left=1002,
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        assert resp.json()["learning"] == 1, "future-due learning card must still count"

    async def test_queue_stats_review_decrements_when_card_graded_in_tt(self, api_app_state):
        """Grading a card in TT must visibly decrement the review badge — the
        graded card's due_date moves into the future so its collocation drops
        out of `count_review_due_collocations`. Mirrors the user report:
        'I'm stuck at 126; should be 123' after grading 3 reviews in TT.
        """
        from datetime import date, timedelta

        db = api_app_state
        today = date.today()

        # Seed 3 review-state cards due today.
        for i in range(3):
            seed_direction(
                db,
                text=f"rev_{i}",
                translation="t",
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_date=today,
                reps=5,
                anki_card_id=100 + i,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            before = (await client.get("/api/srs/queue-stats")).json()
        assert before["review"] == 3

        # Simulate a TT grade: state stays REVIEW, due_date jumps into the future.
        rows, _ = db.list_collocations(search="rev_0", limit=1)
        row_id, _, _ = rows[0]
        db.update_direction_by_id(
            row_id,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_at=datetime.combine(today + timedelta(days=3), time(4, 0), tzinfo=UTC),
                stability=2.0,
                difficulty=5.0,
                reps=6,
                lapses=0,
                anki_card_id=100,
            ),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            after = (await client.get("/api/srs/queue-stats")).json()
        assert after["review"] == 2, f"after grading one card in TT, badge must decrement; got {after['review']}"

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
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                stability=1.0,
                difficulty=5.0,
                reps=5,
                lapses=2,
                state=SRSState.RELEARNING,
            )
            db.update_direction(item.guid, direction, ds)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")

        assert resp.status_code == 200
        data = resp.json()
        assert data["learning"] == 2  # RELEARNING counts as learning
        assert data["review"] == 0

    async def test_review_badge_floors_at_zero(self, api_app_state):
        """Review badge never goes negative when reviews_today exceeds cap."""
        db = api_app_state
        db.set_anki_state_cache("daily_review_cap", "97")
        today = date.today()
        for i in range(101):
            _add_review_due_collocation(db, f"word{i}", today)
        _stamp_reviews_completed_today(db, today, count=200)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["review"] == 0

    async def test_review_badge_includes_review_cap_fields_in_response(self, api_app_state):
        """Response includes daily_review_cap and review_cap_source."""
        db = api_app_state
        db.set_anki_state_cache("daily_review_cap", "75")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "daily_review_cap" in data
        assert "review_cap_source" in data
        assert data["daily_review_cap"] == 75
        assert data["review_cap_source"] == "cache"

    async def test_review_badge_preserves_existing_new_cap_keys(self, api_app_state):
        """daily_new_cap and cap_source still present for backwards compat."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "daily_new_cap" in data
        assert "cap_source" in data


class TestReviewQueue:
    async def test_returns_empty_queue_when_nothing_due(self, api_app_state):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        assert resp.json()["queue"] == []

    async def test_review_queue_caps_review_cards_at_daily_review_cap(self, api_app_state):
        """The review cap limits the SERVED queue, not just the badge.

        Anki gathers at most ``review_limit - reviews_today`` review cards into the
        study session — so a 50-review cap means you review 50, then the review
        portion is done. TT mirrors this in `_compute_live_main` (alongside the
        new-card cap that was already applied). Without it the badge said 50 while
        the queue served all due reviews (user report).
        """
        db = api_app_state
        today = date.today()
        for i in range(6):
            _add_review_due_collocation(db, f"rev{i}", today)
        db.set_anki_state_cache("daily_review_cap", "2")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            data = (await client.get("/api/srs/review-queue?session_start=1")).json()

        review_items = [q for q in data["queue"] if q["state"] == "review"]
        assert len(review_items) == 2, f"review queue must cap at 2; got {len(review_items)}"

    async def test_review_queue_uncapped_when_cap_above_available(self, api_app_state):
        """Sanity: a generous cap leaves every due review in the queue (the cap is
        a ceiling, not a fixed size)."""
        db = api_app_state
        today = date.today()
        for i in range(4):
            _add_review_due_collocation(db, f"rev{i}", today)
        db.set_anki_state_cache("daily_review_cap", "50")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            data = (await client.get("/api/srs/review-queue?session_start=1")).json()

        review_items = [q for q in data["queue"] if q["state"] == "review"]
        assert len(review_items) == 4

    async def test_sets_no_store_cache_header(self, api_app_state):
        """Browser must NEVER cache /review-queue. Without `no-store`, a normal
        page refresh can be served from heuristic disk cache — the JS still
        runs `onMount` and makes the fetch call, but the browser returns the
        cached body without hitting the backend. Result: session_start=1 never
        reaches /review-queue, the frozen queue isn't rebuilt, and TT/Anki
        diverge until a hard refresh (Cmd+Shift+R) bypasses the cache.

        Discovered when a user reported "had to hard-refresh to make rebuild
        fire" — the rebuild logic was correct; the cache was eating the
        request.
        """
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
            resp_ss = await client.get("/api/srs/review-queue?session_start=1")
        for r in (resp, resp_ss):
            cc = r.headers.get("cache-control", "")
            assert "no-store" in cc, f"expected no-store; got Cache-Control={cc!r}"

    async def test_queue_stats_sets_no_store_cache_header(self, api_app_state):
        """Same constraint as /review-queue: the badge counts must reflect every
        request's live state (sync just happened, card just graded, etc.)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        cc = resp.headers.get("cache-control", "")
        assert "no-store" in cc, f"expected no-store; got Cache-Control={cc!r}"

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
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
            last_review=today,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)
        prod_dir = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.REVIEW,
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
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
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
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
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
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
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
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
            DirectionState(
                direction=Direction.RECOGNITION,
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                state=SRSState.REVIEW,
                stability=0.1,
            ),
        )
        db.update_direction_by_id(
            row_id,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                state=SRSState.REVIEW,
                stability=0.2,
            ),
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
        date.today()
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
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
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

    async def test_fnv1a_64_zero_args_returns_offset_basis(self, api_app_state):
        """Empty input returns FNV-1a's 64-bit offset basis, cast to signed i64."""
        from app.api.srs import _fnv1a_64_i64

        # 0xcbf29ce484222325 - 2**64
        assert _fnv1a_64_i64() == -3750763034362895579

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
            due_at=datetime.combine(due, time(4, 0), tzinfo=UTC),
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
            due_at=datetime.combine(due, time(4, 0), tzinfo=UTC),
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

    async def test_merge_directions_empty_inputs(self, api_app_state):
        from app.api.srs import _merge_directions

        result = _merge_directions([], [])
        assert result == []

    async def test_review_queue_new_cards_ordered_by_anki_due_desc(self, api_app_state):
        """Anki HighestPosition parity: synced new cards order by anki_due DESC."""
        from datetime import date

        db = api_app_state

        from app.models.syntactic_unit import SyntacticUnit

        # Both directions of both collocations get synced anki_due values so the
        # ordering is unambiguous (no NULL → no NULLS-FIRST effect).
        unit_a = SyntacticUnit(text="coll_a", translation="a", word_count=2, difficulty=1, source="test")
        unit_b = SyntacticUnit(text="coll_b", translation="b", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit_a, language_code="sl")
        db.add_collocation(unit_b, language_code="sl")

        rows_a, _ = db.list_collocations(search="coll_a", limit=1)
        row_id_a, _item_a, _ = rows_a[0]
        rows_b, _ = db.list_collocations(search="coll_b", limit=1)
        row_id_b, _item_b, _ = rows_b[0]

        # coll_a: rec anki_due=596, prod anki_due=597
        db.update_direction_by_id(
            row_id_a,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.NEW,
                due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                anki_card_id=596,
                anki_due=596,
            ),
        )
        db.update_direction_by_id(
            row_id_a,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                state=SRSState.NEW,
                due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                anki_card_id=597,
                anki_due=597,
            ),
        )
        # coll_b: rec anki_due=200, prod anki_due=201 (both lower than coll_a)
        db.update_direction_by_id(
            row_id_b,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.NEW,
                due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                anki_card_id=200,
                anki_due=200,
            ),
        )
        db.update_direction_by_id(
            row_id_b,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                state=SRSState.NEW,
                due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                anki_card_id=201,
                anki_due=201,
            ),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        new_items = [q for q in queue if q["state"] == "new"]
        # Higher anki_due first → coll_a (596) before coll_b (200); sibling-bury
        # removes the prod duplicate of each.
        assert len(new_items) >= 2
        assert new_items[0]["text"] == "coll_a"
        assert new_items[1]["text"] == "coll_b"

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

        # Pin last_unbury_day=today so the queue's daily unbury sweep is a no-op
        # and the buried state survives long enough to be filtered out.
        db.set_anki_state_cache("last_unbury_day", today.isoformat())

        # Create a collocation with recognition=buried, production=new
        unit = SyntacticUnit(text="buried_test", translation="test", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")

        rows, _ = db.list_collocations(search="buried_test", limit=1)
        row_id, item, _ = rows[0]

        # Set recognition as buried
        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.BURIED,
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
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

    async def test_review_queue_runs_daily_unbury_sweep(self, api_app_state):
        """Stale state='buried' rows (from a prior day) are restored on first queue load."""
        from datetime import date, timedelta

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        # Pretend the last sweep ran yesterday.
        db.set_anki_state_cache("last_unbury_day", yesterday)

        unit = SyntacticUnit(text="stale_buried", translation="x", word_count=2, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        rows, _ = db.list_collocations(search="stale_buried", limit=1)
        row_id, item, _ = rows[0]
        # Stale-buried row: reps=4 → should restore to REVIEW under sweep.
        # bury_kind='sched' so the daily sweep releases it (user-bury would stick).
        orig = item.directions[Direction.RECOGNITION]
        db.update_direction_by_id(
            row_id,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.BURIED,
                due_at=datetime.combine(orig.due_at.date(), time(4, 0), tzinfo=UTC),
                stability=orig.stability,
                difficulty=orig.difficulty,
                reps=4,
                lapses=orig.lapses,
                anki_card_id=12345,
                bury_kind="sched",
            ),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.get("/api/srs/review-queue")

        item_after = db.get_collocation("stale_buried")
        assert item_after.directions[Direction.RECOGNITION].state == SRSState.REVIEW

    async def test_merge_directions_sinks_phantom_directions(self, api_app_state):
        """Layer 33: a `state=new` direction whose collocation IS already linked
        to an Anki note (anki_note_id IS NOT NULL) but whose own anki_due is NULL
        is a stale/phantom direction (cross-note link, sync mismatch, etc.). It
        must NOT surface above synced rows. Only fresh /listen auto-adds —
        whose collocation.anki_note_id IS NULL — get the NULLS FIRST treatment.
        """
        from datetime import date

        from app.api.srs import _merge_directions

        # Phantom: collocation linked to Anki, but this direction has anki_due=NULL.
        phantom = self._make_item(date(2026, 1, 1), 999, Direction.PRODUCTION, anki_due=None)
        phantom.anki_note_id = 12345

        # Fresh /listen add: anki_note_id is NULL → genuinely new to TT.
        fresh = self._make_item(date(2026, 1, 1), 1, Direction.PRODUCTION, anki_due=None)
        fresh.anki_note_id = None

        # Synced row: anki_due=500.
        synced = self._make_item(date(2026, 1, 1), 2, Direction.PRODUCTION, anki_due=500)
        synced.anki_note_id = 99999

        rec = []
        prod = [(1, fresh, "sl"), (2, synced, "sl"), (3, phantom, "sl")]
        result = _merge_directions(rec, prod)

        order = [item.anki_note_id for _, item, _, _ in result]
        assert order == [None, 99999, 12345], f"unexpected order: {order}"

    async def test_review_queue_new_head_unaffected_by_overfetch_truncation(self, api_app_state):
        """Layer 32 overfetch sizing, re-grounded for Phase 3. The new-card pool is
        fetched unbounded-ish per direction so the merge sees every introducible
        card. Setup: 120 production-only-new notes (recognition already review) with
        high anki_due, plus a paired both-NEW note (paired_low, rec=10/prod=11).

        Phase 3 (corrected from the old "production-first" premise): paired_low's
        production is gated out because its recognition is still NEW, so the gate —
        not an overfetch-truncation accident — leaves recognition to surface, and
        the template sort (ord 0 before ord 1) puts it at the head. The rec=review
        notes' productions remain introducible and the overfetch keeps them
        un-truncated. Empirically Anki introduces recognition before production
        (604/36 across the user's paired notes), so recognition-first is correct.
        """
        from datetime import date, timedelta

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()
        db.set_anki_state_cache("daily_new_cap", "5")

        # Lots of high-due production-only-new notes (recognition is state=review).
        for i in range(120):
            anki_due_prod = 1_000_000 + i
            txt = f"prod_only_{i}"
            db.add_collocation(
                SyntacticUnit(text=txt, translation="t", word_count=1, difficulty=1, source="test"),
                language_code="sl",
            )
            rows, _ = db.list_collocations(search=txt, limit=1)
            row_id, _, _ = rows[0]
            db.update_direction_by_id(
                row_id,
                Direction.RECOGNITION,
                DirectionState(
                    direction=Direction.RECOGNITION,
                    state=SRSState.REVIEW,
                    due_at=datetime.combine(today + timedelta(days=30), time(4, 0), tzinfo=UTC),
                    anki_card_id=anki_due_prod - 1,
                    anki_due=anki_due_prod - 1,
                    stability=10.0,
                ),
            )
            db.update_direction_by_id(
                row_id,
                Direction.PRODUCTION,
                DirectionState(
                    direction=Direction.PRODUCTION,
                    state=SRSState.NEW,
                    due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                    anki_card_id=anki_due_prod,
                    anki_due=anki_due_prod,
                ),
            )

        # One paired note with LOW anki_due in both ords. With small overfetch its
        # prod gets dropped, leaving rec to wrongly survive bury.
        db.add_collocation(
            SyntacticUnit(text="paired_low", translation="t", word_count=1, difficulty=1, source="test"),
            language_code="sl",
        )
        rows, _ = db.list_collocations(search="paired_low", limit=1)
        row_id, _, _ = rows[0]
        db.update_direction_by_id(
            row_id,
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.NEW,
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                anki_card_id=10,
                anki_due=10,
            ),
        )
        db.update_direction_by_id(
            row_id,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                state=SRSState.NEW,
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                anki_card_id=11,
                anki_due=11,
            ),
        )

        from app.api.srs import _compute_live_main

        live = _compute_live_main(db)
        new_cards = [t for t in live if t[1].directions[t[3]].state == SRSState.NEW]
        assert new_cards
        # Phase 3 + template sort: paired_low's recognition (its production is gated
        # out while recognition is NEW) is the only ord=0 new card and sorts ahead
        # of the production-only notes' ord=1 cards.
        first_new = new_cards[0]
        assert (first_new[1].syntactic_unit.text, first_new[3]) == ("paired_low", Direction.RECOGNITION)
        prod_texts = {t[1].syntactic_unit.text for t in new_cards if t[3] == Direction.PRODUCTION}
        # rec=review notes' productions are introducible and not truncated by overfetch.
        assert any(txt.startswith("prod_only_") for txt in prod_texts)
        # paired_low's production stays held while its recognition is NEW.
        assert "paired_low" not in prod_texts

    async def test_review_queue_new_head_recognition_first_for_paired_new(self, api_app_state):
        """Phase 3 (corrected): for paired-NEW notes the new-queue head is
        RECOGNITION — production is held until recognition graduates. This matches
        Anki, which is direction-agnostic and orders new cards by deck position
        (recognition cards sit at a lower position than production), so recognition
        is introduced first — empirically 604/36 across the user's paired notes.
        Supersedes the earlier "production-first" assertion (Layer 28), whose
        premise was wrong. Two both-NEW notes:
          - časa: rec anki_due=1001997, prod anki_due=1001998
          - sekira: rec anki_due=1001974, prod anki_due=1001974
        Both productions are gated out (recognition still NEW); the surviving
        recognitions order by gather (anki_due DESC): časa, then sekira.
        """
        from datetime import date

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()
        # No reviews or learning today — just probe the new-bucket head.
        for txt, rec_due, prod_due in [
            ("časa", 1001997, 1001998),
            ("sekira", 1001974, 1001974),
        ]:
            unit = SyntacticUnit(text=txt, translation="t", word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            rows, _ = db.list_collocations(search=txt, limit=1)
            row_id, item, _ = rows[0]
            db.update_direction_by_id(
                row_id,
                Direction.RECOGNITION,
                DirectionState(
                    direction=Direction.RECOGNITION,
                    state=SRSState.NEW,
                    due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                    anki_card_id=rec_due,
                    anki_due=rec_due,
                ),
            )
            db.update_direction_by_id(
                row_id,
                Direction.PRODUCTION,
                DirectionState(
                    direction=Direction.PRODUCTION,
                    state=SRSState.NEW,
                    due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                    anki_card_id=prod_due,
                    anki_due=prod_due,
                ),
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        queue = resp.json()["queue"]
        new_items = [q for q in queue if q["state"] == "new"]
        assert len(new_items) >= 2
        # Phase 3: both notes are paired-NEW, so both productions are gated out;
        # the surviving recognitions order by gather (anki_due DESC): časa, sekira.
        assert (new_items[0]["text"], new_items[0]["direction"]) == ("časa", "recognition")
        assert (new_items[1]["text"], new_items[1]["direction"]) == ("sekira", "recognition")
        assert all(q["direction"] == "recognition" for q in new_items)

    async def test_review_queue_includes_audio_url_when_audio_exists(self, api_app_state):
        from datetime import date

        db = api_app_state
        row_id = seed_direction(
            db,
            text="mleko",
            translation="milk",
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=date.today(),
        )
        db.add_media(
            row_id,
            kind="audio_forvo",
            filename="sl_mleko.mp3",
            path="/tmp/sl_mleko.mp3",
            anki_filename="sl_mleko.mp3",
            sha256="abc",
            size_bytes=100,
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        item = next(q for q in queue if q["text"] == "mleko")
        assert item["audio_url"] == "/api/srs/media/sl_mleko.mp3"

    async def test_review_queue_includes_image_url_when_image_exists(self, api_app_state):
        from datetime import date

        db = api_app_state
        row_id = seed_direction(
            db,
            text="jabolko",
            translation="apple",
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=date.today(),
        )
        db.add_media(
            row_id,
            kind="image",
            filename="apple.jpg",
            path="/tmp/apple.jpg",
            anki_filename="apple.jpg",
            sha256="def",
            size_bytes=200,
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        item = next(q for q in queue if q["text"] == "jabolko")
        assert item["image_url"] == "/api/srs/media/apple.jpg"

    async def test_flat_fields_track_the_queued_direction_not_recognition(self, api_app_state):
        """For dual-direction vocab where the QUEUED direction is production, the
        flat fields (reps, last_review, due_date, stability, difficulty, lapses)
        must reflect production's values — not recognition's.

        Regression: `_queue_item_to_dict` previously overrode only `state`,
        leaving the rest leaking through from `_item_to_dict`'s recognition-only
        defaulting. Symptom: a well-trained card (production reps=27, last_review
        yesterday) would appear in the API as reps=6, last_review=null because
        recognition had different stats. Confuses any frontend that inspects
        those fields to decide rendering.
        """
        from datetime import UTC, date, datetime, timedelta

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()

        unit = SyntacticUnit(text="podpisati", translation="sign", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("podpisati")

        # Production is the QUEUED direction: due today, heavily reviewed.
        prod_ds = DirectionState(
            direction=Direction.PRODUCTION,
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
            stability=2.0,
            difficulty=6.5,
            reps=27,
            lapses=1,
            state=SRSState.REVIEW,
            last_review=datetime.now(UTC) - timedelta(days=1),
        )
        db.update_direction(item.guid, Direction.PRODUCTION, prod_ds)

        # Recognition is NOT queued today: future due, fewer reps, no last_review.
        rec_ds = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(today + timedelta(days=25), time(4, 0), tzinfo=UTC),
            stability=29.365,
            difficulty=7.169,
            reps=6,
            lapses=0,
            state=SRSState.REVIEW,
            last_review=None,
        )
        db.update_direction(item.guid, Direction.RECOGNITION, rec_ds)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]
        hit = next(q for q in queue if q["text"] == "podpisati")
        assert hit["direction"] == "production"
        # Each of these is recognition's wrong value pre-fix.
        assert hit["state"] == "review"
        assert hit["reps"] == 27, f"reps must come from production; got {hit['reps']} (recognition has 6)"
        assert hit["lapses"] == 1, f"lapses must come from production; got {hit['lapses']}"
        assert hit["stability"] == 2.0, f"stability must come from production; got {hit['stability']}"
        assert hit["difficulty"] == 6.5, f"difficulty must come from production; got {hit['difficulty']}"
        assert hit["due_at"].startswith(today.isoformat()), (
            f"due_at must come from production; got {hit['due_at']} (recognition is +25d)"
        )
        assert hit["last_review"] is not None, "last_review must come from production (graded yesterday), not None"


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
        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            state=SRSState.REVIEW,
        )
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
        rec_dir = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            state=SRSState.REVIEW,
        )
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


class TestLearningStatePriority:
    """Learning-state cards should sort before review cards (Anki queue=1 behavior)."""

    async def test_review_queue_learning_state_sorts_before_overdue_review(self, api_app_state):
        """Regression: ženska prod (learning) should appear before tovornjak prod (overdue review)."""
        from datetime import date, timedelta

        db = api_app_state
        today = date.today()
        # Anchor learning due_at to a definite past instant, not today-04:00-UTC.
        # `seed_direction(due_date=...)` derives due_at from `date + 04:00 UTC`,
        # which lands in the future when CI runs between 00:00–04:00 UTC and
        # drops the LEARNING card into pending_learning instead of ready_learning.
        ready_due_at = datetime.now(UTC) - timedelta(minutes=5)

        # tovornjak prod: state=review, due_date=yesterday (overdue), stability=0.116
        seed_direction(
            db,
            text="tovornjak",
            translation="truck",
            direction=Direction.PRODUCTION,
            state=SRSState.REVIEW,
            due_date=today - timedelta(days=1),
            stability=0.116,
            last_review=today - timedelta(days=2),
        )

        # ženska prod: state=learning, due_at = 5 minutes ago, stability=0.036, last_review=NULL
        seed_direction(
            db,
            text="ženska",
            translation="woman",
            direction=Direction.PRODUCTION,
            state=SRSState.LEARNING,
            due_at=ready_due_at,
            stability=0.036,
        )

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

    async def test_learning_bucket_orders_by_anki_card_id_when_due_unset(self, api_app_state):
        """Learning cards with no due_at and no anki_due fall through to anki_card_id ASC.

        Mirrors Anki's `(reps==0, due)` sort + SQLite stable scan order on `cards.id`.
        Stability is NOT in the key (regression: TT used to insert stability between
        anki_due and anki_card_id, which diverged from Anki when two cards shared due).
        """
        from datetime import date

        db = api_app_state
        today = date.today()

        # Anki-card-id ordering: lower id should come first.
        # Stabilities are assigned out-of-order on purpose so the test would fail if
        # the sort key still considered stability.
        cards = [
            ("mnozica", 300, 0.26),  # lowest id, highest stability
            ("clovek", 301, 0.01),  # middle id, lowest stability
            ("dekle", 302, 0.05),  # highest id, middle stability
        ]
        for text, anki_id, stability in cards:
            seed_direction(
                db,
                text=text,
                translation=f"trans_{text}",
                direction=Direction.PRODUCTION,
                state=SRSState.LEARNING,
                due_date=today,
                stability=stability,
                anki_card_id=anki_id,
            )

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
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
            last_review=today,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, rec_dir)

        prod_dir = DirectionState(
            direction=Direction.PRODUCTION,
            state=SRSState.LEARNING,
            due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
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
            seed_direction(
                db,
                text=text,
                translation=f"trans_{text}",
                direction=Direction.PRODUCTION,
                state=SRSState.LEARNING,
                due_date=today,
                stability=stability,
                anki_card_id=anki_id,
                anki_due=anki_due_ts,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]

        # ženska must come before dojenček despite higher stability — anki_due dominates
        zenska_idx = next(i for i, q in enumerate(queue) if q["text"] == "zenska")
        dojencek_idx = next(i for i, q in enumerate(queue) if q["text"] == "dojencek")
        assert zenska_idx < dojencek_idx, "ženska (earlier anki_due) must come before dojenček"

    async def test_learning_bucket_tied_due_orders_by_anki_card_id_not_stability(self, api_app_state):
        """Regression for vrh/srajca: when two queue=1 cards share the same `due_at` AND the
        same `anki_due` (sub-day epoch second), Anki tiebreaks by `cards.id` ASC (its SQL has
        no ORDER BY, so SQLite's stable scan order — effectively rowid/id — wins). TT must
        match: lower `anki_card_id` first, regardless of stability.

        Pre-fix bug: TT inserted `stability` between `anki_due` and `anki_card_id`, so the
        less-stable card jumped ahead even when Anki showed the lower-id card first.
        """
        from datetime import UTC, date, datetime

        db = api_app_state
        today = date.today()
        # Both cards lapsed in the same review session: identical due_at, identical anki_due.
        shared_due_at = datetime(2026, 5, 9, 2, 11, 16, tzinfo=UTC)
        shared_anki_due = 1778292676

        # Lower anki_card_id has HIGHER stability — opposite of TT's old "lower stability wins" tiebreak,
        # so without the fix TT would surface the high-id (low-stability) card first.
        cards = [
            ("srajca", 1775264031875, 0.861),  # lower id, higher stability — Anki shows this first
            ("vrh", 1775264032476, 0.659),  # higher id, lower stability
        ]
        for text, anki_id, stability in cards:
            seed_direction(
                db,
                text=text,
                translation=f"trans_{text}",
                direction=Direction.PRODUCTION,
                state=SRSState.RELEARNING,
                due_date=today,
                stability=stability,
                anki_card_id=anki_id,
                anki_due=shared_anki_due,
                due_at=shared_due_at,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]

        srajca_idx = next(i for i, q in enumerate(queue) if q["text"] == "srajca")
        vrh_idx = next(i for i, q in enumerate(queue) if q["text"] == "vrh")
        assert srajca_idx < vrh_idx, (
            "srajca (lower anki_card_id) must come before vrh — Anki tiebreaks queue=1 by cards.id, not stability"
        )

    async def test_learning_card_due_after_cutoff_does_not_preempt(self, api_app_state):
        """Regression for svetilka/jabolko: TT must mirror Anki's frozen-cutoff semantics.

        Anki snapshots `current_learning_cutoff` at grade time and only advances it on the
        next grade event. A learning card whose timer expires *between* grades must NOT
        preempt the currently-displayed card (a new/review). It only surfaces after the
        next grade advances the cutoff.

        Pre-fix bug: TT used live `now` for the ready/pending split, so a learning card
        ticking past-due mid-session jumped ahead of the new card Anki was still showing.
        """
        from datetime import UTC, date, datetime, timedelta

        db = api_app_state
        today = date.today()
        now = datetime.now(UTC)

        # Cutoff is 5 minutes ago — simulates "user graded a card 5 min ago, hasn't graded since".
        cutoff = now - timedelta(minutes=5)
        db.set_anki_state_cache("learning_cutoff", cutoff.isoformat())

        # Learning card: due_at is 1 minute ago (past `now`) but FUTURE relative to cutoff.
        # At cutoff time, this card was 4 minutes away from due. Anki's frozen cutoff has
        # not advanced, so it must remain pending.
        learning_due_at = now - timedelta(minutes=1)
        seed_direction(
            db,
            text="late_learn",
            translation="trans",
            direction=Direction.PRODUCTION,
            state=SRSState.LEARNING,
            due_date=today,
            stability=1.0,
            due_at=learning_due_at,
            anki_card_id=42,
        )

        # New card: should be served first because the learning card is still pending vs cutoff.
        seed_direction(
            db,
            text="newcard",
            translation="trans",
            direction=Direction.PRODUCTION,
            state=SRSState.NEW,
            due_date=today,
            anki_card_id=43,
            anki_due=1,
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]

        new_idx = next(i for i, q in enumerate(queue) if q["text"] == "newcard")
        learn_idx = next(i for i, q in enumerate(queue) if q["text"] == "late_learn")
        assert new_idx < learn_idx, (
            "new card must come before learning card whose due_at is past `now` but future "
            "relative to the frozen cutoff — Anki does not preempt the displayed card"
        )

    @pytest.mark.parametrize(
        "label,ripe,has_future,main_card,expect_bump",
        [
            ("empty_main_ripe_pending", True, False, False, True),
            ("empty_main_ripe_and_future", True, True, False, True),
            ("nonempty_main_ripe_pending", True, False, True, False),
            ("empty_main_only_future", False, True, False, False),
        ],
    )
    async def test_review_queue_auto_bump(self, label, ripe, has_future, main_card, expect_bump, api_app_state):
        """Anki-parity auto-bump behavior across 4 scenarios (see parametrize table)."""
        from datetime import UTC, date, datetime, timedelta

        db = api_app_state
        today = date.today()
        now = datetime.now(UTC)
        cutoff = now - timedelta(minutes=5)
        db.set_anki_state_cache("learning_cutoff", cutoff.isoformat())

        def _add_learning(text: str, due_at: datetime, cid: int):
            seed_direction(
                db,
                text=text,
                translation="trans",
                direction=Direction.PRODUCTION,
                state=SRSState.LEARNING,
                due_date=today,
                stability=1.0,
                due_at=due_at,
                anki_card_id=cid,
            )

        def _add_new_card(text: str, cid: int):
            seed_direction(
                db,
                text=text,
                translation="trans",
                direction=Direction.PRODUCTION,
                state=SRSState.NEW,
                due_date=today,
                anki_card_id=cid,
                anki_due=1,
            )

        if ripe:
            _add_learning("ripe_card", now - timedelta(minutes=1), 42)
        if has_future:
            _add_learning("future_card", now + timedelta(minutes=10), 43)
        if main_card:
            _add_new_card("main_card", 44)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        assert resp.status_code == 200
        queue = resp.json()["queue"]

        if expect_bump:
            assert queue[0]["text"] in ("ripe_card",), "ripe pending learning card must surface at head"
            cached = db.get_anki_state_cache("learning_cutoff")
            assert datetime.fromisoformat(cached[0]) >= now - timedelta(seconds=1)
            if has_future:
                future_idx = next(i for i, q in enumerate(queue) if q["text"] == "future_card")
                assert future_idx > 0, "future learning card stays pending after bump"
        else:
            cached = db.get_anki_state_cache("learning_cutoff")
            assert datetime.fromisoformat(cached[0]) == cutoff, "cutoff must not advance"
            if main_card:
                main_idx = next(i for i, q in enumerate(queue) if q["text"] == "main_card")
                ripe_idx = next(i for i, q in enumerate(queue) if q["text"] == "ripe_card")
                assert main_idx < ripe_idx, "main card precedes ripe learning when no bump"

    async def test_feedback_advances_learning_cutoff(self, api_app_state):
        """Grading any card must advance `learning_cutoff` to ~now, mirroring Anki's
        update_learning_cutoff_and_count call after each answer.
        """
        from datetime import UTC, date, datetime, timedelta

        db = api_app_state
        today = date.today()

        # Stale cutoff far in the past.
        stale_cutoff = datetime(2020, 1, 1, tzinfo=UTC)
        db.set_anki_state_cache("learning_cutoff", stale_cutoff.isoformat())

        row_id = seed_direction(
            db,
            text="grade_test",
            translation="trans",
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            stability=1.0,
        )

        before = datetime.now(UTC)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/api/srs/items/{row_id}/direction/recognition/feedback", json={"rating": "good"})
        assert resp.status_code == 200

        cached = db.get_anki_state_cache("learning_cutoff")
        assert cached is not None, "feedback endpoint must populate learning_cutoff cache"
        cached_at = datetime.fromisoformat(cached[0])
        assert cached_at >= before - timedelta(seconds=1), (
            f"learning_cutoff ({cached_at.isoformat()}) must be ≥ pre-grade `now` ({before.isoformat()}) — "
            f"not the stale value {stale_cutoff.isoformat()}"
        )


class TestJustGradedLearningCollapse:
    """Anki parity: when a learning card is graded and main is empty, the just-
    graded card's queue position is shifted past the next-soonest pending card
    so the user doesn't see the same card immediately. Mirrors Anki's
    `requeue_learning_entry` collapse in rslib/scheduler/queue/learning.rs:94-113.
    Without this, TT shows the just-graded card again because its `due_at` is
    still the smallest among pending learning cards.
    """

    async def test_just_graded_card_yields_to_next_pending_when_main_empty(self, api_app_state):
        """Two pending learning cards. Head was just graded (last_review ==
        cutoff). With main empty, queue must serve the OTHER card first.
        """
        from datetime import UTC, date, datetime, timedelta

        from app.srs.queue_stats import advance_learning_cutoff

        db = api_app_state
        today = date.today()
        now = datetime.now(UTC)

        # Pin the learning cutoff at `now` (simulates the grade event).
        advance_learning_cutoff(db, now)

        # Two pending learning cards: srebro (just graded, head) and družina.
        for text, due_offset_min, last_review in [
            ("srebro", 1, now),
            ("družina", 9, now - timedelta(hours=1)),
        ]:
            seed_direction(
                db,
                text=text,
                translation="t",
                direction=Direction.RECOGNITION,
                state=SRSState.LEARNING,
                due_date=today + timedelta(days=1),
                stability=0.5,
                difficulty=8.0,
                reps=2,
                lapses=0,
                left=1001,
                due_at=now + timedelta(minutes=due_offset_min),
                last_review=last_review,
                anki_card_id=100 + due_offset_min,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        queue = resp.json()["queue"]
        # Without collapse: queue[0] = srebro. With collapse: queue[0] = družina.
        assert queue[0]["text"] == "družina"
        assert queue[1]["text"] == "srebro"

    async def test_collapse_does_not_fire_when_head_was_not_just_graded(self, api_app_state):
        """Outer condition (main empty, ≥2 pending) holds, but inner check
        fails because the head's last_review doesn't match the cutoff (no
        recent grade). Order stays as-sorted by due_at.
        """
        from datetime import UTC, date, datetime, timedelta

        from app.srs.queue_stats import advance_learning_cutoff

        db = api_app_state
        today = date.today()
        now = datetime.now(UTC)
        # Cutoff is "now", but no card has last_review matching it.
        advance_learning_cutoff(db, now)

        for text, due_offset_min in [("first", 1), ("second", 9)]:
            seed_direction(
                db,
                text=text,
                translation="t",
                direction=Direction.RECOGNITION,
                state=SRSState.LEARNING,
                due_date=today + timedelta(days=1),
                stability=0.5,
                difficulty=8.0,
                reps=2,
                lapses=0,
                left=1001,
                due_at=now + timedelta(minutes=due_offset_min),
                last_review=now - timedelta(hours=1),
                anki_card_id=100 + due_offset_min,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        queue = resp.json()["queue"]
        assert queue[0]["text"] == "first"
        assert queue[1]["text"] == "second"

    async def test_collapse_does_not_fire_when_main_is_nonempty(self, api_app_state):
        """When main has cards, the collapse must NOT fire — main is served
        next, so the just-graded card stays in pending naturally."""
        from datetime import UTC, date, datetime, timedelta

        from app.srs.queue_stats import advance_learning_cutoff

        db = api_app_state
        today = date.today()
        now = datetime.now(UTC)
        advance_learning_cutoff(db, now)

        # One due review (populates main).
        seed_direction(
            db,
            text="rev_card",
            translation="t",
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            stability=1.0,
            reps=5,
            anki_card_id=500,
        )

        # Two pending learning cards, head just graded.
        for text, due_offset_min, last_review in [
            ("srebro", 1, now),
            ("družina", 9, now - timedelta(hours=1)),
        ]:
            seed_direction(
                db,
                text=text,
                translation="t",
                direction=Direction.RECOGNITION,
                state=SRSState.LEARNING,
                due_date=today + timedelta(days=1),
                stability=0.5,
                difficulty=8.0,
                reps=2,
                lapses=0,
                left=1001,
                due_at=now + timedelta(minutes=due_offset_min),
                last_review=last_review,
                anki_card_id=100 + due_offset_min,
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        queue = resp.json()["queue"]
        # main has rev_card, served first. Then pending in natural order: srebro, družina.
        # No collapse because main wasn't empty.
        srebro_idx = next(i for i, c in enumerate(queue) if c["text"] == "srebro")
        druzina_idx = next(i for i, c in enumerate(queue) if c["text"] == "družina")
        assert srebro_idx < druzina_idx, "with main non-empty, pending order stays srebro→družina (no collapse)"


class TestSessionMainQueueFreeze:
    """Anki parity: `main` (review+new spread mix) is built once per day and frozen.

    Subsequent /review-queue calls return the cached order, filtered to remove
    cards no longer in the live due-pool (graded today, suspended, etc.).
    Without the freeze, TT recomputes the intersperser on every poll and always
    serves the lowest-R review next — diverging from Anki whenever the
    intersperser would have placed a new card mid-sequence.
    """

    async def test_two_consecutive_calls_return_same_order_when_state_unchanged(self, api_app_state):
        """The frozen main queue must not reorder between calls when underlying state is stable."""
        from datetime import date

        db = api_app_state
        today = date.today()
        # Two reviews with very different retrievabilities — the lower-R one would
        # jump to the head if we recomputed instead of using the cached order.
        seed_direction(
            db,
            text="high_r",
            translation="hr",
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            stability=100.0,
            anki_card_id=1,
        )
        seed_direction(
            db,
            text="low_r",
            translation="lr",
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            stability=0.05,
            anki_card_id=2,
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/srs/review-queue")
            r2 = await client.get("/api/srs/review-queue")
        order1 = [(q["id"], q["direction"]) for q in r1.json()["queue"]]
        order2 = [(q["id"], q["direction"]) for q in r2.json()["queue"]]
        assert order1 == order2, "second call must return the cached frozen order"

    async def test_intersperser_position_is_preserved_after_grading_head(self, api_app_state):
        """Anki regression (bogat/jabolko): after grading the first few reviews from the
        frozen main queue, the next card must be whatever the intersperser placed at the
        next position — even if it's a new card sitting between higher-R reviews.
        """
        from datetime import date, timedelta

        db = api_app_state
        today = date.today()

        # 4 reviews + 1 new. Intersperser ratio=(4+1)/(1+1)=2.5; the new card
        # lands at position 2: [rev0, rev1, new0, rev2, rev3].
        # rev0..rev3 have monotonically rising stability so rev0 is lowest-R
        # (front of the review sub-queue). After grading rev0 + rev1 a rebuild
        # would surface rev2 next; the frozen cache must serve new0 instead.
        review_specs = [
            ("rev0", 0.01, 1001),
            ("rev1", 0.05, 1002),
            ("rev2", 0.10, 1003),
            ("rev3", 0.20, 1004),
        ]
        for text, stab, anki_id in review_specs:
            seed_direction(
                db,
                text=text,
                translation=f"t_{text}",
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_date=today,
                stability=stab,
                anki_card_id=anki_id,
            )

        seed_direction(
            db,
            text="new0",
            translation="t_new0",
            direction=Direction.RECOGNITION,
            state=SRSState.NEW,
            due_date=today,
            anki_card_id=2001,
            anki_due=1,
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/srs/review-queue")
            order1 = [q["text"] for q in r1.json()["queue"]]
            assert order1[:5] == ["rev0", "rev1", "new0", "rev2", "rev3"], (
                f"intersperser should place new card at position 2 with 4 reviews + 1 new; got {order1[:5]}"
            )

            # Grade rev0 + rev1 by transitioning them out of the due-pool.
            for text, _, anki_id in review_specs[:2]:
                rows, _ = db.list_collocations(search=text, limit=1)
                row_id, _, _ = rows[0]
                db.update_direction_by_id(
                    row_id,
                    Direction.RECOGNITION,
                    DirectionState(
                        direction=Direction.RECOGNITION,
                        state=SRSState.REVIEW,
                        due_at=datetime.combine(today + timedelta(days=10), time(4, 0), tzinfo=UTC),
                        stability=10.0,
                        anki_card_id=anki_id,
                    ),
                )

            # Live rebuild of (rev2, rev3, new0) would put rev2 first (lowest R).
            # Frozen cache must serve new0 first — its position in the original
            # frozen sequence — matching Anki's "main is built once and popped".
            r2 = await client.get("/api/srs/review-queue")
            order2 = [q["text"] for q in r2.json()["queue"]]
            assert order2[0] == "new0", (
                f"After grading positions 0-1, next must be new0 (frozen position 2), "
                f"not the rebuild-surfaced rev2. Got order: {order2}"
            )

    async def test_cache_invalidates_when_day_changes(self, api_app_state):
        """A stale cache from a previous day must not leak into today's queue."""
        import json
        from datetime import date, timedelta

        db = api_app_state
        today = date.today()

        # Pre-populate a cache from yesterday containing a stale card key.
        yesterday = today - timedelta(days=1)
        db.set_anki_state_cache(
            "session_main_queue",
            json.dumps({"day": yesterday.isoformat(), "items": [{"cid": 99999, "dir": "recognition"}]}),
        )

        seed_direction(
            db,
            text="fresh_card",
            translation="fc",
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            stability=1.0,
            anki_card_id=42,
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        order = [q["text"] for q in resp.json()["queue"]]
        assert "fresh_card" in order, "stale yesterday-cache must be discarded; today's queue must rebuild"

        # Cache should now be keyed on today.
        cached_row = db.get_anki_state_cache("session_main_queue")
        assert cached_row is not None
        assert json.loads(cached_row[0])["day"] == today.isoformat()

    async def test_session_start_advances_learning_cutoff_to_now(self, api_app_state):
        """Regression for orodje vs bogat: page mount must advance the cutoff so
        learning cards whose timer expired during the user's idle period jump back
        into `ready_learning` (mirrors Anki advancing `current_learning_cutoff` at
        every deck-open via `update_learning_cutoff_and_count`).

        Without `session_start=1`, the cutoff stays frozen at the last grade and a
        learning card with `due_at` even seconds past the stale cutoff sits in
        pending_learning at the tail — while Anki, which rebuilt at restart,
        surfaces it at the head.
        """
        from datetime import UTC, date, datetime, timedelta

        db = api_app_state
        today = date.today()
        now = datetime.now(UTC)

        # Stale cutoff from "earlier today" — before the learning card became due.
        stale_cutoff = now - timedelta(minutes=10)
        db.set_anki_state_cache("learning_cutoff", stale_cutoff.isoformat())

        # Learning card: due_at is past `now` but FUTURE relative to the stale cutoff.
        # With the stale cutoff this card is `pending_learning`; only after the
        # session_start advance does it become `ready_learning`.
        seed_direction(
            db,
            text="late_learn",
            translation="trans",
            direction=Direction.PRODUCTION,
            state=SRSState.LEARNING,
            due_date=today,
            stability=1.0,
            due_at=now - timedelta(minutes=1),
            anki_card_id=42,
        )

        # A review card so the queue has both candidates.
        seed_direction(
            db,
            text="rev_card",
            translation="rev",
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            stability=1.0,
            anki_card_id=43,
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Without session_start: stale cutoff → learning card pending → review first.
            r1 = await client.get("/api/srs/review-queue")
            order1 = [q["text"] for q in r1.json()["queue"]]
            rev_idx = order1.index("rev_card")
            late_idx = order1.index("late_learn")
            assert rev_idx < late_idx, f"with stale cutoff, learning card stays pending at tail; got {order1}"

            # With session_start=1: cutoff advances to ~now → learning card is ready.
            r2 = await client.get("/api/srs/review-queue?session_start=1")
            order2 = [q["text"] for q in r2.json()["queue"]]
            assert order2[0] == "late_learn", f"after session_start, learning card must be at head; got {order2}"

    async def test_new_state_latecomer_appended_at_tail(self, api_app_state):
        """A new-state collocation imported mid-day (after the cache was frozen)
        must be tail-appended — TT's user-facing parity allowance for mid-day
        additions. Review-state latecomers, in contrast, are dropped (see
        test_review_state_latecomer_is_dropped).
        """
        import json
        from datetime import date

        db = api_app_state
        today = date.today()

        row_a = seed_direction(
            db,
            text="card_a",
            translation="ta",
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            stability=1.0,
            anki_card_id=100,
        )
        seed_direction(
            db,
            text="card_new",
            translation="tn",
            direction=Direction.RECOGNITION,
            state=SRSState.NEW,
            due_date=today,
            stability=1.0,
            anki_card_id=200,
            anki_due=1,
        )

        # Cache pretends only card_a was in the original frozen order.
        db.set_anki_state_cache(
            "session_main_queue",
            json.dumps({"day": today.isoformat(), "items": [{"cid": row_a, "dir": "recognition"}]}),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        order = [q["text"] for q in resp.json()["queue"]]
        assert "card_new" in order, f"new-state latecomer must be tail-appended; got {order}"
        assert order.index("card_a") < order.index("card_new"), (
            f"cached card_a must come before tail-appended card_new; got {order}"
        )

    async def test_intersperser_uses_start_of_day_ratio(self, api_app_state):
        """Regression for the obraz-vs-spalnica drift: TT must compute the
        intersperser ratio from session-start counts (R_remaining + graded_today,
        new_pool_at_session_start), not current remaining counts. Otherwise TT
        places new cards at a tighter spacing than Anki's session-start ratio.

        Internally consistent Anki-mirror: at session start a 10-review + 3-new
        queue would have placed news at intersperser positions ~2.75, ~5.5, ~8.25.
        The user has graded 8 from the head — 6 reviews + 2 news. TT now has 4
        review-due + 1 new + 8 reviewed today (6 review-state, 2 new-state).
          - Current-counts ratio: (4+1)/(1+1) = 2.50 → news at position 1 or 2
          - Start-of-day ratio:   (12+1)/(3+1) = 3.25 → news at position 2 or 3
        Asserting first_new >= 2 distinguishes start-of-day from current-counts.
        """
        from datetime import UTC, date, datetime, timedelta

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()

        db.set_anki_state_cache("daily_new_cap", "10")
        graded_lr = anki_day_anchor(today).isoformat()

        # 6 review-state cards graded earlier today (prior_state=review).
        for i in range(6):
            unit = SyntacticUnit(text=f"graded_rev_{i}", translation="t", word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            item = db.get_collocation(f"graded_rev_{i}")
            db.update_direction(
                item.guid,
                Direction.RECOGNITION,
                DirectionState(
                    direction=Direction.RECOGNITION,
                    state=SRSState.REVIEW,
                    due_at=datetime.combine(today + timedelta(days=3), time(4, 0), tzinfo=UTC),
                    stability=2.0,
                    reps=6,
                    lapses=0,
                    last_review=datetime.fromisoformat(graded_lr),
                    prior_state=SRSState.REVIEW,
                    anki_card_id=1000 + i,
                ),
            )

        # 2 new-state cards graded earlier today (prior_state=new → counts toward N_start).
        for i in range(2):
            unit = SyntacticUnit(text=f"intro_{i}", translation="t", word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            item = db.get_collocation(f"intro_{i}")
            db.update_direction(
                item.guid,
                Direction.RECOGNITION,
                DirectionState(
                    direction=Direction.RECOGNITION,
                    state=SRSState.LEARNING,
                    due_at=datetime.combine(today + timedelta(days=1), time(4, 0), tzinfo=UTC),
                    stability=1.0,
                    reps=1,
                    lapses=0,
                    last_review=datetime.fromisoformat(graded_lr),
                    prior_state=SRSState.NEW,
                    anki_card_id=1500 + i,
                    introduced_at=datetime.fromisoformat(graded_lr),
                ),
            )

        # 4 review-state cards still due today (form the remaining review pool).
        for i in range(4):
            seed_direction(
                db,
                text=f"rev_{i}",
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_date=today,
                stability=1.0,
                reps=5,
                lapses=0,
                anki_card_id=2000 + i,
            )

        # 1 remaining new card.
        seed_direction(
            db,
            text="new_remain",
            direction=Direction.RECOGNITION,
            state=SRSState.NEW,
            due_date=today,
            stability=1.0,
            anki_card_id=3000,
            anki_due=1,
        )

        # Sanity-check the inputs to the override.
        assert db.count_review_due_collocations(today) == 4
        assert len(db.list_collocations_reviewed_today(today)) == 8
        assert db.count_new_introduced_today(today) == 2

        # Force a fresh build of session_main_queue.
        db.delete_anki_state_cache("session_main_queue")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        states = [q["state"] for q in resp.json()["queue"]]
        first_new_idx = next((i for i, s in enumerate(states) if s == "new"), None)
        assert first_new_idx is not None, f"no new card served; got states {states}"
        assert first_new_idx >= 2, (
            f"start-of-day ratio 3.25 places first new at pos >= 2; current-counts "
            f"ratio 2.50 would put it at pos 1. Got first_new_idx={first_new_idx}, "
            f"states={states[:5]}"
        )

    async def test_review_state_latecomer_is_dropped(self, api_app_state):
        """A review-state card that joins live_main but isn't in the cache must be
        dropped from today's queue, not tail-appended. Mirrors Anki's behavior:
        a queue=1→queue=2 graduation does NOT re-enter today's review flow
        (scheduler/queue/learning.rs:60-77; maybe_requeue_learning_card returns
        None for non-intraday-learning cards). Cache invalidation on sync /
        deck-config change is the legitimate path for review-state changes;
        this test guards the parity behavior in the meantime.
        """
        import json
        from datetime import date

        db = api_app_state
        today = date.today()

        row_a = seed_direction(
            db,
            text="cached_card",
            translation="ca",
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            stability=1.0,
            anki_card_id=100,
        )
        seed_direction(
            db,
            text="graduated_card",
            translation="gc",
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_date=today,
            stability=1.0,
            anki_card_id=200,
        )

        # Cache contains only cached_card — graduated_card is the "latecomer".
        db.set_anki_state_cache(
            "session_main_queue",
            json.dumps({"day": today.isoformat(), "items": [{"cid": row_a, "dir": "recognition"}]}),
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/review-queue")
        order = [q["text"] for q in resp.json()["queue"]]
        assert "cached_card" in order, f"cached card must remain; got {order}"
        assert "graduated_card" not in order, (
            f"review-state latecomer must be dropped (mirrors Anki excluding "
            f"graduated cards from today's flow); got {order}"
        )

    async def test_session_start_rebuilds_frozen_queue_with_current_pool(self, api_app_state):
        """Anki parity: `/review-queue?session_start=1` mirrors deck-open semantics.

        Anki rebuilds its queue every time the deck is opened (lazy build on first
        access). The frontend already sends `session_start=1` on /review mount.
        Before this fix, that flag only advanced `learning_cutoff`; the frozen
        main queue stayed at the last sync_pull snapshot. Result: if Anki was
        closed/reopened mid-day (which rebuilds Anki's queue with the post-grade
        pool) and TT was not synced, the two apps' intersperser positions
        diverged irreversibly until next sync.

        After this fix, session_start=1 also clears + rebuilds the frozen
        session_main_queue, so a fresh page-mount aligns TT's queue moment with
        Anki's.
        """
        from datetime import date

        from app.models.syntactic_unit import SyntacticUnit

        db = api_app_state
        today = date.today()

        # Seed two reviews. First /review-queue call will freeze with these.
        for text, anki_id, stab in [("alpha", 1001, 10.0), ("beta", 1002, 5.0)]:
            unit = SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            rows, _ = db.list_collocations(search=text, limit=1)
            row_id, _, _ = rows[0]
            db.update_direction_by_id(
                row_id,
                Direction.RECOGNITION,
                DirectionState(
                    direction=Direction.RECOGNITION,
                    state=SRSState.REVIEW,
                    due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                    stability=stab,
                    anki_card_id=anki_id,
                ),
            )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.get("/api/srs/review-queue")
            order1 = [q["text"] for q in r1.json()["queue"]]
            assert set(order1) == {"alpha", "beta"}, f"initial freeze should contain alpha + beta; got {order1}"

            # Add a new review card with very low R — under a fresh rebuild it
            # would land at the head. Under the existing frozen cache it must
            # NOT appear (review-state latecomers are dropped per Layer 29).
            seed_direction(
                db,
                text="gamma",
                translation="g",
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_date=today,
                stability=0.01,
                anki_card_id=1003,
            )

            # Without session_start: frozen cache stands; gamma is excluded.
            r2 = await client.get("/api/srs/review-queue")
            order2 = [q["text"] for q in r2.json()["queue"]]
            assert "gamma" not in order2, (
                f"sanity check: without session_start the frozen cache excludes "
                f"the review-state latecomer; got {order2}"
            )

            # With session_start=1: rebuild. gamma should join the queue.
            r3 = await client.get("/api/srs/review-queue?session_start=1")
            order3 = [q["text"] for q in r3.json()["queue"]]
            assert "gamma" in order3, (
                f"session_start=1 must rebuild the frozen queue and include the new review; got {order3}"
            )


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

        # Set up learning state at step 0 (Anki encoding: total_remaining=2 → left=2)
        from app.models.srs_item import Direction, DirectionState, SRSState

        now = datetime.now(UTC)
        dstate = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.LEARNING,
            stability=1.0,
            left=2,
            due_at=now + timedelta(minutes=1),  # Step 0: 1 minute
            dirty_fsrs=True,
            last_rating=3,
        )
        db.update_direction_by_id(row_id, Direction.RECOGNITION, dstate)

        # Rate Good (should advance to step 1: total_remaining decrements to 1)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(f"/api/srs/items/{row_id}/direction/recognition/feedback", json={"rating": "good"})
        assert resp.status_code == 200
        data = resp.json()

        # Should return learning state with due_at
        assert data["new_state"] == "learning"
        assert "new_due_at" in data, "Response should include new_due_at for learning cards"
        assert "left" in data, "Response should include left for learning cards"
        assert data["left"] == 1, f"Expected left=1 (total_remaining=1) after GOOD, got {data.get('left')}"

        # Parse new_due_at and verify it's in the future
        from datetime import datetime

        due_at = datetime.fromisoformat(data["new_due_at"])
        assert due_at > datetime.now(UTC), "new_due_at should be in the future"

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
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
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
        date.today()
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
                # FSRS rolled past local midnight
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
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
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


class TestIgnoredLemmas:
    async def test_add_ignored_lemma(self, api_app_state):
        db = api_app_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/ignored-lemmas",
                json={"lemma": "Ana", "language_code": "sl"},
            )
        assert resp.status_code == 200
        assert db.get_ignored_lemmas("sl") == {"ana"}

    async def test_add_ignored_lemma_lowercases(self, api_app_state):
        db = api_app_state
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/ignored-lemmas",
                json={"lemma": "AnA", "language_code": "sl"},
            )
        assert resp.status_code == 200
        assert db.get_ignored_lemmas("sl") == {"ana"}

    async def test_remove_ignored_lemma(self, api_app_state):
        db = api_app_state
        db.add_ignored_lemma("sl", "ana")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete(
                "/api/srs/ignored-lemmas?lemma=Ana&language_code=sl",
            )
        assert resp.status_code == 200
        assert db.get_ignored_lemmas("sl") == set()

    async def test_remove_nonexistent_ignored_lemma(self, api_app_state):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.delete(
                "/api/srs/ignored-lemmas?lemma=ana&language_code=sl",
            )
        assert resp.status_code == 200
