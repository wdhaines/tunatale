"""Bit-exact port of Anki's FSRS load balancer.

Mirrors ``rslib/src/scheduler/states/load_balancer.rs`` (Anki 25.09 / rand 0.9.4).
When ``loadBalancerEnabled`` is set, Anki relocates each graded interval to a
less-loaded day *within* the fuzz range, weighting candidate days by how many
cards are already due there. The pick is deterministic given the fuzz seed
(``card.id + reps``) and the collection-wide due histogram.

TunaTale can reproduce it bit-for-bit because, for the single-preset Slovene
deck, TT's own ``collocation_directions`` IS the entire same-preset histogram
(see Layer 53 in ``docs/anki-parity-layers.md``). This module is the per-card
``find_interval``; the caller builds the histogram and threads it into
``schedule()`` (mirroring ``with_review_fuzz`` trying the balancer first).

The RNG primitives (``ChaCha12Rng``, ``weighted_index_sample``) live in
``_anki_rng.py``. The fuzz-bounds helper is shared with ``fsrs.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.srs.anki_mirror._anki_rng import ChaCha12Rng, f32, weighted_index_sample

# rslib/.../states/load_balancer.rs:21-34
MAX_LOAD_BALANCE_INTERVAL = 90
LOAD_BALANCE_DAYS = int(MAX_LOAD_BALANCE_INTERVAL * 1.1)  # 99
_SIBLING_MODIFIER_STEPS = (-5, -4, -3, -2, -1, 0, 1, 2, 3, 4, 5)
_SIBLING_MODIFIER_RANGE = (1.0, 0.8, 0.6, 0.4, 0.2, 0.000001, 0.2, 0.4, 0.6, 0.8, 1.0)

# EasyDay load modifiers (rslib/.../load_balancer.rs:53-63). Default is all-Normal.
_EASY_MINIMUM = 0.0001
_EASY_REDUCED = 0.5
_EASY_NORMAL = 1.0


def _easy_day_from_percentage(pct: float) -> float:
    """Map an easy-days percentage to its load modifier (EasyDay::from + load_modifier)."""
    if pct == 1.0:
        return _EASY_NORMAL
    if pct == 0.0:
        return _EASY_MINIMUM
    return _EASY_REDUCED


@dataclass
class _LoadBalancerDay:
    """One day's bucket: cards due that day + the set of their note ids (siblings)."""

    cards: list[tuple[int, int]] = field(default_factory=list)  # (card_id, note_id)
    notes: set[int] = field(default_factory=set)

    def add(self, cid: int, nid: int) -> None:
        self.cards.append((cid, nid))
        self.notes.add(nid)

    def remove(self, cid: int) -> None:
        for i, (c, rnid) in enumerate(self.cards):
            if c == cid:
                self.cards.pop(i)
                if not any(n == rnid for _, n in self.cards):
                    self.notes.discard(rnid)
                return

    def has_sibling(self, nid: int) -> bool:
        return nid in self.notes


def _interval_to_weekday(interval: int, next_day_at: int) -> int:
    """Weekday (Mon=0..Sun=6) of the day ``interval`` days from the next rollover.

    Mirrors ``interval_to_weekday`` (load_balancer.rs:450-456): the target day is
    ``next_day_at + (interval - 1) * 86400`` in local time.
    """
    import datetime

    target = datetime.datetime.fromtimestamp(next_day_at + (interval - 1) * 86400)
    return target.weekday()


def _calculate_easy_days_modifiers(
    easy_days_load: list[float], weekdays: list[int], review_counts: list[int]
) -> list[float]:
    """Port of ``calculate_easy_days_modifiers`` (load_balancer.rs:324-357).

    ``easy_days_load`` is the 7-element list of per-weekday load modifiers. For
    the default (all Normal = 1.0) this returns all 1.0 and the Reduced branch
    never fires.
    """
    total_review_count = sum(review_counts)
    total_percents = f32(sum(easy_days_load[wd] for wd in weekdays))
    out: list[float] = []
    for wd, review_count in zip(weekdays, review_counts, strict=True):
        modifier = easy_days_load[wd]
        if modifier == _EASY_REDUCED:
            half = 0.5
            other_days_review_total = f32(total_review_count - review_count)
            other_days_percent_total = f32(total_percents - half)
            normalized_count = f32(review_count / half)
            reduced_day_threshold = f32(other_days_review_total / other_days_percent_total)
            modifier = _EASY_MINIMUM if normalized_count > reduced_day_threshold else _EASY_NORMAL
        out.append(modifier)
    return out


def _calculate_sibling_modifiers(
    days: list[_LoadBalancerDay], before_days: int, after_days: int, note_id: int | None
) -> list[float]:
    """Port of ``calculate_sibling_modifiers`` (load_balancer.rs:370-410).

    Nudges days that already hold a sibling of ``note_id`` (and their neighbours,
    with a ±5-day falloff) toward a lower weight so siblings don't clump.
    """
    n = after_days - before_days + 1
    modifiers = [1.0] * n
    if note_id is None:
        return modifiers

    sibling_days = {i for i, day in enumerate(days) if day.has_sibling(note_id)}
    for sibling_day in sibling_days:
        for step, mod_value in zip(_SIBLING_MODIFIER_STEPS, _SIBLING_MODIFIER_RANGE, strict=True):
            target_day = sibling_day + step - before_days
            if 0 <= target_day < n:
                modifiers[target_day] = f32(modifiers[target_day] * mod_value)
    return modifiers


@dataclass
class _LoadBalancerInterval:
    target_interval: int
    review_count: int
    sibling_modifier: float
    easy_days_modifier: float


def _f32_powi3(x: float) -> float:
    """``f32::powi(3)`` via exponentiation-by-squaring: ``(x*x) * x`` in f32."""
    return f32(f32(x * x) * x)


def _select_weighted_interval(intervals: list[_LoadBalancerInterval], fuzz_seed: int) -> int | None:
    """Port of ``select_weighted_interval`` (load_balancer.rs:419-448).

    weight = ``(1/count)^2.15 * (1/interval)^3 * sibling_modifier * easy_days_modifier``
    (f32), or ``1.0`` when ``count == 0``. Picks an index via ``WeightedIndex`` seeded
    by ``StdRng::seed_from_u64(fuzz_seed)``.
    """
    targets: list[int] = []
    weights: list[float] = []
    for iv in intervals:
        if iv.review_count == 0:
            weight = 1.0
        else:
            card_count_weight = f32(math.pow(f32(1.0 / f32(iv.review_count)), 2.15))
            card_interval_weight = _f32_powi3(f32(1.0 / f32(iv.target_interval)))
            weight = f32(
                f32(f32(card_count_weight * card_interval_weight) * iv.sibling_modifier) * iv.easy_days_modifier
            )
        targets.append(iv.target_interval)
        weights.append(weight)

    if not weights or f32(sum(f32(w) for w in weights)) <= 0.0:  # pragma: no cover - floors keep total > 0
        return None
    rng = ChaCha12Rng(fuzz_seed & 0xFFFFFFFFFFFFFFFF)
    idx = weighted_index_sample(weights, rng)
    return targets[idx]


class LoadBalancer:
    """Single-preset load balancer over a ``LOAD_BALANCE_DAYS``-day due histogram.

    ``days[i]`` holds the cards due ``i`` days from today (col-day ``today + i``).
    Build it from TT state, then call ``find_interval`` from the fuzz pipeline and
    ``add_card`` after each grade to mirror Anki's per-answer histogram mutation.
    """

    def __init__(
        self,
        easy_days_percentages: list[float] | None,
        next_day_at: int,
        *,
        bury_reviews: bool = True,
    ) -> None:
        self.days: list[_LoadBalancerDay] = [_LoadBalancerDay() for _ in range(LOAD_BALANCE_DAYS)]
        # Empty percentages → all Normal (parse_easy_days_percentages, load_balancer.rs:284-298).
        if easy_days_percentages:
            self.easy_days = [_easy_day_from_percentage(p) for p in easy_days_percentages]
        else:
            self.easy_days = [_EASY_NORMAL] * 7
        self.next_day_at = next_day_at
        # Anki only feeds the note_id into the sibling modifier when the deck's
        # bury_reviews is on (answering/mod.rs:247, `.then_some(note_id)`). When
        # off, find_interval drops note_id so siblings never nudge the pick.
        self.bury_reviews = bury_reviews

    def add_card(self, cid: int, nid: int, interval: int) -> None:
        if 0 <= interval < LOAD_BALANCE_DAYS:
            self.days[interval].add(cid, nid)

    def find_interval(
        self, interval: float, minimum: int, maximum: int, fuzz_seed: int, note_id: int | None
    ) -> int | None:
        """Port of ``LoadBalancer::find_interval`` (load_balancer.rs:210-265).

        Returns the load-balanced day, or ``None`` when the card is scheduled far
        enough out that balancing is skipped (caller falls back to pure fuzz).
        """
        from app.srs.fsrs import _constrained_fuzz_bounds

        if interval > MAX_LOAD_BALANCE_INTERVAL or minimum > MAX_LOAD_BALANCE_INTERVAL:
            return None

        before_days, after_days = _constrained_fuzz_bounds(interval, minimum, maximum)
        interval_days = self.days[before_days : after_days + 1]

        # Mirror Anki's `.then_some(note_id)`: siblings only matter when the deck
        # buries reviews. With bury_reviews off, note_id is dropped entirely.
        effective_note_id = note_id if self.bury_reviews else None

        review_counts = [len(day.cards) for day in interval_days]
        weekdays = [_interval_to_weekday(i + before_days, self.next_day_at) for i in range(len(interval_days))]
        easy_days_modifier = _calculate_easy_days_modifiers(self.easy_days, weekdays, review_counts)
        sibling_modifier = _calculate_sibling_modifiers(self.days, before_days, after_days, effective_note_id)

        intervals = [
            _LoadBalancerInterval(
                target_interval=i + before_days,
                review_count=review_counts[i],
                sibling_modifier=sibling_modifier[i],
                easy_days_modifier=easy_days_modifier[i],
            )
            for i in range(len(interval_days))
        ]
        return _select_weighted_interval(intervals, fuzz_seed)
