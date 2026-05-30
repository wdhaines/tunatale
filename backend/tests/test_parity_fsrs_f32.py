"""TT f32 FSRS helpers vs fsrs-rs ``next_states`` — storage-precision parity.

Pins the numpy.float32 conversion of TT's FSRS arithmetic (Layer 59) against
fsrs-rs's published Python bindings as the precision oracle. This is the layer
that lets compare-shadow's ``stability_replayed`` actually approach the literal
``0`` target instead of accumulating 1-ULP-at-4dp false positives.

**Why storage precision, not raw ``==``.** numpy's f32 transcendentals
(``expf``/``logf``/``powf``) are *not* bit-identical to fsrs-rs's Rust libm
across CPU architectures: macOS-ARM agrees to the last bit, x86-Linux (CI) can
differ by 1 ULP. But Anki only ever *stores* ``cards.data.s`` rounded to 4dp and
``.d`` to 3dp (``round_to_places``; TT mirrors via ``_quantize_*``), and every
such 1-ULP raw difference is far below that resolution — both sides round to the
identical stored value. So the parity contract is "TT and fsrs-rs agree on the
value Anki persists," which is what we assert. This still catches any ≥0.0001
stability / ≥0.001 difficulty divergence (the regressions Layer 59 targets); it
only absorbs sub-storage platform noise that no stored value could ever reflect.

Why not go through the Anki oracle harness for this? Because the
``answer_card``-end path bakes in Anki's grade-time scheduling (delta_t from
``next_day_at.elapsed_days_since(lrt)``, learning-step transitions, fuzz, etc.),
which we already exercise in ``test_parity_transitions``. This file isolates
**only** the FSRS pure-arithmetic helpers, calling fsrs-rs's ``next_states``
directly so divergences point at op-order / FACTOR / quantization, not at
state-machine bookkeeping.
"""

from __future__ import annotations

import math
import platform
from collections.abc import Callable

import pytest

from app.models.srs_item import Rating
from app.srs.fsrs import (
    DEFAULT_FSRS5_PARAMS,
    _forgetting_curve,
    _next_difficulty,
    _next_stability_lapse,
    _next_stability_recall,
    _quantize_difficulty,
    _quantize_stability,
)

# numpy's f32 expf/logf/powf are bit-reproducible with fsrs-rs's Rust libm only on
# the deployment architecture (Apple-Silicon arm64, where both TT and the user's
# Anki actually run). On x86 (CI) they differ by ~1 ULP, which can tip a 4dp/3dp
# rounding boundary. Crucially, a genuine op-order/FACTOR regression is *also* ~1
# ULP — so the precision pin is only meaningful where transcendental noise is zero.
# We therefore assert storage-exactness on arm64 (the value Anki persists; this is
# what local ./test.sh pre-commit on the deploy platform enforces) and fall back to
# a gross-error tolerance on other arches (keeps x86 CI honest without ULP false
# failures, the same "precision-pin is local" stance as the Anki oracle harness).
_STRICT_FSRS_PARITY = platform.machine().lower() in ("arm64", "aarch64")
_GROSS_REL_TOL = 1e-4  # ~100× the observed cross-libm ULP noise, ≪ any real-bug delta


def _assert_fsrs_parity(tt: float, anki: float, quantize: Callable[[float], float], msg: str, *, strict: bool) -> None:
    """On the deploy arch, TT and fsrs-rs must agree on the value Anki *stores*
    (``quantize`` = 4dp stability / 3dp difficulty). Off it, only assert ballpark
    agreement, since cross-libm f32 transcendental noise is indistinguishable from
    a 1-ULP precision bug and would otherwise fail near rounding boundaries."""
    if strict:
        assert quantize(tt) == quantize(anki), msg
    else:
        assert math.isclose(tt, anki, rel_tol=_GROSS_REL_TOL), msg


def test_assert_fsrs_parity_both_modes() -> None:
    """Cover both comparison modes regardless of the host arch running coverage."""
    # strict: equal-at-storage passes, differ-at-storage fails.
    _assert_fsrs_parity(1.23450, 1.23451, _quantize_stability, "eq@4dp", strict=True)
    with pytest.raises(AssertionError):
        _assert_fsrs_parity(1.2, 1.9, _quantize_stability, "neq@4dp", strict=True)
    # loose: ULP-scale noise passes, gross divergence fails.
    _assert_fsrs_parity(36.40507, 36.40503, _quantize_stability, "ulp", strict=False)
    with pytest.raises(AssertionError):
        _assert_fsrs_parity(1.0, 2.0, _quantize_stability, "gross", strict=False)


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
    """For HARD/GOOD/EASY, TT's recall stability must match fsrs-rs at the value Anki stores (4dp)."""
    r = _forgetting_curve(elapsed, s_prev, decay=-0.5)
    ns = _fsrs_rs_next(s_prev, d_prev, elapsed)
    for rating, attr in [(Rating.HARD, "hard"), (Rating.GOOD, "good"), (Rating.EASY, "easy")]:
        tt_s = _next_stability_recall(d_prev, s_prev, r, rating, W)
        anki_s = getattr(ns, attr).memory.stability
        _assert_fsrs_parity(
            tt_s,
            anki_s,
            _quantize_stability,
            f"recall {attr} (s={s_prev}, d={d_prev}, elapsed={elapsed}): TT={tt_s!r} fsrs-rs={anki_s!r}",
            strict=_STRICT_FSRS_PARITY,
        )


@pytest.mark.parametrize("s_prev, d_prev, elapsed", _INPUTS)
def test_lapse_bit_exact_vs_fsrs_rs(s_prev: float, d_prev: float, elapsed: int) -> None:
    """For AGAIN (lapse), TT's lapse stability must match fsrs-rs at the value Anki stores (4dp)."""
    r = _forgetting_curve(elapsed, s_prev, decay=-0.5)
    tt_s = _next_stability_lapse(d_prev, s_prev, r, W)
    anki_s = _fsrs_rs_next(s_prev, d_prev, elapsed).again.memory.stability
    _assert_fsrs_parity(
        tt_s,
        anki_s,
        _quantize_stability,
        f"lapse (s={s_prev}, d={d_prev}, elapsed={elapsed}): TT={tt_s!r} fsrs-rs={anki_s!r}",
        strict=_STRICT_FSRS_PARITY,
    )


@pytest.mark.parametrize("s_prev, d_prev, elapsed", _INPUTS)
def test_difficulty_bit_exact_vs_fsrs_rs(s_prev: float, d_prev: float, elapsed: int) -> None:
    """Difficulty update must match fsrs-rs at the value Anki stores (3dp) across all four ratings."""
    ns = _fsrs_rs_next(s_prev, d_prev, elapsed)
    for rating, attr in [
        (Rating.AGAIN, "again"),
        (Rating.HARD, "hard"),
        (Rating.GOOD, "good"),
        (Rating.EASY, "easy"),
    ]:
        tt_d = _next_difficulty(d_prev, rating, W)
        anki_d = getattr(ns, attr).memory.difficulty
        _assert_fsrs_parity(
            tt_d,
            anki_d,
            _quantize_difficulty,
            f"difficulty {attr} (d={d_prev}): TT={tt_d!r} fsrs-rs={anki_d!r}",
            strict=_STRICT_FSRS_PARITY,
        )


def test_forgetting_curve_bit_exact_vs_fsrs_rs() -> None:
    """TT's forgetting curve (factor + op order) is the FSRS retrievability formula
    Anki's queue-sort SQL uses. Test by inverting through ``next_states``: if r is
    correct, the next-state stability output will agree downstream at storage
    precision. This is a second test of the same precision claim from a different
    vantage."""
    # Spot-check: at (s, elapsed) = (40, 30), the curve r is the operand fed into
    # _next_stability_recall in test_recall_bit_exact_vs_fsrs_rs. If recall test
    # passes, this passes by construction — but a direct test makes the failure
    # mode unambiguous if FACTOR drifts.
    r = _forgetting_curve(30, 40.0, decay=-0.5)
    # fsrs-rs doesn't expose forgetting_curve directly; verify via downstream
    # recall agreement at the value Anki stores (4dp).
    assert 0.0 < r < 1.0, f"R out of range: {r}"
    tt_s = _next_stability_recall(6.84, 40.0, r, Rating.GOOD, W)
    anki_s = _fsrs_rs_next(40.0, 6.84, 30).good.memory.stability
    _assert_fsrs_parity(
        tt_s,
        anki_s,
        _quantize_stability,
        f"forgetting curve cascade: TT={tt_s!r} fsrs-rs={anki_s!r}",
        strict=_STRICT_FSRS_PARITY,
    )
