"""Tests for the Anki safety envelope (safe_open context manager)."""

import sqlite3
from unittest.mock import patch

import pytest

from app.anki.safety import safe_open


class TestSafeOpen:
    def test_yields_readable_connection(self, fake_anki_db, tmp_path):
        with safe_open(fake_anki_db, backup_dir=tmp_path / "bak") as ctx:
            row = ctx.conn.execute("SELECT COUNT(*) FROM notes").fetchone()
            assert row[0] == 5

    def test_backup_created_in_backup_dir(self, fake_anki_db, tmp_path):
        backup_dir = tmp_path / "bak"
        with safe_open(fake_anki_db, backup_dir=backup_dir) as ctx:
            bak = ctx.backup_path
        assert bak.exists()
        assert bak.parent == backup_dir

    def test_backup_is_valid_sqlite(self, fake_anki_db, tmp_path):
        backup_dir = tmp_path / "bak"
        with safe_open(fake_anki_db, backup_dir=backup_dir) as ctx:
            bak = ctx.backup_path
        conn = sqlite3.connect(str(bak))
        result = conn.execute("PRAGMA integrity_check").fetchone()[0]
        conn.close()
        assert result == "ok"

    def test_uses_connection_backup_not_shutil(self, fake_anki_db, tmp_path):
        """Backup must use Connection.backup(), never shutil.copy2/copyfile."""
        backup_dir = tmp_path / "bak"
        with (
            patch("shutil.copy2") as mock_copy2,
            patch("shutil.copyfile") as mock_copyfile,
            safe_open(fake_anki_db, backup_dir=backup_dir),
        ):
            pass
        mock_copy2.assert_not_called()
        mock_copyfile.assert_not_called()

    def test_source_sha256_unchanged_after_exit(self, fake_anki_db, tmp_path):
        from app.anki.safety import _sha256_file

        pre = _sha256_file(fake_anki_db)
        with safe_open(fake_anki_db, backup_dir=tmp_path / "bak") as ctx:
            assert ctx.source_sha256 == pre
        assert _sha256_file(fake_anki_db) == pre

    def test_readonly_connection_raises_on_insert(self, fake_anki_db, tmp_path):
        with safe_open(fake_anki_db, backup_dir=tmp_path / "bak") as ctx, pytest.raises(sqlite3.OperationalError):
            ctx.conn.execute("INSERT INTO notes VALUES (999,'x',1,0,0,'','x','x',0,0,'')")

    def test_rejects_when_db_is_exclusively_locked(self, fake_anki_db, tmp_path):
        """safe_open must abort with RuntimeError when another conn holds EXCLUSIVE."""
        lock_conn = sqlite3.connect(str(fake_anki_db), timeout=0)
        lock_conn.execute("BEGIN EXCLUSIVE")
        try:
            with (
                pytest.raises(RuntimeError, match="[Ll]ocked|[Rr]unning|[Bb]usy"),
                safe_open(fake_anki_db, backup_dir=tmp_path / "bak"),
            ):
                pass
        finally:
            lock_conn.rollback()
            lock_conn.close()

    def test_uses_settings_backup_dir_when_none_passed(self, fake_anki_db, tmp_path, monkeypatch):
        """When backup_dir=None, safe_open uses settings.anki_backup_dir."""
        from app.anki import safety as safety_mod

        custom_dir = tmp_path / "custom_bak"
        fake_settings = type("S", (), {"anki_backup_dir": custom_dir})()
        monkeypatch.setattr(safety_mod, "settings", fake_settings)
        with safe_open(fake_anki_db) as ctx:
            pass
        assert ctx.backup_path.parent == custom_dir

    def test_sha256_change_raises_not_warns(self, fake_anki_db, tmp_path):
        """SHA256 mismatch on context exit must raise RuntimeError, not just warn."""
        from unittest.mock import patch

        from app.anki import safety

        call_count = [0]

        def patched_sha256(path):
            call_count[0] += 1
            return "aaa" if call_count[0] == 1 else "bbb"

        with (
            pytest.raises(RuntimeError, match="sha256|SHA256|changed|backup"),
            patch.object(safety, "_sha256_file", side_effect=patched_sha256),
            safe_open(fake_anki_db, backup_dir=tmp_path / "bak"),
        ):
            pass

    def test_sha256_mismatch_prints_warning_and_raises(self, fake_anki_db, tmp_path, capsys):
        """SHA256 mismatch prints a warning to stderr AND raises RuntimeError."""
        from unittest.mock import patch

        from app.anki import safety

        call_count = [0]

        def patched_sha256(path):
            call_count[0] += 1
            return "aaa" if call_count[0] == 1 else "bbb"

        with (
            pytest.raises(RuntimeError),
            patch.object(safety, "_sha256_file", side_effect=patched_sha256),
            safe_open(fake_anki_db, backup_dir=tmp_path / "bak"),
        ):
            pass

        captured = capsys.readouterr()
        assert "WARNING" in captured.err or "sha256" in captured.err.lower() or "changed" in captured.err.lower()

    def test_bad_backup_deleted_on_note_count_mismatch(self, tmp_path):
        """_validate_backup deletes backup and raises on note count mismatch."""
        import sqlite3 as sq3

        bak = tmp_path / "bak.anki2"
        conn = sq3.connect(str(bak))
        conn.execute("CREATE TABLE notes (id INTEGER)")
        conn.execute("INSERT INTO notes VALUES (1)")
        conn.commit()
        conn.close()
        from app.anki.safety import _validate_backup

        with pytest.raises((RuntimeError, Exception)):
            _validate_backup(bak, source_note_count=99)  # mismatch: 1 vs 99
        assert not bak.exists()

    def test_bad_backup_deleted_on_bad_integrity_check_result(self, tmp_path):
        """_validate_backup deletes backup and raises when integrity_check returns non-ok."""
        from unittest.mock import MagicMock, patch

        from app.anki import safety as safety_mod
        from app.anki.safety import _validate_backup

        bak = tmp_path / "bak.anki2"
        bak.write_bytes(b"placeholder")

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: s
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchone.return_value = ("malformed",)

        with (
            patch.object(safety_mod.sqlite3, "connect", return_value=mock_conn),
            pytest.raises(RuntimeError, match="integrity_check"),
        ):
            _validate_backup(bak, source_note_count=0)

        assert not bak.exists()

    def test_bad_backup_deleted_on_integrity_failure(self, fake_anki_db, tmp_path):
        """If the backup fails integrity_check, it is deleted and run aborts."""
        from app.anki.safety import _validate_backup

        bad = tmp_path / "bad.anki2"
        bad.write_bytes(b"not sqlite")
        with pytest.raises((RuntimeError, Exception)):
            _validate_backup(bad, source_note_count=5)
        assert not bad.exists()

    def test_opens_real_anki_schema_with_unicase_collation(self, tmp_path):
        """Regression: real Anki collections declare ``COLLATE unicase`` on several tables
        and indexes. Without registering the collation on every connection we open,
        PRAGMA integrity_check and any query against those indexes raises
        ``no such collation sequence: unicase``."""
        db_path = tmp_path / "collection.anki2"
        # Build a DB that looks like real Anki: unicase column + index on it.
        seed = sqlite3.connect(str(db_path))

        def _unicase(a, b):
            af, bf = a.casefold(), b.casefold()
            return (af > bf) - (af < bf)

        seed.create_collation("unicase", _unicase)
        seed.execute(
            "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
            "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT, "
            "dconf TEXT, tags TEXT)"
        )
        seed.execute("INSERT INTO col VALUES (1,0,0,0,11,0,0,0,'{}','{}','{}','{}','{}')")
        seed.execute(
            "CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, "
            "usn INTEGER, tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT)"
        )
        seed.execute("INSERT INTO notes VALUES (1,'g1',0,0,0,'','f\x1ft','',0,0,'')")
        seed.execute("CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT NOT NULL COLLATE unicase)")
        seed.execute("CREATE UNIQUE INDEX idx_decks_name ON decks (name)")
        seed.execute("INSERT INTO decks VALUES (1, 'Default')")
        seed.commit()
        seed.close()

        # Must not raise — safe_open now registers `unicase` on every conn it opens.
        with safe_open(db_path, backup_dir=tmp_path / "bak") as ctx:
            count = ctx.conn.execute("SELECT COUNT(*) FROM decks WHERE name='default'").fetchone()[0]
        assert count == 1  # case-insensitive match proves the collation is active


# ── probe_lock + AnkiRunningError ─────────────────────────────────────────────


class TestProbeLock:
    def test_returns_false_when_lock_acquirable(self, fake_anki_db):
        from app.anki.safety import probe_lock

        assert probe_lock(fake_anki_db) is False

    def test_returns_true_when_db_locked(self, fake_anki_db):
        import sqlite3

        from app.anki.safety import probe_lock

        lock_conn = sqlite3.connect(str(fake_anki_db), timeout=0)
        lock_conn.execute("BEGIN EXCLUSIVE")
        try:
            assert probe_lock(fake_anki_db) is True
        finally:
            lock_conn.rollback()
            lock_conn.close()


def test_anki_running_error_is_runtime_error():
    from app.anki.safety import AnkiRunningError

    err = AnkiRunningError("test")
    assert isinstance(err, RuntimeError)
