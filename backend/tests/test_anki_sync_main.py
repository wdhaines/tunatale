"""Tests for Anki sync CLI main() function."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from app.anki.sync import (
    CreateNewReport,
    PullReport,
    PushReport,
    RecomputeDivergence,
    _write_sync_soak_log,
    main,
    run_full_sync,
)
from app.srs.database import SRSDatabase

# The complete phase list run_full_sync must execute on every non-dry sync.
# Pinned here so dropping any one phase from one entry point (the b0a4b8a
# regression: the peer-sync button silently lost create_new + every refresh_*)
# turns a test red instead of shipping a stale-config / unsynced-card sync.
_REFRESH_FUNCS = [
    "refresh_col_crt",
    "refresh_daily_new_cap",
    "refresh_daily_review_cap",
    "refresh_desired_retention",
    "refresh_fsrs_params",
    "refresh_fsrs_short_term_flag",
    "refresh_maximum_review_interval",
    "refresh_review_settings",
    "refresh_learning_steps",
    "refresh_load_balancer_enabled",
    "refresh_easy_days",
    "warn_if_multi_deck_preset",
]


def _patch_all_refreshes(monkeypatch):
    """No-op every deck-config refresh so synthetic in-memory collections (which
    lack a full deck_config schema) can exercise main()/run_full_sync end-to-end
    without a realistic config blob. The refresh phase-list is pinned separately
    by TestRunFullSync — these tests assert other phases."""
    for name in _REFRESH_FUNCS:
        monkeypatch.setattr(f"app.srs.queue_stats.{name}", lambda *a, **k: None)


class TestRunFullSync:
    """run_full_sync is the SINGLE canonical TT↔Anki sync sequence. Both entry
    points (the closed-collection /api/anki/sync endpoint and the peer-sync
    reconcile via main) must delegate to it, so no path can drop a phase."""

    def _make_spy_sync(self, calls):
        from unittest.mock import MagicMock

        sync = MagicMock()
        sync.detect_and_reset_orphans = MagicMock(side_effect=lambda: calls.append("orphans"))

        async def _create(**kwargs):
            calls.append("create")
            return CreateNewReport()

        sync.sync_create_new = _create
        sync.sync_push = MagicMock(side_effect=lambda **kw: (calls.append("push"), PushReport())[1])
        sync.sync_pull = MagicMock(side_effect=lambda **kw: (calls.append("pull"), PullReport())[1])
        return sync

    def _patch_refreshes(self, monkeypatch, recorder):
        for name in _REFRESH_FUNCS:
            monkeypatch.setattr(
                f"app.srs.queue_stats.{name}",
                lambda *a, _n=name, **k: recorder.append(_n),
            )

    async def test_runs_every_phase_in_order_when_not_dry_run(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock

        calls: list[str] = []
        refreshed: list[str] = []
        sync = self._make_spy_sync(calls)
        self._patch_refreshes(monkeypatch, refreshed)
        monkeypatch.setattr("app.anki.sync._write_sync_soak_log", lambda *a, **k: calls.append("soak"))

        db = MagicMock()
        db.get_event_sync_pull_mode.return_value = "new"

        create, push, pull = await run_full_sync(
            sync,
            MagicMock(),
            db,
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
            sync_log_path=tmp_path / "sync.log",
            dry_run=False,
        )

        # Core phases run in the create→push→pull order, soak last.
        assert calls == ["orphans", "create", "push", "pull", "soak"]
        # Every deck-config refresh fired — this is the gap that bit the peer path.
        assert set(refreshed) == set(_REFRESH_FUNCS)
        assert isinstance(create, CreateNewReport)
        assert isinstance(push, PushReport)
        assert isinstance(pull, PullReport)

    async def test_dry_run_skips_refresh_and_soak_but_still_syncs(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock

        calls: list[str] = []
        refreshed: list[str] = []
        sync = self._make_spy_sync(calls)
        self._patch_refreshes(monkeypatch, refreshed)
        monkeypatch.setattr("app.anki.sync._write_sync_soak_log", lambda *a, **k: calls.append("soak"))

        db = MagicMock()
        db.get_event_sync_pull_mode.return_value = "new"

        await run_full_sync(
            sync,
            MagicMock(),
            db,
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
            sync_log_path=tmp_path / "sync.log",
            dry_run=True,
        )

        assert calls == ["orphans", "create", "push", "pull"]
        assert refreshed == []

    async def test_passes_media_fn_and_force_fsrs_through(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock

        captured = {}
        sync = MagicMock()
        sync.detect_and_reset_orphans = MagicMock()

        async def _create(**kwargs):
            captured["media_fn"] = kwargs.get("_media_fn")
            return CreateNewReport()

        sync.sync_create_new = _create
        sync.sync_push = MagicMock(side_effect=lambda **kw: captured.update(force=kw.get("force_fsrs")) or PushReport())
        sync.sync_pull = MagicMock(return_value=PullReport())
        self._patch_refreshes(monkeypatch, [])
        monkeypatch.setattr("app.anki.sync._write_sync_soak_log", lambda *a, **k: None)

        sentinel = object()
        db = MagicMock()
        db.get_event_sync_pull_mode.return_value = "new"

        await run_full_sync(
            sync,
            MagicMock(),
            db,
            deck_name="D",
            model_name="M",
            sync_log_path=tmp_path / "sync.log",
            media_fn=sentinel,
            force_fsrs=True,
            dry_run=False,
        )

        assert captured["media_fn"] is sentinel
        assert captured["force"] is True


class TestMainDelegatesToRunFullSync:
    """main() (the peer-sync reconcile) must route through run_full_sync, not a
    bespoke subset of phases."""

    def test_main_calls_run_full_sync(self, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock

        anki_conn = sqlite3.connect(":memory:")
        anki_conn.execute("CREATE TABLE col (ver INTEGER, crt INTEGER)")
        anki_conn.execute("INSERT INTO col VALUES (18, 0)")
        anki_conn.commit()

        spy = AsyncMock(return_value=(CreateNewReport(), PushReport(), PullReport()))
        monkeypatch.setattr("app.anki.sync.run_full_sync", spy)

        tt_db = SRSDatabase(":memory:")

        class FakeSettings:
            anki_collection_path = "unused"
            anki_deck_name = "0. Slovene"
            anki_model_name = "Slovene Vocabulary"
            database_url = "sqlite:///:memory:"

        @contextmanager
        def fake_safe_open(path, mode):
            yield type("Ctx", (), {"conn": anki_conn})()

        exit_code = main(
            argv=[],
            _settings=FakeSettings(),
            _safe_open_fn=fake_safe_open,
            _sync_log_path=tmp_path / "sync.log",
            _db=tt_db,
        )

        assert exit_code == 0
        assert spy.await_count == 1
        # Default (CLI) call passes no media generator.
        assert spy.await_args.kwargs["media_fn"] is None

    def test_main_forwards_media_fn_and_media_dir(self, tmp_path, monkeypatch):
        """When peer_sync supplies a media generator + media dir, main() threads
        them into run_full_sync / OfflineWriter (so peer-sync'd cards get media)."""
        from unittest.mock import AsyncMock

        anki_conn = sqlite3.connect(":memory:")
        anki_conn.execute("CREATE TABLE col (ver INTEGER, crt INTEGER)")
        anki_conn.execute("INSERT INTO col VALUES (18, 0)")
        anki_conn.commit()

        spy = AsyncMock(return_value=(CreateNewReport(), PushReport(), PullReport()))
        monkeypatch.setattr("app.anki.sync.run_full_sync", spy)
        captured_media_dir = {}
        real_writer = __import__("app.anki.sync", fromlist=["OfflineWriter"]).OfflineWriter

        def _spy_writer(conn, media_dir=None):
            captured_media_dir["v"] = media_dir
            return real_writer(conn, media_dir=media_dir)

        monkeypatch.setattr("app.anki.sync.OfflineWriter", _spy_writer)

        tt_db = SRSDatabase(":memory:")
        sentinel_fn = object()
        media_dir = tmp_path / "collection.media"

        class FakeSettings:
            anki_collection_path = "unused"
            anki_deck_name = "0. Slovene"
            anki_model_name = "Slovene Vocabulary"
            database_url = "sqlite:///:memory:"

        @contextmanager
        def fake_safe_open(path, mode):
            yield type("Ctx", (), {"conn": anki_conn})()

        exit_code = main(
            argv=[],
            _settings=FakeSettings(),
            _safe_open_fn=fake_safe_open,
            _sync_log_path=tmp_path / "sync.log",
            _db=tt_db,
            _media_dir=media_dir,
            _media_fn=sentinel_fn,
        )

        assert exit_code == 0
        assert spy.await_args.kwargs["media_fn"] is sentinel_fn
        assert captured_media_dir["v"] == media_dir


class TestMainOrphanThreshold:
    """main() must return non-zero (not raise) when the orphan-threshold guard
    trips, so peer_sync aborts with a clean PeerSyncError instead of a 500.
    Regression: OrphanThresholdExceededError is a plain Exception, and main()
    only caught RuntimeError — and run_full_sync now runs orphan detection on
    the peer path, exposing it."""

    def test_orphan_threshold_returns_1_not_raises(self, tmp_path, monkeypatch):
        from app.anki.sync import OrphanThresholdExceededError

        anki_conn = sqlite3.connect(":memory:")
        anki_conn.execute("CREATE TABLE col (ver INTEGER, crt INTEGER)")
        anki_conn.execute("INSERT INTO col VALUES (18, 0)")
        anki_conn.commit()
        tt_db = SRSDatabase(":memory:")

        def _raise(self):
            raise OrphanThresholdExceededError("too many orphans — aborting")

        monkeypatch.setattr("app.anki.sync.AnkiSync.detect_and_reset_orphans", _raise)

        class FakeSettings:
            anki_collection_path = "unused"
            anki_deck_name = "0. Slovene"
            anki_model_name = "Slovene Vocabulary"
            database_url = "sqlite:///:memory:"

        @contextmanager
        def fake_safe_open(path, mode):
            yield type("Ctx", (), {"conn": anki_conn})()

        exit_code = main(
            argv=[],
            _settings=FakeSettings(),
            _safe_open_fn=fake_safe_open,
            _sync_log_path=tmp_path / "sync.log",
            _db=tt_db,
        )
        assert exit_code == 1


class TestMainCreateNew:
    """main() (the peer-sync reconcile path) must mint Anki notes for TT
    collocations that have no anki_note_id yet — otherwise TT-originated cards
    never reach Anki (only the legacy /api/anki/sync endpoint ran create_new).
    """

    def _fake_settings(self):
        class FakeSettings:
            anki_collection_path = "unused"
            anki_deck_name = "0. Slovene"
            anki_model_name = "Slovene Vocabulary"
            database_url = "sqlite:///:memory:"

        return FakeSettings()

    def test_main_creates_anki_notes_for_unlinked_collocations(self, tmp_path, monkeypatch):
        """A NEW collocation with anki_note_id IS NULL is linked + minted by main()."""
        from app.models.syntactic_unit import SyntacticUnit
        from tests.test_anki_sync_create_new import _make_dual_collection_conn

        anki_conn = _make_dual_collection_conn()
        tt_db = SRSDatabase(":memory:")
        tt_db.add_collocation(
            SyntacticUnit(text="oprostiti", translation="to excuse", word_count=1, difficulty=1, source="user")
        )
        assert tt_db.get_collocation("oprostiti").anki_note_id is None

        @contextmanager
        def fake_safe_open(path, mode):
            yield type("Ctx", (), {"conn": anki_conn})()

        # Isolate the create-new behavior from the heavy push/pull/refresh machinery.
        monkeypatch.setattr(
            "app.anki.sync.AnkiSync.sync_push",
            lambda self, dry_run=False, force_fsrs=False: PushReport(),
        )
        monkeypatch.setattr(
            "app.anki.sync.AnkiSync.sync_pull",
            lambda self, dry_run=False: PullReport(),
        )
        _patch_all_refreshes(monkeypatch)

        exit_code = main(
            argv=[],
            _settings=self._fake_settings(),
            _safe_open_fn=fake_safe_open,
            _sync_log_path=tmp_path / "sync.log",
            _db=tt_db,
        )

        assert exit_code == 0
        assert tt_db.get_collocation("oprostiti").anki_note_id is not None
        assert len(anki_conn.execute("SELECT id FROM notes").fetchall()) == 1

    def test_main_dry_run_does_not_create_notes(self, tmp_path, monkeypatch):
        """Dry run reports the count but writes no Anki note and leaves TT unlinked."""
        from app.models.syntactic_unit import SyntacticUnit
        from tests.test_anki_sync_create_new import _make_dual_collection_conn

        anki_conn = _make_dual_collection_conn()
        tt_db = SRSDatabase(":memory:")
        tt_db.add_collocation(
            SyntacticUnit(text="oprostiti", translation="to excuse", word_count=1, difficulty=1, source="user")
        )

        @contextmanager
        def fake_safe_open(path, mode):
            yield type("Ctx", (), {"conn": anki_conn})()

        monkeypatch.setattr(
            "app.anki.sync.AnkiSync.sync_push",
            lambda self, dry_run=False, force_fsrs=False: PushReport(),
        )
        monkeypatch.setattr(
            "app.anki.sync.AnkiSync.sync_pull",
            lambda self, dry_run=False: PullReport(),
        )

        exit_code = main(
            argv=["--dry-run"],
            _settings=self._fake_settings(),
            _safe_open_fn=fake_safe_open,
            _sync_log_path=tmp_path / "sync.log",
            _db=tt_db,
        )

        assert exit_code == 0
        assert tt_db.get_collocation("oprostiti").anki_note_id is None
        assert len(anki_conn.execute("SELECT id FROM notes").fetchall()) == 0

    def test_main_discovers_model_name_when_unset(self, tmp_path, monkeypatch):
        """When anki_model_name is empty, main() discovers it (here via the model-name
        cache). _CACHE_PATH is pinned to tmp by conftest, so seed it explicitly rather
        than depending on a real ~/.tunatale/anki_model_name.txt (absent on CI)."""
        import app.anki.model_discovery as md
        from app.models.syntactic_unit import SyntacticUnit
        from tests.test_anki_sync_create_new import _make_dual_collection_conn

        md._CACHE_PATH.write_text("Slovene Vocabulary\n")

        anki_conn = _make_dual_collection_conn()
        tt_db = SRSDatabase(":memory:")
        tt_db.add_collocation(
            SyntacticUnit(text="oprostiti", translation="to excuse", word_count=1, difficulty=1, source="user")
        )

        class FakeSettings:
            anki_collection_path = "unused"
            anki_deck_name = "0. Slovene"
            anki_model_name = ""
            database_url = "sqlite:///:memory:"

        @contextmanager
        def fake_safe_open(path, mode):
            yield type("Ctx", (), {"conn": anki_conn})()

        monkeypatch.setattr(
            "app.anki.sync.AnkiSync.sync_push",
            lambda self, dry_run=False, force_fsrs=False: PushReport(),
        )
        monkeypatch.setattr(
            "app.anki.sync.AnkiSync.sync_pull",
            lambda self, dry_run=False: PullReport(),
        )
        _patch_all_refreshes(monkeypatch)

        exit_code = main(
            argv=[],
            _settings=FakeSettings(),
            _safe_open_fn=fake_safe_open,
            _sync_log_path=tmp_path / "sync.log",
            _db=tt_db,
        )

        assert exit_code == 0
        assert tt_db.get_collocation("oprostiti").anki_note_id is not None


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
        _patch_all_refreshes(monkeypatch)

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
