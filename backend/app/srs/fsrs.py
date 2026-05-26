"""FSRS-5 spaced repetition scheduling algorithm.

Reference: https://github.com/open-spaced-repetition/fsrs5
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from app.anki.protobuf_wire import compute_anki_day_index, review_due_at_for_col_day
from app.models.srs_item import Direction, DirectionState, Rating, RevlogRow, SRSItem, SRSState
from app.srs._anki_rng import ChaCha12Rng, random_range_f32, random_range_u32


def _learning_step_fuzz_seconds(anki_card_id: int | None, reps: int, step_seconds: int) -> int:
    """Anki-parity in-seconds learning fuzz, bit-exact with Anki's RNG.

    Returns `step_seconds + uniform_int[0, min(0.25 * step_seconds, 300))`,
    sampled via the same `StdRng::seed_from_u64(seed) → random_range(low..high)`
    chain Anki uses at `learning_ivl_with_fuzz` (rslib/.../answering/learning.rs).
    Seed is `(anki_card_id or 0) + reps` mod 2^64 — same as `get_fuzz_seed_for_id_and_reps`
    (rslib/.../answering/mod.rs:642). The bit-exact port lives in `_anki_rng.py`.

    Without bit-exact RNG parity, lockstep-grading TT vs Anki diverges by up to
    0.25*step on every learning grade because Python's RNG ≠ Rust's even with
    the same seed. With it, TT's `due_at` matches Anki's `cards.due` to the second.
    """
    upper_offset = min(int(step_seconds * 0.25), 300)
    if upper_offset <= 0:
        return step_seconds
    seed = ((anki_card_id or 0) + reps) & 0xFFFFFFFFFFFFFFFF
    rng = ChaCha12Rng(seed)
    return step_seconds + random_range_u32(rng, 0, upper_offset)


def _due_at_after_step(now: datetime, prev: DirectionState, delay_min: float) -> datetime:
    """Schedule a learning-step due_at with Anki-parity fuzz applied to the step."""
    step_seconds = int(round(delay_min * 60))
    fuzzed = _learning_step_fuzz_seconds(prev.anki_card_id, prev.reps, step_seconds)
    return now + timedelta(seconds=fuzzed)


def _review_due_at_from_interval(
    review_date: date,
    interval: int,
    col_crt: int | None,
    now: datetime,
    rollover_hour: int = 4,
) -> datetime:
    """Compute day-level (REVIEW state) due_at, matching sync_pull's convention.

    Layer 49: pre-fix this site used ``datetime.combine(review_date + interval,
    time(0,0), UTC)``, while sync_pull writeback uses 04:00 UTC anchored on
    Anki's col_day arithmetic via ``compute_due_at``. The two paths disagreed by
    4 hours of time-of-day plus any day offset from grades crossing the col_day
    boundary. Both now route through ``review_due_at_for_col_day``.

    When ``col_crt`` is None (no Anki sync yet — TT-only state), falls back to
    legacy UTC-midnight on ``review_date + interval``.
    """
    if col_crt is None:
        return datetime.combine(review_date + timedelta(days=interval), time(0, 0), tzinfo=UTC)
    today_col_day = compute_anki_day_index(col_crt, rollover_hour, now)
    return review_due_at_for_col_day(col_crt, today_col_day + interval, rollover_hour)


_FUZZ_RANGES: tuple[tuple[float, float, float], ...] = (
    (2.5, 7.0, 0.15),
    (7.0, 20.0, 0.10),
    (20.0, float("inf"), 0.05),
)


def _rust_round_half_away(x: float) -> int:
    """Mirror Rust's ``f32::round`` — half away from zero, not banker's rounding."""
    if x >= 0:
        return int(x + 0.5)
    return -int(-x + 0.5)


def _fuzz_delta(interval: float) -> float:
    """Port of ``fuzz_delta`` (rslib/.../states/fuzz.rs:111-119).

    Cumulative ±-band starting at 1.0, accumulating ``range.factor *
    (min(iv, range.end) - range.start).max(0)`` across all three FUZZ_RANGES.
    Not a single-range pick — interval=101 yields delta=7.025, not 5.05.
    """
    if interval < 2.5:
        return 0.0
    delta = 1.0
    for start, end, factor in _FUZZ_RANGES:
        delta += factor * max(0.0, min(interval, end) - start)
    return delta


def _constrained_fuzz_bounds(interval: float, minimum: int, maximum: int) -> tuple[int, int]:
    """Port of ``constrained_fuzz_bounds`` (rslib/.../states/fuzz.rs:82-97)."""
    minimum = min(minimum, maximum)
    interval = max(float(minimum), min(float(maximum), interval))
    delta = _fuzz_delta(interval)
    lower = max(minimum, min(maximum, _rust_round_half_away(interval - delta)))
    upper = max(minimum, min(maximum, _rust_round_half_away(interval + delta)))
    if upper == lower and upper > 2 and upper < maximum:  # pragma: no cover
        # Defensive parity with rslib/.../states/fuzz.rs:92-94. Not reachable
        # with our `_fuzz_delta` (which is either 0 below 2.5 or ≥ 1 above) and
        # `_rust_round_half_away`, but mirrored exactly so source-side changes
        # transfer cleanly.
        upper = lower + 1
    return lower, upper


def _review_interval_fuzz(
    raw_interval_days: float,
    anki_card_id: int | None,
    reps: int,
    max_interval: int = 36500,
) -> int:
    """Mirror Anki's ``with_review_fuzz`` bit-exact (rslib/.../states/fuzz.rs:65-77).

    Seed = ``(anki_card_id or 0) + reps`` mod 2^64 (rslib/.../answering/mod.rs:642-647).
    Factor is sampled via ``ChaCha12Rng(seed).random_range(0.0..1.0)`` for ``f32``;
    ``random_range_f32`` mirrors Rust's canonical 24-bit-mantissa formula.

    ``reps`` is the value at grade time (Anki ``card.reps`` *before* the increment).
    For reschedule of an existing graded row, pass ``current_reps - 1`` to recover
    the at-grade seed (Anki ``for_reschedule=true``).
    """
    lower, upper = _constrained_fuzz_bounds(raw_interval_days, 1, max_interval)
    seed = ((anki_card_id or 0) + reps) & 0xFFFFFFFFFFFFFFFF
    factor = random_range_f32(ChaCha12Rng(seed))
    return lower + int(factor * (1 + upper - lower))


# FSRS-5 default parameters (w vector, 19 values)
_DEFAULT_WEIGHTS: tuple[float, ...] = (
    0.4072,  # w0: initial stability for Again
    1.1829,  # w1: initial stability for Hard
    3.1262,  # w2: initial stability for Good
    15.4722,  # w3: initial stability for Easy
    7.2102,  # w4: initial difficulty
    0.5316,  # w5: initial difficulty decay
    1.0651,  # w6: difficulty mean-reversion weight
    0.0589,  # w7: difficulty update weight
    1.5330,  # w8: stability increase factor
    0.1544,  # w9: stability increase decay
    1.0050,  # w10: stability increase R-factor
    1.9767,  # w11: lapse stability factor
    0.0967,  # w12: lapse stability difficulty decay
    0.2573,  # w13: lapse stability S-factor
    2.2930,  # w14: lapse stability R-factor
    0.5100,  # w15: hard penalty
    2.9898,  # w16: easy bonus
    0.5100,  # w17: (unused in v5)
    0.4350,  # w18: (unused in v5)
)

FACTOR = 19 / 81  # = 0.234...


@dataclass(frozen=True)
class FSRSParams:
    """FSRS scheduling parameters (weights + desired retention).

    Supports FSRS-5 (19 weights) and FSRS-6 (21 weights).
    ``decay`` and ``version`` are derived from the weight count.
    """

    weights: tuple[float, ...]
    desired_retention: float = 0.9
    decay: float = 0.5
    version: int = 5
    maximum_review_interval: int = 36500

    def __post_init__(self) -> None:
        n = len(self.weights)
        if n == 19:
            object.__setattr__(self, "decay", 0.5)
            object.__setattr__(self, "version", 5)
        elif n == 21:
            object.__setattr__(self, "decay", self.weights[20])
            object.__setattr__(self, "version", 6)
        else:
            raise ValueError(f"FSRSParams requires 19 (FSRS-5) or 21 (FSRS-6) weights, got {n}")


DEFAULT_FSRS5_PARAMS = FSRSParams(weights=_DEFAULT_WEIGHTS)


def _forgetting_curve(elapsed_days: float, stability: float, decay: float = -0.5) -> float:
    """Retrievability at elapsed_days given stability and decay."""
    return (1 + FACTOR * elapsed_days / stability) ** decay


def _elapsed_days_for_fsrs(
    last_review: datetime | date | None,
    ref_now: datetime,
    col_crt: int | None = None,
    rollover_hour: int = 4,
) -> float:
    """Mirror Anki's `extract_fsrs_retrievability` dual branch for elapsed time.

    Anki's lapse + recall stability formulas both feed off `delta_t = now - lrt`
    in fractional days when `cards.data.lrt` is present (FSRS-effective last
    review timestamp), and fall back to integer `today_col_day - (due - ivl)`
    when it isn't.

    TT mirrors via the marker `parse_fsrs_data` sets: midnight UTC `last_review`
    = day-level fallback (no lrt was present); any sub-day component = lrt was
    present and `last_review` carries it.

    The same dual-branch logic also lives in `compute_retrievability` for R-asc
    sort.

    Layer 45: when *col_crt* is provided and the day-level branch is taken,
    compute elapsed as ``today_col_day - review_col_day`` using
    ``compute_anki_day_index``, which respects Anki's 4am-local rollover
    boundary. When *col_crt* is ``None`` (pre-sync, no cache), fall back to
    UTC-date subtraction (the legacy behavior, preserved for backward compat).
    """
    if last_review is None:
        return 0.0
    if isinstance(last_review, datetime):
        is_day_level = (
            last_review.hour == 0
            and last_review.minute == 0
            and last_review.second == 0
            and last_review.microsecond == 0
        )
        if is_day_level and col_crt is not None:
            today_col_day = compute_anki_day_index(col_crt, rollover_hour, ref_now)
            review_col_day = compute_anki_day_index(col_crt, rollover_hour, last_review)
            return max(0, today_col_day - review_col_day)
        if is_day_level:
            return max(0, (ref_now.date() - last_review.date()).days)
        return max(0.0, (ref_now - last_review).total_seconds() / 86400.0)
    # `last_review` is a date (no time-of-day at all) — day-level by definition.
    return max(0, (ref_now.date() - last_review).days)


def _grade_elapsed_days(
    last_review: datetime | date | None,
    ref_now: datetime,
    col_crt: int | None = None,
    rollover_hour: int = 4,
) -> int:
    """Layer 50: grade-time ``days_elapsed`` is INTEGER col-day diff.

    Mirrors Anki's answering path: ``next_day_at.elapsed_days_since(lrt)``
    (``rslib/.../scheduler/answering/mod.rs:480-487``), which is u64
    integer division by 86400 (``rslib/.../timestamp.rs:31``). Anki uses
    INTEGER regardless of whether ``cards.data.lrt`` carries sub-day
    precision — the dual fractional/integer branch lives only in
    ``extract_fsrs_retrievability`` (queue-sort R), NOT in the answering
    flow that drives stability_after_success / stability_after_failure.

    Layer 50 finding (2026-05-22 Stage 3b empirical measurement): TT was
    routing grade-time R through ``_elapsed_days_for_fsrs``, which returns
    fractional days for sub-day-precise ``last_review``. This produced
    systematic ~5-7% stability drift on every REVIEW grade. Switching to
    integer col-day diff gave bit-exact match across all 65 single
    REVIEW→REVIEW passing grades in the snapshots.

    Keep ``_elapsed_days_for_fsrs`` for queue-sort R (Layer 11/15 dual
    branch) — that path matches Anki's ``extract_fsrs_retrievability``,
    which IS fractional when lrt is present.
    """
    if last_review is None:
        return 0
    if isinstance(last_review, datetime):
        if col_crt is not None:
            today_col_day = compute_anki_day_index(col_crt, rollover_hour, ref_now)
            review_col_day = compute_anki_day_index(col_crt, rollover_hour, last_review)
            return max(0, today_col_day - review_col_day)
        return max(0, (ref_now.date() - last_review.date()).days)
    return max(0, (ref_now.date() - last_review).days)


def compute_retrievability(
    direction_state: DirectionState,
    today: date,
    now: datetime | None = None,
    desired_retention: float = 0.9,
    decay: float = -0.5,
    col_crt: int | None = None,
) -> float:
    """Return retrievability (0-1) for a direction_state.

    Null stability or null last_review → return ``desired_retention``.
    Empirically Anki places review cards with no memory_state (``data='{}'``) at
    the R-asc position that ``desired_retention`` would occupy, not at the
    SQLite NULLs-first head. The 0.9 default mirrors Anki's app-level default
    when no deck-config value is cached.

    Mirrors Anki's `extract_fsrs_retrievability` (rslib storage/sqlite.rs):
      - When cards.data has `lrt` (FSRS-effective last-review timestamp,
        precise to seconds), Anki uses `now - lrt` in seconds → fractional days.
        TT detects this via a non-midnight time-of-day on `last_review`.
      - When cards.data lacks `lrt` (older cards / pre-FSRS migration), Anki
        falls back to `(today_col_day - (due - ivl)) * 86400` → INTEGER days.
        TT mirrors via `(today - last_review.date()).days` when last_review
        is at midnight UTC (the marker that `parse_fsrs_data` set day-level).
    Using fractional days for non-lrt cards produced a slightly smaller R than
    Anki (e.g. 0.706 vs 0.723), flipping R-asc order against Anki's.
    """
    stability = direction_state.stability
    last_review = direction_state.last_review
    if stability is None or last_review is None:
        return desired_retention
    if isinstance(last_review, datetime):
        ref_now = now if now is not None else datetime.now(UTC)
        elapsed = _elapsed_days_for_fsrs(last_review, ref_now, col_crt=col_crt)
    else:
        elapsed = max(0, (today - last_review).days)
    return _forgetting_curve(elapsed, stability, decay)


def _next_interval(stability: float, desired_retention: float, decay: float = -0.5) -> int:
    """Days until next review at the given desired_retention."""
    interval = stability / FACTOR * (desired_retention ** (1 / decay) - 1)
    return max(1, min(_rust_round_half_away(interval), 36500))


def _greater_than_last(interval: int, scheduled_days: int) -> int:
    """Anki's greater_than_last: returns scheduled_days + 1 if interval > scheduled_days else 0.

    Mirrors rslib/src/scheduler/states/review.rs ``greater_than_last``.
    """
    if interval > scheduled_days:
        return scheduled_days + 1
    return 0


def _constrain_passing_intervals(
    hard_raw: int,
    good_raw: int,
    easy_raw: int,
    scheduled_days: int,
) -> tuple[int, int, int]:
    """Anki parity cascade: each rating must beat the next-easier one by ≥1 day.

    For each rating: ``constrained = max(raw, floor)`` where ``floor`` is derived
    from ``greater_than_last`` and the next-easier constrained value.

    Mirrors rslib/src/scheduler/states/review.rs ``constrain_passing_interval``.
    Returns (hard, good, easy) constrained.

    Layer 51 note: this helper applies the cascade as a *pure-integer* floor on
    pre-fuzz raw intervals. Anki's actual flow interleaves cascade and fuzz —
    each rating's fuzz call receives the cascade-derived ``minimum`` argument,
    which clamps the fuzzed result's lower bound. Use
    ``_passing_intervals_with_fuzz`` for the grade-time pipeline (REVIEW + EASY
    graduation); this helper remains for callers that need the pre-fuzz cascade
    output in isolation.
    """
    hard = max(hard_raw, max(_greater_than_last(hard_raw, scheduled_days), 1))
    good = max(good_raw, max(_greater_than_last(good_raw, scheduled_days), hard + 1))
    easy = max(easy_raw, max(_greater_than_last(easy_raw, scheduled_days), good + 1))
    return (hard, good, easy)


def _passing_intervals_with_fuzz(
    raw_hard: float,
    raw_good: float,
    raw_easy: float,
    scheduled_days: int,
    anki_card_id: int | None,
    reps: int,
    max_interval: int = 36500,
    *,
    load_balancer: object | None = None,
    note_id: int | None = None,
) -> tuple[int, int, int]:
    """Mirror Anki's interleaved cascade + fuzz pipeline (Layer 51).

    Anki's ``passing_fsrs_review_intervals`` (rslib/.../states/review.rs:178-211)
    computes for each rating in (hard, good, easy) order:
        ``minimum = max(greater_than_last(round(raw_i), scheduled_days), prev_fuzzed + 1)``
        ``result_i = with_review_fuzz(raw_i_float, minimum, max_interval)``

    The same ``fuzz_factor`` (sampled once via ``ChaCha12Rng(card.id + reps)``)
    is reused across all three ratings — ``ctx.fuzz_factor`` is set once per
    ``card_state_updater`` (rslib/.../answering/mod.rs:92, 517) and then
    threaded into each ``with_review_fuzz`` call.

    Pre-Layer-51, TT applied ``_constrain_passing_intervals`` as a pure-integer
    cascade first, then ``_review_interval_fuzz`` with ``minimum=1`` on the
    chosen rating's cascade output. The cascade floor was thus NOT carried into
    the fuzz lower bound — for low ChaCha factors, fuzz could drop the
    interval back below the cascade floor. Produced systematic off-by-1-day
    drift in 34/65 single-grade REVIEW→REVIEW cases (Stage 3b 2026-05-22
    measurement, post Layer 50). Anki bit-exact across all 65 with this fix.
    """
    seed = ((anki_card_id or 0) + reps) & 0xFFFFFFFFFFFFFFFF
    factor = random_range_f32(ChaCha12Rng(seed))

    def _fuzz(interval_raw: float, minimum: int) -> int:
        # Anki's `with_review_fuzz` (fuzz.rs:36-42) tries the load balancer first
        # and only falls back to pure fuzz when it's absent / out of range.
        if load_balancer is not None:
            balanced = load_balancer.find_interval(interval_raw, minimum, max_interval, seed, note_id)
            if balanced is not None:
                return balanced
        lower, upper = _constrained_fuzz_bounds(interval_raw, minimum, max_interval)
        return lower + int(factor * (1 + upper - lower))

    hard_min = max(_greater_than_last(_rust_round_half_away(raw_hard), scheduled_days), 1)
    hard = _fuzz(raw_hard, hard_min)
    good_min = max(_greater_than_last(_rust_round_half_away(raw_good), scheduled_days), hard + 1)
    good = _fuzz(raw_good, good_min)
    easy_min = max(_greater_than_last(_rust_round_half_away(raw_easy), scheduled_days), good + 1)
    easy = _fuzz(raw_easy, easy_min)
    return (hard, good, easy)


def _next_interval_raw(stability: float, desired_retention: float, decay: float = -0.5) -> float:
    """Raw FSRS interval (Layer 51). Layer 48's ``_next_interval`` rounds to
    integer for the cascade path; Anki passes the *float* interval into
    ``with_review_fuzz`` so the fuzz_delta computation uses unrounded input.
    """
    return stability / FACTOR * (desired_retention ** (1 / decay) - 1)


def _graduation_intervals_with_fuzz(
    raw_hard: float,
    raw_good: float,
    raw_easy: float,
    anki_card_id: int | None,
    reps: int,
    max_interval: int = 36500,
    *,
    load_balancer: object | None = None,
    note_id: int | None = None,
) -> tuple[int, int, int]:
    """Mirror Anki's graduation fuzz pipeline (Layer 52).

    Anki's LEARNING/RELEARNING graduation (rslib/.../states/learning.rs:86-178
    and relearning.rs:104-184) does NOT apply the passing-review cascade. Each
    rating's fuzz call uses ``minimum=1`` directly:

        hard = with_review_fuzz(round(raw_hard).max(1.0), 1, max)
        good = with_review_fuzz(round(raw_good).max(1.0), 1, max)
        # EASY is the only rating that floors against good:
        good_for_easy = with_review_fuzz(raw_good, 1, max)  # float interval
        easy = with_review_fuzz(round(raw_easy).max(1.0), good_for_easy + 1, max)

    Note the EASY-only asymmetry: it computes a SEPARATE good_for_easy using
    the FLOAT raw_good (NOT rounded), then uses good_for_easy + 1 as the floor
    for EASY. The chosen-rating GOOD output still uses the rounded raw_good.

    All four fuzz calls reuse the same ``ChaCha12Rng(card.id + reps)`` factor.

    Pre-Layer-52 TT routed graduation through ``_passing_intervals_with_fuzz``
    (with ``scheduled_days=0``), applying the passing-review cascade
    (``good_min = max(gtl, hard_fuzzed + 1)``). For graduation scenarios where
    hard_fuzzed + 1 > 1 AND the fuzz factor would otherwise place good below
    that floor, TT shifted good up by +1 day. Surfaced via the multi-grade
    drill (2026-05-23): 28/40 REVIEW→AGAIN→GOOD-graduation cases showed
    systematic +1 day relative to Anki's stored ``cards.ivl``.
    """
    seed = ((anki_card_id or 0) + reps) & 0xFFFFFFFFFFFFFFFF
    factor = random_range_f32(ChaCha12Rng(seed))

    def _fuzz(interval_in: float, minimum: int) -> int:
        if load_balancer is not None:
            balanced = load_balancer.find_interval(interval_in, minimum, max_interval, seed, note_id)
            if balanced is not None:
                return balanced
        lower, upper = _constrained_fuzz_bounds(interval_in, minimum, max_interval)
        return lower + int(factor * (1 + upper - lower))

    # HARD: round(raw_hard).max(1.0), min=1
    hard = _fuzz(max(1.0, float(_rust_round_half_away(raw_hard))), 1)
    # GOOD (chosen-rating): round(raw_good).max(1.0), min=1
    good = _fuzz(max(1.0, float(_rust_round_half_away(raw_good))), 1)
    # EASY: floor against good_for_easy = fuzz(raw_good_float, min=1).
    # NOTE: this good_for_easy uses the FLOAT raw_good (not rounded); differs
    # from the chosen-rating GOOD output above. Both reuse the same factor.
    good_for_easy = _fuzz(raw_good, 1)
    easy = _fuzz(max(1.0, float(_rust_round_half_away(raw_easy))), good_for_easy + 1)
    return (hard, good, easy)


def _scheduled_days_for_grade(prev: DirectionState, col_crt: int | None) -> int:
    """Layer 51 (companion to interleaved fuzz). Mirror Anki's ``ReviewState
    .scheduled_days = card.interval`` (rslib/.../answering/current.rs:107):
    the previous-grade chosen interval, used as the floor input for the
    cascade.

    TT doesn't store ``ivl`` separately. The reconstruction is
    ``anki_due - col_day(last_review)`` — both endpoints in Anki's col_day
    arithmetic. With ``anki_due`` available (synced) and ``last_review`` as
    a real lrt timestamp, this exactly matches ``card.ivl`` at sync time.

    Pre-Layer-51 used ``(prev.due_at - prev.last_review).days``. That formula
    truncates ~32 hours to 1 day for sub-day-precise lrt + Layer 49's 04:00
    UTC due_at anchor — off by 1 for every sub-day-precise card. Fine before
    Layer 51 (cascade floor wasn't carried into fuzz), bad after.

    Fallback when ``anki_due`` is unset (TT-only state pre-sync): keep the
    legacy timestamp-diff. Inaccurate for sub-day lrt but the only signal we
    have without an Anki round-trip.
    """
    if prev.last_review is None:
        return 0
    lr = (
        prev.last_review
        if isinstance(prev.last_review, datetime)
        else datetime.combine(prev.last_review, time(0, 0), tzinfo=UTC)
    )
    if col_crt is not None and prev.anki_due is not None:
        review_col_day = compute_anki_day_index(col_crt, 4, lr)
        return max(0, prev.anki_due - review_col_day)
    return max(0, (prev.due_at - lr).days)


def _quantize_stability(s: float) -> float:
    # Mirror Anki's per-grade rounding (rslib/src/storage/card/data.rs:95).
    # Anki stores `cards.data.s` rounded to 4 dp and reads it back on the next
    # grade; without matching this, TT propagates full f64 precision and drifts
    # ~1% over a 10-grade learning sequence.
    return round(s, 4)


def _quantize_difficulty(d: float) -> float:
    # Mirror Anki's per-grade rounding (rslib/src/storage/card/data.rs:98).
    return round(d, 3)


def _init_stability(rating: Rating, w: tuple[float, ...]) -> float:
    return w[rating.value - 1]


def _init_difficulty(rating: Rating, w: tuple[float, ...]) -> float:
    d = w[4] - math.exp(w[5] * (rating.value - 1)) + 1
    return max(1.0, min(10.0, d))


def _next_difficulty(d: float, rating: Rating, w: tuple[float, ...]) -> float:
    delta_d = -w[6] * (rating.value - 3)
    next_d = d + (10 - d) / 9 * delta_d
    easy_init = _init_difficulty(Rating.EASY, w)
    next_d = w[7] * (easy_init - next_d) + next_d
    return max(1.0, min(10.0, next_d))


def _next_stability_recall(d: float, s: float, r: float, rating: Rating, w: tuple[float, ...]) -> float:
    hard_penalty = w[15] if rating == Rating.HARD else 1.0
    easy_bonus = w[16] if rating == Rating.EASY else 1.0
    return s * (
        math.exp(w[8]) * (11 - d) * s ** (-w[9]) * (math.exp((1 - r) * w[10]) - 1) * hard_penalty * easy_bonus + 1
    )


def _next_stability_lapse(d: float, s: float, r: float, w: tuple[float, ...]) -> float:
    # fsrs-rs `stability_after_failure` (model.rs:91-105) caps the post-lapse
    # stability at `last_s / exp(w[17] * w[18])`. Without this ceiling the raw
    # formula overshoots for low-s cards; surfaced as Layer 42 via the
    # `test_parity_fsrs_schedule` oracle harness.
    new_s = w[11] * d ** (-w[12]) * ((s + 1) ** w[13] - 1) * math.exp((1 - r) * w[14])
    new_s_min = s / math.exp(w[17] * w[18])
    return min(new_s, new_s_min)


def _stability_short_term(last_s: float, rating: Rating, params: FSRSParams) -> float:
    """FSRS short-term stability update for same-day grades.

    Mirrors ``model.rs:107-115`` in fsrs-rs:
      ``sinc = exp(w[17] * (rating - 3 + w[18])) * last_s^(-w[19])``
      ``if rating >= 3: sinc = max(sinc, 1.0)``
      ``new_s = last_s * sinc``

    For FSRS-5 the ``last_s^(-w[19])`` term vanishes (``w[19]`` effectively 0).
    For FSRS-6 ``w[19]`` is a learned parameter.
    """
    w = params.weights
    w19 = w[19] if params.version == 6 else 0.0
    sinc = math.exp(w[17] * (rating.value - 3 + w[18])) * (last_s ** (-w19))
    if rating.value >= 3:
        sinc = max(sinc, 1.0)
    return last_s * sinc


def _parse_left(left: int | None) -> int:
    """Decode Anki's `cards.left` to total_remaining steps.

    Anki stores left as `today_left * 1000 + total_remaining`. Only the low 3
    digits drive the state machine — `Card::remaining_steps()` in
    rslib/src/card/mod.rs:218 always returns `self.remaining_steps % 1000`.
    The high digits are a queue-bookkeeping count for "how many more times
    today" and don't change which learn step the card is on.

    Returns 0 for None / 0, which the caller treats as "no left tracked yet"
    (typically a fresh entry, normalized to total_steps = full count).
    """
    if not left:
        return 0
    return left % 1000


def _pack_left(total_remaining: int) -> int:
    """Encode total_remaining steps into Anki's `cards.left` format.

    Modern Anki writes the count directly (no `today_left` prefix); the legacy
    `today_left * 1000 + total_remaining` form is still accepted on read but
    not produced. Anki's `Card.remaining_steps % 1000` decodes both.
    """
    return total_remaining


def _grade_prior_state(prev: DirectionState, new_state: SRSState) -> SRSState:
    """Compute `prior_state` for a graded direction.

    Sticky-NEW semantic: when a card was introduced today
    (`prior_state='new'` set by sync's NEW→graded transition or by an
    in-session first grade), keep `prior_state='new'` across every grade
    on the card's intro arc — learning steps **and** graduation to REVIEW.
    Anki's `newToday` counter increments on first grade and never
    decrements during the day; `count_new_introduced_today` must mirror
    that, which requires the marker to survive the LEARNING→REVIEW
    transition.

    The only release is REVIEW→RELEARNING (a lapse). The lapse revlog
    must record `prior_state='review'` so Anki's `revlog.type` is 1
    (Review). After a lapse, the card has effectively "left" its intro
    arc — losing the marker here is acceptable; revlog correctness wins.

    For all other transitions, `prior_state` captures the immediately-
    previous state — what `_derive_revlog_shape` needs for revlog `type`.
    """
    if prev.prior_state == SRSState.NEW and new_state != SRSState.RELEARNING:
        return SRSState.NEW
    return prev.state


def _get_steps_for_state(state: SRSState) -> tuple[list[float], str]:
    """Get learning/relearning steps for the given state.

    Returns (steps_list, step_field_name).
    """
    from app.srs.queue_stats import resolve_learning_steps, resolve_relearning_steps

    if state == SRSState.RELEARNING:
        return resolve_relearning_steps()
    else:
        return resolve_learning_steps()


def schedule(
    item: SRSItem,
    rating: Rating,
    review_date: date | None = None,
    direction: Direction = Direction.RECOGNITION,
    params: FSRSParams = DEFAULT_FSRS5_PARAMS,
    time_ms: int = 0,
    now: datetime | None = None,
    col_crt: int | None = None,
    load_balancer: object | None = None,
) -> SRSItem:
    """Apply a review rating to the given direction of an SRSItem.

    Updates only the specified direction; the other is left untouched.
    Marks `dirty_fsrs=True` on the updated direction so the Anki-sync layer
    can later push the change.

    Implements Anki-parity learning steps:
    - NEW + AGAIN/HARD → LEARNING (step 0)
    - NEW + GOOD → LEARNING (step 1) or graduate if 1-step deck
    - NEW + EASY → graduate immediately to REVIEW (FSRS init at EASY weights)
    - LEARNING/RELEARNING + AGAIN → reset to step 0
    - LEARNING/RELEARNING + HARD → same step
    - LEARNING/RELEARNING + GOOD → next step, or graduate if last
    - LEARNING/RELEARNING + EASY → graduate immediately
    - REVIEW + AGAIN → RELEARNING (step 0)
    - REVIEW + HARD/GOOD/EASY → REVIEW (FSRS interval)
    """
    if review_date is None:
        review_date = date.today()

    if now is None:
        now = datetime.now(tz=UTC)
    if review_date == date.today():
        last_review_dt = now
    else:
        last_review_dt = datetime.combine(review_date, datetime.min.time(), tzinfo=UTC)

    from dataclasses import replace

    w = params.weights
    neg_decay = -params.decay
    prev = item.directions[direction]

    # Handle learning step semantics for LEARNING and RELEARNING states
    if prev.state in (SRSState.LEARNING, SRSState.RELEARNING):
        return _schedule_with_steps(
            item,
            prev,
            rating,
            review_date,
            direction,
            params,
            time_ms,
            now,
            last_review_dt,
            col_crt=col_crt,
            load_balancer=load_balancer,
        )

    # Handle NEW state with learning steps (Anki parity)
    if prev.state == SRSState.NEW:
        return _schedule_new(
            item,
            prev,
            rating,
            direction,
            time_ms,
            now,
            last_review_dt,
            params,
            review_date=review_date,
            col_crt=col_crt,
            load_balancer=load_balancer,
        )

    # REVIEW state logic
    else:
        # REVIEW state. Layer 50: grade-time R uses INTEGER col-day diff
        # (Anki's `next_day_at.elapsed_days_since(lrt)` is u64 integer div
        # by 86400). The lrt-fractional branch lives only in queue-sort R,
        # not in the answering path. See `_grade_elapsed_days`.
        last = prev.last_review or last_review_dt
        elapsed = _grade_elapsed_days(last, last_review_dt, col_crt=col_crt)
        r = _forgetting_curve(elapsed, prev.stability, neg_decay)

        if rating == Rating.AGAIN:
            return _schedule_review_again(
                item,
                prev,
                rating,
                review_date,
                direction,
                params,
                time_ms,
                now,
                last_review_dt,
                col_crt=col_crt,
                load_balancer=load_balancer,
            )
        else:
            # Compute stabilities for all three passing ratings (cascade needs all)
            s_hard = _next_stability_recall(prev.difficulty, prev.stability, r, Rating.HARD, w)
            s_good = _next_stability_recall(prev.difficulty, prev.stability, r, Rating.GOOD, w)
            s_easy = _next_stability_recall(prev.difficulty, prev.stability, r, Rating.EASY, w)
            rating_to_s = {Rating.HARD: s_hard, Rating.GOOD: s_good, Rating.EASY: s_easy}
            new_stability = rating_to_s[rating]
            new_difficulty = _next_difficulty(prev.difficulty, rating, w)
            new_reps = prev.reps + 1
            new_lapses = prev.lapses
            new_state = SRSState.REVIEW

    # fsrs-rs S_MIN = 0.001 (fsrs-rs/src/simulation.rs:41); 4dp/3dp rounding
    # mirrors Anki's per-grade quantization in cards.data.
    new_stability = _quantize_stability(max(0.001, new_stability))
    new_difficulty = _quantize_difficulty(max(1.0, min(10.0, new_difficulty)))
    # Anki parity cascade: each rating's interval must beat scheduled_days and
    # the next-easier rating (rslib/.../states/review.rs:constrain_passing_interval).
    # Layer 51 (scheduled_days fix): mirror Anki's `card.interval` via
    # `anki_due - col_day(last_review)` instead of `(due_at - last_review).days`.
    scheduled_days = _scheduled_days_for_grade(prev, col_crt)
    # Layer 51: Anki interleaves cascade + fuzz — minimum from greater_than_last
    # is passed into with_review_fuzz, which clamps the fuzz lower bound. TT's
    # pre-Layer-51 two-step (cascade first, then fuzz with minimum=1) let fuzz
    # drop intervals below the cascade floor. Pass pre-fuzz floats (Anki's
    # `states.X.interval` is float; rounding happens inside fuzz_bounds).
    raw_hard_f = _next_interval_raw(_quantize_stability(max(0.001, s_hard)), params.desired_retention, neg_decay)
    raw_good_f = _next_interval_raw(_quantize_stability(max(0.001, s_good)), params.desired_retention, neg_decay)
    raw_easy_f = _next_interval_raw(_quantize_stability(max(0.001, s_easy)), params.desired_retention, neg_decay)
    fuzzed = _passing_intervals_with_fuzz(
        raw_hard_f,
        raw_good_f,
        raw_easy_f,
        scheduled_days,
        prev.anki_card_id,
        prev.reps,
        params.maximum_review_interval,
        load_balancer=load_balancer,
        note_id=item.anki_note_id,
    )
    interval = {Rating.HARD: fuzzed[0], Rating.GOOD: fuzzed[1], Rating.EASY: fuzzed[2]}[rating]
    new_due_at = _review_due_at_from_interval(review_date, interval, col_crt, now)

    new_dir = replace(
        prev,
        stability=new_stability,
        difficulty=new_difficulty,
        due_at=new_due_at,
        reps=new_reps,
        lapses=new_lapses,
        state=new_state,
        last_review=last_review_dt,
        last_review_time_ms=time_ms,
        dirty_fsrs=True,
        last_rating=rating.value,
        prior_state=_grade_prior_state(prev, new_state),
        prior_left=prev.left,
        prior_stability=prev.stability,
    )
    new_directions = dict(item.directions)
    new_directions[direction] = new_dir
    return SRSItem(
        syntactic_unit=item.syntactic_unit,
        directions=new_directions,
        guid=item.guid,
        anki_note_id=item.anki_note_id,
    )


def _schedule_new(
    item: SRSItem,
    prev: DirectionState,
    rating: Rating,
    direction: Direction,
    time_ms: int,
    now: datetime,
    last_review_dt: datetime,
    params: FSRSParams = DEFAULT_FSRS5_PARAMS,
    review_date: date | None = None,
    col_crt: int | None = None,
    load_balancer: object | None = None,
) -> SRSItem:
    """NEW + any rating: walk learn_steps like Anki.

    AGAIN/HARD → step 0; GOOD → step 1 (or graduate if last); EASY → graduate immediately.
    """
    from dataclasses import replace

    if rating == Rating.EASY:
        return _graduate_to_review(
            item,
            prev,
            rating,
            direction,
            time_ms,
            now,
            last_review_dt,
            params,
            review_date=review_date,
            col_crt=col_crt,
            load_balancer=load_balancer,
        )

    steps, _ = _get_steps_for_state(SRSState.LEARNING)
    if not steps:
        return _graduate_to_review(
            item,
            prev,
            rating,
            direction,
            time_ms,
            now,
            last_review_dt,
            params,
            review_date=review_date,
            col_crt=col_crt,
            load_balancer=load_balancer,
        )

    total_steps = len(steps)
    if rating == Rating.GOOD:
        # Advance to step 1; graduate if only one step total
        if total_steps == 1:
            return _graduate_to_review(
                item,
                prev,
                rating,
                direction,
                time_ms,
                now,
                last_review_dt,
                params,
                review_date=review_date,
                col_crt=col_crt,
                load_balancer=load_balancer,
            )
        step_index = 1
    else:  # AGAIN or HARD: stay at step 0
        step_index = 0

    # First grade out of NEW: seed stability from w[0..3] (matches Anki's
    # fsrs-rs `step()` with `state=None`). DirectionState.stability defaults
    # to 1.0, so we can't infer "no prior FSRS" from stability alone —
    # `_schedule_new` is only called when prev.state == NEW, so the seed
    # branch is unconditional here.
    w = params.weights
    new_stability = _quantize_stability(_init_stability(rating, w))
    new_difficulty = _quantize_difficulty(_init_difficulty(rating, w))

    # total_remaining = steps left until graduation = total_steps - step_index
    new_left = _pack_left(total_steps - step_index)
    # Anki's Hard-on-first-step delay (rslib/.../scheduler/states/steps.rs:38-66):
    #   - ≥2 steps: avg of first two steps (e.g. [1,10] → 330s)
    #   - 1 step:   min(again*1.5, again + 1 day) (e.g. [10] → 900s)
    # Again uses step[0] verbatim regardless.
    if rating == Rating.HARD:
        again_secs = steps[0] * 60
        delay_min = (steps[0] + steps[1]) / 2 if total_steps > 1 else min(again_secs * 1.5, again_secs + 86400) / 60
    else:
        delay_min = steps[step_index]
    new_due_at = _due_at_after_step(now, prev, delay_min)

    new_dir = replace(
        prev,
        state=SRSState.LEARNING,
        due_at=new_due_at,
        left=new_left,
        stability=new_stability,
        difficulty=new_difficulty,
        reps=prev.reps + 1,
        lapses=prev.lapses,
        last_review=last_review_dt,
        last_review_time_ms=time_ms,
        dirty_fsrs=True,
        last_rating=rating.value,
        prior_state=_grade_prior_state(prev, SRSState.LEARNING),
        prior_left=prev.left,
        prior_stability=prev.stability,
        # Layer 26: stamp the first-grade event so count_new_introduced_today
        # reflects Anki's `newToday` increment exactly once per intro arc.
        introduced_at=last_review_dt,
    )
    new_directions = dict(item.directions)
    new_directions[direction] = new_dir
    return SRSItem(
        syntactic_unit=item.syntactic_unit,
        directions=new_directions,
        guid=item.guid,
        anki_note_id=item.anki_note_id,
    )


def _schedule_review_again(
    item: SRSItem,
    prev: DirectionState,
    rating: Rating,
    review_date: date,
    direction: Direction,
    params: FSRSParams,
    time_ms: int,
    now: datetime,
    last_review_dt: datetime,
    col_crt: int | None = None,
    load_balancer: object | None = None,
) -> SRSItem:
    """Handle REVIEW + AGAIN: enter RELEARNING with relearning steps."""
    from dataclasses import replace

    w = params.weights
    # Layer 50: grade-time elapsed is INTEGER col-day diff (Anki's answering
    # path uses `next_day_at.elapsed_days_since(lrt)`, u64 integer div by
    # 86400). The fractional-from-lrt branch lives in queue-sort R only,
    # not here. See `_grade_elapsed_days`.
    last = prev.last_review or last_review_dt
    elapsed = _grade_elapsed_days(last, last_review_dt, col_crt=col_crt)
    # fsrs-rs model.rs:154-163: short-term stability overrides the lapse formula
    # when delta_t == 0 (same-day grade). The deck option only governs card-state
    # transitions, not memory_state — so this branch is not flag-gated.
    if elapsed == 0 and prev.stability is not None:
        new_stability = _stability_short_term(prev.stability, Rating.AGAIN, params)
        new_difficulty = _next_difficulty(prev.difficulty, rating, w)
    else:
        r = _forgetting_curve(elapsed, prev.stability, -params.decay) if prev.stability > 0 else 1.0
        new_stability = _next_stability_lapse(prev.difficulty, prev.stability, r, w)
        new_difficulty = _next_difficulty(prev.difficulty, rating, w)
    new_stability = _quantize_stability(new_stability)
    new_difficulty = _quantize_difficulty(new_difficulty)

    steps, _ = _get_steps_for_state(SRSState.RELEARNING)

    if not steps:
        # Empty steps = graduate immediately (same as Anki)
        return _graduate_to_review(
            item,
            prev,
            rating,
            direction,
            time_ms,
            now,
            last_review_dt,
            params,
            review_date=review_date,
            col_crt=col_crt,
            load_balancer=load_balancer,
        )

    # Start at step 0 of relearning: total_remaining = full count
    total_steps = len(steps)
    new_left = _pack_left(total_steps)
    new_due_at = _due_at_after_step(now, prev, steps[0])

    new_dir = replace(
        prev,
        stability=new_stability,
        difficulty=new_difficulty,
        state=SRSState.RELEARNING,
        due_at=new_due_at,
        left=new_left,
        reps=prev.reps + 1,
        lapses=prev.lapses + 1,
        last_review=last_review_dt,
        last_review_time_ms=time_ms,
        dirty_fsrs=True,
        last_rating=rating.value,
        prior_state=_grade_prior_state(prev, SRSState.RELEARNING),
        prior_left=prev.left,
        prior_stability=prev.stability,
    )
    new_directions = dict(item.directions)
    new_directions[direction] = new_dir
    return SRSItem(
        syntactic_unit=item.syntactic_unit,
        directions=new_directions,
        guid=item.guid,
        anki_note_id=item.anki_note_id,
    )


def _schedule_with_steps(
    item: SRSItem,
    prev: DirectionState,
    rating: Rating,
    review_date: date,
    direction: Direction,
    params: FSRSParams,
    time_ms: int,
    now: datetime,
    last_review_dt: datetime,
    col_crt: int | None = None,
    load_balancer: object | None = None,
) -> SRSItem:
    """Handle LEARNING/RELEARNING with step semantics."""
    from dataclasses import replace

    steps, _ = _get_steps_for_state(prev.state)

    if not steps:
        # Empty steps list = graduate immediately
        return _graduate_to_review(
            item,
            prev,
            rating,
            direction,
            time_ms,
            now,
            last_review_dt,
            params,
            review_date=review_date,
            col_crt=col_crt,
            load_balancer=load_balancer,
        )

    total_steps = len(steps)
    total_remaining = _parse_left(prev.left)
    # Heal cards with absent or out-of-range `left` (legacy data, sync gaps):
    # treat as fresh entry with all steps still ahead.
    if total_remaining <= 0 or total_remaining > total_steps:
        total_remaining = total_steps
    # Anki's step index for the CURRENT card (rslib/.../states/steps.rs:23):
    # idx = total_steps - total_remaining. idx=0 means first step.
    current_step_index = total_steps - total_remaining

    # Short-term stability update (Anki: learning.rs:40 sets memory_state
    # unconditionally from fsrs_next_states). The fsrsShortTermWithStepsEnabled
    # deck option only governs card-state transitions, not memory_state itself.
    w = params.weights
    if prev.stability is not None:
        new_stability = _quantize_stability(_stability_short_term(prev.stability, rating, params))
        new_difficulty = _quantize_difficulty(_next_difficulty(prev.difficulty, rating, w))
    else:
        new_stability = prev.stability
        new_difficulty = prev.difficulty

    if rating == Rating.AGAIN:
        # Reset to step 0 (all steps remaining)
        new_left = _pack_left(total_steps)
        new_due_at = _due_at_after_step(now, prev, steps[0])

        new_dir = replace(
            prev,
            due_at=new_due_at,
            left=new_left,
            stability=new_stability,
            difficulty=new_difficulty,
            reps=prev.reps + 1,
            lapses=prev.lapses + (1 if prev.state == SRSState.REVIEW else 0),
            last_review=last_review_dt,
            last_review_time_ms=time_ms,
            dirty_fsrs=True,
            last_rating=rating.value,
            prior_state=_grade_prior_state(prev, prev.state),
            prior_left=prev.left,
            prior_stability=prev.stability,
        )

    elif rating == Rating.HARD:
        # Stay on same step — total_remaining unchanged.
        # Anki's rslib special-cases Hard on the first step (idx==0):
        #   - With ≥2 steps: delay = avg of first two steps.
        #     [1,10] → 330s (confirmed by Anki's unit test + TT revlog).
        #   - With 1 step:   delay = min(again*1.5, again + 1 day).
        #     [10] → 900s (Anki unit test: `assert_delay_secs!([10.0], 1, Some(600), Some(900), None)`,
        #     rslib/.../scheduler/states/steps.rs:55-66, 119).
        # On any later step (idx > 0): delay = current step verbatim.
        new_left = _pack_left(total_remaining)
        if current_step_index == 0:
            again_secs = steps[0] * 60
            delay_min = (steps[0] + steps[1]) / 2 if len(steps) > 1 else min(again_secs * 1.5, again_secs + 86400) / 60
        else:
            delay_min = steps[current_step_index]
        new_due_at = _due_at_after_step(now, prev, delay_min)

        new_dir = replace(
            prev,
            due_at=new_due_at,
            left=new_left,
            stability=new_stability,
            difficulty=new_difficulty,
            reps=prev.reps + 1,
            lapses=prev.lapses,
            last_review=last_review_dt,
            last_review_time_ms=time_ms,
            dirty_fsrs=True,
            last_rating=rating.value,
            prior_state=_grade_prior_state(prev, prev.state),
            prior_left=prev.left,
            prior_stability=prev.stability,
        )

    elif rating == Rating.GOOD:
        # Advance one step. Anki's good_delay_secs returns None when the next
        # index is past the last step, which is exactly the graduation case
        # (rslib/.../states/steps.rs:68). Equivalent: total_remaining == 1.
        if total_remaining <= 1:
            # On last step: graduate to REVIEW
            # Anki's 0.5-day short-term rule only applies when relearn_steps is empty
            # or fsrs_short_term_with_steps_enabled is true (relearning.rs:119-130);
            # for non-empty relearn_steps (the common case), we graduate directly.
            return _graduate_to_review(
                item,
                prev,
                rating,
                direction,
                time_ms,
                now,
                last_review_dt,
                params,
                review_date=review_date,
                col_crt=col_crt,
                load_balancer=load_balancer,
            )

        # Decrement total_remaining; advance to next step.
        next_step_index = current_step_index + 1
        new_left = _pack_left(total_remaining - 1)
        new_due_at = _due_at_after_step(now, prev, steps[next_step_index])

        new_dir = replace(
            prev,
            due_at=new_due_at,
            left=new_left,
            stability=new_stability,
            difficulty=new_difficulty,
            reps=prev.reps + 1,
            lapses=prev.lapses,
            last_review=last_review_dt,
            last_review_time_ms=time_ms,
            dirty_fsrs=True,
            last_rating=rating.value,
            prior_state=_grade_prior_state(prev, prev.state),
            prior_left=prev.left,
            prior_stability=prev.stability,
        )

    else:  # Rating.EASY
        # Graduate immediately
        return _graduate_to_review(
            item,
            prev,
            rating,
            direction,
            time_ms,
            now,
            last_review_dt,
            params,
            review_date=review_date,
            col_crt=col_crt,
            load_balancer=load_balancer,
        )

    new_directions = dict(item.directions)
    new_directions[direction] = new_dir
    return SRSItem(
        syntactic_unit=item.syntactic_unit,
        directions=new_directions,
        guid=item.guid,
        anki_note_id=item.anki_note_id,
    )


def _graduate_to_review(
    item: SRSItem,
    prev: DirectionState,
    rating: Rating,
    direction: Direction,
    time_ms: int,
    now: datetime,
    last_review_dt: datetime,
    params: FSRSParams = DEFAULT_FSRS5_PARAMS,
    review_date: date | None = None,
    col_crt: int | None = None,
    load_balancer: object | None = None,
) -> SRSItem:
    """Graduate from LEARNING/RELEARNING to REVIEW with FSRS init."""
    from dataclasses import replace

    w = params.weights
    neg_decay = -params.decay

    if prev.state == SRSState.NEW:
        new_stability = _init_stability(rating, w)
        new_difficulty = _init_difficulty(rating, w)
    else:
        # Same-day LEARNING/RELEARNING graduation. fsrs-rs `step()` overrides
        # success/failure with `stability_short_term` whenever `delta_t == 0`
        # (model.rs:163), regardless of rating. All graduations reached here
        # are same-day grades on sub-day learning steps.
        new_stability = _stability_short_term(prev.stability, rating, params)
        new_difficulty = _next_difficulty(prev.difficulty, rating, w)

    # fsrs-rs S_MIN = 0.001 (fsrs-rs/src/simulation.rs:41); 4dp/3dp rounding
    # mirrors Anki's per-grade quantization in cards.data.
    new_stability = _quantize_stability(max(0.001, new_stability))
    new_difficulty = _quantize_difficulty(max(1.0, min(10.0, new_difficulty)))
    if rating in {Rating.HARD, Rating.GOOD, Rating.EASY}:
        # Anki parity cascade (graduation: scheduled_days=0, no prior review interval)
        s_hard = (
            _init_stability(Rating.HARD, w)
            if prev.state == SRSState.NEW
            else _stability_short_term(prev.stability, Rating.HARD, params)
        )
        s_good = (
            _init_stability(Rating.GOOD, w)
            if prev.state == SRSState.NEW
            else _stability_short_term(prev.stability, Rating.GOOD, params)
        )
        s_easy = (
            _init_stability(Rating.EASY, w)
            if prev.state == SRSState.NEW
            else _stability_short_term(prev.stability, Rating.EASY, params)
        )
        q_hard = _quantize_stability(max(0.001, s_hard))
        q_good = _quantize_stability(max(0.001, s_good))
        q_easy = _quantize_stability(max(0.001, s_easy))
        # Layer 52: graduation uses simple per-rating fuzz (min=1 for HARD/GOOD;
        # EASY floors against good_for_easy = fuzz(raw_good_float, 1)). NOT
        # the passing-review cascade. Anki path: rslib/.../states/learning.rs
        # + relearning.rs answer_hard/good/easy. See `_graduation_intervals_with_fuzz`.
        raw_hard_f = _next_interval_raw(q_hard, params.desired_retention, neg_decay)
        raw_good_f = _next_interval_raw(q_good, params.desired_retention, neg_decay)
        raw_easy_f = _next_interval_raw(q_easy, params.desired_retention, neg_decay)
        fuzzed = _graduation_intervals_with_fuzz(
            raw_hard_f,
            raw_good_f,
            raw_easy_f,
            prev.anki_card_id,
            prev.reps,
            params.maximum_review_interval,
            load_balancer=load_balancer,
            note_id=item.anki_note_id,
        )
        interval = {Rating.HARD: fuzzed[0], Rating.GOOD: fuzzed[1], Rating.EASY: fuzzed[2]}[rating]
    else:
        raw_interval = _next_interval(new_stability, params.desired_retention, neg_decay)
        interval = _review_interval_fuzz(raw_interval, prev.anki_card_id, prev.reps, params.maximum_review_interval)
    new_due_at = _review_due_at_from_interval(review_date, interval, col_crt, now)

    new_dir = replace(
        prev,
        stability=new_stability,
        difficulty=new_difficulty,
        due_at=new_due_at,
        left=None,  # No longer in learning
        reps=prev.reps + 1,
        # prev.lapses already reflects the post-lapse count from the prior REVIEW+AGAIN rating
        state=SRSState.REVIEW,
        last_review=last_review_dt,
        last_review_time_ms=time_ms,
        dirty_fsrs=True,
        last_rating=rating.value,
        prior_state=_grade_prior_state(prev, SRSState.REVIEW),
        prior_left=prev.left,
        prior_stability=prev.stability,
        # Layer 26: NEW + EASY skips _schedule_new and lands here directly. Stamp
        # introduced_at on the first NEW→REVIEW transition; preserve on later
        # LEARNING/RELEARNING → REVIEW graduations.
        introduced_at=(last_review_dt if prev.state == SRSState.NEW else prev.introduced_at),
    )
    new_directions = dict(item.directions)
    new_directions[direction] = new_dir
    return SRSItem(
        syntactic_unit=item.syntactic_unit,
        directions=new_directions,
        guid=item.guid,
        anki_note_id=item.anki_note_id,
    )


def _compute_review_kind(prev_state: SRSState, new_state: SRSState) -> int:
    """Derive Anki revlog.type from the state transition.

    0=Learn 1=Review 2=Relearn 4=Manual
    """
    if new_state == SRSState.REVIEW and prev_state != SRSState.REVIEW:
        return 1
    if new_state == SRSState.RELEARNING:
        return 2
    if prev_state == SRSState.REVIEW and new_state == SRSState.REVIEW:
        return 1
    if new_state in (SRSState.LEARNING, SRSState.NEW):
        return 0
    return 1


def _compute_revlog_interval(prev_state: SRSState, new_dir: DirectionState, prev: DirectionState, now: datetime) -> int:
    """Compute interval for tt_revlog: +days for review, -seconds for learning.

    Mirrors Anki's convention where ``revlog.ivl`` stores positive days for
    review-state transitions and negative seconds for sub-day learning steps.
    """
    if new_dir.state in (SRSState.LEARNING, SRSState.RELEARNING):
        if prev.last_review and new_dir.due_at:
            delta_s = (new_dir.due_at - prev.last_review).total_seconds()
        else:
            delta_s = (new_dir.due_at - now).total_seconds()
        return -max(1, int(delta_s))
    else:
        if prev.last_review and new_dir.due_at:
            days = (new_dir.due_at - prev.last_review).days
        else:
            days = (new_dir.due_at - now).days
        return max(1, days)


def _compute_revlog_last_interval(prev: DirectionState) -> int:
    """Compute last_interval for tt_revlog from previous state."""
    if prev.last_review and prev.due_at:
        days = (prev.due_at - prev.last_review).days
        if days >= 1:
            return days
        delta_s = (prev.due_at - prev.last_review).total_seconds()
        return -max(1, int(delta_s))
    return 0


def build_revlog_row(
    collocation_id: int,
    direction: Direction,
    prev: DirectionState,
    new_dir: DirectionState,
    rating: Rating,
    time_ms: int,
    *,
    now: datetime | None = None,
) -> RevlogRow:
    """Construct a RevlogRow from the outcome of a ``schedule()`` call.

    PK matches Anki's ``revlog.id`` convention: wall-clock milliseconds since
    epoch, taken from ``now``. ``time_ms`` is the elapsed time the user spent
    on the card (Anki's ``revlog.time``) and goes into ``taken_millis``.
    The caller persists the result via ``SRSDatabase.append_revlog()``.
    """
    if now is None:
        now = datetime.now(tz=UTC)
    return RevlogRow(
        id=int(now.timestamp() * 1000),
        collocation_id=collocation_id,
        direction=direction,
        button_chosen=rating.value,
        interval=_compute_revlog_interval(prev.state, new_dir, prev, now),
        last_interval=_compute_revlog_last_interval(prev),
        factor=0,
        taken_millis=time_ms,
        review_kind=_compute_review_kind(prev.state, new_dir.state),
        anki_card_id=prev.anki_card_id,
    )
