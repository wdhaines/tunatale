"""Tests for safe_open(mode='rw') and AnkiContext.audit_changes.

Stage 2b introduces the first write path into the Anki collection. The rw mode
relaxes the post-run SHA256 equality check (the file WILL change by design) but
keeps the lock probe and backup+validation gates. The audit_changes method is
the replacement safety: it diffs backup vs source per-row and rejects any write
the caller didn't plan for.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.plugins.anki_sync.safety import safe_open


class TestSafeOpenRWMode:
    def test_rw_mode_permits_update(self, fake_anki_db, tmp_path):
        with safe_open(fake_anki_db, backup_dir=tmp_path / "bak", mode="rw") as ctx:
            ctx.conn.execute("UPDATE notes SET guid=? WHERE id=?", ("updated_guid", 1001))
            ctx.conn.commit()
            row = ctx.conn.execute("SELECT guid FROM notes WHERE id=?", (1001,)).fetchone()
        assert row[0] == "updated_guid"

    def test_rw_mode_skips_post_run_sha256_check(self, fake_anki_db, tmp_path):
        """Modifying the source in rw mode must NOT raise on context exit."""
        from app.plugins.anki_sync.safety import _sha256_file

        pre = _sha256_file(fake_anki_db)
        with safe_open(fake_anki_db, backup_dir=tmp_path / "bak", mode="rw") as ctx:
            ctx.conn.execute("UPDATE notes SET guid=? WHERE id=?", ("changed", 1001))
            ctx.conn.commit()
        post = _sha256_file(fake_anki_db)
        assert pre != post, "source should actually have been modified"

    def test_ro_mode_is_default(self, fake_anki_db, tmp_path):
        """Omitting mode preserves read-only behavior for all existing callers."""
        with (
            safe_open(fake_anki_db, backup_dir=tmp_path / "bak") as ctx,
            pytest.raises(sqlite3.OperationalError),
        ):
            ctx.conn.execute("UPDATE notes SET guid='x' WHERE id=1001")

    def test_rw_mode_still_enforces_lock_probe(self, fake_anki_db, tmp_path):
        lock_conn = sqlite3.connect(str(fake_anki_db), timeout=0)
        lock_conn.execute("BEGIN EXCLUSIVE")
        try:
            with (
                pytest.raises(RuntimeError, match="[Ll]ocked|[Rr]unning|[Bb]usy"),
                safe_open(fake_anki_db, backup_dir=tmp_path / "bak", mode="rw"),
            ):
                pass
        finally:
            lock_conn.rollback()
            lock_conn.close()

    def test_rw_mode_still_creates_valid_backup(self, fake_anki_db, tmp_path):
        backup_dir = tmp_path / "bak"
        with safe_open(fake_anki_db, backup_dir=backup_dir, mode="rw") as ctx:
            bak = ctx.backup_path
            ctx.conn.execute("UPDATE notes SET guid='x' WHERE id=1001")
            ctx.conn.commit()
        assert bak.exists()
        conn = sqlite3.connect(str(bak))
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            count = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            # Backup should preserve PRE-update state
            guid = conn.execute("SELECT guid FROM notes WHERE id=1001").fetchone()[0]
        finally:
            conn.close()
        assert integrity == "ok"
        assert count == 5
        assert guid == "anki_guid_1"


class TestAuditChanges:
    def test_passes_when_only_planned_rows_changed(self, fake_anki_db, tmp_path):
        with safe_open(fake_anki_db, backup_dir=tmp_path / "bak", mode="rw") as ctx:
            ctx.conn.execute("UPDATE notes SET guid=? WHERE id=?", ("NEW1", 1001))
            ctx.conn.execute("UPDATE notes SET guid=? WHERE id=?", ("NEW2", 1002))
            ctx.conn.commit()
            ctx.audit_changes("notes", "id", "guid", {1001: "NEW1", 1002: "NEW2"})

    def test_raises_on_unplanned_write(self, fake_anki_db, tmp_path):
        with safe_open(fake_anki_db, backup_dir=tmp_path / "bak", mode="rw") as ctx:
            ctx.conn.execute("UPDATE notes SET guid=? WHERE id=?", ("NEW1", 1001))
            ctx.conn.execute("UPDATE notes SET guid=? WHERE id=?", ("ROGUE", 1003))
            ctx.conn.commit()
            with pytest.raises(RuntimeError, match="1003|unplanned|unexpected"):
                ctx.audit_changes("notes", "id", "guid", {1001: "NEW1"})

    def test_raises_on_missing_planned_change(self, fake_anki_db, tmp_path):
        with safe_open(fake_anki_db, backup_dir=tmp_path / "bak", mode="rw") as ctx:
            ctx.conn.execute("UPDATE notes SET guid=? WHERE id=?", ("NEW1", 1001))
            ctx.conn.commit()
            with pytest.raises(RuntimeError, match="1002|missing|not applied"):
                ctx.audit_changes("notes", "id", "guid", {1001: "NEW1", 1002: "NEW2"})

    def test_raises_on_planned_row_with_wrong_value(self, fake_anki_db, tmp_path):
        with safe_open(fake_anki_db, backup_dir=tmp_path / "bak", mode="rw") as ctx:
            ctx.conn.execute("UPDATE notes SET guid=? WHERE id=?", ("WRONG", 1001))
            ctx.conn.commit()
            with pytest.raises(RuntimeError, match="1001|mismatch|wrong|missing"):
                ctx.audit_changes("notes", "id", "guid", {1001: "EXPECTED"})

    def test_passes_when_nothing_changed_and_no_plan(self, fake_anki_db, tmp_path):
        with safe_open(fake_anki_db, backup_dir=tmp_path / "bak", mode="rw") as ctx:
            ctx.audit_changes("notes", "id", "guid", {})

    def test_rejects_unsafe_sql_identifier(self, fake_anki_db, tmp_path):
        """audit_changes must reject identifiers that aren't plain alphanumerics."""
        with (
            safe_open(fake_anki_db, backup_dir=tmp_path / "bak", mode="rw") as ctx,
            pytest.raises(ValueError, match="unsafe SQL identifier"),
        ):
            ctx.audit_changes("notes; DROP TABLE notes--", "id", "guid", {})
