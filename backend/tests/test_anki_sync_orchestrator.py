"""Tests for app.anki.sync_orchestrator (peer-sync bracket).

Covers both Phase 3 (bracket orchestration) and Phase 5 (retargeting).
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import ANY, patch

import pytest

from app.anki.sync_orchestrator import PeerSyncError, bootstrap_collection, main_cli, peer_sync
from app.config import settings

AUTH_RESPONSE = {"hkey": "test-hkey", "endpoint": "http://localhost:8080/"}
NORMAL_SYNC = {"required": 1, "server_message": "OK"}
NO_CHANGE = {"required": 0, "server_message": "no changes"}
FULL_SYNC = {"required": 2, "server_message": "please full-sync"}
FULL_DOWNLOAD = {"required": 3, "server_message": "download required"}
FULL_UPLOAD = {"required": 4, "server_message": "upload required"}


def _mock_run(data: dict) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(data), stderr="")


@pytest.fixture(autouse=True)
def _clear_auth_cache():
    """The hkey cache is a process-global; reset it around every test so login
    expectations (subprocess counts) don't leak between cases."""
    import app.anki.sync_orchestrator as so

    so._AUTH_CACHE = None
    yield
    so._AUTH_CACHE = None


def _login_ops(mock_run) -> list[dict]:
    """The parsed driver inputs whose op is 'login' (one per real authentication)."""
    return [
        payload for call in mock_run.call_args_list if (payload := json.loads(call.kwargs["input"]))["op"] == "login"
    ]


class TestPeerSync:
    def test_full_bracket(self):
        """Happy path: login → pull sync → TT sync → push sync."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run(AUTH_RESPONSE),
                    _mock_run(NORMAL_SYNC),
                    _mock_run(NO_CHANGE),
                ],
            ) as mock_run,
            patch("app.anki.sync.main", return_value=0) as mock_tt,
        ):
            report = peer_sync(dry_run=False)

        assert mock_run.call_count == 3
        mock_tt.assert_called_once_with(argv=[], _settings=ANY, _media_fn=ANY, _media_dir=ANY)
        actual_settings = mock_tt.call_args.kwargs["_settings"]
        assert actual_settings.anki_collection_path == settings.tt_collection_path
        assert report.auth_success
        assert report.pull_required == 1
        assert report.push_required == 0
        assert report.tt_push_pull_exit == 0

    def test_dry_run_skips_push(self):
        """dry_run=True: no push sync, TT sync gets --dry-run."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run(AUTH_RESPONSE),
                    _mock_run(NORMAL_SYNC),
                ],
            ) as mock_run,
            patch("app.anki.sync.main", return_value=0) as mock_tt,
        ):
            report = peer_sync(dry_run=True)

        assert mock_run.call_count == 2
        mock_tt.assert_called_once_with(argv=["--dry-run"], _settings=ANY, _media_fn=ANY, _media_dir=ANY)
        assert report.dry_run

    @pytest.mark.parametrize("full", [FULL_SYNC, FULL_DOWNLOAD, FULL_UPLOAD])
    def test_pull_full_sync_variants_abort(self, full):
        """Any full-sync-required code (2/3/4) on pull aborts before TT sync."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run(AUTH_RESPONSE),
                    _mock_run(full),
                ],
            ),
            patch("app.anki.sync.main") as mock_tt,
            pytest.raises(PeerSyncError, match="FULL_SYNC"),
        ):
            peer_sync(dry_run=False)

        mock_tt.assert_not_called()

    def test_tt_sync_failure_aborts_before_push(self):
        """Non-zero TT sync exit aborts before the push sync runs."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run(AUTH_RESPONSE),
                    _mock_run(NORMAL_SYNC),
                ],
            ) as mock_run,
            patch("app.anki.sync.main", return_value=1),
            pytest.raises(PeerSyncError, match="TT sync"),
        ):
            peer_sync(dry_run=False)

        # login + pull only; the push sync is never reached.
        assert mock_run.call_count == 2

    def test_push_full_sync_aborts(self):
        """Full-sync-required on the push sync raises (push result is validated)."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run(AUTH_RESPONSE),
                    _mock_run(NORMAL_SYNC),
                    _mock_run(FULL_SYNC),
                ],
            ),
            patch("app.anki.sync.main", return_value=0),
            pytest.raises(PeerSyncError, match="FULL_SYNC"),
        ):
            peer_sync(dry_run=False)

    def test_driver_error_surfaces(self):
        """Driver error surfaces as PeerSyncError."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run(AUTH_RESPONSE),
                    _mock_run({"error": "collection not found"}),
                ],
            ),
            patch("app.anki.sync.main"),
            pytest.raises(PeerSyncError, match="collection not found"),
        ):
            peer_sync(dry_run=False)

    def test_login_error(self):
        """Login failure surfaces as PeerSyncError."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run({"error": "invalid credentials"}),
                ],
            ),
            patch("app.anki.sync.main"),
            pytest.raises(PeerSyncError, match="Login failed"),
        ):
            peer_sync(dry_run=False)

    def test_auth_reused_across_syncs(self):
        """Same auth dict is passed to both pull and push sync calls."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run(AUTH_RESPONSE),
                    _mock_run(NORMAL_SYNC),
                    _mock_run(NO_CHANGE),
                ],
            ) as mock_run,
            patch("app.anki.sync.main", return_value=0),
        ):
            peer_sync(dry_run=False)

        pull_input = json.loads(mock_run.call_args_list[1].kwargs["input"])
        push_input = json.loads(mock_run.call_args_list[2].kwargs["input"])
        assert pull_input["auth"] == AUTH_RESPONSE
        assert push_input["auth"] == AUTH_RESPONSE

    def test_pull_leg_is_media_enabled(self):
        """Regression: media must sync on the PULL leg (the always-run bidirectional
        sync_collection), not only the conditional push leg. The pull leg pushes
        dirty collection rows first → has_pending clears → the push leg (and its
        media sync) is skipped → media files stranded on the client. Found doing a
        real backfill: note-field updates reached AnkiWeb but the media did not."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run(AUTH_RESPONSE),
                    _mock_run(NORMAL_SYNC),
                    _mock_run(NO_CHANGE),
                ],
            ) as mock_run,
            patch("app.anki.sync.main", return_value=0),
        ):
            peer_sync(dry_run=False)

        pull_input = json.loads(mock_run.call_args_list[1].kwargs["input"])
        assert pull_input["op"] == "sync"
        assert pull_input["sync_media"] is True, "pull leg must be media-enabled or media strands"

    def test_tt_settings_retargets_path(self):
        """_tt_settings clones settings with anki_collection_path = tt_collection_path."""
        from app.anki.sync_orchestrator import _tt_settings

        s = _tt_settings()
        assert s.anki_collection_path == settings.tt_collection_path
        assert s.anki_collection_path != settings.anki_collection_path

    def test_tt_settings_pins_relative_db_to_backend_dir(self, monkeypatch):
        """A CWD-relative sqlite db is anchored to the backend dir.

        peer_sync re-invokes tt_sync_main, which builds SRSDatabase from
        settings.database_url. The default 'sqlite:///./tunatale.db' is
        CWD-relative; invoked from any CWD other than backend/ it resolves to a
        different, empty db (real db never pull-merged; soak mode mislabels as
        'legacy'). _tt_settings must hand main() an absolute, CWD-independent path.
        """
        import os

        from app.anki.sync_orchestrator import _tt_settings

        monkeypatch.setattr(settings, "database_url", "sqlite:///./tunatale.db")
        path = _tt_settings().database_url.removeprefix("sqlite:///")
        assert os.path.isabs(path), f"expected absolute db path, got {path!r}"
        assert path.endswith("/backend/tunatale.db")

    @pytest.mark.parametrize(
        "url",
        [
            "sqlite:////already/absolute/tunatale.db",  # already absolute
            "sqlite:///:memory:",  # in-memory
            "postgresql://localhost/tunatale",  # non-sqlite
        ],
    )
    def test_tt_settings_leaves_cwd_independent_db_untouched(self, monkeypatch, url):
        """Already-absolute, in-memory, and non-sqlite URLs are passed through verbatim."""
        from app.anki.sync_orchestrator import _tt_settings

        monkeypatch.setattr(settings, "database_url", url)
        assert _tt_settings().database_url == url

    def test_anki_with_spec(self):
        """Empty version → bare 'anki' (never a malformed 'anki=='); set → pinned."""
        from app.anki.sync_orchestrator import _anki_with_spec

        with patch.object(settings, "anki_pkg_version", ""):
            assert _anki_with_spec() == "anki"
        with patch.object(settings, "anki_pkg_version", "25.02"):
            assert _anki_with_spec() == "anki==25.02"


class TestAuthCache:
    """The hkey is a long-lived session token: cache it across syncs (like Anki) and
    only re-login on a miss or when a sync rejects a cached token."""

    def test_auth_cached_across_syncs(self):
        """A second peer_sync reuses the hkey — login runs once, not twice."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run(AUTH_RESPONSE),  # login (first sync only)
                    _mock_run(NORMAL_SYNC),  # pull #1
                    _mock_run(NO_CHANGE),  # push #1
                    _mock_run(NORMAL_SYNC),  # pull #2 (no login before it)
                    _mock_run(NO_CHANGE),  # push #2
                ],
            ) as mock_run,
            patch("app.anki.sync.main", return_value=0),
        ):
            peer_sync(dry_run=False)
            peer_sync(dry_run=False)

        assert len(_login_ops(mock_run)) == 1
        assert mock_run.call_count == 5

    def test_stale_cached_auth_relogins_and_retries(self):
        """A cached hkey the server rejects on the pull → re-login + retry once."""
        import app.anki.sync_orchestrator as so

        so._AUTH_CACHE = AUTH_RESPONSE  # pre-warm the cache with a (now stale) token
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run({"error": "auth failed"}),  # pull with stale hkey
                    _mock_run(AUTH_RESPONSE),  # forced re-login
                    _mock_run(NORMAL_SYNC),  # retried pull
                    _mock_run(NO_CHANGE),  # push
                ],
            ) as mock_run,
            patch("app.anki.sync.main", return_value=0),
        ):
            report = peer_sync(dry_run=False)

        assert report.pull_required == 1
        assert len(_login_ops(mock_run)) == 1  # exactly the refresh login
        assert mock_run.call_count == 4

    def test_fresh_auth_failure_not_retried(self):
        """A pull failure on a *fresh* (uncached) login isn't an expiry → no retry."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run(AUTH_RESPONSE),  # fresh login
                    _mock_run({"error": "network down"}),  # pull fails
                ],
            ) as mock_run,
            patch("app.anki.sync.main", return_value=0),
            pytest.raises(PeerSyncError, match="network down"),
        ):
            peer_sync(dry_run=False)

        assert len(_login_ops(mock_run)) == 1  # no second login
        assert mock_run.call_count == 2


SLOVENE_DECK = b"1"
NORWEGIAN_DECK = b"1726348699710"


def _make_collection_with_curdeck(path, *, val: bytes) -> None:
    """Minimal anki-shaped sqlite file with a `config` `curDeck` row holding *val*
    (the selected deck id, as Anki stores it — the ascii bytes of the id)."""
    import sqlite3

    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, usn INTEGER, mtime_secs INTEGER, val BLOB)")
        con.execute("INSERT INTO config (key, usn, mtime_secs, val) VALUES ('curDeck', -1, 1, ?)", (val,))
        con.commit()
    finally:
        con.close()


def _read_curdeck_val(path) -> bytes | None:
    import sqlite3

    con = sqlite3.connect(path)
    try:
        row = con.execute("SELECT val FROM config WHERE key='curDeck'").fetchone()
        return row[0] if row else None
    finally:
        con.close()


class TestCurDeckMirror:
    """Anki uploads the entire config blob unconditionally every sync, so TT can't
    exclude `curDeck` from the push — it can only push the *right* value. peer-sync
    mirrors the user's real selected deck so it never switches the user's deck."""

    # ── _read_real_curdeck ────────────────────────────────────────────────────

    def test_reads_curdeck_value(self, tmp_path):
        from app.anki.sync_orchestrator import _read_real_curdeck

        real = tmp_path / "real.anki2"
        _make_collection_with_curdeck(real, val=NORWEGIAN_DECK)
        assert _read_real_curdeck(real) == NORWEGIAN_DECK

    def test_missing_collection_reads_none(self, tmp_path):
        from app.anki.sync_orchestrator import _read_real_curdeck

        assert _read_real_curdeck(tmp_path / "absent.anki2") is None

    def test_absent_curdeck_row_reads_none(self, tmp_path):
        """config table present but no curDeck row → None (user never selected a deck)."""
        import sqlite3

        from app.anki.sync_orchestrator import _read_real_curdeck

        real = tmp_path / "real.anki2"
        con = sqlite3.connect(real)
        con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, usn INTEGER, mtime_secs INTEGER, val BLOB)")
        con.commit()
        con.close()
        assert _read_real_curdeck(real) is None

    def test_unreadable_collection_reads_none(self, tmp_path):
        """A present-but-unusable collection (no config table) is swallowed → None."""
        from app.anki.sync_orchestrator import _read_real_curdeck

        garbage = tmp_path / "garbage.anki2"
        garbage.write_bytes(b"not a sqlite database")
        assert _read_real_curdeck(garbage) is None

    # ── _mirror_real_curdeck_into_tt ──────────────────────────────────────────

    def test_mirror_copies_real_value_into_tt(self, tmp_path):
        from app.anki.sync_orchestrator import _mirror_real_curdeck_into_tt

        real, tt = tmp_path / "real.anki2", tmp_path / "tt.anki2"
        _make_collection_with_curdeck(real, val=SLOVENE_DECK)
        _make_collection_with_curdeck(tt, val=NORWEGIAN_DECK)
        _mirror_real_curdeck_into_tt(real, tt)
        assert _read_curdeck_val(tt) == SLOVENE_DECK

    def test_mirror_noop_when_real_absent(self, tmp_path):
        """No real value → TT's curDeck is left untouched (not blanked)."""
        from app.anki.sync_orchestrator import _mirror_real_curdeck_into_tt

        tt = tmp_path / "tt.anki2"
        _make_collection_with_curdeck(tt, val=NORWEGIAN_DECK)
        _mirror_real_curdeck_into_tt(tmp_path / "absent.anki2", tt)
        assert _read_curdeck_val(tt) == NORWEGIAN_DECK

    def test_mirror_noop_when_tt_absent(self, tmp_path):
        """Real value present but no TT collection → silent no-op (does not raise)."""
        from app.anki.sync_orchestrator import _mirror_real_curdeck_into_tt

        real = tmp_path / "real.anki2"
        _make_collection_with_curdeck(real, val=SLOVENE_DECK)
        _mirror_real_curdeck_into_tt(real, tmp_path / "absent.anki2")

    # ── peer_sync wiring ──────────────────────────────────────────────────────

    def test_peer_sync_mirrors_before_push(self):
        """End-to-end: peer_sync rewrites TT's stale curDeck to the user's real deck."""
        _make_collection_with_curdeck(settings.anki_collection_path, val=SLOVENE_DECK)
        _make_collection_with_curdeck(settings.tt_collection_path, val=NORWEGIAN_DECK)
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[_mock_run(AUTH_RESPONSE), _mock_run(NORMAL_SYNC), _mock_run(NO_CHANGE)],
            ),
            patch("app.anki.sync.main", return_value=0),
        ):
            peer_sync(dry_run=False)
        assert _read_curdeck_val(settings.tt_collection_path) == SLOVENE_DECK

    def test_dry_run_still_mirrors_before_pull(self):
        """dry_run skips the push but its pull leg still uploads config, so the
        pre-pull mirror must still fire."""
        _make_collection_with_curdeck(settings.anki_collection_path, val=SLOVENE_DECK)
        _make_collection_with_curdeck(settings.tt_collection_path, val=NORWEGIAN_DECK)
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[_mock_run(AUTH_RESPONSE), _mock_run(NORMAL_SYNC)],
            ),
            patch("app.anki.sync.main", return_value=0),
        ):
            peer_sync(dry_run=True)
        assert _read_curdeck_val(settings.tt_collection_path) == SLOVENE_DECK


def _make_tt_collection(path, *, pending: bool = False, curdeck_val: bytes = NORWEGIAN_DECK) -> None:
    """tt_collection with the synced tables the push-pending probe reads + a curDeck row.
    *pending* seeds a usn=-1 card (something to upload); otherwise the tables are clean."""
    import sqlite3

    con = sqlite3.connect(path)
    try:
        for table in ("cards", "notes", "revlog", "graves"):
            con.execute(f"CREATE TABLE {table} (id INTEGER, usn INTEGER)")
            con.execute(f"INSERT INTO {table} (id, usn) VALUES (1, 5)")  # clean (already synced)
        con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, usn INTEGER, mtime_secs INTEGER, val BLOB)")
        con.execute("INSERT INTO config (key, usn, mtime_secs, val) VALUES ('curDeck', -1, 1, ?)", (curdeck_val,))
        if pending:
            con.execute("INSERT INTO cards (id, usn) VALUES (2, -1)")  # a row awaiting push
        con.commit()
    finally:
        con.close()


class TestHasPendingPush:
    def test_pending_row_is_true(self, tmp_path):
        from app.anki.sync_orchestrator import _has_pending_push

        col = tmp_path / "tt.anki2"
        _make_tt_collection(col, pending=True)
        assert _has_pending_push(col) is True

    def test_clean_collection_is_false(self, tmp_path):
        from app.anki.sync_orchestrator import _has_pending_push

        col = tmp_path / "tt.anki2"
        _make_tt_collection(col, pending=False)
        assert _has_pending_push(col) is False

    def test_missing_file_is_true(self, tmp_path):
        """Unknown → push (safe default)."""
        from app.anki.sync_orchestrator import _has_pending_push

        assert _has_pending_push(tmp_path / "absent.anki2") is True

    def test_unreadable_is_true(self, tmp_path):
        """A collection missing the synced tables → push (safe default), never crash."""
        from app.anki.sync_orchestrator import _has_pending_push

        col = tmp_path / "tt.anki2"
        _make_collection_with_curdeck(col, val=NORWEGIAN_DECK)  # config only, no cards table
        assert _has_pending_push(col) is True


class TestSkipNoOpPush:
    """Most syncs have nothing to upload (the user grades in Anki, not TT); the push leg
    is then a pure 2–4s no-op round-trip. Skip it when no row is usn=-1 (pending push)."""

    def test_skips_push_when_nothing_pending(self):
        """Clean tt_collection (no usn=-1 rows) → push leg is skipped."""
        _make_collection_with_curdeck(settings.anki_collection_path, val=SLOVENE_DECK)
        _make_tt_collection(settings.tt_collection_path, pending=False)
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[_mock_run(AUTH_RESPONSE), _mock_run(NORMAL_SYNC)],  # login + pull only
            ) as mock_run,
            patch("app.anki.sync.main", return_value=0),
        ):
            report = peer_sync(dry_run=False)

        assert mock_run.call_count == 2  # the push subprocess never ran
        assert report.push_required == 0
        assert report.push_message == "skipped: no local changes to push"

    def test_pushes_when_rows_pending(self):
        """A usn=-1 row (a TT grade to upload) → the push leg runs."""
        _make_collection_with_curdeck(settings.anki_collection_path, val=SLOVENE_DECK)
        _make_tt_collection(settings.tt_collection_path, pending=True)
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[_mock_run(AUTH_RESPONSE), _mock_run(NORMAL_SYNC), _mock_run(NO_CHANGE)],
            ) as mock_run,
            patch("app.anki.sync.main", return_value=0),
        ):
            report = peer_sync(dry_run=False)

        assert mock_run.call_count == 3  # push leg ran
        assert report.push_message == "no changes"


class TestDriverInvalidJson:
    def test_non_json_output_raises(self):
        """Non-JSON driver output surfaces as PeerSyncError."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="not json", stderr=""),
            ),
            pytest.raises(PeerSyncError, match="not valid JSON"),
        ):
            _run_driver_wrapper({"op": "login"})


def _run_driver_wrapper(command: dict) -> dict:
    """Thin wrapper so test can import the private function."""
    from app.anki.sync_orchestrator import _run_driver

    return _run_driver(command)


class TestCli:
    def test_bootstrap_flag(self):
        """CLI --bootstrap calls bootstrap_collection."""
        with (
            patch("app.anki.sync_orchestrator.bootstrap_collection") as mock_bootstrap,
            patch("sys.argv", ["prog", "--bootstrap"]),
        ):
            main_cli()

        mock_bootstrap.assert_called_once()

    def test_dry_run_flag(self):
        """CLI --dry-run calls peer_sync(dry_run=True)."""
        with (
            patch("app.anki.sync_orchestrator.peer_sync", return_value=_make_report()) as mock_sync,
            patch("sys.argv", ["prog", "--dry-run"]),
        ):
            main_cli()

        mock_sync.assert_called_once_with(dry_run=True)

    def test_default_routing(self):
        """CLI with no flags calls peer_sync(dry_run=False)."""
        with (
            patch("app.anki.sync_orchestrator.peer_sync", return_value=_make_report()) as mock_sync,
            patch("sys.argv", ["prog"]),
        ):
            main_cli()

        mock_sync.assert_called_once_with(dry_run=False)


def _make_report(**overrides):
    from app.anki.sync_orchestrator import PeerSyncReport

    defaults = PeerSyncReport()
    return PeerSyncReport(**{**defaults.__dict__, **overrides})


class TestBootstrap:
    def test_bootstrap_creates_and_downloads(self, tmp_path):
        """bootstrap_collection creates collection + full_downloads when file missing."""
        collection_path = tmp_path / "tt_collection.anki2"
        assert not collection_path.exists()

        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run(AUTH_RESPONSE),
                    _mock_run({"ok": True}),
                    _mock_run({"ok": True}),
                ],
            ) as mock_run,
            patch.object(settings, "tt_collection_path", collection_path),
        ):
            bootstrap_collection()

        assert mock_run.call_count == 3
        calls = [json.loads(c.kwargs["input"]) for c in mock_run.call_args_list]
        assert calls[0]["op"] == "login"
        assert calls[1]["op"] == "create_collection"
        assert calls[1]["collection_path"] == str(collection_path)
        assert calls[2]["op"] == "full_download"
        assert calls[2]["collection_path"] == str(collection_path)

    def test_bootstrap_skips_create_when_exists(self, tmp_path):
        """bootstrap_collection skips create when file already exists."""
        collection_path = tmp_path / "tt_collection.anki2"
        collection_path.touch()

        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[
                    _mock_run(AUTH_RESPONSE),
                    _mock_run({"ok": True}),
                ],
            ) as mock_run,
            patch.object(settings, "tt_collection_path", collection_path),
        ):
            bootstrap_collection()

        assert mock_run.call_count == 2
        calls = [json.loads(c.kwargs["input"]) for c in mock_run.call_args_list]
        assert calls[0]["op"] == "login"
        assert calls[1]["op"] == "full_download"


class TestSyncPassword:
    def test_keychain_password_found(self):
        from app.anki.sync_orchestrator import _keychain_password

        with patch(
            "app.anki.sync_orchestrator.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="hunter2\n", stderr=""),
        ) as mock_run:
            assert _keychain_password("svc", "acct") == "hunter2"
        assert mock_run.call_args.args[0][0] == "security"

    def test_keychain_password_not_found(self):
        from app.anki.sync_orchestrator import _keychain_password

        with patch(
            "app.anki.sync_orchestrator.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=44, stdout="", stderr="not found"),
        ):
            assert _keychain_password("svc", "acct") is None

    def test_keychain_password_empty_stdout(self):
        from app.anki.sync_orchestrator import _keychain_password

        with patch(
            "app.anki.sync_orchestrator.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="\n", stderr=""),
        ):
            assert _keychain_password("svc", "acct") is None

    def test_keychain_password_security_unavailable(self):
        from app.anki.sync_orchestrator import _keychain_password

        with patch("app.anki.sync_orchestrator.subprocess.run", side_effect=FileNotFoundError):
            assert _keychain_password("svc", "acct") is None

    def test_resolve_prefers_env_over_keychain(self):
        from app.anki.sync_orchestrator import _resolve_sync_password

        with (
            patch.object(settings, "sync_password", "from-env"),
            patch("app.anki.sync_orchestrator._keychain_password") as mock_kc,
        ):
            assert _resolve_sync_password() == "from-env"
            mock_kc.assert_not_called()

    def test_resolve_falls_back_to_keychain(self):
        from app.anki.sync_orchestrator import _resolve_sync_password

        with (
            patch.object(settings, "sync_password", ""),
            patch.object(settings, "sync_username", "me@example.com"),
            patch.object(settings, "sync_keychain_service", "svc"),
            patch("app.anki.sync_orchestrator._keychain_password", return_value="from-keychain") as mock_kc,
        ):
            assert _resolve_sync_password() == "from-keychain"
            mock_kc.assert_called_once_with("svc", "me@example.com")

    def test_resolve_missing_raises(self):
        from app.anki.sync_orchestrator import _resolve_sync_password

        with (
            patch.object(settings, "sync_password", ""),
            patch("app.anki.sync_orchestrator._keychain_password", return_value=None),
            pytest.raises(PeerSyncError, match="No AnkiWeb password"),
        ):
            _resolve_sync_password()


class TestPeerSyncTiming:
    """Per-leg wall-time instrumentation on the peer-sync bracket.

    These let us catch an occasional slow sync after the fact (which leg hung)
    from ``sync.log`` instead of trying to reproduce the conditions live.
    """

    def _full_bracket(self):
        """Run a happy-path peer_sync with all three driver legs mocked."""
        return patch(
            "app.anki.sync_orchestrator.subprocess.run",
            side_effect=[
                _mock_run(AUTH_RESPONSE),
                _mock_run(NORMAL_SYNC),
                _mock_run(NO_CHANGE),
            ],
        ), patch("app.anki.sync.main", return_value=0)

    def test_records_one_timing_per_leg(self):
        """A full (non-dry) sync times every leg plus the total.

        tt_collection doesn't exist under tmp_path, so ``_has_pending_push``
        returns True and the push legs run.
        """
        run_ctx, tt_ctx = self._full_bracket()
        with run_ctx, tt_ctx:
            report = peer_sync(dry_run=False)

        assert set(report.timings) == {
            "auth",
            "mirror_pre",
            "pull",
            "reconcile",
            "pending_check",
            "mirror_pre_push",
            "push",
            "total",
        }
        assert all(v >= 0 for v in report.timings.values())
        # total brackets the whole bracket, so it's >= any single leg.
        for label, secs in report.timings.items():
            if label != "total":
                assert report.timings["total"] >= secs - 1e-6

    def test_dry_run_skips_push_leg_timings(self):
        """dry_run has no pending-check / mirror_pre_push / push legs."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[_mock_run(AUTH_RESPONSE), _mock_run(NORMAL_SYNC)],
            ),
            patch("app.anki.sync.main", return_value=0),
        ):
            report = peer_sync(dry_run=True)

        assert set(report.timings) == {"auth", "mirror_pre", "pull", "reconcile", "total"}

    def test_writes_timing_log_line(self):
        """A greppable PEER_SYNC_TIMING line is appended to settings.sync_log."""
        run_ctx, tt_ctx = self._full_bracket()
        with run_ctx, tt_ctx:
            peer_sync(dry_run=False)

        log = settings.sync_log.read_text()
        assert "PEER_SYNC_TIMING" in log
        assert "dry_run=False" in log
        for field_name in ("auth=", "mirror_pre=", "pull=", "reconcile=", "push=", "total="):
            assert field_name in log, f"missing {field_name} in {log!r}"

    def test_no_timing_log_on_pull_abort(self):
        """A full-sync abort raises before any timing line is written."""
        with (
            patch(
                "app.anki.sync_orchestrator.subprocess.run",
                side_effect=[_mock_run(AUTH_RESPONSE), _mock_run(FULL_SYNC)],
            ),
            patch("app.anki.sync.main"),
            pytest.raises(PeerSyncError, match="FULL_SYNC"),
        ):
            peer_sync(dry_run=False)

        assert not settings.sync_log.exists()


class TestMediaDirResolution:
    """Peer-path media dir resolution + the tt_collection.media → real symlink.

    "No duplicate library, use Anki's dir if it's around, ours if not" — see the
    media-sync design dialogue. The symlink is what lets our driver's media sync
    (which operates on tt_collection's own media dir) push from the real library.
    """

    def _cfg(self, monkeypatch, real, tt_col):
        from app.anki import sync_orchestrator as so

        monkeypatch.setattr(so.settings, "anki_media_path", real)
        monkeypatch.setattr(so.settings, "tt_collection_path", tt_col)
        return so

    def test_resolve_prefers_real_when_present(self, tmp_path, monkeypatch):
        real = tmp_path / "real.media"
        real.mkdir()
        so = self._cfg(monkeypatch, real, tmp_path / "tt_collection.anki2")
        assert so._resolve_media_dir() == real

    def test_resolve_falls_back_to_tt_media_when_anki_absent(self, tmp_path, monkeypatch):
        so = self._cfg(monkeypatch, tmp_path / "nope.media", tmp_path / "tt_collection.anki2")
        assert so._resolve_media_dir() == tmp_path / "tt_collection.media"

    def test_ensure_link_noop_when_anki_absent(self, tmp_path, monkeypatch):
        so = self._cfg(monkeypatch, tmp_path / "nope.media", tmp_path / "tt_collection.anki2")
        so._ensure_tt_media_linked()
        assert not (tmp_path / "tt_collection.media").exists()

    def test_ensure_link_creates_symlink_when_tt_media_absent(self, tmp_path, monkeypatch):
        real = tmp_path / "real.media"
        real.mkdir()
        so = self._cfg(monkeypatch, real, tmp_path / "tt_collection.anki2")
        so._ensure_tt_media_linked()
        link = tmp_path / "tt_collection.media"
        assert link.is_symlink()
        assert link.resolve() == real.resolve()

    def test_ensure_link_replaces_empty_tt_media_dir(self, tmp_path, monkeypatch):
        real = tmp_path / "real.media"
        real.mkdir()
        so = self._cfg(monkeypatch, real, tmp_path / "tt_collection.anki2")
        (tmp_path / "tt_collection.media").mkdir()
        so._ensure_tt_media_linked()
        assert (tmp_path / "tt_collection.media").is_symlink()

    def test_ensure_link_idempotent_when_already_symlinked(self, tmp_path, monkeypatch):
        real = tmp_path / "real.media"
        real.mkdir()
        so = self._cfg(monkeypatch, real, tmp_path / "tt_collection.anki2")
        link = tmp_path / "tt_collection.media"
        link.symlink_to(real, target_is_directory=True)
        so._ensure_tt_media_linked()
        assert link.is_symlink()

    def test_ensure_link_preserves_nonempty_tt_media_dir(self, tmp_path, monkeypatch, caplog):
        import logging

        real = tmp_path / "real.media"
        real.mkdir()
        so = self._cfg(monkeypatch, real, tmp_path / "tt_collection.anki2")
        ttm = tmp_path / "tt_collection.media"
        ttm.mkdir()
        (ttm / "keep.mp3").write_bytes(b"x")
        with caplog.at_level(logging.WARNING):
            so._ensure_tt_media_linked()
        assert not ttm.is_symlink()
        assert (ttm / "keep.mp3").exists()
        assert "non-empty real dir" in caplog.text
