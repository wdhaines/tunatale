"""Tests for Anki sync CLI main() function."""

from __future__ import annotations

from app.anki.sync import main
from app.srs.database import SRSDatabase


class TestMain:
    def test_dry_run_returns_0(self, tmp_path, monkeypatch):
        """main() returns 0 on successful dry run."""
        import sqlite3
        from contextlib import contextmanager

        # Create a fake Anki collection with proper schema
        db_path = tmp_path / "collection.anki2"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("CREATE TABLE col (ver INTEGER, crt INTEGER, decks TEXT)")
            conn.execute("INSERT INTO col VALUES (18, 0, '{}')")
            conn.execute(
                "CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, fields TEXT)"
            )
            conn.execute(
                "CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, ord INTEGER, queue INTEGER, type INTEGER, due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER, lapses INTEGER)"
            )
            conn.commit()

        # Create TunaTale DB
        tt_db = SRSDatabase(":memory:")

        # Mock settings
        class FakeSettings:
            anki_collection_path = str(db_path)
            anki_deck_name = "Test"
            anki_model_name = "Basic"
            sqlite_db_path = ":memory:"

        # Mock safe_open to avoid actual file locking
        @contextmanager
        def fake_safe_open(path, mode):
            conn = sqlite3.connect(str(db_path))
            yield type("Ctx", (), {"conn": conn})()
            conn.close()

        exit_code = main(
            argv=["--dry-run"],
            _settings=FakeSettings(),
            _safe_open_fn=fake_safe_open,
            _db=tt_db,
        )
        assert exit_code == 0

    def test_force_fsrs_without_ack_returns_1(self, tmp_path, monkeypatch):
        """main() returns 1 when --force-fsrs is used without ack."""
        import sqlite3

        db_path = tmp_path / "collection.anki2"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("CREATE TABLE col (ver INTEGER, crt INTEGER)")
            conn.execute("INSERT INTO col VALUES (18, 0)")
            conn.commit()

        class FakeSettings:
            anki_collection_path = str(db_path)
            anki_deck_name = "Test"
            anki_model_name = "Basic"
            database_url = "sqlite:///:memory:"

        exit_code = main(
            argv=["--force-fsrs"],
            _settings=FakeSettings(),
            _force_fsrs_ack_path=tmp_path / "nonexistent_ack.txt",
        )
        assert exit_code == 1

    def test_error_opening_collection_returns_1(self, tmp_path):
        """main() returns 1 when collection cannot be opened."""
        import sqlite3
        from contextlib import contextmanager

        # Create a fake collection that will trigger RuntimeError
        db_path = tmp_path / "collection.anki2"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("CREATE TABLE col (ver INTEGER)")
            conn.commit()

        class FakeSettings:
            anki_collection_path = str(db_path)
            anki_deck_name = "Test"
            anki_model_name = "Basic"
            database_url = "sqlite:///:memory:"

        # Mock safe_open to raise RuntimeError
        @contextmanager
        def fake_safe_open(path, mode):
            raise RuntimeError("Test error")

        exit_code = main(
            argv=[],
            _settings=FakeSettings(),
            _safe_open_fn=fake_safe_open,
        )
        assert exit_code == 1
