"""Per-lemma mastery = aggregated FSRS stability over the learn-set (Phase 5).

Mastery uses *stability*, not retrievability. The scheduler actively regulates
retrievability toward desired_retention (~0.9), so a review card's R lives in a
narrow band and can't distinguish a freshly graduated card from a long-mastered
one — every reviewed word renders the same green. Stability instead grows
monotonically as a word is learned (the user's deck spans ~3–116 days), so it is
what the transcript color ramp should track.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date
from typing import Literal

from app.models.srs_item import Direction, DirectionState, SRSState

# A REVIEW card's mastery is its stability mapped onto [0,1] by a log curve: a
# card stable for >= this many days reads as fully mastered (green). Log scale
# because the early stability gains (1→10 days) are the meaningful learning
# signal while the 100→120 day difference is not; the ceiling is chosen so the
# observed stability range spreads across the full red→green ramp.
MASTERY_STABILITY_CEILING_DAYS = 120.0

# In-steps (learning/relearning) cards sit at a fixed low floor: they are being
# acquired, not yet on the stability ramp.
_LEARNING_FLOOR = 0.15

# Each side of a word (recognition, production) is shown as a BAND named by how
# long its memory holds, not as a percentage (bd tunatale-yh47, 2026-09-10).
# Lower edges are inclusive: stability 7.0 is "weeks", 30.0 is "months".
BAND_WEEKS_FROM_DAYS = 7.0
BAND_MONTHS_FROM_DAYS = 30.0
# The top band ("half a year +") and "well known" are ONE threshold on
# stability. It replaced a due-date rule (next review > 365 days out): a due date
# depends on the deck's desired retention and moves when reviews are
# rescheduled, so the same memory read differently from deck to deck.
WELL_KNOWN_STABILITY_DAYS = 180.0

# "Well known" — the listen preview's stop-asking horizon — is a DUE-DATE rule,
# and deliberately NOT the band above (bd tunatale-38z9, 2026-09-13). A word is
# left alone when its next review is at least this many days out.
#
# The two are different questions. `direction_band` answers "how well do you
# know this", which must not move while you are not reviewing, so it reads
# stability. This answers "when will I next see this", which is exactly a
# schedule fact, so it reads the schedule. Welding them meant each decided the
# other's surface.
#
# 90 days, not the 365 the pre-2026-09-10 rule used: at desired retention 0.95
# FSRS schedules well short of stability, so 365 days out matched 2 cards of
# 1607 on the live Norwegian deck. 90 matches 199, against 194 under the
# stability rule it replaces — chosen to hold the population steady so the
# change is a change of RULE, not a change of how much TunaTale asks.
WELL_KNOWN_DUE_DAYS_AHEAD = 90

MasteryBand = Literal["none", "new", "learning", "days", "weeks", "months", "solid", "suspended"]


def _strength_stability(ds: DirectionState) -> float | None:
    """The stability a band is read from, or None when the state has no strength.

    A REVIEW card carries it; so does a BURIED card that has been reviewed —
    burying hides a card for the day and does not weaken the memory. A buried
    card with no reps was buried while still new.
    """
    if ds.state == SRSState.REVIEW or (ds.state == SRSState.BURIED and ds.reps > 0):
        return ds.stability
    return None


def direction_band(ds: DirectionState | None) -> MasteryBand:
    """The band one side of a word sits in. ``None`` (no card) reads ``"none"``,
    which is distinct from ``"new"`` (a card that has never been studied)."""
    if ds is None:
        return "none"
    if ds.state == SRSState.SUSPENDED:
        return "suspended"
    if ds.state == SRSState.KNOWN:
        return "solid"
    if ds.state in (SRSState.LEARNING, SRSState.RELEARNING):
        return "learning"
    stability = _strength_stability(ds)
    if stability is None:
        return "new"
    if stability < BAND_WEEKS_FROM_DAYS:
        return "days"
    if stability < BAND_MONTHS_FROM_DAYS:
        return "weeks"
    if stability < WELL_KNOWN_STABILITY_DAYS:
        return "months"
    return "solid"


def band_stability(band: str | None, ds: DirectionState | None) -> float | None:
    """The stability a band carries, or None when the band has no strength.

    Only the days/weeks/months/solid bands represent a measured memory, and a
    KNOWN card's stability is not one (bd tunatale-yh47). The reader and the
    listen preview both read this, so the two cannot quote different numbers.
    """
    if band in ("days", "weeks", "months", "solid") and ds is not None and ds.state != SRSState.KNOWN:
        return ds.stability
    return None


def component_mastery(ds: DirectionState) -> float:
    """Mastery of one component (a direction/card) ∈ [0,1].

    NEW → 0.0 (unlearned). LEARNING/RELEARNING → 0.15 fixed floor (in-steps, not
    graduated). KNOWN → 1.0. REVIEW → log-normalized stability, which is
    time-independent: a word keeps the same color between reviews.

    Mastery does NOT depend on ``last_review`` — a card marked KNOWN (via
    ``mark_known``) carries high stability but no review timestamp, and must still
    read as mastered. "Unlearned" is already captured by low stability (s≤1 day →
    ``log10(1)=0``); a separate ``last_review is None`` guard (a relic of the
    retrievability-based formula) would wrongly zero those high-stability cards.
    """
    if ds.state == SRSState.NEW:
        return 0.0
    if ds.state in (SRSState.LEARNING, SRSState.RELEARNING):
        return _LEARNING_FLOOR
    if ds.state == SRSState.KNOWN:
        return 1.0
    mastery = math.log10(max(ds.stability, 1.0)) / math.log10(MASTERY_STABILITY_CEILING_DAYS)
    return max(0.0, min(1.0, mastery))


def side_progress(ds: DirectionState | None) -> float | None:
    """One side of a word's mastery, for the lesson roll-up's per-side percent.

    The old blended percent split by direction (bd tunatale-yh47.7): a missing
    card scores 0.0, exactly as :func:`compute_mastery_progress` scores an
    absent production component, and a SUSPENDED side is None so the roll-up
    leaves it out of that side's mean, as the blend excludes it.
    """
    if ds is None:
        return 0.0
    if ds.state == SRSState.SUSPENDED:
        return None
    return component_mastery(ds)


def compute_mastery_progress(directions: Iterable[DirectionState]) -> float | None:
    """Mean component_mastery over the learn-set. SUSPENDED components excluded.
    None if the set is empty (→ caller renders as not-on-the-ramp).

    An ABSENT production component scores 0.0, exactly like a NEW one: a word you
    have never had to produce is not mastered, however mature its recognition
    card is. Without this, any recognition-only card past
    MASTERY_STABILITY_CEILING_DAYS clamped to a flat 100% — which described 2990
    of 3017 collocations in the Norwegian deck (18.3% of it read fully mastered,
    against 0.3% of the two-direction Slovene deck).

    Presence is checked on the RAW input, before the SUSPENDED filter, so a
    deliberately suspended production card is excluded from the mean without also
    drawing the absent-production penalty — otherwise one card is scored twice.
    Cloze notes are production-only by design and so are never penalized.
    """
    directions = list(directions)  # consumed twice; a generator would read empty
    present = {d.direction for d in directions}
    ms = [component_mastery(d) for d in directions if d.state != SRSState.SUSPENDED]
    if ms and Direction.PRODUCTION not in present:
        ms.append(0.0)
    return sum(ms) / len(ms) if ms else None


def is_well_known(rec: DirectionState | None, today: date) -> bool:
    """True when a direction's next review is at least 90 days out.

    The listen preview stops asking about such a word (only while it is not yet
    due, which this predicate now enforces itself rather than leaning on the
    caller's ``"ahead"`` guard), and the lesson mastery line counts it in the
    known bucket.

    NOT the top colour band. ``direction_band(rec) == "solid"`` is a stability
    rule and stays one; this is a schedule rule. They disagree on purpose — a
    strong memory due next week is "solid" and still worth asking about, and a
    modest memory parked a year out is neither. Before bd tunatale-38z9 they
    were one predicate, which let a 15-day difference in an FSRS *estimate*
    decide whether TunaTale kept quizzing a word.

    LEARNING/RELEARNING are never well known however far out they are parked:
    suppressing a card being acquired would hide work the user owes. The
    stability rule inherited that exclusion from ``direction_band``; this one
    states it, because there is no band to inherit it from. Marked-known cards
    are well known whatever their schedule — ``mark_known`` carries no
    meaningful due date. A missing ``due_at`` reads as not-well-known: with
    nothing to measure the horizon against, keep asking.
    """
    if rec is None:
        return False
    if rec.state == SRSState.KNOWN:
        return True
    # The same state gate `_strength_stability` uses: a BURIED card that has
    # been reviewed still has a real schedule; one with no reps was buried
    # while still new and has nothing to read.
    if rec.state != SRSState.REVIEW and not (rec.state == SRSState.BURIED and rec.reps > 0):
        return False
    if rec.due_at is None:
        return False
    return (rec.due_at.date() - today).days >= WELL_KNOWN_DUE_DAYS_AHEAD
