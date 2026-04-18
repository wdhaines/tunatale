"""Tests for the Anki-side SQL helpers used by the Stage 2b backfill CLI.

Covers:
- read_col_conf / check_anki_web_sync_active — AnkiWeb-sync detection.
- plan_guid_backfill — partitioning notes into updates / noops / conflicts / duplicates.
- apply_guid_backfill — single-transaction UPDATE with col.mod / col.usn bump.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.anki.sqlite_writer import (
    apply_guid_backfill,
    check_anki_web_sync_active,
    plan_guid_backfill,
    read_col_conf,
)
from app.common.guid import compute_guid
from tests.conftest import build_minimal_anki_db


def _set_col_conf(db_path: Path, conf: str | None) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        if conf is None:
            conn.execute("UPDATE col SET conf=NULL")
        else:
            conn.execute("UPDATE col SET conf=?", (conf,))
        conn.commit()
    finally:
        conn.close()


def _set_note_guid(db_path: Path, note_id: int, guid: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE notes SET guid=? WHERE id=?", (guid, note_id))
        conn.commit()
    finally:
        conn.close()


def _set_note_fields(db_path: Path, note_id: int, fields: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE notes SET flds=? WHERE id=?", (fields, note_id))
        conn.commit()
    finally:
        conn.close()


def _set_col_fields(db_path: Path, mod: int | None = None, usn: int | None = None) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        if mod is not None:
            conn.execute("UPDATE col SET mod=?", (mod,))
        if usn is not None:
            conn.execute("UPDATE col SET usn=?", (usn,))
        conn.commit()
    finally:
        conn.close()


def _notes_rows(db_path: Path) -> dict[int, tuple[str, int]]:
    """Return {note_id: (guid, mod)} for every note."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT id, guid, mod FROM notes").fetchall()
    finally:
        conn.close()
    return {r[0]: (r[1], r[2]) for r in rows}


def _col_row(db_path: Path) -> tuple[int, int]:
    """Return (mod, usn) from the col row."""
    conn = sqlite3.connect(str(db_path))
    try:
        r = conn.execute("SELECT mod, usn FROM col").fetchone()
    finally:
        conn.close()
    return r[0], r[1]


class TestReadColConf:
    def test_well_formed_empty_dict(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        try:
            assert read_col_conf(conn) == {}
        finally:
            conn.close()

    def test_with_sync_key(self, fake_anki_db):
        _set_col_conf(fake_anki_db, json.dumps({"syncKey": "abc123"}))
        conn = sqlite3.connect(str(fake_anki_db))
        try:
            assert read_col_conf(conn) == {"syncKey": "abc123"}
        finally:
            conn.close()

    def test_empty_string_returns_empty_dict(self, fake_anki_db):
        _set_col_conf(fake_anki_db, "")
        conn = sqlite3.connect(str(fake_anki_db))
        try:
            assert read_col_conf(conn) == {}
        finally:
            conn.close()

    def test_null_returns_empty_dict(self, fake_anki_db):
        _set_col_conf(fake_anki_db, None)
        conn = sqlite3.connect(str(fake_anki_db))
        try:
            assert read_col_conf(conn) == {}
        finally:
            conn.close()

    def test_malformed_returns_empty_dict(self, fake_anki_db):
        _set_col_conf(fake_anki_db, "{not valid json")
        conn = sqlite3.connect(str(fake_anki_db))
        try:
            assert read_col_conf(conn) == {}
        finally:
            conn.close()

    def test_non_dict_json_returns_empty_dict(self, fake_anki_db):
        """Well-formed JSON that isn't a dict (array, string, number) → {}."""
        _set_col_conf(fake_anki_db, "[1, 2, 3]")
        conn = sqlite3.connect(str(fake_anki_db))
        try:
            assert read_col_conf(conn) == {}
        finally:
            conn.close()

    def test_empty_col_table_returns_empty_dict(self, fake_anki_db):
        """No rows in col → read_col_conf returns {} without error."""
        conn = sqlite3.connect(str(fake_anki_db))
        try:
            conn.execute("DELETE FROM col")
            conn.commit()
            assert read_col_conf(conn) == {}
        finally:
            conn.close()


class TestCheckAnkiWebSyncActive:
    def test_true_when_sync_key_present(self, fake_anki_db):
        _set_col_conf(fake_anki_db, json.dumps({"syncKey": "abc"}))
        conn = sqlite3.connect(str(fake_anki_db))
        try:
            assert check_anki_web_sync_active(conn) is True
        finally:
            conn.close()

    def test_false_when_sync_key_null(self, fake_anki_db):
        _set_col_conf(fake_anki_db, json.dumps({"syncKey": None}))
        conn = sqlite3.connect(str(fake_anki_db))
        try:
            assert check_anki_web_sync_active(conn) is False
        finally:
            conn.close()

    def test_false_when_sync_key_absent(self, fake_anki_db):
        _set_col_conf(fake_anki_db, json.dumps({"other": "value"}))
        conn = sqlite3.connect(str(fake_anki_db))
        try:
            assert check_anki_web_sync_active(conn) is False
        finally:
            conn.close()

    def test_false_on_empty_conf(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        try:
            assert check_anki_web_sync_active(conn) is False
        finally:
            conn.close()


class TestPlanGuidBackfill:
    def test_all_noop_when_guids_already_match(self, fake_anki_db):
        texts = {1001: "banka", 1002: "hiša", 1003: "miza", 1004: "stol", 1005: "knjiga"}
        for nid, text in texts.items():
            _set_note_guid(fake_anki_db, nid, compute_guid(text, "sl"))

        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            plan = plan_guid_backfill(conn, deck_id=12345, force=False)
        finally:
            conn.close()

        assert plan.updates == {}
        assert set(plan.noops) == {1001, 1002, 1003, 1004, 1005}
        assert plan.skipped_conflicts == []
        assert plan.skipped_duplicates == []

    def test_conflict_skipped_by_default(self, fake_anki_db):
        """Default (no --force): differing guid → skipped_conflicts, not updates."""
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            plan = plan_guid_backfill(conn, deck_id=12345, force=False)
        finally:
            conn.close()

        assert plan.updates == {}
        assert {c[0] for c in plan.skipped_conflicts} == {1001, 1002, 1003, 1004, 1005}

    def test_conflicts_promoted_with_force(self, fake_anki_db):
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            plan = plan_guid_backfill(conn, deck_id=12345, force=True)
        finally:
            conn.close()

        expected = {
            1001: compute_guid("banka", "sl"),
            1002: compute_guid("hiša", "sl"),
            1003: compute_guid("miza", "sl"),
            1004: compute_guid("stol", "sl"),
            1005: compute_guid("knjiga", "sl"),
        }
        assert plan.updates == expected
        assert plan.skipped_conflicts == []
        assert plan.noops == []

    def test_mixed_noop_and_update_with_force(self, fake_anki_db):
        _set_note_guid(fake_anki_db, 1001, compute_guid("banka", "sl"))
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            plan = plan_guid_backfill(conn, deck_id=12345, force=True)
        finally:
            conn.close()

        assert plan.noops == [1001]
        assert set(plan.updates.keys()) == {1002, 1003, 1004, 1005}

    def test_duplicates_detected_and_skipped(self, fake_anki_db):
        """Two notes with identical l2_text → both in skipped_duplicates, regardless of --force."""
        _set_note_fields(fake_anki_db, 1002, "banka\x1fbank2")

        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            plan_default = plan_guid_backfill(conn, deck_id=12345, force=False)
            plan_forced = plan_guid_backfill(conn, deck_id=12345, force=True)
        finally:
            conn.close()

        dup_ids_default = {d[0] for d in plan_default.skipped_duplicates}
        dup_ids_forced = {d[0] for d in plan_forced.skipped_duplicates}

        assert 1001 in dup_ids_default and 1002 in dup_ids_default
        assert 1001 in dup_ids_forced and 1002 in dup_ids_forced
        assert 1001 not in plan_forced.updates
        assert 1002 not in plan_forced.updates


class TestApplyGuidBackfill:
    def test_updates_guid_and_bumps_notes_mod(self, fake_anki_db):
        _set_col_fields(fake_anki_db, mod=1_000_000, usn=42)
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            plan = plan_guid_backfill(conn, deck_id=12345, force=True)
            apply_guid_backfill(conn, plan, now_ts=2_000_000)
        finally:
            conn.close()

        rows = _notes_rows(fake_anki_db)
        # Every updated row's guid must match plan.updates
        for nid, new_guid in plan.updates.items():
            assert rows[nid][0] == new_guid
            assert rows[nid][1] == 2_000_000

    def test_bumps_notes_mod_only_on_updated_rows(self, fake_anki_db):
        _set_note_guid(fake_anki_db, 1001, compute_guid("banka", "sl"))
        # Seed distinct pre-run mod values
        conn = sqlite3.connect(str(fake_anki_db))
        try:
            for nid in (1001, 1002, 1003, 1004, 1005):
                conn.execute("UPDATE notes SET mod=? WHERE id=?", (500_000, nid))
            conn.commit()
        finally:
            conn.close()

        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            plan = plan_guid_backfill(conn, deck_id=12345, force=True)
            apply_guid_backfill(conn, plan, now_ts=9_000_000)
        finally:
            conn.close()

        rows = _notes_rows(fake_anki_db)
        # 1001 was a noop → mod unchanged
        assert rows[1001][1] == 500_000
        # 1002..1005 were updates → mod bumped
        for nid in (1002, 1003, 1004, 1005):
            assert rows[nid][1] == 9_000_000

    def test_sets_col_usn_minus_one_and_bumps_col_mod(self, fake_anki_db):
        _set_col_fields(fake_anki_db, mod=1_000_000, usn=42)
        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            plan = plan_guid_backfill(conn, deck_id=12345, force=True)
            apply_guid_backfill(conn, plan, now_ts=7_777_777)
        finally:
            conn.close()

        mod, usn = _col_row(fake_anki_db)
        assert usn == -1
        assert mod == 7_777_777

    def test_empty_plan_is_noop(self, fake_anki_db):
        texts = {1001: "banka", 1002: "hiša", 1003: "miza", 1004: "stol", 1005: "knjiga"}
        for nid, text in texts.items():
            _set_note_guid(fake_anki_db, nid, compute_guid(text, "sl"))
        _set_col_fields(fake_anki_db, mod=1_000_000, usn=42)

        conn = sqlite3.connect(str(fake_anki_db))
        conn.row_factory = sqlite3.Row
        try:
            plan = plan_guid_backfill(conn, deck_id=12345, force=False)
            assert plan.updates == {}
            apply_guid_backfill(conn, plan, now_ts=7_777_777)
        finally:
            conn.close()

        # col.mod / col.usn should be unchanged when no updates planned
        mod, usn = _col_row(fake_anki_db)
        assert mod == 1_000_000
        assert usn == 42

    def test_rolls_back_on_sql_error(self, fake_anki_db):
        """If the UPDATE notes executemany fails, ROLLBACK reverts the transaction."""
        _set_col_fields(fake_anki_db, mod=1_000_000, usn=42)

        class SabotagedConn:
            """Forwards execute/close to a real sqlite3 connection but raises on executemany."""

            def __init__(self, inner):
                self._inner = inner

            def execute(self, *args, **kwargs):
                return self._inner.execute(*args, **kwargs)

            def executemany(self, _sql, _params):
                raise sqlite3.OperationalError("sabotaged")

        real = sqlite3.connect(str(fake_anki_db), isolation_level=None)
        real.row_factory = sqlite3.Row
        try:
            plan = plan_guid_backfill(real, deck_id=12345, force=True)
            sab = SabotagedConn(real)
            with pytest.raises(sqlite3.OperationalError, match="sabotaged"):
                apply_guid_backfill(sab, plan, now_ts=2_000_000)
        finally:
            real.close()

        # Rollback: nothing should have changed on disk
        mod, usn = _col_row(fake_anki_db)
        assert mod == 1_000_000
        assert usn == 42
        rows = _notes_rows(fake_anki_db)
        assert rows[1001][0] == "anki_guid_1"


class TestPlanWithModernDecksTable:
    def test_plan_works_with_modern_decks_table(self, tmp_path):
        db_path = build_minimal_anki_db(tmp_path, use_decks_table=True)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            plan = plan_guid_backfill(conn, deck_id=12345, force=True)
        finally:
            conn.close()
        assert len(plan.updates) == 5
