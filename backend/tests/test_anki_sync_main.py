"""Tests for Anki sync CLI main() function."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from app.anki.sync import (
    CreateNewReport,
    PullReport,
    PushReport,
    RecomputeDivergence,
    _resolve_model_name,
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
    """run_full_sync is the SINGLE canonical TT↔Anki sync sequence. The peer-sync
    reconcile (via main) must delegate to it, so no path can drop a phase."""

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

        create, push, pull, media_report = await run_full_sync(
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
        # No media_dir → media refresh skipped; default dict returned.
        assert media_report == {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}

    async def test_dry_run_skips_refresh_and_soak_but_still_syncs(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock

        calls: list[str] = []
        refreshed: list[str] = []
        sync = self._make_spy_sync(calls)
        self._patch_refreshes(monkeypatch, refreshed)
        monkeypatch.setattr("app.anki.sync._write_sync_soak_log", lambda *a, **k: calls.append("soak"))

        db = MagicMock()

        _, _, _, media_report = await run_full_sync(
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
        assert media_report == {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}

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

        _, _, _, media_report = await run_full_sync(
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
        assert media_report == {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}

    async def test_includes_media_refresh_when_media_dir_set(self, monkeypatch, tmp_path):
        """media_dir=Path triggers the Anki→TT media-refresh phase after pull, before soak."""
        from unittest.mock import MagicMock

        calls: list[str] = []
        sync = self._make_spy_sync(calls)
        self._patch_refreshes(monkeypatch, [])
        monkeypatch.setattr("app.anki.sync._write_sync_soak_log", lambda *a, **k: calls.append("soak"))

        media_spy = MagicMock(
            return_value={"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0},
            side_effect=lambda *a, **k: calls.append("media_refresh"),
        )
        monkeypatch.setattr("app.anki.import_seed.refresh_media_from_conn", media_spy)

        db = MagicMock()

        _, _, _, media_report = await run_full_sync(
            sync,
            MagicMock(),
            db,
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
            sync_log_path=tmp_path / "sync.log",
            media_dir=tmp_path,
            dry_run=False,
        )

        assert calls == ["orphans", "create", "push", "pull", "media_refresh", "soak"]
        media_spy.assert_called_once()
        assert media_spy.call_args.kwargs["deck_name"] == "0. Slovene"

    async def test_skips_media_refresh_when_media_dir_none(self, monkeypatch, tmp_path):
        """media_dir=None (CLI default) skips the media-refresh phase."""
        from unittest.mock import MagicMock

        calls: list[str] = []
        sync = self._make_spy_sync(calls)
        self._patch_refreshes(monkeypatch, [])
        monkeypatch.setattr("app.anki.sync._write_sync_soak_log", lambda *a, **k: calls.append("soak"))

        media_spy = MagicMock()
        monkeypatch.setattr("app.anki.import_seed.refresh_media_from_conn", media_spy)

        db = MagicMock()

        _, _, _, media_report = await run_full_sync(
            sync,
            MagicMock(),
            db,
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
            sync_log_path=tmp_path / "sync.log",
            dry_run=False,
        )

        assert calls == ["orphans", "create", "push", "pull", "soak"]
        media_spy.assert_not_called()
        assert media_report == {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}

    async def test_skips_media_refresh_on_dry_run(self, monkeypatch, tmp_path):
        """dry_run=True skips the media-refresh phase even when media_dir is set."""
        from unittest.mock import MagicMock

        calls: list[str] = []
        sync = self._make_spy_sync(calls)
        self._patch_refreshes(monkeypatch, [])
        monkeypatch.setattr("app.anki.sync._write_sync_soak_log", lambda *a, **k: calls.append("soak"))

        media_spy = MagicMock()
        monkeypatch.setattr("app.anki.import_seed.refresh_media_from_conn", media_spy)

        db = MagicMock()

        _, _, _, media_report = await run_full_sync(
            sync,
            MagicMock(),
            db,
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
            sync_log_path=tmp_path / "sync.log",
            media_dir=tmp_path,
            dry_run=True,
        )

        assert calls == ["orphans", "create", "push", "pull"]
        media_spy.assert_not_called()
        assert media_report == {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}


class TestMainDelegatesToRunFullSync:
    """main() (the peer-sync reconcile) must route through run_full_sync, not a
    bespoke subset of phases."""

    def test_main_calls_run_full_sync(self, tmp_path, monkeypatch):
        from unittest.mock import AsyncMock

        anki_conn = sqlite3.connect(":memory:")
        anki_conn.execute("CREATE TABLE col (ver INTEGER, crt INTEGER)")
        anki_conn.execute("INSERT INTO col VALUES (18, 0)")
        anki_conn.commit()

        spy = AsyncMock(return_value=(CreateNewReport(), PushReport(), PullReport(), {}))
        monkeypatch.setattr("app.anki.sync.run_full_sync", spy)

        tt_db = SRSDatabase(":memory:")

        settings_log = tmp_path / "from_settings" / "sync.log"

        class FakeSettings:
            anki_collection_path = "unused"
            anki_deck_name = "0. Slovene"
            anki_model_name = "Slovene Vocabulary"
            database_url = "sqlite:///:memory:"
            sync_log = settings_log

        @contextmanager
        def fake_safe_open(path, mode):
            yield type("Ctx", (), {"conn": anki_conn})()

        # No _sync_log_path: main() must default the soak-log path from
        # settings.sync_log, NOT a hardcoded ~/.tunatale/logs/sync.log. The
        # hardcoded default ignored the conftest isolation fixture's
        # monkeypatch(settings, "sync_log", tmp), so peer-sync tests (which route
        # through tt_sync_main without _sync_log_path) leaked SYNC_SOAK heartbeats
        # into the user's real production sync.log.
        exit_code = main(
            argv=[],
            _settings=FakeSettings(),
            _safe_open_fn=fake_safe_open,
            _db=tt_db,
        )

        assert exit_code == 0
        assert spy.await_count == 1
        # Default (CLI) call passes no media generator or media dir.
        assert spy.await_args.kwargs["media_fn"] is None
        assert spy.await_args.kwargs["media_dir"] is None
        assert spy.await_args.kwargs["sync_log_path"] == settings_log

    def test_main_forwards_media_fn_and_media_dir(self, tmp_path, monkeypatch):
        """When peer_sync supplies a media generator + media dir, main() threads
        them into run_full_sync / OfflineWriter (so peer-sync'd cards get media)."""
        from unittest.mock import AsyncMock

        anki_conn = sqlite3.connect(":memory:")
        anki_conn.execute("CREATE TABLE col (ver INTEGER, crt INTEGER)")
        anki_conn.execute("INSERT INTO col VALUES (18, 0)")
        anki_conn.commit()

        spy = AsyncMock(
            return_value=(
                CreateNewReport(),
                PushReport(),
                PullReport(),
                {"new_media": 0, "updated_media": 0, "collapsed_media": 0},
            )
        )
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
        assert spy.await_args.kwargs["media_dir"] == media_dir
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

    def test_main_propagates_anki_image_swap_to_tt(self, tmp_path, monkeypatch):
        """Anki→TT: a changed <img> ref on a linked note in tt_collection updates TT's
        media row + copies the new file into backend/media, so an image swapped in
        Anki shows up in TunaTale (the pull-direction media gap)."""
        import app.anki.sync as sync_mod
        from app.models.srs_item import Direction
        from app.models.syntactic_unit import SyntacticUnit
        from tests.test_anki_sync_create_new import _make_dual_collection_conn

        anki_conn = _make_dual_collection_conn()
        note_id = 5555
        # tt_collection note (linked) whose Image field now points at newimg.jpg.
        fields = ["oprostiti", "forgive", "", '<img src="newimg.jpg">', "", "", ""]
        anki_conn.execute(
            "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
            "VALUES (?, 'g-opr', 1000001, 0, 0, '', ?, 'oprostiti', 0, 0, '')",
            (note_id, "\x1f".join(fields)),
        )
        anki_conn.execute("INSERT INTO cards (id, nid, did, ord) VALUES (?, ?, 12345, 0)", (note_id * 10, note_id))
        anki_conn.commit()

        # The new image lives in the (pulled) Anki media dir = main's _media_dir.
        src_media = tmp_path / "collection.media"
        src_media.mkdir()
        (src_media / "newimg.jpg").write_bytes(b"NEWIMAGE")

        tt_db = SRSDatabase(":memory:")
        tt_db.add_collocation(
            SyntacticUnit(text="oprostiti", translation="forgive", word_count=1, difficulty=1, source="user")
        )
        guid = tt_db.get_collocation("oprostiti").guid
        coll_id = tt_db.get_collocation_id_by_guid(guid)
        tt_db.set_anki_ids(guid, note_id, {Direction.RECOGNITION: note_id * 10})
        # Stale TT image row (the old image) — should be replaced by the swap.
        tt_db.add_media(coll_id, "image", "oldimg.jpg", "media/oldimg.jpg", "oldimg.jpg", "oldsha", 3)

        class FakeSettings:
            anki_collection_path = "unused"
            anki_deck_name = "0. Slovene"
            anki_model_name = "Slovene Vocabulary"
            database_url = "sqlite:///:memory:"

        @contextmanager
        def fake_safe_open(path, mode):
            yield type("Ctx", (), {"conn": anki_conn})()

        monkeypatch.setattr(
            "app.anki.sync.AnkiSync.sync_push", lambda self, dry_run=False, force_fsrs=False: PushReport()
        )
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_pull", lambda self, dry_run=False: PullReport())
        _patch_all_refreshes(monkeypatch)

        exit_code = main(
            argv=[],
            _settings=FakeSettings(),
            _safe_open_fn=fake_safe_open,
            _sync_log_path=tmp_path / "sync.log",
            _db=tt_db,
            _media_dir=src_media,
        )

        assert exit_code == 0
        # TT media row now points at the swapped image, and the file is in backend/media.
        assert tt_db.get_image_filename(coll_id) == "newimg.jpg"
        assert (sync_mod._MEDIA_DIR / "newimg.jpg").read_bytes() == b"NEWIMAGE"

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
        """Discovery is the third-tier model_name fallback (after the anki_model_name
        override and the active language's configured vocab notetype). To exercise it,
        target_language is a code with no configured vocab notetype, so neither of the
        first two tiers fires and main() discovers the model via the cache. _CACHE_PATH
        is pinned to tmp by conftest, so seed it explicitly."""
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
            target_language = "zz"  # no configured vocab notetype → discovery fallback fires
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
            sync_log = tmp_path / "sync.log"

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
            sync_log = tmp_path / "sync.log"

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

        _write_sync_soak_log(log_path, pull=pull, push=push)

        text = log_path.read_text()
        assert "SYNC_SOAK pull_notes=2" in text
        assert "pull_notes=2 pull_dirs=5 conflicts=0 recompute_divergences=1" in text
        assert "push_notes=1 push_dirs=3" in text
        assert "RECOMPUTE_DIVERGENCE cid=785 dir=production" in text
        assert "replay_s=11.9706 anki_s=2.5138 replay_d=7.3830 anki_d=7.3830" in text

    def test_write_sync_soak_log_appends(self, tmp_path):
        """Two syncs append two heartbeats (the soak is a growing timeline)."""
        log_path = tmp_path / "sync.log"
        pull = PullReport()
        push = PushReport()
        _write_sync_soak_log(log_path, pull=pull, push=push)
        _write_sync_soak_log(log_path, pull=pull, push=push)
        assert log_path.read_text().count("SYNC_SOAK") == 2

    def test_write_sync_soak_log_emits_invariant_trace(self, tmp_path, srs_db):
        """When the TT db is supplied, a direction row that breaks a column
        invariant (bury_kind set on a non-buried row) produces an INVARIANT_TRACE
        line; a clean DB produces none."""
        from app.models.syntactic_unit import SyntacticUnit

        srs_db.add_collocation(
            SyntacticUnit(text="proba", translation="test", word_count=1, difficulty=1, source="corpus"),
            language_code="sl",
        )
        log_path = tmp_path / "sync.log"
        # Clean DB: exercises the sweep with no violations.
        _write_sync_soak_log(log_path, pull=PullReport(), push=PushReport(), db=srs_db)
        assert "INVARIANT_TRACE" not in log_path.read_text()
        # Seed a coupling violation + a non-null prior_state (both sweep branches).
        with srs_db._get_conn() as conn:
            conn.execute("UPDATE collocation_directions SET bury_kind='sched' WHERE direction='recognition'")
            conn.execute("UPDATE collocation_directions SET prior_state='review' WHERE direction='production'")
        _write_sync_soak_log(log_path, pull=PullReport(), push=PushReport(), db=srs_db)
        text = log_path.read_text()
        assert "INVARIANT_TRACE" in text
        assert "bury_kind" in text

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
        # 0 divergences => clean heartbeat.
        assert "SYNC_SOAK pull_notes=" in text
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


class TestResolveModelName:
    """Notetype resolution for TT-originated cards (per-language vocab notetype)."""

    class _S:
        target_language = "no"
        anki_deck_name = "0. 6000 Most Frequent Norwegian Words [Part 1]"
        anki_model_name = ""
        database_urls = {"sl": "sqlite:///./tunatale_sl.db", "no": "sqlite:///./tunatale_no.db"}

    def test_resolve_model_name_prefers_language_vocab_notetype(self):
        conn = sqlite3.connect(":memory:")
        assert _resolve_model_name(self._S(), "no", conn, "deck") == "Norwegian Vocabulary"
        assert _resolve_model_name(self._S(), "sl", conn, "deck") == "Slovene Vocabulary"
