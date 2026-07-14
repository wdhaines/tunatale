"""Mechanical enforcement of the three per-direction column invariants that
used to live only in `.claude/rules/anki-queue-parity.md` prose (rules 7, 8, 10):

- `prior_state` **sticky-new** (rule 7): `prior_state='new'` persists across every
  grade on the intro arc, released only on REVIEW→RELEARNING.
- `introduced_at` **one-shot** (rule 8 / Layer 26): stamped once, never re-stamped.
- `bury_kind` **tri-state** (rule 10): NULL / 'sched' / 'user' only.

Companion to `tests/test_direction_fields.py` (which closed the *field-list*
half of the same weakness — Layers 17/35/37). These tests pin the invariants to a
single declarative source (`app/srs/direction_fields.py`): the resolver functions
that enforce the transition rules, a pure at-rest validator, and the SQL CHECK
constraints (v35 migration). A new SRSState value / bury kind added to the registry
without a widening migration makes `test_check_domains_match_registry` fail.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.sync_engine import _bury_kind_from_queue, _resolve_introduced_at
from app.srs.database import SRSDatabase
from app.srs.direction_fields import (
    BURY_KIND_DOMAIN,
    DIRECTION_FIELDS,
    DOMAIN_CONSTRAINED_FIELDS,
    PRIOR_STATE_DOMAIN,
    WritePolicy,
    iter_coupling_violations,
    iter_direction_invariant_violations,
    iter_domain_violations,
)
from app.srs.fsrs import _grade_prior_state
from app.srs.migrations import CURRENT_VERSION, migrate_v34_to_v35

_DUE = datetime(2026, 1, 1, 4, 0, tzinfo=UTC)


def _state(**kw: object) -> DirectionState:
    return DirectionState(direction=Direction.RECOGNITION, due_at=_DUE, **kw)  # type: ignore[arg-type]


def _add_direction_row(srs_db: SRSDatabase) -> tuple[int, str]:
    """Insert one real collocation and return (collocation_id, direction) of a row."""
    srs_db.add_collocation(
        SyntacticUnit(text="proba", translation="test", word_count=1, difficulty=1, source="corpus"),
        language_code="sl",
    )
    with srs_db._get_conn() as conn:
        row = conn.execute("SELECT collocation_id, direction FROM collocation_directions LIMIT 1").fetchone()
    return row[0], row[1]


class TestRegistryInvariantMetadata:
    def test_write_policy_members(self) -> None:
        assert {p.name for p in WritePolicy} == {"FREE", "ONE_SHOT", "STICKY_NEW"}

    def test_declared_policies_and_domains(self) -> None:
        by_col = {f.column: f for f in DIRECTION_FIELDS}
        assert by_col["prior_state"].write_policy is WritePolicy.STICKY_NEW
        assert by_col["prior_state"].domain == PRIOR_STATE_DOMAIN
        assert by_col["introduced_at"].write_policy is WritePolicy.ONE_SHOT
        assert by_col["introduced_at"].domain is None
        assert by_col["bury_kind"].write_policy is WritePolicy.FREE
        assert by_col["bury_kind"].domain == BURY_KIND_DOMAIN
        # Every other field is unconstrained.
        for f in DIRECTION_FIELDS:
            if f.column not in {"prior_state", "introduced_at", "bury_kind"}:
                assert f.write_policy is WritePolicy.FREE, f.column
                assert f.domain is None, f.column

    def test_domains_are_single_sourced(self) -> None:
        assert BURY_KIND_DOMAIN == (None, "sched", "user")
        # prior_state domain is derived from the SRSState enum, not hand-listed.
        expected_prior = (None, *(s.value for s in SRSState))
        assert expected_prior == PRIOR_STATE_DOMAIN

    def test_domain_constrained_fields_view(self) -> None:
        assert {f.column for f in DOMAIN_CONSTRAINED_FIELDS} == {"prior_state", "bury_kind"}


class TestValidator:
    def test_clean_state_has_no_violations(self) -> None:
        assert list(iter_direction_invariant_violations(_state())) == []

    def test_valid_prior_state_not_flagged(self) -> None:
        # exercises the enum→.value branch of the domain check
        assert list(iter_domain_violations(_state(prior_state=SRSState.REVIEW))) == []

    def test_bury_kind_out_of_domain_flagged(self) -> None:
        v = list(iter_domain_violations(_state(bury_kind="bogus", state=SRSState.BURIED)))
        assert len(v) == 1
        assert "bury_kind" in v[0]

    def test_valid_bury_kind_not_flagged(self) -> None:
        assert list(iter_domain_violations(_state(bury_kind="user", state=SRSState.BURIED))) == []

    def test_coupling_bury_kind_without_buried_state(self) -> None:
        v = list(iter_coupling_violations(_state(bury_kind="sched", state=SRSState.REVIEW)))
        assert len(v) == 1
        assert "buried" in v[0]

    def test_coupling_ok_when_buried(self) -> None:
        assert list(iter_coupling_violations(_state(bury_kind="sched", state=SRSState.BURIED))) == []

    def test_coupling_ok_when_bury_kind_null(self) -> None:
        assert list(iter_coupling_violations(_state(bury_kind=None, state=SRSState.REVIEW))) == []

    def test_combined_yields_domain_and_coupling(self) -> None:
        v = list(iter_direction_invariant_violations(_state(bury_kind="bogus", state=SRSState.REVIEW)))
        assert len(v) == 2


class TestResolverPins:
    """Tie the resolver functions to the declared WritePolicy — a regression that
    reverts sticky/one-shot behavior (rules 7/8) fails here, not silently in prod."""

    def test_grade_prior_state_obeys_sticky_new(self) -> None:
        pf = next(f for f in DIRECTION_FIELDS if f.column == "prior_state")
        assert pf.write_policy is WritePolicy.STICKY_NEW
        prev = _state(state=SRSState.REVIEW, prior_state=SRSState.NEW)
        for ns in SRSState:
            expected = SRSState.NEW if ns is not SRSState.RELEARNING else prev.state
            assert _grade_prior_state(prev, ns) == expected, ns

    def test_grade_prior_state_non_new_prior_returns_prev_state(self) -> None:
        prev = _state(state=SRSState.REVIEW, prior_state=SRSState.REVIEW)
        for ns in SRSState:
            assert _grade_prior_state(prev, ns) == prev.state, ns

    def test_resolve_introduced_at_obeys_one_shot(self) -> None:
        iaf = next(f for f in DIRECTION_FIELDS if f.column == "introduced_at")
        assert iaf.write_policy is WritePolicy.ONE_SHOT
        stamped = datetime(2025, 6, 1, 4, 0, tzinfo=UTC)
        local = _state(introduced_at=stamped)
        for ns in SRSState:
            for frm in (None, 1_700_000_000_000):
                assert _resolve_introduced_at(local, ns, first_review_ms=frm) == stamped, (ns, frm)

    def test_bury_kind_from_queue_stays_in_domain(self) -> None:
        for q in range(-6, 5):
            assert _bury_kind_from_queue(q) in BURY_KIND_DOMAIN, q


class TestCheckConstraint:
    """The v35 SQL CHECK constraints hard-enforce the two domains at write time."""

    def test_bury_kind_domain_enforced(self, srs_db: SRSDatabase) -> None:
        cid, direction = _add_direction_row(srs_db)
        with srs_db._get_conn() as conn:
            for good in ("sched", "user"):
                conn.execute(
                    "UPDATE collocation_directions SET bury_kind=? WHERE collocation_id=? AND direction=?",
                    (good, cid, direction),
                )
        with pytest.raises(sqlite3.IntegrityError), srs_db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET bury_kind='bogus' WHERE collocation_id=? AND direction=?",
                (cid, direction),
            )

    def test_prior_state_domain_enforced(self, srs_db: SRSDatabase) -> None:
        cid, direction = _add_direction_row(srs_db)
        with srs_db._get_conn() as conn:
            for s in SRSState:
                conn.execute(
                    "UPDATE collocation_directions SET prior_state=? WHERE collocation_id=? AND direction=?",
                    (s.value, cid, direction),
                )
        with pytest.raises(sqlite3.IntegrityError), srs_db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET prior_state='bogus' WHERE collocation_id=? AND direction=?",
                (cid, direction),
            )

    def test_check_domains_match_registry(self, srs_db: SRSDatabase) -> None:
        """Every value the registry declares as in-domain is accepted by the live
        constraint. Add an SRSState/bury kind to the registry without widening the
        v35 CHECK and this test fails (drift detector for the constraint half)."""
        cid, direction = _add_direction_row(srs_db)
        with srs_db._get_conn() as conn:
            for val in BURY_KIND_DOMAIN:
                conn.execute(
                    "UPDATE collocation_directions SET bury_kind=? WHERE collocation_id=? AND direction=?",
                    (val, cid, direction),
                )
            for val in PRIOR_STATE_DOMAIN:
                conn.execute(
                    "UPDATE collocation_directions SET prior_state=? WHERE collocation_id=? AND direction=?",
                    (val, cid, direction),
                )


class TestMigrationV35:
    def test_migration_is_idempotent(self, srs_db: SRSDatabase) -> None:
        # srs_db ran every migration incl. the v35 recreate path during setup.
        with srs_db._get_conn() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION
            conn.execute("PRAGMA user_version = 34")
            migrate_v34_to_v35(conn)  # hits the "already has CHECK" guard
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 35

    def test_data_survives_recreate(self, srs_db: SRSDatabase) -> None:
        cid, direction = _add_direction_row(srs_db)
        with srs_db._get_conn() as conn:
            row = conn.execute(
                "SELECT collocation_id FROM collocation_directions WHERE collocation_id=? AND direction=?",
                (cid, direction),
            ).fetchone()
        assert row is not None
