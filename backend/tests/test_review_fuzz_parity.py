"""Bit-exact parity tests for TT's port of Anki's review-interval fuzz.

We can't import Anki's Rust ``with_review_fuzz`` from Python — it's private to rslib.
Instead we lock the port against:
  - Hand-computed expected values for the ``fuzz_delta`` / ``constrained_fuzz_bounds``
    arithmetic against the FUZZ_RANGES table (`rslib/.../states/fuzz.rs:16-32`).
  - The ``random_range_f32`` formula: ``(u32 >> 8) / 2^24`` — exact, exactly representable.
  - Behavioural invariants of ``_review_interval_fuzz``: deterministic for the same seed,
    bound to ``[1, max_interval]``, untouched below the 2.5-day threshold, and never
    crossing ``upper == lower`` when the source upper > 2 and < max.
"""

from __future__ import annotations

from app.srs._anki_rng import ChaCha12Rng, random_range_f32
from app.srs.fsrs import (
    _constrained_fuzz_bounds,
    _fuzz_delta,
    _review_interval_fuzz,
    _rust_round_half_away,
)


class TestFuzzDelta:
    """``fuzz_delta`` is the cumulative ±-band Anki uses to pick fuzz_bounds."""

    def test_below_25_days_is_zero(self):
        assert _fuzz_delta(0.0) == 0.0
        assert _fuzz_delta(1.0) == 0.0
        assert _fuzz_delta(2.499) == 0.0

    def test_band_at_25_days_starts_at_1_plus_zero(self):
        # interval=2.5: starts at 1.0, no contribution (start == interval for first range)
        assert _fuzz_delta(2.5) == 1.0

    def test_band_inside_first_range(self):
        # interval=5.0: 1.0 + 0.15 * (5.0 - 2.5) = 1.0 + 0.375 = 1.375
        assert _fuzz_delta(5.0) == 1.375

    def test_band_inside_second_range(self):
        # interval=10.0: 1.0 + 0.15*(7-2.5) + 0.10*(10-7) = 1.0 + 0.675 + 0.3 = 1.975
        assert abs(_fuzz_delta(10.0) - 1.975) < 1e-9

    def test_band_inside_third_range(self):
        # interval=50.0: 1.0 + 0.15*4.5 + 0.10*13 + 0.05*(50-20) = 1.0 + 0.675 + 1.3 + 1.5 = 4.475
        assert abs(_fuzz_delta(50.0) - 4.475) < 1e-9

    def test_band_far_in_third_range(self):
        # interval=101 — the taborišče case from the divergence investigation.
        # 1.0 + 0.15*4.5 + 0.10*13 + 0.05*(101-20) = 1.0 + 0.675 + 1.3 + 4.05 = 7.025
        assert abs(_fuzz_delta(101.0) - 7.025) < 1e-9


class TestConstrainedFuzzBounds:
    def test_at_25_days_clamped_to_minimum(self):
        # interval=2.5 → bounds = (round(2.5-1.0), round(2.5+1.0)) = (2, 4) — wait, that's
        # before clamping. Then minimum=1, maximum=10000, so (2, 4) stays.
        # Actually rust round-half-away of 1.5 = 2 and 3.5 = 4 → (2, 4).
        lower, upper = _constrained_fuzz_bounds(2.5, 1, 10000)
        assert (lower, upper) == (2, 4)

    def test_collapse_below_threshold_no_bump(self):
        """interval < 2.5 produces delta=0 → bounds collapse to (round(iv), round(iv)).
        Anki's bump-by-1 condition requires upper > 2, so this case must NOT bump."""
        lower, upper = _constrained_fuzz_bounds(2.0, 1, 100)
        assert lower == upper == 2

    def test_negative_rust_round(self):
        """Cover the negative branch of ``_rust_round_half_away``."""
        assert _rust_round_half_away(-1.5) == -2
        assert _rust_round_half_away(-0.5) == -1
        assert _rust_round_half_away(-0.4) == 0


class TestRandomRangeF32:
    """``random_range_f32`` should produce values in [0, 1) with 24-bit granularity."""

    def test_in_unit_interval(self):
        for seed in (0, 1, 100, 99999, 0xFFFF_FFFF_FFFF_FFFF):
            f = random_range_f32(ChaCha12Rng(seed))
            assert 0.0 <= f < 1.0

    def test_deterministic_for_same_seed(self):
        a = random_range_f32(ChaCha12Rng(42))
        b = random_range_f32(ChaCha12Rng(42))
        assert a == b

    def test_different_seeds_yield_different_factors(self):
        # Not a guarantee in general, but with these unrelated seeds it holds.
        values = {random_range_f32(ChaCha12Rng(s)) for s in range(10)}
        assert len(values) == 10


class TestReviewIntervalFuzz:
    def test_short_intervals_unfuzzed(self):
        """interval < 2.5 returns clamped round(interval) without RNG."""
        for raw in (0.0, 0.5, 1.0, 2.0, 2.4):
            assert _review_interval_fuzz(raw, anki_card_id=42, reps=7) == max(1, round(raw))

    def test_deterministic_for_same_card_reps(self):
        a = _review_interval_fuzz(50.0, anki_card_id=1234, reps=5)
        b = _review_interval_fuzz(50.0, anki_card_id=1234, reps=5)
        assert a == b

    def test_within_fuzz_bounds(self):
        """Result must fall within constrained_fuzz_bounds for the raw interval."""
        for raw, card_id, reps in [
            (50.0, 1234, 5),
            (10.0, 5678, 12),
            (101.0, 90010, 27),  # taborišče case
        ]:
            lower, upper = _constrained_fuzz_bounds(raw, 1, 36500)
            f = _review_interval_fuzz(raw, anki_card_id=card_id, reps=reps)
            assert lower <= f <= upper, f"{f} not in [{lower}, {upper}] for raw={raw}"

    def test_max_interval_caps_result(self):
        """Result must never exceed max_interval."""
        f = _review_interval_fuzz(10000.0, anki_card_id=1, reps=1, max_interval=100)
        assert f <= 100

    def test_anki_card_id_none_treated_as_zero(self):
        """None card_id uses seed=reps (matches _learning_step_fuzz_seconds convention)."""
        a = _review_interval_fuzz(50.0, anki_card_id=None, reps=7)
        b = _review_interval_fuzz(50.0, anki_card_id=0, reps=7)
        assert a == b
