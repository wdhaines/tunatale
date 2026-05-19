"""Runtime invariant checks for the FSRS state.

Centralises post-grade / post-sync_pull validation that ``due_at`` is always
the FSRS-derivable projection from ``(state, stability, last_review, reps,
anki_card_id, last_rating)`` — see ``.claude/rules/anki-queue-parity.md``.

Designed to fire in dev/test, log-and-continue in production:
  - ``check_due_invariant(db, raise_on_violation=True)`` raises on the first
    offender (used by tests and dev mode).
  - ``check_due_invariant(db, raise_on_violation=False)`` returns a count and
    logs the first three offenders (used in production sync_pull/grade hooks).

Pre-conditions for an invariant violation:
  - ``state ∈ {review, relearning}`` (learning is sub-day, expected to differ).
  - ``last_rating IS NOT NULL`` (no grade ⇒ nothing to derive against).
  - ``last_review IS NOT NULL`` (same).
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.srs.fsrs import (
    DEFAULT_FSRS5_PARAMS,
    FSRSParams,
    _next_interval,
    _review_interval_fuzz,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DueInvariantViolation:
    collocation_id: int
    direction: str
    stored_due_at: str
    expected_due_at: str
    stability: float
    reps: int
    last_review: str


def _params_from_cache(conn: sqlite3.Connection) -> FSRSParams:
    """Reconstruct FSRSParams from anki_state_cache; fall back to defaults."""
    import json

    row = conn.execute("SELECT value FROM anki_state_cache WHERE key='fsrs_params'").fetchone()
    if not row:
        return DEFAULT_FSRS5_PARAMS
    try:
        weights = tuple(json.loads(row[0])["weights"])
    except (json.JSONDecodeError, KeyError, TypeError):
        return DEFAULT_FSRS5_PARAMS
    if len(weights) not in (19, 21):
        return DEFAULT_FSRS5_PARAMS
    dr_row = conn.execute("SELECT value FROM anki_state_cache WHERE key='desired_retention'").fetchone()
    dr = float(dr_row[0]) if dr_row else 0.9
    return FSRSParams(weights=weights, desired_retention=dr)


def check_due_invariant(conn: sqlite3.Connection, *, raise_on_violation: bool = False) -> list[DueInvariantViolation]:
    """Walk review-state rows; verify ``due_at`` matches the fuzzed FSRS projection.

    For each violation, return a ``DueInvariantViolation``. If
    ``raise_on_violation`` is True, raise ``AssertionError`` on the first one.
    """
    params = _params_from_cache(conn)
    neg_decay = -params.decay
    rows = conn.execute(
        """
        SELECT cd.collocation_id, cd.direction, cd.due_at, cd.stability,
               cd.reps, cd.last_review, cd.anki_card_id, cd.state
        FROM collocation_directions cd
        WHERE cd.state = 'review'
          AND cd.last_rating IS NOT NULL
          AND cd.last_review IS NOT NULL
        """
    ).fetchall()

    violations: list[DueInvariantViolation] = []
    for r in rows:
        stored_due_at = r[2]
        stored = datetime.fromisoformat(stored_due_at)
        last_review = datetime.fromisoformat(r[5])

        raw = _next_interval(r[3], params.desired_retention, neg_decay)
        # at-grade reps = current reps - 1 (Anki's `for_reschedule=true` semantics).
        fuzzed = _review_interval_fuzz(raw, r[6], max(0, r[4] - 1), params.maximum_review_interval)
        expected = last_review + timedelta(days=fuzzed)
        # Expected lives at a day boundary; compare at day granularity to be robust
        # to legacy rows stamped at 4am vs midnight.
        if stored.date() != expected.date():
            v = DueInvariantViolation(
                collocation_id=r[0],
                direction=r[1],
                stored_due_at=stored_due_at,
                expected_due_at=expected.isoformat(),
                stability=r[3],
                reps=r[4],
                last_review=r[5],
            )
            violations.append(v)
            if raise_on_violation:
                raise AssertionError(f"Due invariant violation: {v}")

    if violations:
        log.warning(
            "due_at invariant: %d violation(s); first three: %s",
            len(violations),
            violations[:3],
        )
    return violations
