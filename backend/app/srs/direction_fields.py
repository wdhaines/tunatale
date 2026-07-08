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

Column-level *invariants* (not just the column set) are declared here too, so
they're enforced mechanically instead of by prose vigilance — the same class as
the field-list bug, this time for rules 7/8/10:
- `write_policy` (`WritePolicy`): the write-time transition rule — STICKY_NEW
  (prior_state, rule 7), ONE_SHOT (introduced_at, rule 8) — pinned to its enforcing
  resolver by `tests/test_direction_invariants.py`.
- `domain`: the at-rest allowed value set (bury_kind tri-state rule 10; prior_state
  ⊆ SRSState), single-sourced into BOTH the pure validator here
  (`iter_direction_invariant_violations`, swept per-sync into `INVARIANT_TRACE`
  soak lines) AND the v35 SQL CHECK constraint (`migrations.py`).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum

from app.models.srs_item import DirectionState, SRSState


class WritePolicy(Enum):
    """Write-time transition invariant for a direction column.

    Declares, as data, the column-level rules that previously lived only in
    ``.claude/rules/anki-queue-parity.md`` prose (rules 7, 8, 10). The resolver
    functions that actually enforce the transition rules are pinned to this
    declaration by ``tests/test_direction_invariants.py`` — a regression that
    reverts sticky/one-shot behavior fails a test instead of silently drifting.
    """

    FREE = "free"  # no write-time restriction
    ONE_SHOT = "one_shot"  # set once on NEW→non-NEW, never re-stamped (rule 8 / Layer 26): introduced_at
    STICKY_NEW = "sticky_new"  # prior_state='new' persists until REVIEW→RELEARNING (rule 7): prior_state


@dataclass(frozen=True)
class DirectionField:
    """One collocation_directions column mirrored onto DirectionState."""

    column: str  # collocation_directions column name
    model_field: str  # DirectionState attribute name
    sync_comparable: bool  # participates in _direction_differs
    reason: str  # parity semantics behind sync_comparable
    # Column-level invariant metadata (rules 7/8/10), formerly prose-only:
    write_policy: WritePolicy = WritePolicy.FREE  # transition rule; pinned to the enforcing resolver by tests
    # At-rest allowed value set (stored/SQL form; includes None when nullable).
    # Drives the pure validator AND the v35 SQL CHECK constraint; None = unconstrained.
    domain: tuple[object, ...] | None = None


# Domains are single-sourced here, NOT hand-listed at each use. bury_kind's
# tri-state (rule 10) and prior_state's value set (derived from the SRSState enum)
# both flow to the validator and the SQL CHECK from these two tuples.
BURY_KIND_DOMAIN: tuple[object, ...] = (None, "sched", "user")
PRIOR_STATE_DOMAIN: tuple[object, ...] = (None, *(s.value for s in SRSState))


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
        write_policy=WritePolicy.STICKY_NEW,
        domain=PRIOR_STATE_DOMAIN,
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
        write_policy=WritePolicy.ONE_SHOT,
    ),
    DirectionField(
        "bury_kind",
        "bury_kind",
        True,
        "Layer 35 follow-up: kind-only flips (sched↔user) were silent no-ops, locking rows in the wrong kind",
        domain=BURY_KIND_DOMAIN,
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
DOMAIN_CONSTRAINED_FIELDS: tuple[DirectionField, ...] = tuple(f for f in DIRECTION_FIELDS if f.domain is not None)


def _stored_value(state: DirectionState, model_field: str) -> object:
    """The at-rest (SQL) form of a DirectionState field: enums become their value."""
    val = getattr(state, model_field)
    return val.value if isinstance(val, Enum) else val


def iter_domain_violations(state: DirectionState) -> Iterator[str]:
    """Yield a message for each domain-constrained field whose value is outside
    its declared ``domain``. Belt-and-suspenders alongside the SQL CHECK — usable
    on in-memory DirectionStates before they reach the DB (e.g. a sync diagnostic)."""
    for f in DOMAIN_CONSTRAINED_FIELDS:
        val = _stored_value(state, f.model_field)
        if val not in f.domain:  # type: ignore[operator]  # domain is not None here
            yield f"{f.column}={val!r} outside domain {f.domain!r}"


def iter_coupling_violations(state: DirectionState) -> Iterator[str]:
    """Yield the cross-column invariant the per-field domain can't express:
    a ``bury_kind`` is only set on a buried row (rule 10 / Layer 35)."""
    if state.bury_kind is not None and state.state is not SRSState.BURIED:
        yield f"bury_kind={state.bury_kind!r} set but state={state.state.value!r} (expected 'buried')"


def iter_direction_invariant_violations(state: DirectionState) -> Iterator[str]:
    """All at-rest invariant violations for one direction (domain + coupling).

    The *transition* invariants (STICKY_NEW, ONE_SHOT) are not at-rest checkable;
    they are pinned to their resolver functions in ``tests/test_direction_invariants.py``.
    """
    yield from iter_domain_violations(state)
    yield from iter_coupling_violations(state)
