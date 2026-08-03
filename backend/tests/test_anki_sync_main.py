"""Tests for Anki sync CLI main() function."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

import pytest

from app.plugins.anki_sync.sync import (
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
    "refresh_new_cards_ignore_review_limit",
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
        sync.warn_if_guid_collisions = MagicMock(side_effect=lambda: (calls.append("guid_collisions"), 0)[1])
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

    def _importable_media_setup(self, tmp_path):
        """A collection + TT db + media dir wired so the REAL media-refresh phase
        has exactly one file to import.

        Returned so each media test can assert on the phase's actual effect
        (``new_media``) instead of on a mock's call record: with this setup a run
        that executes the phase reports ``new_media == 1``, and a run that skips
        it reports 0. That difference is the evidence — no ``patch("app.…")``.
        """
        from app.models.srs_item import Direction
        from app.models.syntactic_unit import SyntacticUnit
        from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

        conn = _make_dual_collection_conn()
        note_id = 4242
        fields = ["voda", "water", "", '<img src="voda.jpg">', "", "", ""]
        conn.execute(
            "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
            "VALUES (?, 'g-voda', 1000001, 0, 0, '', ?, 'voda', 0, 0, '')",
            (note_id, "\x1f".join(fields)),
        )
        conn.execute("INSERT INTO cards (id, nid, did, ord) VALUES (?, ?, 12345, 0)", (note_id * 10, note_id))
        conn.commit()

        anki_media = tmp_path / "collection.media"
        anki_media.mkdir()
        (anki_media / "voda.jpg").write_bytes(b"VODAIMAGE")

        db = SRSDatabase(":memory:")
        db.add_collocation(
            SyntacticUnit(text="voda", translation="water", word_count=1, difficulty=1, source="corpus"),
            language_code="sl",
        )
        guid = db.get_collocation("voda").guid
        db.set_anki_ids(guid, note_id, {Direction.RECOGNITION: note_id * 10})
        return conn, db, anki_media

    async def test_runs_every_phase_in_order_when_not_dry_run(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock

        calls: list[str] = []
        refreshed: list[str] = []
        sync = self._make_spy_sync(calls)
        self._patch_refreshes(monkeypatch, refreshed)

        soak_log = tmp_path / "sync.log"
        db = SRSDatabase(":memory:")

        create, push, pull, media_report = await run_full_sync(
            sync,
            MagicMock(),
            db,
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
            sync_log_path=soak_log,
            dry_run=False,
        )

        # Core phases run in the create→push→pull order, soak last.
        assert calls == ["guid_collisions", "orphans", "create", "push", "pull"]
        # The soak heartbeat is the real file the phase writes, not a mock marker.
        assert "SYNC_SOAK" in soak_log.read_text()
        # Every deck-config refresh fired — this is the gap that bit the peer path.
        assert set(refreshed) == set(_REFRESH_FUNCS)
        assert isinstance(create, CreateNewReport)
        assert isinstance(push, PushReport)
        assert isinstance(pull, PullReport)
        # No media_dir → media refresh skipped; default dict returned.
        assert media_report == {
            "new_media": 0,
            "updated_media": 0,
            "unchanged_media": 0,
            "collapsed_media": 0,
            "image_fetch_failed": 0,
        }

    async def test_dry_run_skips_refresh_and_soak_but_still_syncs(self, monkeypatch, tmp_path):
        from unittest.mock import MagicMock

        calls: list[str] = []
        refreshed: list[str] = []
        sync = self._make_spy_sync(calls)
        self._patch_refreshes(monkeypatch, refreshed)

        soak_log = tmp_path / "sync.log"
        db = SRSDatabase(":memory:")

        _, _, _, media_report = await run_full_sync(
            sync,
            MagicMock(),
            db,
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
            sync_log_path=soak_log,
            dry_run=True,
        )

        assert calls == ["guid_collisions", "orphans", "create", "push", "pull"]
        # dry_run writes no soak artifact at all.
        assert not soak_log.exists()
        assert refreshed == []
        assert media_report == {
            "new_media": 0,
            "updated_media": 0,
            "unchanged_media": 0,
            "collapsed_media": 0,
            "image_fetch_failed": 0,
        }

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

        sentinel = object()
        soak_log = tmp_path / "sync.log"
        db = SRSDatabase(":memory:")

        _, _, _, media_report = await run_full_sync(
            sync,
            MagicMock(),
            db,
            deck_name="D",
            model_name="M",
            sync_log_path=soak_log,
            media_fn=sentinel,
            force_fsrs=True,
            dry_run=False,
        )

        assert captured["media_fn"] is sentinel
        assert captured["force"] is True
        assert media_report == {
            "new_media": 0,
            "updated_media": 0,
            "unchanged_media": 0,
            "collapsed_media": 0,
            "image_fetch_failed": 0,
        }

    async def test_includes_media_refresh_when_media_dir_set(self, monkeypatch, tmp_path):
        """media_dir=Path runs the Anki→TT media-refresh phase for real.

        Evidence is the phase's effect — a file copied into TT's media dir and a
        ``new_media`` count — not a mock's call record. Also pins that
        ``image_fetch_failed`` survives the wholesale reassignment of
        ``media_report`` to ``refresh_media_from_conn``'s dict: the real function
        genuinely returns no image key, so the merge in ``run_full_sync`` is what
        keeps the create report's ``image_failed`` in the final report. (The old
        version faked that missing key in a stub; now it is the real contract.)

        The media/soak *relative* order is deliberately not asserted here — with
        both phases real there is no non-mock observable for it. The ordering that
        the b0a4b8a class is about (orphans→create→push→pull) still is.
        """

        calls: list[str] = []
        sync = self._make_spy_sync(calls)

        async def _create_with_failures(**kwargs):
            calls.append("create")
            return CreateNewReport(image_failed=3)

        sync.sync_create_new = _create_with_failures
        self._patch_refreshes(monkeypatch, [])

        conn, db, anki_media = self._importable_media_setup(tmp_path)
        # media_dir is the SOURCE (where the pulled Anki media lives); the dest is
        # the module path constant, pinned to tmp (allowlisted path-constant pin).
        tt_media = tmp_path / "tt_media"
        tt_media.mkdir()
        monkeypatch.setattr("app.plugins.anki_sync.sync._MEDIA_DIR", tt_media)
        soak_log = tmp_path / "sync.log"

        _, _, _, media_report = await run_full_sync(
            sync,
            conn,
            db,
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
            sync_log_path=soak_log,
            media_dir=anki_media,
            dry_run=False,
        )

        assert calls == ["guid_collisions", "orphans", "create", "push", "pull"]
        # The phase ran: the image is now in TT's media dir with the right bytes.
        assert media_report["new_media"] == 1
        assert (tt_media / "voda.jpg").read_bytes() == b"VODAIMAGE"
        # image_fetch_failed survives refresh_media_from_conn's reassignment.
        assert media_report["image_fetch_failed"] == 3
        assert "SYNC_SOAK" in soak_log.read_text()

    async def test_skips_media_refresh_when_media_dir_none(self, monkeypatch, tmp_path):
        """media_dir=None (CLI default) skips the media-refresh phase.

        The setup has an importable image, so ``new_media == 0`` and an empty TT
        media dir are positive evidence the phase did NOT run — not merely the
        absence of a mock call.
        """
        calls: list[str] = []
        sync = self._make_spy_sync(calls)
        self._patch_refreshes(monkeypatch, [])

        conn, db, anki_media = self._importable_media_setup(tmp_path)
        # media_dir is the SOURCE (where the pulled Anki media lives); the dest is
        # the module path constant, pinned to tmp (allowlisted path-constant pin).
        tt_media = tmp_path / "tt_media"
        tt_media.mkdir()
        monkeypatch.setattr("app.plugins.anki_sync.sync._MEDIA_DIR", tt_media)
        soak_log = tmp_path / "sync.log"

        _, _, _, media_report = await run_full_sync(
            sync,
            conn,
            db,
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
            sync_log_path=soak_log,
            dry_run=False,
        )

        assert calls == ["guid_collisions", "orphans", "create", "push", "pull"]
        assert list(tt_media.iterdir()) == []
        assert media_report == {
            "new_media": 0,
            "updated_media": 0,
            "unchanged_media": 0,
            "collapsed_media": 0,
            "image_fetch_failed": 0,
        }

    async def test_skips_media_refresh_on_dry_run(self, monkeypatch, tmp_path):
        """dry_run=True skips the media-refresh phase even when media_dir is set.

        Same positive evidence as above: the importable image stays unimported.
        """
        calls: list[str] = []
        sync = self._make_spy_sync(calls)
        self._patch_refreshes(monkeypatch, [])

        conn, db, anki_media = self._importable_media_setup(tmp_path)
        # media_dir is the SOURCE (where the pulled Anki media lives); the dest is
        # the module path constant, pinned to tmp (allowlisted path-constant pin).
        tt_media = tmp_path / "tt_media"
        tt_media.mkdir()
        monkeypatch.setattr("app.plugins.anki_sync.sync._MEDIA_DIR", tt_media)
        soak_log = tmp_path / "sync.log"

        _, _, _, media_report = await run_full_sync(
            sync,
            conn,
            db,
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
            sync_log_path=soak_log,
            media_dir=anki_media,
            dry_run=True,
        )

        assert calls == ["guid_collisions", "orphans", "create", "push", "pull"]
        assert list(tt_media.iterdir()) == []
        assert media_report == {
            "new_media": 0,
            "updated_media": 0,
            "unchanged_media": 0,
            "collapsed_media": 0,
            "image_fetch_failed": 0,
        }


class TestMainDelegatesToRunFullSync:
    """main() (the peer-sync reconcile) must route through run_full_sync, not a
    bespoke subset of phases."""

    def test_main_defaults_the_soak_log_path_from_settings(self, tmp_path, monkeypatch):
        """No ``_sync_log_path``: main() must default the soak-log path from
        ``settings.sync_log``, NOT a hardcoded ``~/.tunatale/logs/sync.log``.

        The hardcoded default ignored the conftest isolation fixture's
        ``monkeypatch(settings, "sync_log", tmp)``, so peer-sync tests (which route
        through tt_sync_main without ``_sync_log_path``) leaked SYNC_SOAK
        heartbeats into the user's real production sync.log.

        The evidence is the heartbeat file appearing at that path after a REAL
        sync. Asserting ``spy.await_args.kwargs["sync_log_path"]`` — as this used
        to — could not have caught a run_full_sync that accepted the argument and
        wrote somewhere else, which is precisely the bug's shape.
        """
        from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

        anki_conn = _make_dual_collection_conn()
        tt_db = SRSDatabase(":memory:")
        settings_log = tmp_path / "from_settings" / "sync.log"

        class FakeSettings:
            anki_collection_path = "unused"
            anki_deck_name = "0. Slovene"
            anki_model_name = "Slovene Vocabulary"
            target_language = "sl"
            database_url = "sqlite:///:memory:"
            sync_log = settings_log

        @contextmanager
        def fake_safe_open(path, mode):
            yield type("Ctx", (), {"conn": anki_conn})()

        _patch_all_refreshes(monkeypatch)
        exit_code = main(
            argv=[],
            _settings=FakeSettings(),
            _safe_open_fn=fake_safe_open,
            _db=tt_db,
        )

        assert exit_code == 0
        # The nested parent dir was created and the heartbeat landed there.
        assert "SYNC_SOAK" in settings_log.read_text()

    def test_main_forwards_media_fn_and_media_dir(self, tmp_path, monkeypatch):
        """When peer_sync supplies a media generator + media dir, main() threads
        them into run_full_sync / OfflineWriter (so peer-sync'd cards get media).

        Both seams are proven by ONE outcome: the generated audio bytes land in
        ``_media_dir``. That can only happen if ``_media_fn`` reached
        ``sync_create_new`` AND ``_media_dir`` reached the ``OfflineWriter`` that
        writes the file (``store_media_file`` is a no-op when its media_dir is
        None). The previous version mocked run_full_sync and wrapped OfflineWriter
        in a spy, so it checked that main() *passed* the arguments — never that
        anything downstream used them.
        """
        from app.cards.media.pipeline import MediaResult
        from app.models.syntactic_unit import SyntacticUnit
        from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

        anki_conn = _make_dual_collection_conn()
        tt_db = SRSDatabase(":memory:")
        # An unlinked collocation, so sync_create_new mints it and asks for media.
        tt_db.add_collocation(
            SyntacticUnit(text="voda", translation="water", word_count=1, difficulty=1, source="user"),
            language_code="sl",
        )

        media_calls: list[str] = []

        async def _media_fn(word, english, *, used_image_urls, source_sentence="", grammar=""):
            media_calls.append(word)
            return MediaResult(audio_bytes=b"AUDIOBYTES", audio_source="tts")

        media_dir = tmp_path / "collection.media"
        media_dir.mkdir()

        class FakeSettings:
            anki_collection_path = "unused"
            anki_deck_name = "0. Slovene"
            anki_model_name = "Slovene Vocabulary"
            target_language = "sl"
            database_url = "sqlite:///:memory:"

        @contextmanager
        def fake_safe_open(path, mode):
            yield type("Ctx", (), {"conn": anki_conn})()

        _patch_all_refreshes(monkeypatch)
        monkeypatch.setattr("app.plugins.anki_sync.sync._MEDIA_DIR", tmp_path / "tt_media")
        (tmp_path / "tt_media").mkdir()

        exit_code = main(
            argv=[],
            _settings=FakeSettings(),
            _safe_open_fn=fake_safe_open,
            _sync_log_path=tmp_path / "sync.log",
            _db=tt_db,
            _media_dir=media_dir,
            _media_fn=_media_fn,
        )

        assert exit_code == 0
        # media_fn reached sync_create_new…
        assert media_calls == ["voda"]
        # …and media_dir reached the OfflineWriter that wrote the bytes.
        written = list(media_dir.glob("*.mp3"))
        assert len(written) == 1
        assert written[0].read_bytes() == b"AUDIOBYTES"


class TestMainOrphanThreshold:
    """main() must return non-zero (not raise) when the orphan-threshold guard
    trips, so peer_sync aborts with a clean PeerSyncError instead of a 500.
    Regression: OrphanThresholdExceededError is a plain Exception, and main()
    only caught RuntimeError — and run_full_sync now runs orphan detection on
    the peer path, exposing it."""

    def test_orphan_threshold_returns_1_not_raises(self, tmp_path, monkeypatch):
        """The REAL orphan guard trips and main() converts it to exit 1.

        Previously ``detect_and_reset_orphans`` was replaced with a stub that
        raised, so this proved only that main() catches the exception type — the
        guard's own trigger condition (``orphan_count / len(tt_card_ids) > 0.25``)
        was never exercised from here. Now a TT direction points at a card id
        that does not exist in the collection: 1 orphan of 1 tracked card = 100%,
        and the real guard raises.
        """
        from app.models.srs_item import Direction
        from app.models.syntactic_unit import SyntacticUnit
        from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

        # Collection has the deck but no cards at all → every TT pointer is dead.
        anki_conn = _make_dual_collection_conn()
        tt_db = SRSDatabase(":memory:")
        tt_db.add_collocation(
            SyntacticUnit(text="oprostiti", translation="to excuse", word_count=1, difficulty=1, source="user"),
            language_code="sl",
        )
        guid = tt_db.get_collocation("oprostiti").guid
        tt_db.set_anki_ids(guid, 999001, {Direction.RECOGNITION: 9990010})
        assert tt_db.list_anki_card_ids() == {9990010}

        class FakeSettings:
            anki_collection_path = "unused"
            anki_deck_name = "0. Slovene"
            anki_model_name = "Slovene Vocabulary"
            target_language = "sl"
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
            target_language = "sl"
            database_url = "sqlite:///:memory:"

        return FakeSettings()

    def test_main_creates_anki_notes_for_unlinked_collocations(self, tmp_path, monkeypatch):
        """A NEW collocation with anki_note_id IS NULL is linked + minted by main()."""
        from app.models.syntactic_unit import SyntacticUnit
        from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

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
        import app.plugins.anki_sync.sync as sync_mod
        from app.models.srs_item import Direction
        from app.models.syntactic_unit import SyntacticUnit
        from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

        anki_conn = _make_dual_collection_conn()
        note_id = 5555
        # tt_collection note (linked) whose Image field now points at newimg.jpg.
        fields = ["oprostiti", "forgive", "", '<img src="newimg.jpg">', "", "", ""]
        anki_conn.execute(
            "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
            "VALUES (?, 'g-opr', 1000001, 0, 0, '', ?, 'oprostiti', 0, 0, '')",
            (note_id, "\x1f".join(fields)),
        )
        # Full column set, as real Anki always writes. The partial 4-column INSERT
        # this replaced left reps/lapses/type/queue NULL, which real sync_pull reads
        # straight into DirectionState and then fails to persist (NOT NULL on
        # collocation_directions.reps). It went unnoticed because sync_pull was
        # mocked out here — the pull path this test is named for never ran.
        anki_conn.execute(
            "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, "
            "factor, reps, lapses, left, odue, odid, flags, data) "
            "VALUES (?, ?, 12345, 0, 0, -1, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (note_id * 10, note_id),
        )
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
            target_language = "sl"
            database_url = "sqlite:///:memory:"

        @contextmanager
        def fake_safe_open(path, mode):
            yield type("Ctx", (), {"conn": anki_conn})()

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
        from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

        anki_conn = _make_dual_collection_conn()
        tt_db = SRSDatabase(":memory:")
        tt_db.add_collocation(
            SyntacticUnit(text="oprostiti", translation="to excuse", word_count=1, difficulty=1, source="user")
        )

        @contextmanager
        def fake_safe_open(path, mode):
            yield type("Ctx", (), {"conn": anki_conn})()

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
        import app.plugins.anki_sync.model_discovery as md
        from app.models.syntactic_unit import SyntacticUnit
        from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

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
            target_language = "sl"
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
            target_language = "sl"
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
        """A non-dry CLI sync persists a SYNC_SOAK heartbeat whose counts are the
        REAL pull's.

        This used to assert ``pull_dirs=4`` — a number that existed only because
        ``sync_pull`` was mocked to return ``PullReport(directions_updated=4)``.
        The heartbeat is the soak's health signal, so a fabricated count made the
        one test that reads it prove nothing. Now a genuinely-divergent Anki row
        drives the count, and the assertion is derived from the sync's own report.
        """
        from app.models.srs_item import Direction
        from app.models.syntactic_unit import SyntacticUnit
        from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

        anki_conn = _make_dual_collection_conn()
        note_id = 7777
        anki_conn.execute(
            "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
            "VALUES (?, 'g-opr', 1000001, 0, 0, '', ?, 'oprostiti', 0, 0, '')",
            (note_id, "\x1f".join(["oprostiti", "to excuse", "", "", "", "", ""])),
        )
        # A REVIEW card with real FSRS memory state — TT's linked direction is NEW,
        # so _direction_differs fires and sync_pull actually writes.
        anki_conn.execute(
            "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, "
            "factor, reps, lapses, left, odue, odid, flags, data) "
            "VALUES (?, ?, 12345, 0, 0, 0, 2, 2, 100, 21, 2500, 5, 1, 0, 0, 0, 0, ?)",
            (note_id * 10, note_id, '{"s": 21.5, "d": 5.2}'),
        )
        anki_conn.commit()

        tt_db = SRSDatabase(":memory:")
        tt_db.add_collocation(
            SyntacticUnit(text="oprostiti", translation="to excuse", word_count=1, difficulty=1, source="user"),
            language_code="sl",
        )
        guid = tt_db.get_collocation("oprostiti").guid
        tt_db.set_anki_ids(guid, note_id, {Direction.RECOGNITION: note_id * 10})

        class FakeSettings:
            anki_collection_path = "unused"
            anki_deck_name = "0. Slovene"
            anki_model_name = "Slovene Vocabulary"
            target_language = "sl"

        @contextmanager
        def fake_safe_open(path, mode):
            yield type("Ctx", (), {"conn": anki_conn})()

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
        # Both soak line types, produced by a REAL sync rather than a hand-built
        # PullReport: the heartbeat, and the per-divergence detail. TT has no
        # revlog for this card, so its forward replay (s=1.0) cannot reproduce
        # Anki's s=21.5 — a genuine recompute divergence, which is exactly the
        # signal the soak exists to surface.
        assert "SYNC_SOAK pull_notes=0 pull_dirs=1 conflicts=0 recompute_divergences=1" in text
        assert "RECOMPUTE_DIVERGENCE cid=1 dir=recognition" in text
        assert "anki_s=21.5000" in text
        # And TT's row now carries Anki's state — the pull actually wrote.
        with tt_db._get_conn() as conn:
            reps, stability = conn.execute(
                "SELECT reps, stability FROM collocation_directions WHERE direction = 'recognition'"
            ).fetchone()
        assert reps == 5
        assert stability == pytest.approx(21.5)

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
            target_language = "sl"

        @contextmanager
        def fake_safe_open(path, mode):
            conn = sqlite3.connect(str(db_path))
            yield type("Ctx", (), {"conn": conn})()
            conn.close()

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
        target_language = "sl"
        database_urls = {"sl": "sqlite:///./tunatale_sl.db", "no": "sqlite:///./tunatale_no.db"}

    def test_resolve_model_name_prefers_language_vocab_notetype(self):
        conn = sqlite3.connect(":memory:")
        assert _resolve_model_name(self._S(), "no", conn, "deck") == "Norwegian Vocabulary"
        assert _resolve_model_name(self._S(), "sl", conn, "deck") == "Slovene Vocabulary"
