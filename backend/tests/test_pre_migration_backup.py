"""Tests for backup-before-migrate and the schema-too-new refusal.

The deploy story promises "roll back to a previous SHA in one command". That is
true of the image and false of the data: a new build runs `app/srs/migrations.py`
against the existing volume on startup, so rolling the image back afterwards
leaves a NEWER SCHEMA UNDER OLDER CODE. These tests pin the two halves of the
fix — a pre-migration snapshot that the rolling daily rotation cannot age out,
and a refusal to boot older code against a newer database.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

import app.srs.migrations as M
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.srs.db_base import _CREATE_COLLOCATIONS_V0
from app.srs.migrations import CURRENT_VERSION, SchemaTooNewError, migrate
from app.storage.db_backup import rotate_db_backups, snapshot_before_migration


def _version(path: Path) -> int:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _v0_conn() -> sqlite3.Connection:
    """In-memory DB at the v0 base schema, the way ``db_base`` bootstraps one."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(_CREATE_COLLOCATIONS_V0)
    conn.commit()
    return conn


def _make_db(path: Path, marker: str, version: int) -> None:
    """A minimal DB that looks populated to ``_has_anything_to_lose``.

    The row goes in ``collocations`` specifically: that is the table `migrate`
    asks "is there anything here worth snapshotting?", so a fixture using any
    other table would be silently skipped.
    """
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE collocations (text TEXT)")
    conn.execute("INSERT INTO collocations (text) VALUES (?)", (marker,))
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()


def _marker(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT text FROM collocations").fetchone()[0]
    finally:
        conn.close()


class TestSnapshotBeforeMigration:
    def test_writes_a_readable_copy_tagged_with_the_version_left_behind(self, tmp_path: Path) -> None:
        src = tmp_path / "tunatale_sl.db"
        _make_db(src, "slovene-data", 41)
        backup_dir = tmp_path / "pre-migration-backups"

        dest = snapshot_before_migration(src, backup_dir, from_version=41)

        assert dest == backup_dir / "tunatale_sl.pre-v41.db"
        assert _version(dest) == 41
        assert _marker(dest) == "slovene-data"

    def test_first_snapshot_of_a_version_wins_and_is_never_overwritten(self, tmp_path: Path) -> None:
        """A retried migration must not clobber the true pre-migration state.

        If 41→42 fails half-way and the DB is left mutated, the second attempt
        re-enters `migrate` at v41 and would snapshot the ALREADY-DAMAGED file
        over the good one. Earliest wins, exactly as `rotate_db_backups` does
        within a calendar day.
        """
        src = tmp_path / "tunatale_sl.db"
        _make_db(src, "original", 41)
        backup_dir = tmp_path / "pre-migration-backups"
        dest = snapshot_before_migration(src, backup_dir, from_version=41)

        src.unlink()
        _make_db(src, "damaged-by-a-failed-attempt", 41)
        again = snapshot_before_migration(src, backup_dir, from_version=41)

        assert again == dest
        assert _marker(dest) == "original"

    def test_raises_rather_than_migrating_unprotected(self, tmp_path: Path) -> None:
        """Deliberately UNLIKE `rotate_db_backups`, which swallows everything.

        A rolling daily backup that fails must not block startup. A
        pre-migration snapshot that fails must block the migration — proceeding
        would destroy the only copy of the pre-migration state, which is the
        one thing this whole mechanism exists to prevent.
        """
        src = tmp_path / "tunatale_sl.db"
        _make_db(src, "slovene-data", 41)
        blocked = tmp_path / "not-a-dir"
        blocked.write_text("I am a file, not a directory")

        with pytest.raises(OSError):
            snapshot_before_migration(src, blocked / "backups", from_version=41)

    def test_migrate_does_not_swallow_the_failure(self, tmp_path: Path, monkeypatch) -> None:
        """Oracle: no migration path can silently discard the only copy.

        The half that matters is here rather than in the function above — the
        tempting "improvement" is to wrap the call site in the same try/except
        `rotate_db_backups` uses, which would boot happily and migrate
        unprotected. The schema must be untouched after the refusal.
        """
        src = tmp_path / "tunatale_sl.db"
        _make_db(src, "slovene-data", CURRENT_VERSION - 1)
        blocked = tmp_path / "not-a-dir"
        blocked.write_text("I am a file, not a directory")

        ran: list[str] = []
        monkeypatch.setitem(M._MIGRATIONS, CURRENT_VERSION - 1, lambda c: ran.append("migrated"))
        conn = sqlite3.connect(src)
        try:
            with pytest.raises(OSError):
                migrate(conn, db_path=src, pre_migration_backup_dir=blocked / "backups")
        finally:
            conn.close()

        assert ran == []
        assert _version(src) == CURRENT_VERSION - 1


class TestExemptFromRollingRotation:
    def test_rotation_cannot_prune_a_pre_migration_snapshot(self, tmp_path: Path) -> None:
        """Oracle: the pre-migration snapshot survives the rolling rotation.

        Belt and braces. The default dirs differ (`db_backup_dir` vs
        `migration_backup_dir`), but `_prune` used to glob `{stem}.*.db`, which
        matches `tunatale_sl.pre-v41.db` — and since 'p' sorts after any ISO
        date, the pre-migration snapshot would have been counted as the NEWEST
        daily backup, both surviving itself and evicting a real one. Pruning is
        now scoped to date-shaped names, so co-locating the two is harmless.
        """
        shared = tmp_path / "backups"
        shared.mkdir()
        keeper = shared / "tunatale_sl.pre-v41.db"
        _make_db(keeper, "pre-migration", 41)
        for day in range(10, 18):
            _make_db(shared / f"tunatale_sl.2026-08-{day}.db", f"daily-{day}", 42)

        src = tmp_path / "tunatale_sl.db"
        _make_db(src, "live", 42)
        rotate_db_backups([src], shared, keep_days=3, today=date(2026, 8, 18))

        assert keeper.exists()
        dailies = sorted(p.name for p in shared.glob("tunatale_sl.20*.db"))
        assert dailies == [
            "tunatale_sl.2026-08-16.db",
            "tunatale_sl.2026-08-17.db",
            "tunatale_sl.2026-08-18.db",
        ]


class TestMigrateTakesTheSnapshot:
    def test_snapshot_written_before_a_pending_migration_runs(self, tmp_path: Path, monkeypatch) -> None:
        src = tmp_path / "tunatale_sl.db"
        _make_db(src, "slovene-data", CURRENT_VERSION - 1)
        backup_dir = tmp_path / "pre-migration-backups"
        conn = sqlite3.connect(src)
        conn.row_factory = sqlite3.Row

        applied: list[int] = []

        def _noop(c: sqlite3.Connection) -> None:
            # The snapshot must already exist by the time any migration body runs.
            applied.append((backup_dir / f"tunatale_sl.pre-v{CURRENT_VERSION - 1}.db").exists())
            M._set_version(c, CURRENT_VERSION)

        monkeypatch.setitem(M._MIGRATIONS, CURRENT_VERSION - 1, _noop)
        try:
            migrate(conn, db_path=src, pre_migration_backup_dir=backup_dir)
        finally:
            conn.close()

        assert applied == [True]
        assert _version(backup_dir / f"tunatale_sl.pre-v{CURRENT_VERSION - 1}.db") == CURRENT_VERSION - 1

    def test_no_snapshot_for_a_database_with_nothing_to_lose(self, tmp_path: Path) -> None:
        """A freshly bootstrapped DB runs v0 → v42 with no rows in it.

        Snapshotting that preserves nothing and litters a directory that is
        never pruned. Caught for real: the first run of the backend suite wrote
        five empty 20 KB `*.pre-v0.db` files into the developer's actual
        ~/.tunatale, because API tests drive `app.main`'s lifespan against
        throwaway DBs whose stems collide with the live ones.
        """
        db_path = tmp_path / "tunatale_sl.db"
        backup_dir = tmp_path / "pre-migration-backups"
        SRSDatabase(str(db_path), pre_migration_backup_dir=backup_dir)

        assert _version(db_path) == CURRENT_VERSION
        assert not backup_dir.exists()

    def test_snapshot_taken_once_the_database_holds_data(self, tmp_path: Path, monkeypatch) -> None:
        """The mirror of the test above: rows present ⇒ snapshot taken."""
        db_path = tmp_path / "tunatale_sl.db"
        backup_dir = tmp_path / "pre-migration-backups"
        older = CURRENT_VERSION - 1

        monkeypatch.setattr(M, "CURRENT_VERSION", older)
        db = SRSDatabase(str(db_path))
        db.add_collocation(SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus"))
        monkeypatch.undo()

        SRSDatabase(str(db_path), pre_migration_backup_dir=backup_dir)
        assert (backup_dir / f"tunatale_sl.pre-v{older}.db").exists()

    def test_nothing_to_lose_when_the_content_table_is_absent(self) -> None:
        """A DB with no ``collocations`` table at all holds nothing to protect.

        ``migrate_v0_to_v1`` guards on exactly this shape, so the chain already
        contemplates it; asking a table that isn't there must answer "no data"
        rather than raising and taking the boot down with it.
        """
        conn = sqlite3.connect(":memory:")
        try:
            assert M._has_anything_to_lose(conn) is False
            conn.execute("CREATE TABLE collocations (text TEXT)")
            assert M._has_anything_to_lose(conn) is False
            conn.execute("INSERT INTO collocations (text) VALUES ('banka')")
            assert M._has_anything_to_lose(conn) is True
        finally:
            conn.close()

    def test_no_snapshot_when_the_schema_is_already_current(self, tmp_path: Path) -> None:
        """Almost every boot has nothing to do; it must not write a snapshot."""
        src = tmp_path / "tunatale_sl.db"
        _make_db(src, "slovene-data", CURRENT_VERSION)
        backup_dir = tmp_path / "pre-migration-backups"
        conn = sqlite3.connect(src)
        try:
            migrate(conn, db_path=src, pre_migration_backup_dir=backup_dir)
        finally:
            conn.close()

        assert not backup_dir.exists()

    def test_no_snapshot_without_a_configured_dir(self) -> None:
        """In-memory and unit-test callers pass neither argument."""
        conn = _v0_conn()
        try:
            migrate(conn)
            assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION
        finally:
            conn.close()

    def test_no_snapshot_when_only_the_dir_is_given(self, tmp_path: Path) -> None:
        """An in-memory DB has a backup dir but no file to snapshot."""
        backup_dir = tmp_path / "pre-migration-backups"
        conn = _v0_conn()
        try:
            migrate(conn, pre_migration_backup_dir=backup_dir)
            assert conn.execute("PRAGMA user_version").fetchone()[0] == CURRENT_VERSION
        finally:
            conn.close()

        assert not backup_dir.exists()


class TestSchemaTooNew:
    def test_older_code_refuses_a_newer_database(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(f"PRAGMA user_version = {CURRENT_VERSION + 3}")
        try:
            with pytest.raises(SchemaTooNewError) as exc:
                migrate(conn)
        finally:
            conn.close()

        message = str(exc.value)
        # Both versions must be named — "it broke" is not actionable at 3am.
        assert f"v{CURRENT_VERSION + 3}" in message
        assert f"v{CURRENT_VERSION}" in message

    def test_refusal_names_the_snapshot_that_would_fix_it(self, tmp_path: Path) -> None:
        src = tmp_path / "tunatale_sl.db"
        _make_db(src, "slovene-data", CURRENT_VERSION + 1)
        conn = sqlite3.connect(src)
        try:
            with pytest.raises(SchemaTooNewError) as exc:
                migrate(conn, db_path=src)
        finally:
            conn.close()

        assert f"tunatale_sl.pre-v{CURRENT_VERSION}.db" in str(exc.value)

    def test_a_current_or_older_database_is_accepted(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute(f"PRAGMA user_version = {CURRENT_VERSION}")
        try:
            migrate(conn)
        finally:
            conn.close()


class TestReversibilityIsDocumented:
    """The written reversibility statement must not fall behind the code.

    A rollback decision is made from the table in docs/deployment.md, so a
    migration that lands without a row there is a migration nobody can classify
    under pressure. Pinning the table to ``_MIGRATIONS`` makes the doc part of
    the change rather than a follow-up nobody files.
    """

    DOC = Path(__file__).resolve().parents[2] / "docs" / "deployment.md"

    def _rows(self) -> dict[int, str]:
        rows: dict[int, str] = {}
        for line in self.DOC.read_text().splitlines():
            match = re.match(r"\|\s*v(\d+) → v(\d+)\s*\|\s*([^|]+?)\s*\|", line)
            if match:
                assert int(match.group(2)) == int(match.group(1)) + 1, line
                rows[int(match.group(1))] = match.group(3)
        return rows

    def test_every_migration_has_a_row(self) -> None:
        assert set(self._rows()) == set(M._MIGRATIONS)

    def test_every_row_states_a_known_class(self) -> None:
        classes = {c.strip("*") for c in self._rows().values()}
        assert classes == {"Additive", "Backfill", "Destructive"}

    def test_class_counts_match_the_prose(self) -> None:
        """The summary sentence above the table states these three numbers."""
        counts = Counter(c.strip("*") for c in self._rows().values())
        assert counts == {"Additive": 26, "Backfill": 3, "Destructive": 13}
        assert f"{counts['Additive']} of the {len(M._MIGRATIONS)} are additive" in self.DOC.read_text()


class TestRoundTripThroughSRSDatabase:
    def test_restored_snapshot_opens_cleanly_under_the_older_build(self, tmp_path: Path, monkeypatch) -> None:
        """The decisive oracle, end to end against the real migration chain.

        Build a DB with a build that stops at v41, then open it with the real
        build (v42). The 41→42 migration must leave a snapshot that the v41
        build can still open without the refusal firing.
        """
        db_path = tmp_path / "tunatale_sl.db"
        backup_dir = tmp_path / "pre-migration-backups"
        older = CURRENT_VERSION - 1

        monkeypatch.setattr(M, "CURRENT_VERSION", older)
        db = SRSDatabase(str(db_path))
        db.add_collocation(SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus"))
        assert _version(db_path) == older
        monkeypatch.undo()

        SRSDatabase(str(db_path), pre_migration_backup_dir=backup_dir)
        assert _version(db_path) == CURRENT_VERSION

        snapshot = backup_dir / f"tunatale_sl.pre-v{older}.db"
        assert _version(snapshot) == older

        # Roll the code back: the older build opens the restored snapshot.
        monkeypatch.setattr(M, "CURRENT_VERSION", older)
        restored = tmp_path / "restored.db"
        restored.write_bytes(snapshot.read_bytes())
        SRSDatabase(str(restored))
        assert _version(restored) == older

        # ...and refuses the live DB the newer build advanced.
        with pytest.raises(SchemaTooNewError):
            SRSDatabase(str(db_path))

    def test_in_memory_database_needs_no_snapshot(self, tmp_path: Path) -> None:
        backup_dir = tmp_path / "pre-migration-backups"
        SRSDatabase(":memory:", pre_migration_backup_dir=backup_dir)
        assert not backup_dir.exists()
