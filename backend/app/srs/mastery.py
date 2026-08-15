"""Per-lemma mastery = aggregated FSRS stability over the learn-set (Phase 5).

Mastery uses *stability*, not retrievability. The scheduler actively regulates
retrievability toward desired_retention (~0.9), so a review card's R lives in a
narrow band and can't distinguish a freshly graduated card from a long-mastered
one — every reviewed word renders the same green. Stability instead grows
monotonically as a word is learned (the user's deck spans ~3–116 days), so it is
what the transcript color ramp should track.
"""

from __future__ import annotations

import datetime
import math
from collections.abc import Iterable

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


def is_due_beyond_horizon(due_at: datetime.datetime | str, today: datetime.date, horizon: int) -> bool:
    """True when a card's due date is more than *horizon* days past *today*.

    ``due_at`` is datetime-or-string depending on load path — the same idiom
    ``_listen_grade_class`` uses. ``today`` is always ``anki_today()``, i.e. a
    ``date`` (the Anki day, not ``date.today()``); an unparseable ``due_at`` is
    not beyond the horizon.

    Lives here rather than in ``api/srs.py`` (where it started) so the listen
    preview and the transcript can share ONE definition of the cutoff — the
    display saying "review" while the preview says "well known" is exactly the
    divergence this move exists to make impossible.
    """
    if isinstance(due_at, datetime.datetime):
        due_date = due_at.date()
    else:
        try:
            due_date = datetime.date.fromisoformat(str(due_at)[:10])
        except ValueError:
            return False
    return (due_date - today).days > horizon


def is_well_known(rec: DirectionState | None, today: datetime.date, horizon: int) -> bool:
    """True when a recognition direction is scheduled past the horizon.

    "Well known" = REVIEW state with a real due date more than *horizon* days
    out. LEARNING/RELEARNING are never well-known however far the due date
    drifts (suppressing a card being acquired would hide work the user owes),
    and a NULL ``due_at`` is not well-known either — a card whose schedule is
    unknown stays visible.

    Marked-known cards land here after a sync returns them as REVIEW due
    ~2126, which is how the far-future rule covers them without depending on
    ``SRSState.KNOWN`` surviving the round trip.
    """
    if rec is None or rec.state != SRSState.REVIEW or rec.due_at is None:
        return False
    return is_due_beyond_horizon(rec.due_at, today, horizon)
