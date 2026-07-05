"""Tests pinning `app.srs.direction_fields` as the single source of truth for
the `collocation_directions` field registry.

Two hand-maintained lists currently derive from this schema by hand:
`_DIR_COLUMNS` in `app/srs/db_base.py` and the field-by-field comparison in
`_direction_differs` (`app/anki/sync_engine.py`, re-exported via
`app.anki.sync`). Layers 17/35/37 were each a field missing from one of these
hand-maintained lists (`left`, `bury_kind`, `anki_card_mod` respectively).
These tests assert that both derive mechanically from a single
`DIRECTION_FIELDS` registry, so a future field addition can't repeat that
class of bug by being added to the schema/model but forgotten in one of the
consuming lists.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

from app.anki.sync import _direction_differs
from app.models.srs_item import Direction, DirectionState, SRSState
from app.srs.database import SRSDatabase
from app.srs.db_base import _DIR_COLUMNS
from app.srs.direction_fields import (
    DIRECTION_FIELDS,
    NON_STATE_COLUMNS,
    SYNC_COMPARABLE_MODEL_FIELDS,
)

_BASE_DUE_AT = datetime(2026, 1, 1, 4, 0, tzinfo=UTC)

# Type-correct replacement values for every DirectionState field the registry
# tracks, each distinct from the base state's default/value below. This is
# the only per-field literal table permitted in this file — everything else
# must be derived from DIRECTION_FIELDS.
_ALTERNATES: dict[str, object] = {
    "stability": 2.5,
    "difficulty": 6.0,
    "due_at": _BASE_DUE_AT + timedelta(days=1),
    "reps": 3,
    "lapses": 1,
    "state": SRSState.REVIEW,
    "last_review": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
    "last_review_time_ms": 1234,
    "anki_card_id": 123,
    "anki_card_mod": 456,
    "anki_due": 789,
    "dirty_fsrs": True,
    "last_synced_at": "2026-01-01T00:00:00",
    "last_rating": 3,
    "left": 1001,
    "prior_state": SRSState.NEW,
    "prior_left": 2002,
    "prior_stability": 0.5,
    "introduced_at": datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
    "bury_kind": "sched",
    "fsrs_force_next": True,
}


def test_registry_covers_every_table_column(srs_db: SRSDatabase) -> None:
    """Every actual `collocation_directions` column is either a registry
    entry or an explicitly-listed non-state column, and never both."""
    with srs_db._get_conn() as conn:
        actual_columns = {row[1] for row in conn.execute("PRAGMA table_info(collocation_directions)")}

    registry_columns = {f.column for f in DIRECTION_FIELDS}

    assert registry_columns.isdisjoint(NON_STATE_COLUMNS)
    assert registry_columns | NON_STATE_COLUMNS == actual_columns


def test_registry_matches_direction_state_fields() -> None:
    """Every DirectionState field except `direction` itself has a registry
    entry, and vice versa."""
    model_fields = {f.name for f in dataclasses.fields(DirectionState)} - {"direction"}
    registry_model_fields = {f.model_field for f in DIRECTION_FIELDS}

    assert registry_model_fields == model_fields


def test_registry_has_no_duplicates() -> None:
    columns = [f.column for f in DIRECTION_FIELDS]
    model_fields = [f.model_field for f in DIRECTION_FIELDS]

    assert len(columns) == len(set(columns))
    assert len(model_fields) == len(set(model_fields))
    assert all(f.reason for f in DIRECTION_FIELDS)


def test_dir_columns_derived_from_registry() -> None:
    assert tuple(f.column for f in DIRECTION_FIELDS) == _DIR_COLUMNS


def test_sync_comparable_fields_derived() -> None:
    assert tuple(f.model_field for f in DIRECTION_FIELDS if f.sync_comparable) == SYNC_COMPARABLE_MODEL_FIELDS


def test_direction_differs_per_field_matches_registry() -> None:
    """`_direction_differs` flags a change in field X iff the registry marks
    X as `sync_comparable`. A field missing from the diff (Layers 17/35/37)
    or a stray field that shouldn't be there both fail this test."""
    base = DirectionState(direction=Direction.RECOGNITION, due_at=_BASE_DUE_AT)

    assert _direction_differs(base, dataclasses.replace(base)) is False

    for f in DIRECTION_FIELDS:
        mutated = dataclasses.replace(base, **{f.model_field: _ALTERNATES[f.model_field]})
        assert _direction_differs(base, mutated) == f.sync_comparable, (
            f"field {f.model_field!r} (column {f.column!r}): expected "
            f"_direction_differs to return {f.sync_comparable} — {f.reason}"
        )
