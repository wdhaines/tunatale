"""Tests for per-lemma mastery (Phase 5)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from app.models.srs_item import Direction, DirectionState, SRSState
from app.srs.fsrs import compute_retrievability


def _ds(
    state: SRSState = SRSState.NEW,
    stability: float = 1.0,
    last_review: datetime | None = None,
) -> DirectionState:
    return DirectionState(
        direction=Direction.RECOGNITION,
        due_at=datetime(2026, 6, 1, 4, 0, tzinfo=UTC),
        state=state,
        stability=stability,
        last_review=last_review,
    )


class TestComponentMastery:
    def test_new_returns_zero(self):
        from app.srs.mastery import component_mastery

        ds = _ds(state=SRSState.NEW)
        today = date(2026, 6, 1)
        val = component_mastery(ds, today, now=datetime.now(UTC), col_crt=None)
        assert val == 0.0

    def test_new_is_not_default_retrievability(self):
        """The carve-out: NEW must give 0.0, NOT the 0.9 desired_retention fallback."""
        from app.srs.mastery import component_mastery

        ds = _ds(state=SRSState.NEW)
        today = date(2026, 6, 1)
        val = component_mastery(ds, today, now=datetime.now(UTC), col_crt=None)
        assert val == 0.0
        assert val != 0.9

    def test_last_review_none_returns_zero(self):
        from app.srs.mastery import component_mastery

        ds = _ds(state=SRSState.REVIEW, last_review=None)
        today = date(2026, 6, 1)
        val = component_mastery(ds, today, now=datetime.now(UTC), col_crt=None)
        assert val == 0.0

    def test_learning_returns_0_15(self):
        from app.srs.mastery import component_mastery

        ds = _ds(state=SRSState.LEARNING, last_review=datetime(2026, 6, 1, 4, 0, tzinfo=UTC))
        today = date(2026, 6, 1)
        val = component_mastery(ds, today, now=datetime.now(UTC), col_crt=None)
        assert val == 0.15

    def test_relearning_returns_0_15(self):
        from app.srs.mastery import component_mastery

        ds = _ds(state=SRSState.RELEARNING, last_review=datetime(2026, 6, 1, 4, 0, tzinfo=UTC))
        today = date(2026, 6, 1)
        val = component_mastery(ds, today, now=datetime.now(UTC), col_crt=None)
        assert val == 0.15

    def test_known_returns_1_0(self):
        from app.srs.mastery import component_mastery

        ds = _ds(state=SRSState.KNOWN, last_review=datetime(2026, 6, 1, 4, 0, tzinfo=UTC))
        today = date(2026, 6, 1)
        val = component_mastery(ds, today, now=datetime.now(UTC), col_crt=None)
        assert val == 1.0

    def test_review_matches_compute_retrievability(self):
        from app.srs.mastery import component_mastery

        ds = _ds(
            state=SRSState.REVIEW,
            stability=50.0,
            last_review=datetime(2026, 5, 15, 4, 0, tzinfo=UTC),
        )
        today = date(2026, 6, 1)
        now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        val = component_mastery(ds, today, now=now, col_crt=None)
        expected = compute_retrievability(ds, today, now=now, col_crt=None)
        assert val == expected

    def test_suspended_returns_through_branch_not_testable_directly_on_component(self):
        """component_mastery doesn't handle SUSPENDED; compute_mastery_progress filters it."""


class TestComputeMasteryProgress:
    def test_empty_list_returns_none(self):
        from app.srs.mastery import compute_mastery_progress

        today = date(2026, 6, 1)
        val = compute_mastery_progress([], today, now=datetime.now(UTC), col_crt=None)
        assert val is None

    def test_all_suspended_returns_none(self):
        from app.srs.mastery import compute_mastery_progress

        ds = _ds(state=SRSState.SUSPENDED, last_review=datetime(2026, 6, 1, 4, 0, tzinfo=UTC))
        today = date(2026, 6, 1)
        val = compute_mastery_progress([ds], today, now=datetime.now(UTC), col_crt=None)
        assert val is None

    def test_mean_of_new_and_review(self):
        from app.srs.mastery import compute_mastery_progress

        new_ds = _ds(state=SRSState.NEW)
        review_ds = _ds(
            state=SRSState.REVIEW,
            stability=100.0,
            last_review=datetime(2026, 5, 30, 4, 0, tzinfo=UTC),
        )
        today = date(2026, 6, 1)
        now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        val = compute_mastery_progress([new_ds, review_ds], today, now=now, col_crt=None)
        r_val = compute_retrievability(review_ds, today, now=now, col_crt=None)
        expected = (0.0 + r_val) / 2
        assert abs(val - expected) < 1e-6

    def test_suspended_excluded_from_denominator(self):
        from app.srs.mastery import compute_mastery_progress

        new_ds = _ds(state=SRSState.NEW)
        suspended_ds = _ds(state=SRSState.SUSPENDED, last_review=datetime(2026, 6, 1, 4, 0, tzinfo=UTC))
        today = date(2026, 6, 1)
        val = compute_mastery_progress([new_ds, suspended_ds], today, now=datetime.now(UTC), col_crt=None)
        # Only NEW counts → 0.0 / 1 = 0.0
        assert val == 0.0

    def test_adding_zero_mastery_lowers_mean(self):
        from app.srs.mastery import compute_mastery_progress

        review_ds = _ds(
            state=SRSState.REVIEW,
            stability=100.0,
            last_review=datetime(2026, 5, 30, 4, 0, tzinfo=UTC),
        )
        today = date(2026, 6, 1)
        now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        val_just_review = compute_mastery_progress([review_ds], today, now=now, col_crt=None)
        val_with_new = compute_mastery_progress([review_ds, _ds(state=SRSState.NEW)], today, now=now, col_crt=None)
        assert val_with_new < val_just_review
