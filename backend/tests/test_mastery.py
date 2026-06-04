"""Tests for per-lemma mastery (Phase 5).

Mastery is the FSRS *stability* (log-normalized), not retrievability: the
scheduler holds retrievability near desired_retention, so it can't distinguish a
freshly graduated card from a long-mastered one. Stability grows monotonically
as a word is learned, which is what the transcript color ramp should track.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from app.models.srs_item import Direction, DirectionState, SRSState
from app.srs.mastery import MASTERY_STABILITY_CEILING_DAYS


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


def _expected_review_mastery(stability: float) -> float:
    m = math.log10(max(stability, 1.0)) / math.log10(MASTERY_STABILITY_CEILING_DAYS)
    return max(0.0, min(1.0, m))


class TestComponentMastery:
    def test_new_returns_zero(self):
        from app.srs.mastery import component_mastery

        assert component_mastery(_ds(state=SRSState.NEW)) == 0.0

    def test_new_is_not_default_retrievability(self):
        """The carve-out: NEW must give 0.0, NOT the 0.9 desired_retention fallback."""
        from app.srs.mastery import component_mastery

        val = component_mastery(_ds(state=SRSState.NEW))
        assert val == 0.0
        assert val != 0.9

    def test_review_mastery_ignores_missing_last_review(self):
        """Stability-based mastery does not need last_review (a retrievability-era
        relic). A high-stability REVIEW card with last_review=None — exactly what
        mark_known produces — must read as mastered, not 0.0. Low stability still
        floors at 0 via the log curve, not via a last_review guard."""
        from app.srs.mastery import component_mastery

        assert component_mastery(_ds(state=SRSState.REVIEW, stability=1.0, last_review=None)) == 0.0
        assert component_mastery(_ds(state=SRSState.REVIEW, stability=24317.0, last_review=None)) == 1.0

    def test_learning_returns_floor(self):
        from app.srs.mastery import component_mastery

        ds = _ds(state=SRSState.LEARNING, last_review=datetime(2026, 6, 1, 4, 0, tzinfo=UTC))
        assert component_mastery(ds) == 0.15

    def test_relearning_returns_floor(self):
        from app.srs.mastery import component_mastery

        ds = _ds(state=SRSState.RELEARNING, last_review=datetime(2026, 6, 1, 4, 0, tzinfo=UTC))
        assert component_mastery(ds) == 0.15

    def test_known_returns_1_0(self):
        """KNOWN → 1.0 even with last_review=None, which is what mark_known leaves
        (it sets state + stability but no review timestamp)."""
        from app.srs.mastery import component_mastery

        assert component_mastery(_ds(state=SRSState.KNOWN, last_review=None)) == 1.0

    def test_review_uses_log_stability(self):
        from app.srs.mastery import component_mastery

        ds = _ds(state=SRSState.REVIEW, stability=12.0, last_review=datetime(2026, 5, 15, 4, 0, tzinfo=UTC))
        assert abs(component_mastery(ds) - _expected_review_mastery(12.0)) < 1e-9

    def test_review_at_ceiling_is_fully_mastered(self):
        from app.srs.mastery import component_mastery

        ds = _ds(
            state=SRSState.REVIEW,
            stability=MASTERY_STABILITY_CEILING_DAYS,
            last_review=datetime(2026, 5, 1, 4, 0, tzinfo=UTC),
        )
        assert component_mastery(ds) == 1.0

    def test_review_above_ceiling_clamps_to_one(self):
        from app.srs.mastery import component_mastery

        ds = _ds(
            state=SRSState.REVIEW,
            stability=MASTERY_STABILITY_CEILING_DAYS * 3,
            last_review=datetime(2026, 5, 1, 4, 0, tzinfo=UTC),
        )
        assert component_mastery(ds) == 1.0

    def test_review_at_or_below_one_day_is_zero(self):
        """stability <= 1 day floors at 0.0 (log10(1)=0), exercising the max() guard."""
        from app.srs.mastery import component_mastery

        ds_one = _ds(state=SRSState.REVIEW, stability=1.0, last_review=datetime(2026, 5, 31, 4, 0, tzinfo=UTC))
        ds_sub = _ds(state=SRSState.REVIEW, stability=0.4, last_review=datetime(2026, 5, 31, 4, 0, tzinfo=UTC))
        assert component_mastery(ds_one) == 0.0
        assert component_mastery(ds_sub) == 0.0

    def test_review_mastery_is_monotonic_in_stability(self):
        """The reported-bug regression: two REVIEW cards with near-identical
        retrievability but very different stability (the user's hotelu s=108 vs
        še s=3.4) must read as clearly different mastery — retrievability could
        not separate them."""
        from app.srs.mastery import component_mastery

        lr = datetime(2026, 5, 28, 4, 0, tzinfo=UTC)
        low = component_mastery(_ds(state=SRSState.REVIEW, stability=3.4, last_review=lr))
        high = component_mastery(_ds(state=SRSState.REVIEW, stability=108.0, last_review=lr))
        assert high > low
        assert high - low > 0.4  # visibly different, not a 3.7° hue nudge

    def test_review_is_time_independent(self):
        """Mastery depends only on stored stability — a word's color does not
        drift between reviews (the opposite of the retrievability scheme)."""
        from app.srs.mastery import component_mastery

        early = _ds(state=SRSState.REVIEW, stability=40.0, last_review=datetime(2026, 1, 1, 4, 0, tzinfo=UTC))
        recent = _ds(state=SRSState.REVIEW, stability=40.0, last_review=datetime(2026, 6, 1, 4, 0, tzinfo=UTC))
        assert component_mastery(early) == component_mastery(recent)


class TestComputeMasteryProgress:
    def test_empty_list_returns_none(self):
        from app.srs.mastery import compute_mastery_progress

        assert compute_mastery_progress([]) is None

    def test_all_suspended_returns_none(self):
        from app.srs.mastery import compute_mastery_progress

        ds = _ds(state=SRSState.SUSPENDED, last_review=datetime(2026, 6, 1, 4, 0, tzinfo=UTC))
        assert compute_mastery_progress([ds]) is None

    def test_mean_of_new_and_review(self):
        from app.srs.mastery import compute_mastery_progress

        new_ds = _ds(state=SRSState.NEW)
        review_ds = _ds(state=SRSState.REVIEW, stability=100.0, last_review=datetime(2026, 5, 30, 4, 0, tzinfo=UTC))
        val = compute_mastery_progress([new_ds, review_ds])
        expected = (0.0 + _expected_review_mastery(100.0)) / 2
        assert abs(val - expected) < 1e-9

    def test_suspended_excluded_from_denominator(self):
        from app.srs.mastery import compute_mastery_progress

        new_ds = _ds(state=SRSState.NEW)
        suspended_ds = _ds(state=SRSState.SUSPENDED, last_review=datetime(2026, 6, 1, 4, 0, tzinfo=UTC))
        # Only NEW counts → 0.0 / 1 = 0.0
        assert compute_mastery_progress([new_ds, suspended_ds]) == 0.0

    def test_adding_zero_mastery_lowers_mean(self):
        from app.srs.mastery import compute_mastery_progress

        review_ds = _ds(state=SRSState.REVIEW, stability=100.0, last_review=datetime(2026, 5, 30, 4, 0, tzinfo=UTC))
        val_just_review = compute_mastery_progress([review_ds])
        val_with_new = compute_mastery_progress([review_ds, _ds(state=SRSState.NEW)])
        assert val_with_new < val_just_review
