"""Tests for per-lemma mastery (Phase 5).

Mastery is the FSRS *stability* (log-normalized), not retrievability: the
scheduler holds retrievability near desired_retention, so it can't distinguish a
freshly graduated card from a long-mastered one. Stability grows monotonically
as a word is learned, which is what the transcript color ramp should track.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

from app.models.srs_item import Direction, DirectionState, SRSState
from app.srs.mastery import (
    BAND_MONTHS_FROM_DAYS,
    BAND_WEEKS_FROM_DAYS,
    MASTERY_STABILITY_CEILING_DAYS,
    WELL_KNOWN_DUE_DAYS_AHEAD,
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


_TODAY = date(2026, 6, 1)


def _due_in(days: int) -> datetime:
    """A due_at exactly ``days`` after _TODAY, on the 04:00-UTC day convention."""
    return datetime.combine(_TODAY, datetime.min.time(), tzinfo=UTC).replace(hour=4) + timedelta(days=days)


class TestIsWellKnown:
    """ "Well known" is a DUE-DATE rule: the next review is >= 90 days out.

    Reverted to a due-date horizon on 2026-09-13 (bd tunatale-38z9). It had
    been a stability rule since 2026-09-10 (bd tunatale-yh47), which replaced
    an older 365-day due-date rule.

    Why back: the predicate decides what the listen preview STOPS ASKING
    about, and "when will I next see this" is a schedule question, not a
    memory question. It is also legible — a learner can read a due date in
    Anki and check the call, where a stability of 181d vs 166d is a hidden
    model estimate they cannot audit.

    What did NOT come back is the coupling. Stability still names the colour
    band (:func:`direction_band`), so a word's colour stays put between
    reviews while the preview's horizon moves with the schedule. The two
    predicates now answer different questions and are tested apart; see
    :meth:`TestDirectionBand.test_well_known_is_not_the_top_band`.

    The 2026-09-10 rationale for leaving due dates — that a reschedule moves
    them — was measured against the 2026-09-12 reschedule incident and did not
    hold: the due rule flipped 25 of 1607 recognition cards where the
    stability rule flipped 46.
    """

    def test_the_horizon_constant(self):
        assert WELL_KNOWN_DUE_DAYS_AHEAD == 90

    def test_due_exactly_at_the_horizon_is_well_known(self):
        assert is_well_known(_dir(SRSState.REVIEW, 1.0, due_at=_due_in(90)), _TODAY) is True

    def test_due_just_inside_the_horizon_is_not(self):
        assert is_well_known(_dir(SRSState.REVIEW, 1.0, due_at=_due_in(89)), _TODAY) is False

    def test_a_strong_memory_due_soon_is_not_well_known(self):
        # Inverts the stability rule: 200d of stability, but it comes up in 30
        # days, so the preview keeps asking. This is the case bd tunatale-38z9
        # is about — the schedule, not the estimate, decides.
        assert is_well_known(_dir(SRSState.REVIEW, 200.0, due_at=_due_in(30)), _TODAY) is False

    def test_a_weak_memory_due_far_out_is_well_known(self):
        assert is_well_known(_dir(SRSState.REVIEW, 10.0, due_at=_due_in(400)), _TODAY) is True

    def test_marked_known_is_well_known_whatever_its_due_date(self):
        # mark_known carries no meaningful schedule; the state alone decides.
        assert is_well_known(_dir(SRSState.KNOWN, 1.0, due_at=_due_in(0)), _TODAY) is True

    def test_in_steps_cards_are_never_well_known(self):
        # Suppressing a card being acquired would hide work the user owes,
        # however far out it happens to be parked. The stability rule got this
        # from direction_band; the due rule needs its own guard.
        for state in (SRSState.LEARNING, SRSState.RELEARNING):
            assert is_well_known(_dir(state, 500.0, due_at=_due_in(400)), _TODAY) is False, state

    def test_new_and_suspended_are_not_well_known(self):
        for state in (SRSState.NEW, SRSState.SUSPENDED):
            assert is_well_known(_dir(state, 500.0, due_at=_due_in(400)), _TODAY) is False, state

    def test_buried_review_card_keeps_its_horizon(self):
        # Burying hides a card for the day; it does not move the next review.
        assert is_well_known(_dir(SRSState.BURIED, 10.0, due_at=_due_in(400), reps=5), _TODAY) is True
        assert is_well_known(_dir(SRSState.BURIED, 10.0, due_at=_due_in(400), reps=0), _TODAY) is False

    def test_a_due_card_is_never_well_known(self):
        assert is_well_known(_dir(SRSState.REVIEW, 500.0, due_at=_due_in(-1)), _TODAY) is False

    def test_a_null_due_date_is_not_well_known(self):
        # Nothing to measure the horizon against, so keep asking rather than
        # silence a word on a missing value.
        assert is_well_known(_dir(SRSState.REVIEW, 400.0, due_at=None), _TODAY) is False

    def test_missing_direction_is_not_well_known(self):
        assert is_well_known(None, _TODAY) is False


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

    def test_well_known_is_not_the_top_band(self):
        """The colour band and the listen horizon are deliberately DIFFERENT.

        They were one set until bd tunatale-38z9 (2026-09-13). Keeping them
        welded meant a schedule fact (when the card comes up) decided a memory
        display (what colour the word is), and a memory estimate decided what
        the preview asked about — each answering the other's question.

        Both directions of the disagreement are asserted, so this cannot pass
        by one predicate becoming vacuously false.
        """
        # Strong memory, comes up soon: top band, but still worth asking about.
        strong_soon = _dir(SRSState.REVIEW, 400.0, due_at=_due_in(30))
        assert direction_band(strong_soon) == "solid"
        assert is_well_known(strong_soon, _TODAY) is False

        # Weak memory parked far out: not the top band, but not worth asking.
        weak_far = _dir(SRSState.REVIEW, 10.0, due_at=_due_in(400))
        assert direction_band(weak_far) == "weeks"
        assert is_well_known(weak_far, _TODAY) is True
