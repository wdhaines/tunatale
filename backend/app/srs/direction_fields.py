"""Field registry: collocation_directions ↔ DirectionState ↔ sync diff.

Single source for the per-direction column set. `_DIR_COLUMNS` (db_base) and
`_direction_differs` (sync_engine) both derive from `DIRECTION_FIELDS`, so a
field can no longer be added to the schema/model but forgotten in one of the
consuming lists — the class of bug behind Layers 17 (`left`), 35 (`bury_kind`)
and 37 (`anki_card_mod`). `tests/test_direction_fields.py` pins the registry
against the real table schema and the real model.

Adding a column to `collocation_directions`:
1. Migration in `app/srs/migrations.py` + `DirectionState` field.
2. Register it here — decide `sync_comparable` deliberately: True means a
   candidate-vs-local difference in this field alone triggers a sync self-heal
   write; False means changes to only this field are ignored by the merge.
   Flipping an existing flag is a parity behavior change → own Layer.
3. Writer sites still enumerate columns by hand: `update_direction`
   (db_directions), the `add_collocation` INSERTs (db_collocations), and the
   sync UPDATEs in db_sync. The schema-coverage test lands you here; this
   checklist is what to fan out to.

Columns that are deliberately NOT part of the DirectionState snapshot go in
`NON_STATE_COLUMNS` instead (identity keys + SQL-only side fields).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DirectionField:
    """One collocation_directions column mirrored onto DirectionState."""

    column: str  # collocation_directions column name
    model_field: str  # DirectionState attribute name
    sync_comparable: bool  # participates in _direction_differs
    reason: str  # parity semantics behind sync_comparable


# Order matters: this is the SELECT order behind `_DIR_COLUMNS` (kept identical
# to the pre-registry tuple; due_date dropped in v25 — due_at is the single
# source of truth for due-time).
DIRECTION_FIELDS: tuple[DirectionField, ...] = (
    DirectionField("stability", "stability", True, "core FSRS memory state; Anki-ahead merges must land"),
    DirectionField("fsrs_difficulty", "difficulty", True, "core FSRS memory state (column↔attr rename)"),
    DirectionField(
        "due_at",
        "due_at",
        True,
        "rule 6: grading-drift convergence + learning step advances would be silently skipped without it",
    ),
    DirectionField("reps", "reps", True, "FSRS counter; feeds revlog + graduation checks"),
    DirectionField("lapses", "lapses", True, "FSRS counter; feeds relearning detection"),
    DirectionField("state", "state", True, "queue membership; every state-class transition must write"),
    DirectionField("last_review", "last_review", True, "graded-today filters (sibling-bury, introduced) read it"),
    DirectionField(
        "last_review_time_ms",
        "last_review_time_ms",
        False,
        "same grade event as last_review, which is compared; not independently diffed today — flipping "
        "this to True is a behavior change (own Layer)",
    ),
    DirectionField("anki_card_id", "anki_card_id", True, "pointer re-links (orphan recovery) must persist"),
    DirectionField(
        "anki_card_mod",
        "anki_card_mod",
        True,
        "Layer 37: FNV tiebreaker input (Anki's fnvhash(id, mod)) — an un-synced mod bump drifts the R-tied sort order",
    ),
    DirectionField("anki_due", "anki_due", True, "Anki-side due; new-card gather order input (Layer 25/28)"),
    DirectionField("dirty_fsrs", "dirty_fsrs", True, "push bookkeeping; a cleared/raised flag must persist"),
    DirectionField(
        "last_synced_at",
        "last_synced_at",
        False,
        "benign sync timestamp; comparing it would make every pull a spurious write",
    ),
    DirectionField("last_rating", "last_rating", False, "benign bookkeeping; excluded to avoid spurious writes"),
    DirectionField("left", "left", True, "Layer 17: learning step-state; step advances were silently skipped"),
    DirectionField(
        "prior_state",
        "prior_state",
        True,
        "rule 7 sticky intro marker; drives revlog type + newToday parity, self-heals must fire",
    ),
    DirectionField(
        "prior_left", "prior_left", False, "push-time revlog snapshot (TT bookkeeping); merge candidates don't carry it"
    ),
    DirectionField(
        "prior_stability",
        "prior_stability",
        False,
        "push-time revlog snapshot (TT bookkeeping); merge candidates don't carry it",
    ),
    DirectionField(
        "introduced_at",
        "introduced_at",
        False,
        "Layer 26 one-shot stamp; written via _resolve_introduced_at, not the differs-gated merge write",
    ),
    DirectionField(
        "bury_kind",
        "bury_kind",
        True,
        "Layer 35 follow-up: kind-only flips (sched↔user) were silent no-ops, locking rows in the wrong kind",
    ),
    DirectionField("fsrs_force_next", "fsrs_force_next", False, "TT-only one-shot push flag; never synced from Anki"),
)

# collocation_directions columns deliberately NOT mirrored onto DirectionState.
NON_STATE_COLUMNS: frozenset[str] = frozenset(
    {
        "collocation_id",  # FK identity
        "direction",  # row key; DirectionState.direction is set from it, outside _DIR_COLUMNS
        # mark_known/restore_known SQL-only snapshot (db_directions) — restored
        # in-place, never read into a DirectionState.
        "known_prior_state",
        "known_prior_stability",
        "known_prior_due_at",
    }
)

# Derived views — consume these instead of re-enumerating fields.
SYNC_COMPARABLE_MODEL_FIELDS: tuple[str, ...] = tuple(f.model_field for f in DIRECTION_FIELDS if f.sync_comparable)
