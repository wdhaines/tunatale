"""Single-level undo for TT-native grades (the popover's "Got it ✓" → "Undo ↩").

A grade in ``drill_feedback`` mutates exactly two durable things: the
direction row (``update_direction_by_id``) and a ``tt_revlog`` row that the
next sync pushes to Anki. Undo restores the verbatim pre-grade
``DirectionState`` and deletes that revlog row — but ONLY while the grade is
still TT-local:

- the snapshot's revlog row must still be the direction's latest (a newer
  grade — drill or /listen — supersedes it), and
- the direction must still carry ``dirty_fsrs=1``. Once a sync clears it the
  review lives in Anki; deleting TT state then would just be re-clobbered by
  the next pull (queue-parity rule 6), so we refuse instead.

Single-level by design (one snapshot in ``anki_state_cache``), mirroring how
the UI uses it: the popover that just graded flips its button to "Undo ↩".
The learning-cutoff advance and the request-scoped load-balancer histogram
are intentionally NOT unwound: the cutoff only ever advances in Anki too, and
the balancer is rebuilt per request from the (restored) due dates.
"""

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING

from app.models.srs_item import Direction, DirectionState, SRSState

if TYPE_CHECKING:
    from app.srs.database import SRSDatabase

UNDO_CACHE_KEY = "last_grade_undo"

_DATETIME_FIELDS = ("due_at", "last_review", "introduced_at")


class UndoNotAvailable(Exception):
    """The last grade can no longer be undone (superseded, synced, or absent)."""


def direction_to_dict(ds: DirectionState) -> dict[str, object]:
    """Serialize a DirectionState to a JSON-safe dict (round-trip pinned by tests)."""
    return {
        "direction": ds.direction.value,
        "due_at": ds.due_at.isoformat(),
        "stability": ds.stability,
        "difficulty": ds.difficulty,
        "reps": ds.reps,
        "lapses": ds.lapses,
        "state": ds.state.value,
        "last_review": ds.last_review.isoformat() if ds.last_review is not None else None,
        "last_review_time_ms": ds.last_review_time_ms,
        "anki_card_id": ds.anki_card_id,
        "anki_due": ds.anki_due,
        "anki_card_mod": ds.anki_card_mod,
        "bury_kind": ds.bury_kind,
        "dirty_fsrs": ds.dirty_fsrs,
        "last_synced_at": ds.last_synced_at,
        "last_rating": ds.last_rating,
        "left": ds.left,
        "prior_state": ds.prior_state.value if ds.prior_state is not None else None,
        "prior_left": ds.prior_left,
        "prior_stability": ds.prior_stability,
        "introduced_at": ds.introduced_at.isoformat() if ds.introduced_at is not None else None,
        "fsrs_force_next": ds.fsrs_force_next,
    }


def direction_from_dict(data: dict[str, object]) -> DirectionState:
    """Inverse of :func:`direction_to_dict`."""
    kwargs = dict(data)
    kwargs["direction"] = Direction(kwargs["direction"])
    kwargs["state"] = SRSState(kwargs["state"])
    if kwargs["prior_state"] is not None:
        kwargs["prior_state"] = SRSState(kwargs["prior_state"])
    for field in _DATETIME_FIELDS:
        if kwargs[field] is not None:
            kwargs[field] = datetime.datetime.fromisoformat(kwargs[field])
    return DirectionState(**kwargs)  # type: ignore[arg-type]


def record_grade_snapshot(
    db: SRSDatabase,
    *,
    item_id: int,
    direction: Direction,
    prior: DirectionState,
    revlog_id: int,
) -> None:
    """Store the pre-grade snapshot, replacing any previous one (single-level)."""
    db.set_anki_state_cache(
        UNDO_CACHE_KEY,
        json.dumps(
            {
                "collocation_id": item_id,
                "direction": direction.value,
                "revlog_id": revlog_id,
                "prior": direction_to_dict(prior),
            }
        ),
    )


def undo_last_grade(db: SRSDatabase, *, item_id: int, direction: Direction) -> DirectionState:
    """Restore the pre-grade state for (item, direction), or raise UndoNotAvailable."""
    cached = db.get_anki_state_cache(UNDO_CACHE_KEY)
    if cached is None:
        raise UndoNotAvailable("nothing to undo")
    snapshot = json.loads(cached[0])  # (value, updated_at)
    if snapshot["collocation_id"] != item_id or snapshot["direction"] != direction.value:
        raise UndoNotAvailable("last grade was for a different card")
    if db.latest_revlog_id_for_direction(item_id, direction) != snapshot["revlog_id"]:
        raise UndoNotAvailable("a newer grade superseded this one")

    result = db.get_collocation_by_id(item_id)
    if result is None:  # pragma: no cover - the API 404s before reaching here
        raise UndoNotAvailable("item no longer exists")
    _, item, _ = result
    current = item.directions.get(direction)
    if current is None or not current.dirty_fsrs:
        raise UndoNotAvailable("grade already synced to Anki")

    prior = direction_from_dict(snapshot["prior"])
    db.delete_revlog_row(snapshot["revlog_id"])
    db.update_direction_by_id(item_id, direction, prior)
    db.delete_anki_state_cache(UNDO_CACHE_KEY)
    return prior
