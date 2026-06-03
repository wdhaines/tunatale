"""Tests for Anki sync CLI main() function."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from app.anki.sync import (
    PullReport,
    PushReport,
    RecomputeDivergence,
    _write_sync_soak_log,
    main,
)
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


class TestSyncSoakLog:
    def test_write_sync_soak_log_summary_and_detail(self, tmp_path):
        """_write_sync_soak_log emits one SYNC_SOAK heartbeat + one detail line
        per recompute divergence, and creates the parent dir."""
        log_path = tmp_path / "nested" / "sync.log"
        pull = PullReport(
            notes_updated=2,
            directions_updated=5,
            recompute_divergences=[
                RecomputeDivergence(
                    collocation_id=785,
                    direction="production",
                    replay_stability=11.9706,
                    replay_difficulty=7.383,
                    anki_stability=2.5138,
                    anki_difficulty=7.383,
                )
            ],
        )
        push = PushReport(notes_pushed=1, directions_pushed=3)

        _write_sync_soak_log(log_path, event_mode="new", pull=pull, push=push)

        text = log_path.read_text()
        assert "SYNC_SOAK mode=new" in text
        assert "pull_notes=2 pull_dirs=5 conflicts=0 recompute_divergences=1" in text
        assert "push_notes=1 push_dirs=3" in text
        assert "RECOMPUTE_DIVERGENCE cid=785 dir=production" in text
        assert "replay_s=11.9706 anki_s=2.5138 replay_d=7.3830 anki_d=7.3830" in text

    def test_write_sync_soak_log_appends(self, tmp_path):
        """Two syncs append two heartbeats (the soak is a growing timeline)."""
        log_path = tmp_path / "sync.log"
        pull = PullReport()
        push = PushReport()
        _write_sync_soak_log(log_path, event_mode="new", pull=pull, push=push)
        _write_sync_soak_log(log_path, event_mode="new", pull=pull, push=push)
        assert log_path.read_text().count("SYNC_SOAK") == 2

    def test_non_dry_run_writes_soak_log(self, tmp_path, monkeypatch):
        """A non-dry CLI sync persists a SYNC_SOAK heartbeat to the injected path."""
        db_path = tmp_path / "collection.anki2"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("CREATE TABLE col (ver INTEGER, crt INTEGER, decks TEXT)")
            conn.execute("INSERT INTO col VALUES (18, 0, '{}')")
            conn.commit()

        tt_db = SRSDatabase(":memory:")

        class FakeSettings:
            anki_collection_path = str(db_path)
            anki_deck_name = "Test"
            anki_model_name = "Basic"

        @contextmanager
        def fake_safe_open(path, mode):
            conn = sqlite3.connect(str(db_path))
            yield type("Ctx", (), {"conn": conn})()
            conn.close()

        monkeypatch.setattr(
            "app.anki.sync.AnkiSync.sync_push",
            lambda self, dry_run=False, force_fsrs=False: PushReport(directions_pushed=3),
        )
        monkeypatch.setattr(
            "app.anki.sync.AnkiSync.sync_pull",
            lambda self, dry_run=False: PullReport(directions_updated=4),
        )

        log_path = tmp_path / "logs" / "sync.log"
        exit_code = main(
            argv=[],
            _settings=FakeSettings(),
            _safe_open_fn=fake_safe_open,
            _sync_log_path=log_path,
            _db=tt_db,
        )

        assert exit_code == 0
        text = log_path.read_text()
        # :memory: db defaults to legacy mode; 0 divergences => clean heartbeat.
        assert "SYNC_SOAK mode=legacy" in text
        assert "recompute_divergences=0" in text
        assert "pull_dirs=4 conflicts=0" in text

    def test_dry_run_skips_soak_log(self, tmp_path, monkeypatch):
        """A dry run leaves no soak artifact (mirrors 'dry_run writes nothing')."""
        db_path = tmp_path / "collection.anki2"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("CREATE TABLE col (ver INTEGER, crt INTEGER, decks TEXT)")
            conn.execute("INSERT INTO col VALUES (18, 0, '{}')")
            conn.commit()

        tt_db = SRSDatabase(":memory:")

        class FakeSettings:
            anki_collection_path = str(db_path)
            anki_deck_name = "Test"
            anki_model_name = "Basic"

        @contextmanager
        def fake_safe_open(path, mode):
            conn = sqlite3.connect(str(db_path))
            yield type("Ctx", (), {"conn": conn})()
            conn.close()

        monkeypatch.setattr(
            "app.anki.sync.AnkiSync.sync_push",
            lambda self, dry_run=False, force_fsrs=False: PushReport(),
        )
        monkeypatch.setattr(
            "app.anki.sync.AnkiSync.sync_pull",
            lambda self, dry_run=False: PullReport(),
        )

        log_path = tmp_path / "logs" / "sync.log"
        exit_code = main(
            argv=["--dry-run"],
            _settings=FakeSettings(),
            _safe_open_fn=fake_safe_open,
            _sync_log_path=log_path,
            _db=tt_db,
        )

        assert exit_code == 0
        assert not log_path.exists()
