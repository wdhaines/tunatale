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
from app.srs.mastery import (
    BAND_MONTHS_FROM_DAYS,
    BAND_WEEKS_FROM_DAYS,
    MASTERY_STABILITY_CEILING_DAYS,
    WELL_KNOWN_STABILITY_DAYS,
    direction_band,
    is_well_known,
)


def _ds(
    state: SRSState = SRSState.NEW,
    stability: float = 1.0,
    last_review: datetime | None = None,
    direction: Direction = Direction.RECOGNITION,
) -> DirectionState:
    return DirectionState(
        direction=direction,
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

        new_ds = _ds(state=SRSState.NEW, direction=Direction.PRODUCTION)
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

    def test_recognition_only_never_reads_fully_mastered(self):
        """A word with no production card is not mastered, however mature recognition is.

        The Norwegian deck is recognition-only for 2990 of 3017 collocations, so
        before this rule 18.3% of it read 100% (vs 0.3% of the two-direction
        Slovene deck) — the clamp at MASTERY_STABILITY_CEILING_DAYS meant any
        recognition card past ~120 days stability rendered as fully known.
        """
        from app.srs.mastery import compute_mastery_progress

        mature = _ds(state=SRSState.REVIEW, stability=10_000.0, last_review=datetime(2026, 5, 30, 4, 0, tzinfo=UTC))
        assert compute_mastery_progress([mature]) == 0.5

    def test_absent_production_scores_as_unlearned(self):
        """An absent production card counts exactly like a NEW one: 0.0."""
        from app.srs.mastery import compute_mastery_progress

        recog = _ds(state=SRSState.REVIEW, stability=100.0, last_review=datetime(2026, 5, 30, 4, 0, tzinfo=UTC))
        absent = compute_mastery_progress([recog])
        explicit_new = compute_mastery_progress([recog, _ds(state=SRSState.NEW, direction=Direction.PRODUCTION)])
        assert absent == explicit_new

    def test_both_directions_mature_still_reaches_full(self):
        from app.srs.mastery import compute_mastery_progress

        kw = {"state": SRSState.REVIEW, "stability": 10_000.0, "last_review": datetime(2026, 5, 30, 4, 0, tzinfo=UTC)}
        pair = [_ds(**kw), _ds(direction=Direction.PRODUCTION, **kw)]
        assert compute_mastery_progress(pair) == 1.0

    def test_production_only_card_is_not_penalized(self):
        """Cloze notes are PRODUCTION-only by design — they have no missing half."""
        from app.srs.mastery import compute_mastery_progress

        prod = _ds(
            state=SRSState.REVIEW,
            stability=10_000.0,
            last_review=datetime(2026, 5, 30, 4, 0, tzinfo=UTC),
            direction=Direction.PRODUCTION,
        )
        assert compute_mastery_progress([prod]) == 1.0

    def test_suspended_production_is_not_double_counted(self):
        """A suspended production card is deliberately not studied, not absent.

        It stays out of the mean (existing rule) and must NOT also trigger the
        absent-production penalty — that would score one card twice.
        """
        from app.srs.mastery import compute_mastery_progress

        recog = _ds(state=SRSState.REVIEW, stability=10_000.0, last_review=datetime(2026, 5, 30, 4, 0, tzinfo=UTC))
        suspended_prod = _ds(
            state=SRSState.SUSPENDED,
            last_review=datetime(2026, 6, 1, 4, 0, tzinfo=UTC),
            direction=Direction.PRODUCTION,
        )
        assert compute_mastery_progress([recog, suspended_prod]) == 1.0

    def test_adding_zero_mastery_lowers_mean(self):
        from app.srs.mastery import compute_mastery_progress

        review_ds = _ds(state=SRSState.REVIEW, stability=100.0, last_review=datetime(2026, 5, 30, 4, 0, tzinfo=UTC))
        val_just_review = compute_mastery_progress([review_ds])
        val_with_new = compute_mastery_progress([review_ds, _ds(state=SRSState.NEW)])
        assert val_with_new < val_just_review


def _dir(
    state: SRSState,
    stability: float = 1.0,
    due_at: datetime | None = datetime(2026, 6, 1, 4, 0, tzinfo=UTC),
    reps: int = 0,
) -> DirectionState:
    return DirectionState(direction=Direction.RECOGNITION, state=state, stability=stability, due_at=due_at, reps=reps)


class TestIsWellKnown:
    """ "Well known" is a STABILITY rule: the memory holds for >= 180 days.

    It replaced a due-date rule (next review more than 365 days out) on
    2026-09-10 (bd tunatale-yh47). A due date depends on the deck's desired
    retention — at 0.95 FSRS schedules about half the stability ahead — and it
    moves when due dates are rescheduled by hand, so the same memory read as
    "well known" in one deck and not in another. One definition still feeds the
    listen preview (stop asking about the word) and the transcript (render it as
    known), and it is the top mastery band by construction.
    """

    def test_stability_at_the_threshold_is_well_known(self):
        assert is_well_known(_dir(SRSState.REVIEW, WELL_KNOWN_STABILITY_DAYS)) is True

    def test_stability_just_under_the_threshold_is_not(self):
        assert is_well_known(_dir(SRSState.REVIEW, WELL_KNOWN_STABILITY_DAYS - 0.01)) is False

    def test_a_near_due_date_does_not_demote_a_strong_memory(self):
        # The decisive case the old rule got wrong: stability 200 at desired
        # retention 0.95 is scheduled ~100 days out, never past a 365-day
        # horizon, so the old rule could not call it well known.
        assert is_well_known(_dir(SRSState.REVIEW, 200.0, due_at=datetime(2026, 7, 1, tzinfo=UTC))) is True

    def test_a_moved_due_date_does_not_promote_a_weak_memory(self):
        # The Slovene shape (bd tunatale-xzp6): due dates pushed far past what
        # the stability implies. The memory is what counts, not the calendar.
        assert is_well_known(_dir(SRSState.REVIEW, 100.0, due_at=datetime(2028, 1, 1, tzinfo=UTC))) is False

    def test_a_null_due_date_does_not_matter(self):
        assert is_well_known(_dir(SRSState.REVIEW, 400.0, due_at=None)) is True

    def test_marked_known_is_well_known(self):
        # A KNOWN card sits in the top band, so it must also be well known —
        # the two are one set. (Listen never defers it: its grade class is None.)
        assert is_well_known(_dir(SRSState.KNOWN, 1.0)) is True

    def test_in_steps_cards_are_never_well_known(self):
        # Suppressing a card being acquired would hide work the user owes,
        # however large a stability it carries.
        for state in (SRSState.LEARNING, SRSState.RELEARNING):
            assert is_well_known(_dir(state, 500.0)) is False, state

    def test_new_and_suspended_are_not_well_known(self):
        for state in (SRSState.NEW, SRSState.SUSPENDED):
            assert is_well_known(_dir(state, 500.0)) is False, state

    def test_buried_review_card_keeps_its_strength(self):
        # Burying hides a card for the day; it does not weaken the memory.
        assert is_well_known(_dir(SRSState.BURIED, 300.0, reps=5)) is True
        assert is_well_known(_dir(SRSState.BURIED, 300.0, reps=0)) is False

    def test_missing_direction_is_not_well_known(self):
        assert is_well_known(None) is False


class TestDirectionBand:
    """Each side of a word is shown as a band named by how long it holds."""

    def test_the_threshold_constants(self):
        assert (BAND_WEEKS_FROM_DAYS, BAND_MONTHS_FROM_DAYS, WELL_KNOWN_STABILITY_DAYS) == (7.0, 30.0, 180.0)

    def test_no_card_is_none(self):
        assert direction_band(None) == "none"

    def test_non_review_states(self):
        assert direction_band(_dir(SRSState.NEW, 500.0)) == "new"
        assert direction_band(_dir(SRSState.LEARNING, 500.0)) == "learning"
        assert direction_band(_dir(SRSState.RELEARNING, 500.0)) == "learning"
        assert direction_band(_dir(SRSState.KNOWN, 1.0)) == "solid"
        assert direction_band(_dir(SRSState.SUSPENDED, 500.0)) == "suspended"

    def test_review_bands_by_stability_with_inclusive_lower_edges(self):
        cases = {
            0.2: "days",
            6.99: "days",
            7.0: "weeks",
            29.99: "weeks",
            30.0: "months",
            179.99: "months",
            180.0: "solid",
            79251.0: "solid",
        }
        for stability, band in cases.items():
            assert direction_band(_dir(SRSState.REVIEW, stability)) == band, stability

    def test_buried_uses_stability_once_reviewed_else_reads_new(self):
        assert direction_band(_dir(SRSState.BURIED, 10.0, reps=3)) == "weeks"
        assert direction_band(_dir(SRSState.BURIED, 10.0, reps=0)) == "new"

    def test_top_band_and_well_known_are_one_set(self):
        # By construction, not by two constants that happen to agree.
        for state in SRSState:
            for stability in (0.5, 7.0, 30.0, 179.0, 180.0, 400.0):
                for reps in (0, 4):
                    ds = _dir(state, stability, reps=reps)
                    assert (direction_band(ds) == "solid") == is_well_known(ds), (state, stability, reps)
