"""Tests for FSRS retrievability computation and short-term scheduler."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.srs_item import Direction, DirectionState, Rating, SRSItem, SRSState
from app.srs.fsrs import schedule


class TestComputeRetrievability:
    """Tests for compute_retrievability."""

    def test_null_stability_returns_one(self):
        """Null stability means no FSRS data — sort last."""
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=None,
            last_review=date.today(),
        )
        from app.srs.fsrs import compute_retrievability

        assert compute_retrievability(state, date.today()) == 1.0

    def test_null_last_review_returns_one(self):
        """Null last_review means never reviewed — sort last."""
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=0.5,
            last_review=None,
        )
        from app.srs.fsrs import compute_retrievability

        assert compute_retrievability(state, date.today()) == 1.0

    def test_both_null_returns_one(self):
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=None,
            last_review=None,
        )
        from app.srs.fsrs import compute_retrievability

        assert compute_retrievability(state, date.today()) == 1.0

    def test_low_stability_low_retrievability(self):
        """Lower stability → lower retrievability for same elapsed days."""
        today = date(2026, 5, 2)
        state_low = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date(2026, 5, 1),
            stability=0.086,
            last_review=date(2026, 5, 1),
        )
        state_high = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date(2026, 5, 1),
            stability=0.5,
            last_review=date(2026, 5, 1),
        )
        from app.srs.fsrs import compute_retrievability

        r_low = compute_retrievability(state_low, today)
        r_high = compute_retrievability(state_high, today)
        assert r_low < r_high

    def test_same_stability_same_retrievability(self):
        """Same stability and elapsed → same retrievability."""
        today = date(2026, 5, 2)
        state1 = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date(2026, 5, 1),
            stability=0.4,
            last_review=date(2026, 5, 1),
        )
        state2 = DirectionState(
            direction=Direction.PRODUCTION,
            due_date=date(2026, 5, 1),
            stability=0.4,
            last_review=date(2026, 5, 1),
        )
        from app.srs.fsrs import compute_retrievability

        assert compute_retrievability(state1, today) == compute_retrievability(state2, today)

    def test_elapsed_zero_returns_one(self):
        """Just reviewed today → retrievability ≈ 1."""
        today = date(2026, 5, 2)
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=today,
            stability=0.086,
            last_review=today,
        )
        from app.srs.fsrs import compute_retrievability

        r = compute_retrievability(state, today)
        assert r == 1.0

    def test_actual_values_from_plan(self):
        """Verify the exact values from the plan: prašič s=0.4 vs vlak s=0.086, today=2026-05-02, last_review=2026-05-01."""
        today = date(2026, 5, 2)
        prasic = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date(2026, 5, 1),
            stability=0.4,
            last_review=date(2026, 5, 1),
        )
        vlak = DirectionState(
            direction=Direction.PRODUCTION,
            due_date=date(2026, 5, 1),
            stability=0.086,
            last_review=date(2026, 5, 1),
        )
        from app.srs.fsrs import compute_retrievability

        r_prasic = compute_retrievability(prasic, today)
        r_vlak = compute_retrievability(vlak, today)
        # vlak has much lower stability → lower retrievability → should come first
        assert r_vlak < r_prasic


class TestRelearnGraduation:
    """Tests for RELEARNING → REVIEW graduation.

    When relearn_steps is non-empty, Anki graduates directly to REVIEW
    on the last step (relearning.rs:119-130). The 0.5-day short-term rule
    only applies when relearn_steps is empty.
    """

    def _make_relearning_item(self, stability=0.086, difficulty=5.0, reps=18, lapses=1):
        """Create an SRSItem with a RELEARNING direction."""
        utc = ZoneInfo("UTC")
        now = datetime(2026, 5, 2, 10, 0, 0, tzinfo=utc)
        last_review = datetime(2026, 5, 1, 10, 0, 0, tzinfo=utc)

        dir_state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date(2026, 5, 1),
            stability=stability,
            difficulty=difficulty,
            reps=reps,
            lapses=lapses,
            state=SRSState.RELEARNING,
            last_review=last_review,
            last_review_time_ms=60000,
            left=1001,  # 1 step remaining, 1 total step
            due_at=now,
            dirty_fsrs=False,
        )
        return SRSItem(
            syntactic_unit=None,  # type: ignore[arg-type]
            directions={Direction.RECOGNITION: dir_state},
            guid="test-relearn-1",
            anki_note_id=1775264031901,
        )

    def test_relearn_last_step_good_long_interval_graduates(self):
        """RELEARNING + GOOD on last step with long FSRS interval → REVIEW.

        High stability → FSRS interval >= 0.5d → graduates to REVIEW.
        """
        # High stability → interval will be >= 0.5 days
        item = self._make_relearning_item(stability=10.0, difficulty=3.0, reps=18, lapses=1)

        result = schedule(
            item,
            Rating.GOOD,
            review_date=date(2026, 5, 2),
            direction=Direction.RECOGNITION,
            time_ms=60000,
        )

        recog = result.directions[Direction.RECOGNITION]
        # Should graduate to REVIEW
        assert recog.state == SRSState.REVIEW, f"Expected REVIEW, got {recog.state}"
        # due_date should be set (days-based, not sub-day)
        assert recog.due_date is not None
        # due_at should be None (no longer in learning)
        assert recog.due_at is None

    def test_review_again_then_good_graduates_to_review(self):
        """REVIEW + AGAIN + GOOD graduates to REVIEW (Anki behavior with non-empty relearn_steps).

        Anki's relearning.rs: when relearn_steps is non-empty, GOOD on the last
        relearn step graduates directly to REVIEW. The 0.5-day short-term rule
        only applies when relearn_steps is empty or fsrs_short_term_with_steps_enabled.

        Sequence (1-step relearn ladder: relearn_steps=[10.0]):
        1. REVIEW + AGAIN → RELEARNING (step 0)
        2. RELEARNING + GOOD on last (only) step → REVIEW (graduate)
        """
        utc = ZoneInfo("UTC")
        now = datetime(2026, 5, 1, 8, 0, 0, tzinfo=utc)

        dir_state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date(2026, 4, 24),  # 7 days ago
            stability=0.086,  # Low stability
            difficulty=5.0,
            reps=18,
            lapses=1,
            state=SRSState.REVIEW,
            last_review=datetime(2026, 4, 24, 8, 0, 0, tzinfo=utc),
            last_review_time_ms=60000,
            dirty_fsrs=False,
        )
        item = SRSItem(
            syntactic_unit=None,  # type: ignore[arg-type]
            directions={Direction.RECOGNITION: dir_state},
            guid="test-roznat-1",
            anki_note_id=1775264031901,
        )

        # Step 1: REVIEW + AGAIN → RELEARNING
        result1 = schedule(
            item,
            Rating.AGAIN,
            review_date=date(2026, 5, 1),
            direction=Direction.RECOGNITION,
            time_ms=60000,
            now=now,
        )
        state1 = result1.directions[Direction.RECOGNITION]
        assert state1.state == SRSState.RELEARNING, f"Step 1: Expected RELEARNING, got {state1.state}"
        assert state1.lapses == 2

        # Step 2: RELEARNING + GOOD on last (only) step → REVIEW (graduate)
        result2 = schedule(
            result1,
            Rating.GOOD,
            review_date=date(2026, 5, 1),
            direction=Direction.RECOGNITION,
            time_ms=60000,
            now=now + timedelta(minutes=10),
        )
        state2 = result2.directions[Direction.RECOGNITION]
        assert state2.state == SRSState.REVIEW, f"Step 2: Expected REVIEW, got {state2.state}"
        # Should have graduated: no left/due_at
        assert state2.left is None
        assert state2.due_at is None
        assert state2.due_date is not None
