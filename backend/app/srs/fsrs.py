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

DECAY = -0.5
FACTOR = 19 / 81  # = 0.234...


@dataclass(frozen=True)
class FSRSParams:
    """FSRS scheduling parameters (weights + desired retention)."""

    weights: tuple[float, ...]  # 19 floats for FSRS-5
    desired_retention: float = 0.9

    def __post_init__(self) -> None:
        if len(self.weights) != 19:
            raise ValueError(f"FSRSParams requires exactly 19 weights, got {len(self.weights)}")


DEFAULT_FSRS5_PARAMS = FSRSParams(weights=_DEFAULT_WEIGHTS)


def _forgetting_curve(elapsed_days: float, stability: float) -> float:
    """Retrievability at elapsed_days given stability."""
    return (1 + FACTOR * elapsed_days / stability) ** DECAY


def compute_retrievability(direction_state: DirectionState, today: date) -> float:
    """Return retrievability (0-1) for a direction_state, handling edge cases.

    Null stability or null last_review → return 1.0 (sorts last among due cards).
    """
    stability = direction_state.stability
    last_review = direction_state.last_review
    if stability is None or last_review is None:
        return 1.0
    # Handle both date and datetime for last_review
    last_review_date = last_review.date() if isinstance(last_review, datetime) else last_review
    elapsed = max(0, (today - last_review_date).days)
    return _forgetting_curve(elapsed, stability)


def _next_interval(stability: float, desired_retention: float) -> int:
    """Days until next review at the given desired_retention."""
    interval = stability / FACTOR * (desired_retention ** (1 / DECAY) - 1)
    return max(1, min(round(interval), 36500))


def _init_stability(rating: Rating, w: tuple[float, ...]) -> float:
    return w[rating.value - 1]


def _init_difficulty(rating: Rating, w: tuple[float, ...]) -> float:
    d = w[4] - math.exp(w[5] * (rating.value - 1)) + 1
    return max(1.0, min(10.0, d))


def _next_difficulty(d: float, rating: Rating, w: tuple[float, ...]) -> float:
    next_d = d - w[6] * (rating.value - 3)
    # Mean-reversion toward w[4] (the initial difficulty for a "normal" item)
    next_d = w[7] * w[4] + (1 - w[7]) * next_d
    return max(1.0, min(10.0, next_d))


def _next_stability_recall(d: float, s: float, r: float, rating: Rating, w: tuple[float, ...]) -> float:
    hard_penalty = w[15] if rating == Rating.HARD else 1.0
    easy_bonus = w[16] if rating == Rating.EASY else 1.0
    return s * (
        math.exp(w[8]) * (11 - d) * s ** (-w[9]) * (math.exp((1 - r) * w[10]) - 1) * hard_penalty * easy_bonus + 1
    )


def _next_stability_lapse(d: float, s: float, r: float, w: tuple[float, ...]) -> float:
    return w[11] * d ** (-w[12]) * ((s + 1) ** w[13] - 1) * math.exp((1 - r) * w[14])


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
    prev = item.directions[direction]

    # Handle learning step semantics for LEARNING and RELEARNING states
    if prev.state in (SRSState.LEARNING, SRSState.RELEARNING):
        return _schedule_with_steps(item, prev, rating, review_date, direction, params, time_ms, now, last_review_dt)

    # Handle NEW state with learning steps (Anki parity)
    if prev.state == SRSState.NEW:
        return _schedule_new(item, prev, rating, direction, time_ms, now, last_review_dt, params)

    # REVIEW state logic
    else:
        # REVIEW state
        last = prev.last_review or last_review_dt
        last_date = last.date() if isinstance(last, datetime) else last
        elapsed = max(0, (last_review_dt.date() - last_date).days)
        r = _forgetting_curve(elapsed, prev.stability)

        if rating == Rating.AGAIN:
            # REVIEW + AGAIN → RELEARNING
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
    interval = _next_interval(new_stability, params.desired_retention)
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
        prior_state=prev.state,
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

    # total_remaining = steps left until graduation = total_steps - step_index
    new_left = _pack_left(total_steps - step_index)
    # Anki's Hard-on-first-step delay = avg of first two steps when ≥2 steps;
    # Again uses step[0] verbatim. See _schedule_with_steps for the same rule.
    if rating == Rating.HARD and step_index == 0 and total_steps > 1:
        delay_min = (steps[0] + steps[1]) / 2
    else:
        delay_min = steps[step_index]
    new_due_at = _due_at_after_step(now, prev, delay_min)

    new_dir = replace(
        prev,
        state=SRSState.LEARNING,
        due_date=new_due_at.date(),
        due_at=new_due_at,
        left=new_left,
        reps=prev.reps + 1,
        lapses=prev.lapses,
        last_review=last_review_dt,
        last_review_time_ms=time_ms,
        dirty_fsrs=True,
        last_rating=rating.value,
        prior_state=prev.state,
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
    # Compute real retrievability from elapsed time since last review
    last = prev.last_review or last_review_dt
    last_date = last.date() if isinstance(last, datetime) else last
    elapsed = max(0, (last_review_dt.date() - last_date).days)
    r = _forgetting_curve(elapsed, prev.stability) if prev.stability > 0 else 1.0
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
        prior_state=prev.state,
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

    if rating == Rating.AGAIN:
        # Reset to step 0 (all steps remaining)
        new_left = _pack_left(total_steps)
        new_due_at = _due_at_after_step(now, prev, steps[0])

        new_dir = replace(
            prev,
            due_date=new_due_at.date(),
            due_at=new_due_at,
            left=new_left,
            reps=prev.reps + 1,
            lapses=prev.lapses + (1 if prev.state == SRSState.REVIEW else 0),
            last_review=last_review_dt,
            last_review_time_ms=time_ms,
            dirty_fsrs=True,
            last_rating=rating.value,
            prior_state=prev.state,
            prior_left=prev.left,
            prior_stability=prev.stability,
        )

    elif rating == Rating.HARD:
        # Stay on same step — total_remaining unchanged.
        # Anki's rslib special-cases Hard on the first step of a multi-step
        # deck: the delay is the average of the first two steps, not the
        # current step (rslib/src/scheduler/states/learning.rs). Empirically
        # confirmed by revlog `ivl=-330` for Hard on a [1, 10] first step.
        new_left = _pack_left(total_remaining)
        if current_step_index == 0 and len(steps) > 1:
            delay_min = (steps[0] + steps[1]) / 2
        else:
            delay_min = steps[current_step_index]
        new_due_at = _due_at_after_step(now, prev, delay_min)

        new_dir = replace(
            prev,
            due_date=new_due_at.date(),
            due_at=new_due_at,
            left=new_left,
            reps=prev.reps + 1,
            lapses=prev.lapses,
            last_review=last_review_dt,
            last_review_time_ms=time_ms,
            dirty_fsrs=True,
            last_rating=rating.value,
            prior_state=prev.state,
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
            reps=prev.reps + 1,
            lapses=prev.lapses,
            last_review=last_review_dt,
            last_review_time_ms=time_ms,
            dirty_fsrs=True,
            last_rating=rating.value,
            prior_state=prev.state,
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

    if prev.state == SRSState.NEW:
        new_stability = _init_stability(rating, w)
        new_difficulty = _init_difficulty(rating, w)
    else:
        # Lapse or learning graduation
        elapsed = 0  # Graduation = fresh start
        r = _forgetting_curve(elapsed, prev.stability) if prev.stability > 0 else 1.0

        if prev.state == SRSState.RELEARNING:
            new_stability = _next_stability_lapse(prev.difficulty, prev.stability, r, w)
            new_difficulty = _next_difficulty(prev.difficulty, rating, w)
        else:
            new_stability = _next_stability_recall(prev.difficulty, prev.stability, r, rating, w)
            new_difficulty = _next_difficulty(prev.difficulty, rating, w)

    new_stability = max(0.1, new_stability)
    new_difficulty = max(1.0, min(10.0, new_difficulty))
    interval = _next_interval(new_stability, params.desired_retention)
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
        prior_state=prev.state,
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
