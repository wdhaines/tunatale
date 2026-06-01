"""Per-lemma mastery = aggregated retrievability over the learn-set (Phase 5)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from app.models.srs_item import DirectionState, SRSState
from app.srs.fsrs import compute_retrievability


def component_mastery(
    ds: DirectionState, today: date, now: datetime | None, col_crt: int | None,
    desired_retention: float = 0.9,
) -> float:
    """Mastery of one component (a direction/card) ∈ [0,1].

    NEW/never-reviewed → 0.0 (unlearned). LEARNING/RELEARNING → 0.15 fixed floor
    (in-steps, not graduated). REVIEW → aggregated retrievability. KNOWN → 1.0.
    """
    if ds.state == SRSState.NEW or ds.last_review is None:
        return 0.0
    if ds.state in (SRSState.LEARNING, SRSState.RELEARNING):
        return 0.15
    if ds.state == SRSState.KNOWN:
        return 1.0
    return compute_retrievability(ds, today, now=now, desired_retention=desired_retention, col_crt=col_crt)


def compute_mastery_progress(
    directions: Iterable[DirectionState], today: date, now: datetime | None,
    col_crt: int | None, desired_retention: float = 0.9,
) -> float | None:
    """Mean component_mastery over the learn-set. SUSPENDED components excluded.
    None if the set is empty (→ caller renders as not-on-the-ramp).
    """
    ms = [
        component_mastery(d, today, now, col_crt, desired_retention)
        for d in directions if d.state != SRSState.SUSPENDED
    ]
    return sum(ms) / len(ms) if ms else None
