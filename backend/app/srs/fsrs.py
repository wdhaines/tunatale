"""FSRS-5 spaced repetition scheduling algorithm.

Reference: https://github.com/open-spaced-repetition/fsrs5
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.models.srs_item import Direction, DirectionState, Rating, SRSItem, SRSState
from app.srs._anki_rng import ChaCha12Rng, random_range_u32


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

_DEFAULT_FSRS6_WEIGHTS: tuple[float, ...] = (
    0.212,  # w0: initial stability for Again
    1.2931,  # w1: initial stability for Hard
    2.3065,  # w2: initial stability for Good
    8.2956,  # w3: initial stability for Easy
    6.4133,  # w4: initial difficulty
    0.8334,  # w5: initial difficulty decay
    3.0194,  # w6: difficulty mean-reversion weight
    0.001,  # w7: difficulty update weight
    1.8722,  # w8: stability increase factor
    0.1666,  # w9: stability increase decay
    0.796,  # w10: stability increase R-factor
    1.4835,  # w11: lapse stability factor
    0.0614,  # w12: lapse stability difficulty decay
    0.2629,  # w13: lapse stability S-factor
    1.6483,  # w14: lapse stability R-factor
    0.6014,  # w15: hard penalty
    1.8729,  # w16: easy bonus
    0.5425,  # w17: short-term exponent base
    0.0912,  # w18: short-term rating offset
    0.0658,  # w19: short-term stability exponent (FSRS-6)
    0.1542,  # w20: decay (FSRS-6 default)
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
DEFAULT_FSRS6_PARAMS = FSRSParams(weights=_DEFAULT_FSRS6_WEIGHTS)


def _forgetting_curve(elapsed_days: float, stability: float, decay: float = -0.5) -> float:
    """Retrievability at elapsed_days given stability and decay."""
    return (1 + FACTOR * elapsed_days / stability) ** decay


def _elapsed_days_for_fsrs(last_review: datetime | date | None, ref_now: datetime) -> float:
    """Mirror Anki's `extract_fsrs_retrievability` dual branch for elapsed time.

    Anki's lapse + recall stability formulas both feed off `delta_t = now - lrt`
    in fractional days when `cards.data.lrt` is present (FSRS-effective last
    review timestamp), and fall back to integer `today_col_day - (due - ivl)`
    when it isn't.

    TT mirrors via the marker `parse_fsrs_data` sets: midnight UTC `last_review`
    = day-level fallback (no lrt was present); any sub-day component = lrt was
    present and `last_review` carries it.

    The same dual-branch logic already lives in `compute_retrievability` for R-asc
    sort. Until 2026-05-18 it did NOT live in `_schedule_review_again` or the
    REVIEW+other-ratings path — those used integer days unconditionally,
    producing 2-4% stability gaps with Anki for sub-day-elapsed grades.
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
        if is_day_level:
            return max(0, (ref_now.date() - last_review.date()).days)
        return max(0.0, (ref_now - last_review).total_seconds() / 86400.0)
    # `last_review` is a date (no time-of-day at all) — day-level by definition.
    return max(0, (ref_now.date() - last_review).days)


def compute_retrievability(
    direction_state: DirectionState,
    today: date,
    now: datetime | None = None,
    desired_retention: float = 0.9,
    decay: float = -0.5,
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
        # Detect day-level fallback values (midnight UTC, no sub-day component).
        # `parse_fsrs_data` sets these via `_compute_last_review` when cards.data
        # has no `lrt` field — Anki itself uses integer-day elapsed for these.
        is_day_level = (
            last_review.hour == 0
            and last_review.minute == 0
            and last_review.second == 0
            and last_review.microsecond == 0
        )
        if is_day_level:
            elapsed = max(0, (today - last_review.date()).days)
        else:
            ref_now = now if now is not None else datetime.now(UTC)
            elapsed_seconds = (ref_now - last_review).total_seconds()
            elapsed = max(0.0, elapsed_seconds / 86400.0)
    else:
        elapsed = max(0, (today - last_review).days)
    return _forgetting_curve(elapsed, stability, decay)


def _next_interval(stability: float, desired_retention: float, decay: float = -0.5) -> int:
    """Days until next review at the given desired_retention."""
    interval = stability / FACTOR * (desired_retention ** (1 / decay) - 1)
    return max(1, min(round(interval), 36500))


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
    return w[11] * d ** (-w[12]) * ((s + 1) ** w[13] - 1) * math.exp((1 - r) * w[14])


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
        return _schedule_with_steps(item, prev, rating, review_date, direction, params, time_ms, now, last_review_dt)

    # Handle NEW state with learning steps (Anki parity)
    if prev.state == SRSState.NEW:
        return _schedule_new(item, prev, rating, direction, time_ms, now, last_review_dt, params)

    # REVIEW state logic
    else:
        # REVIEW state. `_elapsed_days_for_fsrs` mirrors Anki's dual-branch:
        # fractional days from lrt-precision last_review, integer otherwise.
        last = prev.last_review or last_review_dt
        elapsed = _elapsed_days_for_fsrs(last, last_review_dt)
        r = _forgetting_curve(elapsed, prev.stability, neg_decay)

        if rating == Rating.AGAIN:
            return _schedule_review_again(
                item, prev, rating, review_date, direction, params, time_ms, now, last_review_dt
            )
        else:
            new_stability = _next_stability_recall(prev.difficulty, prev.stability, r, rating, w)
            new_difficulty = _next_difficulty(prev.difficulty, rating, w)
            new_reps = prev.reps + 1
            new_lapses = prev.lapses
            new_state = SRSState.REVIEW

    new_stability = max(0.1, new_stability)
    new_difficulty = max(1.0, min(10.0, new_difficulty))
    interval = _next_interval(new_stability, params.desired_retention, neg_decay)
    new_due = review_date + timedelta(days=interval)

    new_dir = replace(
        prev,
        stability=new_stability,
        difficulty=new_difficulty,
        due_date=new_due,
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
) -> SRSItem:
    """NEW + any rating: walk learn_steps like Anki.

    AGAIN/HARD → step 0; GOOD → step 1 (or graduate if last); EASY → graduate immediately.
    """
    from dataclasses import replace

    if rating == Rating.EASY:
        return _graduate_to_review(item, prev, rating, direction, time_ms, now, last_review_dt, params)

    steps, _ = _get_steps_for_state(SRSState.LEARNING)
    if not steps:
        return _graduate_to_review(item, prev, rating, direction, time_ms, now, last_review_dt, params)

    total_steps = len(steps)
    if rating == Rating.GOOD:
        # Advance to step 1; graduate if only one step total
        if total_steps == 1:
            return _graduate_to_review(item, prev, rating, direction, time_ms, now, last_review_dt, params)
        step_index = 1
    else:  # AGAIN or HARD: stay at step 0
        step_index = 0

    # First grade out of NEW: seed stability from w[0..3] (matches Anki's
    # fsrs-rs `step()` with `state=None`). DirectionState.stability defaults
    # to 1.0, so we can't infer "no prior FSRS" from stability alone —
    # `_schedule_new` is only called when prev.state == NEW, so the seed
    # branch is unconditional here.
    w = params.weights
    new_stability = _init_stability(rating, w)
    new_difficulty = _init_difficulty(rating, w)

    # total_remaining = steps left until graduation = total_steps - step_index
    new_left = _pack_left(total_steps - step_index)
    # Anki's Hard-on-first-step delay (rslib/.../scheduler/states/steps.rs:38-66):
    #   - ≥2 steps: avg of first two steps (e.g. [1,10] → 330s)
    #   - 1 step:   min(again*1.5, again + 1 day) (e.g. [10] → 900s)
    # Again uses step[0] verbatim regardless.
    if rating == Rating.HARD and step_index == 0:
        again_secs = steps[0] * 60
        delay_min = (steps[0] + steps[1]) / 2 if total_steps > 1 else min(again_secs * 1.5, again_secs + 86400) / 60
    else:
        delay_min = steps[step_index]
    new_due_at = _due_at_after_step(now, prev, delay_min)

    new_dir = replace(
        prev,
        state=SRSState.LEARNING,
        due_date=new_due_at.date(),
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
) -> SRSItem:
    """Handle REVIEW + AGAIN: enter RELEARNING with relearning steps."""
    from dataclasses import replace

    w = params.weights
    # Real retrievability needs elapsed in the same units Anki uses — fractional
    # days from `lrt` when present (sub-day precision on `prev.last_review`),
    # integer days otherwise. See `_elapsed_days_for_fsrs`.
    last = prev.last_review or last_review_dt
    elapsed = _elapsed_days_for_fsrs(last, last_review_dt)
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

    steps, _ = _get_steps_for_state(SRSState.RELEARNING)

    if not steps:
        # Empty steps = graduate immediately (same as Anki)
        return _graduate_to_review(item, prev, rating, direction, time_ms, now, last_review_dt, params)

    # Start at step 0 of relearning: total_remaining = full count
    total_steps = len(steps)
    new_left = _pack_left(total_steps)
    new_due_at = _due_at_after_step(now, prev, steps[0])

    new_dir = replace(
        prev,
        stability=new_stability,
        difficulty=new_difficulty,
        state=SRSState.RELEARNING,
        due_date=new_due_at.date(),
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
) -> SRSItem:
    """Handle LEARNING/RELEARNING with step semantics."""
    from dataclasses import replace

    steps, _ = _get_steps_for_state(prev.state)

    if not steps:
        # Empty steps list = graduate immediately
        return _graduate_to_review(item, prev, rating, direction, time_ms, now, last_review_dt, params)

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
        new_stability = _stability_short_term(prev.stability, rating, params)
        new_difficulty = _next_difficulty(prev.difficulty, rating, w)
    else:
        new_stability = prev.stability
        new_difficulty = prev.difficulty

    if rating == Rating.AGAIN:
        # Reset to step 0 (all steps remaining)
        new_left = _pack_left(total_steps)
        new_due_at = _due_at_after_step(now, prev, steps[0])

        new_dir = replace(
            prev,
            due_date=new_due_at.date(),
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
            due_date=new_due_at.date(),
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
            return _graduate_to_review(item, prev, rating, direction, time_ms, now, last_review_dt, params)

        # Decrement total_remaining; advance to next step.
        next_step_index = current_step_index + 1
        new_left = _pack_left(total_remaining - 1)
        new_due_at = _due_at_after_step(now, prev, steps[next_step_index])

        new_dir = replace(
            prev,
            due_date=new_due_at.date(),
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
        return _graduate_to_review(item, prev, rating, direction, time_ms, now, last_review_dt, params)

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
) -> SRSItem:
    """Graduate from LEARNING/RELEARNING to REVIEW with FSRS init."""
    from dataclasses import replace

    w = params.weights
    neg_decay = -params.decay

    if prev.state == SRSState.NEW:
        new_stability = _init_stability(rating, w)
        new_difficulty = _init_difficulty(rating, w)
    else:
        # Lapse or learning graduation
        elapsed = 0  # Graduation = fresh start
        r = _forgetting_curve(elapsed, prev.stability, neg_decay) if prev.stability > 0 else 1.0

        if prev.state == SRSState.RELEARNING:
            new_stability = _next_stability_lapse(prev.difficulty, prev.stability, r, w)
            new_difficulty = _next_difficulty(prev.difficulty, rating, w)
        else:
            new_stability = _next_stability_recall(prev.difficulty, prev.stability, r, rating, w)
            new_difficulty = _next_difficulty(prev.difficulty, rating, w)

    new_stability = max(0.1, new_stability)
    new_difficulty = max(1.0, min(10.0, new_difficulty))
    interval = _next_interval(new_stability, params.desired_retention, neg_decay)
    new_due = date.today() + timedelta(days=interval)

    new_dir = replace(
        prev,
        stability=new_stability,
        difficulty=new_difficulty,
        due_date=new_due,
        due_at=None,  # No longer need sub-day precision
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
