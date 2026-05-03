"""Tests for FSRS retrievability computation."""

from datetime import date

from app.models.srs_item import Direction, DirectionState
from app.srs.fsrs import compute_retrievability


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
        assert compute_retrievability(state, date.today()) == 1.0

    def test_null_last_review_returns_one(self):
        """Null last_review means never reviewed — sort last."""
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=0.5,
            last_review=None,
        )
        assert compute_retrievability(state, date.today()) == 1.0

    def test_both_null_returns_one(self):
        state = DirectionState(
            direction=Direction.RECOGNITION,
            due_date=date.today(),
            stability=None,
            last_review=None,
        )
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
        r_prasic = compute_retrievability(prasic, today)
        r_vlak = compute_retrievability(vlak, today)
        # vlak has much lower stability → lower retrievability → should come first
        assert r_vlak < r_prasic
