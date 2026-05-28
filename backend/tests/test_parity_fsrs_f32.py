"""TT f32 FSRS helpers vs fsrs-rs ``next_states`` — bit-exact parity.

Pins the numpy.float32 conversion of TT's FSRS arithmetic (Layer N+1) against
fsrs-rs's published Python bindings as the precision oracle. This is the layer
that lets compare-shadow's ``stability_replayed`` actually approach the literal
``0`` target instead of accumulating 1-ULP-at-4dp false positives.

Why not go through the Anki oracle harness for this? Because the
``answer_card``-end path bakes in Anki's grade-time scheduling (delta_t from
``next_day_at.elapsed_days_since(lrt)``, learning-step transitions, fuzz, etc.),
which we already exercise in ``test_parity_transitions``. This file isolates
**only** the FSRS pure-arithmetic helpers, calling fsrs-rs's ``next_states``
directly so divergences point at op-order / FACTOR / quantization, not at
state-machine bookkeeping.
"""

from __future__ import annotations

import pytest

from app.models.srs_item import Rating
from app.srs.fsrs import (
    DEFAULT_FSRS5_PARAMS,
    _forgetting_curve,
    _next_difficulty,
    _next_stability_lapse,
    _next_stability_recall,
)

fsrs_rs_python = pytest.importorskip("fsrs_rs_python")

W = DEFAULT_FSRS5_PARAMS.weights


def _fsrs_rs_next(s: float, d: float, elapsed: int, retention: float = 0.9):
    """Run fsrs-rs ``next_states`` on a (s, d) starting state for ``elapsed`` days."""
    f = fsrs_rs_python.FSRS(W)
    prev = fsrs_rs_python.MemoryState(s, d)
    return f.next_states(prev, retention, elapsed)


# Inputs span: stability floor (~0.5), low (5), medium (40), high (200);
# difficulties span easy/medium/hard learners. Each (s, d) gets tested at three
# elapsed-day values to cross multiple R regimes.
_INPUTS = [
    (0.5, 7.7, 30),  # Interday graduation case from test_parity_transitions
    (5.0, 5.5, 5),  # Medium-stability fresh review
    (40.0, 6.84, 31),  # Baker-style production case (matches today's diverged card)
    (50.0, 2.0, 30),  # High-stability HARD case from test_parity_review_hard_high_stability
    (200.0, 5.0, 60),  # Long-tail high-stability
    (0.002, 10.0, 1),  # FSRS S_MIN floor (low-s, high-d)
]


@pytest.mark.parametrize("s_prev, d_prev, elapsed", _INPUTS)
def test_recall_bit_exact_vs_fsrs_rs(s_prev: float, d_prev: float, elapsed: int) -> None:
    """For HARD/GOOD/EASY, TT's recall stability must match fsrs-rs bit-exact (f32)."""
    r = _forgetting_curve(elapsed, s_prev, decay=-0.5)
    ns = _fsrs_rs_next(s_prev, d_prev, elapsed)
    for rating, attr in [(Rating.HARD, "hard"), (Rating.GOOD, "good"), (Rating.EASY, "easy")]:
        tt_s = _next_stability_recall(d_prev, s_prev, r, rating, W)
        anki_s = getattr(ns, attr).memory.stability
        assert tt_s == anki_s, (
            f"recall {attr} (s={s_prev}, d={d_prev}, elapsed={elapsed}): TT={tt_s!r} fsrs-rs={anki_s!r}"
        )


@pytest.mark.parametrize("s_prev, d_prev, elapsed", _INPUTS)
def test_lapse_bit_exact_vs_fsrs_rs(s_prev: float, d_prev: float, elapsed: int) -> None:
    """For AGAIN (lapse), TT's lapse stability must match fsrs-rs bit-exact (f32)."""
    r = _forgetting_curve(elapsed, s_prev, decay=-0.5)
    tt_s = _next_stability_lapse(d_prev, s_prev, r, W)
    anki_s = _fsrs_rs_next(s_prev, d_prev, elapsed).again.memory.stability
    assert tt_s == anki_s, f"lapse (s={s_prev}, d={d_prev}, elapsed={elapsed}): TT={tt_s!r} fsrs-rs={anki_s!r}"


@pytest.mark.parametrize("s_prev, d_prev, elapsed", _INPUTS)
def test_difficulty_bit_exact_vs_fsrs_rs(s_prev: float, d_prev: float, elapsed: int) -> None:
    """Difficulty update must match fsrs-rs bit-exact (f32) across all four ratings."""
    ns = _fsrs_rs_next(s_prev, d_prev, elapsed)
    for rating, attr in [
        (Rating.AGAIN, "again"),
        (Rating.HARD, "hard"),
        (Rating.GOOD, "good"),
        (Rating.EASY, "easy"),
    ]:
        tt_d = _next_difficulty(d_prev, rating, W)
        anki_d = getattr(ns, attr).memory.difficulty
        assert tt_d == anki_d, f"difficulty {attr} (d={d_prev}): TT={tt_d!r} fsrs-rs={anki_d!r}"


def test_forgetting_curve_bit_exact_vs_fsrs_rs() -> None:
    """TT's forgetting curve (factor + op order) is the FSRS retrievability formula
    Anki's queue-sort SQL uses. Test by inverting through ``next_states``: if r is
    bit-exact, the next-state stability output will be bit-exact downstream. This
    is a second test of the same precision claim from a different vantage."""
    # Spot-check: at (s, elapsed) = (40, 30), the curve r is the operand fed into
    # _next_stability_recall in test_recall_bit_exact_vs_fsrs_rs. If recall test
    # passes, this passes by construction — but a direct test makes the failure
    # mode unambiguous if FACTOR drifts.
    r = _forgetting_curve(30, 40.0, decay=-0.5)
    # fsrs-rs doesn't expose forgetting_curve directly; verify via downstream
    # recall agreement.
    assert 0.0 < r < 1.0, f"R out of range: {r}"
    # Recall through TT and fsrs-rs must agree (already covered by parametrized
    # test, but this version uses the same r explicitly).
    tt_s = _next_stability_recall(6.84, 40.0, r, Rating.GOOD, W)
    anki_s = _fsrs_rs_next(40.0, 6.84, 30).good.memory.stability
    assert tt_s == anki_s, f"forgetting curve cascade: TT={tt_s!r} fsrs-rs={anki_s!r}"
