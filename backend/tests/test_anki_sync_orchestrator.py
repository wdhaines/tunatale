"""Tests for app.plugins.anki_sync.sync_orchestrator (peer-sync bracket).

Covers both Phase 3 (bracket orchestration) and Phase 5 (retargeting).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from app.config import settings
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.sync_orchestrator import PeerSyncError, bootstrap_collection, main_cli, peer_sync

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
    """The hkey cache and driver process are process-globals; reset them around every
    test so login expectations (subprocess counts) don't leak between cases."""
    import app.plugins.anki_sync.sync_orchestrator as so

    so._AUTH_CACHE = None
    # Kill any leftover persistent driver process from a prior test.
    with so._DRIVER_LOCK:
        so._kill_driver()
    yield
    so._AUTH_CACHE = None
    with so._DRIVER_LOCK:
        so._kill_driver()


class TestPeerSync:
    def test_tt_settings_retargets_path(self):
        """_tt_settings clones settings with anki_collection_path = tt_collection_path."""
        from app.plugins.anki_sync.sync_orchestrator import _tt_settings

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

        from app.plugins.anki_sync.sync_orchestrator import _tt_settings

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
        from app.plugins.anki_sync.sync_orchestrator import _tt_settings

        monkeypatch.setattr(settings, "database_url", url)
        assert _tt_settings().database_url == url

    def test_tt_settings_resolves_per_language_db_and_deck(self, monkeypatch):
        """A configured language_code retargets db_url AND deck AND target_language.

        Regression: the peer-sync endpoint resolves request.state.srs_db per the
        X-TT-Language header but never threaded the language into peer_sync, so the
        reconcile always ran settings.database_url / settings.anki_deck_name (the
        .env default language). A Slovene grade then never reached Anki because the
        reconcile pushed the Norwegian deck/db. _tt_settings(code) must pick the
        per-language db + deck so the active language is the one that syncs.
        """
        from app.plugins.anki_sync.sync_orchestrator import _tt_settings

        monkeypatch.setattr(
            settings,
            "database_urls",
            {"sl": "sqlite:///./tunatale_sl.db", "no": "sqlite:///./tunatale_no.db"},
        )
        s = _tt_settings("no")
        assert s.database_url.endswith("/backend/tunatale_no.db")
        assert s.anki_deck_name == "0. 6000 Most Frequent Norwegian Words [Part 1]"
        assert s.target_language == "no"

    def test_tt_settings_unknown_or_none_language_uses_default(self, monkeypatch):
        """None (CLI path) or an unconfigured code falls back to the default db + deck."""
        from app.plugins.anki_sync.sync_orchestrator import _tt_settings

        monkeypatch.setattr(settings, "database_urls", {"sl": "sqlite:///./tunatale_sl.db"})
        monkeypatch.setattr(settings, "database_url", "sqlite:///./tunatale_sl.db")
        monkeypatch.setattr(settings, "anki_deck_name", "1. Slovene")
        for code in (None, "zz"):
            s = _tt_settings(code)
            assert s.anki_deck_name == "1. Slovene", code
            assert s.database_url.endswith("/backend/tunatale_sl.db"), code

    def test_anki_with_spec(self):
        """Empty version → bare 'anki' (never a malformed 'anki=='); set → pinned."""
        from app.plugins.anki_sync.sync_orchestrator import _anki_with_spec

        with patch.object(settings, "anki_pkg_version", ""):
            assert _anki_with_spec() == "anki"
        with patch.object(settings, "anki_pkg_version", "25.02"):
            assert _anki_with_spec() == "anki==25.02"


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
        # `col` table so the mirror can bump col.mod (the value that actually wins
        # Anki's whole-blob, last-writer-wins config sync). Real collections always
        # have it; this minimal fake must too. mod starts at 1 (epoch-ish) so a bump
        # toward `now` is observable.
        con.execute("CREATE TABLE col (mod INTEGER)")
        con.execute("INSERT INTO col (mod) VALUES (1)")
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


def _read_col_mod(path) -> int | None:
    import sqlite3

    con = sqlite3.connect(path)
    try:
        row = con.execute("SELECT mod FROM col").fetchone()
        return row[0] if row else None
    finally:
        con.close()


class TestCurDeckMirror:
    """Anki uploads the entire config blob unconditionally every sync, so TT can't
    exclude `curDeck` from the push — it can only push the *right* value. peer-sync
    mirrors the user's real selected deck so it never switches the user's deck."""

    # ── _read_real_curdeck ────────────────────────────────────────────────────

    def test_reads_curdeck_value(self, tmp_path):
        from app.plugins.anki_sync.sync_orchestrator import _read_real_curdeck

        real = tmp_path / "real.anki2"
        _make_collection_with_curdeck(real, val=NORWEGIAN_DECK)
        assert _read_real_curdeck(real) == NORWEGIAN_DECK

    def test_missing_collection_reads_none(self, tmp_path):
        from app.plugins.anki_sync.sync_orchestrator import _read_real_curdeck

        assert _read_real_curdeck(tmp_path / "absent.anki2") is None

    def test_absent_curdeck_row_reads_none(self, tmp_path):
        """config table present but no curDeck row → None (user never selected a deck)."""
        import sqlite3

        from app.plugins.anki_sync.sync_orchestrator import _read_real_curdeck

        real = tmp_path / "real.anki2"
        con = sqlite3.connect(real)
        con.execute("CREATE TABLE config (key TEXT PRIMARY KEY, usn INTEGER, mtime_secs INTEGER, val BLOB)")
        con.commit()
        con.close()
        assert _read_real_curdeck(real) is None

    def test_unreadable_collection_reads_none(self, tmp_path):
        """A present-but-unusable collection (no config table) is swallowed → None."""
        from app.plugins.anki_sync.sync_orchestrator import _read_real_curdeck

        garbage = tmp_path / "garbage.anki2"
        garbage.write_bytes(b"not a sqlite database")
        assert _read_real_curdeck(garbage) is None

    def test_locked_collection_reads_none_fast_and_warns(self, tmp_path, caplog):
        """A hard-locked real collection (Anki holding a long transaction) must
        not stall the sync for sqlite's 5s default busy timeout, and the skipped
        mirror must be visible — a silent no-op means TT may re-assert a stale
        deck (the 188a08b regression class). Observed live 2026-06-11:
        mirror_pre=5.2s on every sync while Anki held the lock."""
        import logging
        import sqlite3
        import time as _time

        from app.plugins.anki_sync.sync_orchestrator import _read_real_curdeck

        real = tmp_path / "real.anki2"
        _make_collection_with_curdeck(real, val=SLOVENE_DECK)
        holder = sqlite3.connect(real)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            t0 = _time.monotonic()
            with caplog.at_level(logging.WARNING, logger="app.plugins.anki_sync.sync_orchestrator"):
                assert _read_real_curdeck(real) is None
            assert _time.monotonic() - t0 < 2.0, "must fail fast, not wait out the 5s default timeout"
            assert "curDeck mirror" in caplog.text
        finally:
            holder.rollback()
            holder.close()

    # ── _mirror_real_curdeck_into_tt ──────────────────────────────────────────

    def test_mirror_copies_real_value_into_tt(self, tmp_path):
        from app.plugins.anki_sync.sync_orchestrator import _mirror_real_curdeck_into_tt

        real, tt = tmp_path / "real.anki2", tmp_path / "tt.anki2"
        _make_collection_with_curdeck(real, val=SLOVENE_DECK)
        _make_collection_with_curdeck(tt, val=NORWEGIAN_DECK)
        _mirror_real_curdeck_into_tt(real, tt)
        assert _read_curdeck_val(tt) == SLOVENE_DECK

    def test_mirror_bumps_col_mod_so_tt_wins_config_sync(self, tmp_path):
        """The mirror must bump tt_collection.col.mod, or the value never wins.

        Anki syncs the whole config blob last-writer-wins by col.mod (NOT per-key
        usn or config mtime_secs — see anki-source-expert findings, rslib
        sync/collection/changes.rs + meta.rs). If the mirror only writes
        curDeck.val/mtime_secs, TT is never the newer side, so the bidirectional
        pull leg's set_all_config wipes TT's mirrored value back to the server's
        stale curDeck — the deck keeps switching to Norwegian. Bumping col.mod to
        ~now makes local_is_newer true → TT uploads its config and the server
        withholds its own (no revert).
        """
        import time

        from app.plugins.anki_sync.sync_orchestrator import _mirror_real_curdeck_into_tt

        real, tt = tmp_path / "real.anki2", tmp_path / "tt.anki2"
        _make_collection_with_curdeck(real, val=SLOVENE_DECK)
        _make_collection_with_curdeck(tt, val=NORWEGIAN_DECK)  # col.mod starts at 1

        now_ms = int(time.time() * 1000)
        _mirror_real_curdeck_into_tt(real, tt)

        assert _read_curdeck_val(tt) == SLOVENE_DECK
        assert _read_col_mod(tt) >= now_ms, "col.mod must be bumped to ~now so TT wins the col.mod compare"

    def test_mirror_noop_when_real_absent(self, tmp_path):
        """No real value → TT's curDeck is left untouched (not blanked)."""
        from app.plugins.anki_sync.sync_orchestrator import _mirror_real_curdeck_into_tt

        tt = tmp_path / "tt.anki2"
        _make_collection_with_curdeck(tt, val=NORWEGIAN_DECK)
        _mirror_real_curdeck_into_tt(tmp_path / "absent.anki2", tt)
        assert _read_curdeck_val(tt) == NORWEGIAN_DECK

    def test_mirror_noop_when_tt_absent(self, tmp_path):
        """Real value present but no TT collection → silent no-op (does not raise)."""
        from app.plugins.anki_sync.sync_orchestrator import _mirror_real_curdeck_into_tt

        real = tmp_path / "real.anki2"
        _make_collection_with_curdeck(real, val=SLOVENE_DECK)
        _mirror_real_curdeck_into_tt(real, tmp_path / "absent.anki2")

    def test_mirror_locked_tt_collection_skips_fast_and_warns(self, tmp_path, caplog):
        """A locked tt_collection must not crash peer_sync (the write previously
        raised OperationalError after the 5s default timeout) — skip + warn."""
        import logging
        import sqlite3
        import time as _time

        from app.plugins.anki_sync.sync_orchestrator import _mirror_real_curdeck_into_tt

        real, tt = tmp_path / "real.anki2", tmp_path / "tt.anki2"
        _make_collection_with_curdeck(real, val=SLOVENE_DECK)
        _make_collection_with_curdeck(tt, val=NORWEGIAN_DECK)
        holder = sqlite3.connect(tt)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            t0 = _time.monotonic()
            with caplog.at_level(logging.WARNING, logger="app.plugins.anki_sync.sync_orchestrator"):
                _mirror_real_curdeck_into_tt(real, tt)  # must not raise
            assert _time.monotonic() - t0 < 2.0
            assert "curDeck mirror" in caplog.text
        finally:
            holder.rollback()
            holder.close()
        assert _read_curdeck_val(tt) == NORWEGIAN_DECK  # untouched


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
        from app.plugins.anki_sync.sync_orchestrator import _has_pending_push

        col = tmp_path / "tt.anki2"
        _make_tt_collection(col, pending=True)
        assert _has_pending_push(col) is True

    def test_clean_collection_is_false(self, tmp_path):
        from app.plugins.anki_sync.sync_orchestrator import _has_pending_push

        col = tmp_path / "tt.anki2"
        _make_tt_collection(col, pending=False)
        assert _has_pending_push(col) is False

    def test_missing_file_is_true(self, tmp_path):
        """Unknown → push (safe default)."""
        from app.plugins.anki_sync.sync_orchestrator import _has_pending_push

        assert _has_pending_push(tmp_path / "absent.anki2") is True

    def test_unreadable_is_true(self, tmp_path):
        """A collection missing the synced tables → push (safe default), never crash."""
        from app.plugins.anki_sync.sync_orchestrator import _has_pending_push

        col = tmp_path / "tt.anki2"
        _make_collection_with_curdeck(col, val=NORWEGIAN_DECK)  # config only, no cards table
        assert _has_pending_push(col) is True


class TestDriverInvalidJson:
    def test_non_json_output_raises(self):
        """Non-JSON driver output surfaces as PeerSyncError."""
        import app.plugins.anki_sync.sync_orchestrator as so

        # Patch _driver_cmd so both the initial and retry spawns use a fake
        # that writes non-JSON to stdout.  Without this, the retry would
        # spawn the real driver (needs anki) and hang.
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(
            so,
            "_driver_cmd",
            lambda: [
                sys.executable,
                "-c",
                "import sys,os;os.environ['QT_QPA_PLATFORM']='offscreen'\n"
                "_o=sys.stdout;sys.stdout=sys.stderr\n"
                "for line in sys.stdin:_o.write('not json\\n');_o.flush()\n",
            ],
        )
        try:
            with pytest.raises(PeerSyncError, match="not valid JSON"):
                so._run_driver({"op": "login"})
        finally:
            monkeypatch.undo()
            with so._DRIVER_LOCK:
                so._kill_driver()


def _run_driver_wrapper(command: dict) -> dict:
    """Thin wrapper so test can import the private function."""
    from app.plugins.anki_sync.sync_orchestrator import _run_driver

    return _run_driver(command)


class TestCli:
    def test_bootstrap_flag(self):
        """CLI --bootstrap calls bootstrap_collection."""
        with (
            patch("app.plugins.anki_sync.sync_orchestrator.bootstrap_collection") as mock_bootstrap,
            patch("sys.argv", ["prog", "--bootstrap"]),
        ):
            main_cli()

        mock_bootstrap.assert_called_once()

    def test_dry_run_flag(self):
        """CLI --dry-run calls peer_sync(dry_run=True)."""
        with (
            patch("app.plugins.anki_sync.sync_orchestrator.peer_sync", return_value=_make_report()) as mock_sync,
            patch("sys.argv", ["prog", "--dry-run"]),
        ):
            main_cli()

        mock_sync.assert_called_once_with(dry_run=True)

    def test_default_routing(self):
        """CLI with no flags calls peer_sync(dry_run=False)."""
        with (
            patch("app.plugins.anki_sync.sync_orchestrator.peer_sync", return_value=_make_report()) as mock_sync,
            patch("sys.argv", ["prog"]),
        ):
            main_cli()

        mock_sync.assert_called_once_with(dry_run=False)


def _make_report(**overrides):
    from app.plugins.anki_sync.sync_orchestrator import PeerSyncReport

    defaults = PeerSyncReport()
    return PeerSyncReport(**{**defaults.__dict__, **overrides})


class TestBootstrap:
    def test_bootstrap_creates_and_downloads(self, tmp_path, monkeypatch):
        """bootstrap_collection creates collection + full_downloads when file missing."""
        import app.plugins.anki_sync.sync_orchestrator as so

        collection_path = tmp_path / "tt_collection.anki2"
        assert not collection_path.exists()

        op_log: list[dict] = []
        responses = iter([AUTH_RESPONSE, {"ok": True}, {"ok": True}])

        def _fake_driver(command: dict, timeout: int = 120) -> dict:
            op_log.append(command)
            return next(responses)

        monkeypatch.setattr(so, "_run_driver", _fake_driver)
        with patch.object(settings, "tt_collection_path", collection_path):
            bootstrap_collection()

        assert len(op_log) == 3
        assert op_log[0]["op"] == "login"
        assert op_log[1]["op"] == "create_collection"
        assert op_log[1]["collection_path"] == str(collection_path)
        assert op_log[2]["op"] == "full_download"
        assert op_log[2]["collection_path"] == str(collection_path)

    def test_bootstrap_skips_create_when_exists(self, tmp_path, monkeypatch):
        """bootstrap_collection skips create when file already exists."""
        import app.plugins.anki_sync.sync_orchestrator as so

        collection_path = tmp_path / "tt_collection.anki2"
        collection_path.touch()

        op_log: list[dict] = []
        responses = iter([AUTH_RESPONSE, {"ok": True}])

        def _fake_driver(command: dict, timeout: int = 120) -> dict:
            op_log.append(command)
            return next(responses)

        monkeypatch.setattr(so, "_run_driver", _fake_driver)
        with patch.object(settings, "tt_collection_path", collection_path):
            bootstrap_collection()

        assert len(op_log) == 2
        assert op_log[0]["op"] == "login"
        assert op_log[1]["op"] == "full_download"


class TestSyncPassword:
    def test_keychain_password_found(self):
        from app.plugins.anki_sync.sync_orchestrator import _keychain_password

        with patch(
            "app.plugins.anki_sync.sync_orchestrator.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="hunter2\n", stderr=""),
        ) as mock_run:
            assert _keychain_password("svc", "acct") == "hunter2"
        assert mock_run.call_args.args[0][0] == "security"

    def test_keychain_password_not_found(self):
        from app.plugins.anki_sync.sync_orchestrator import _keychain_password

        with patch(
            "app.plugins.anki_sync.sync_orchestrator.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=44, stdout="", stderr="not found"),
        ):
            assert _keychain_password("svc", "acct") is None

    def test_keychain_password_empty_stdout(self):
        from app.plugins.anki_sync.sync_orchestrator import _keychain_password

        with patch(
            "app.plugins.anki_sync.sync_orchestrator.subprocess.run",
            return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="\n", stderr=""),
        ):
            assert _keychain_password("svc", "acct") is None

    def test_keychain_password_security_unavailable(self):
        from app.plugins.anki_sync.sync_orchestrator import _keychain_password

        with patch("app.plugins.anki_sync.sync_orchestrator.subprocess.run", side_effect=FileNotFoundError):
            assert _keychain_password("svc", "acct") is None

    def test_resolve_prefers_env_over_keychain(self):
        from app.plugins.anki_sync.sync_orchestrator import _resolve_sync_password

        with (
            patch.object(settings, "sync_password", "from-env"),
            patch("app.plugins.anki_sync.sync_orchestrator._keychain_password") as mock_kc,
        ):
            assert _resolve_sync_password() == "from-env"
            mock_kc.assert_not_called()

    def test_resolve_falls_back_to_keychain(self):
        from app.plugins.anki_sync.sync_orchestrator import _resolve_sync_password

        with (
            patch.object(settings, "sync_password", ""),
            patch.object(settings, "sync_username", "me@example.com"),
            patch.object(settings, "sync_keychain_service", "svc"),
            patch(
                "app.plugins.anki_sync.sync_orchestrator._keychain_password", return_value="from-keychain"
            ) as mock_kc,
        ):
            assert _resolve_sync_password() == "from-keychain"
            mock_kc.assert_called_once_with("svc", "me@example.com")

    def test_resolve_missing_raises(self):
        from app.plugins.anki_sync.sync_orchestrator import _resolve_sync_password

        with (
            patch.object(settings, "sync_password", ""),
            patch("app.plugins.anki_sync.sync_orchestrator._keychain_password", return_value=None),
            pytest.raises(PeerSyncError, match="No AnkiWeb password"),
        ):
            _resolve_sync_password()


# ── Sociable peer-sync tests (Phase 7) ─────────────────────────────────────────
#
# These test peer_sync() with a real on-disk SyntheticCollection, patching only
# the driver process boundary (_run_driver). This exercises the full run_full_sync
# pipeline (sync_create_new, sync_push, sync_pull, refresh-*) without needing a
# real Anki installation.
#
# Escalation trigger: if SyntheticCollection needs more than ~2 small builder
# extensions to satisfy OfflineReader / refresh-*, stop and escalate. It means
# the synthetic schema and the reader have drifted.

CLOZE_NOTETYPE_MID = 1704067201


@pytest.fixture
def sociable_tt_collection(monkeypatch):
    """Create a real on-disk Anki collection at settings.tt_collection_path.

    Deck is set to ``settings.anki_deck_name`` (``0. Slovene``) with both
    ``Basic`` and ``Cloze`` notetypes. Pins ``anki_model_name`` so model
    discovery doesn't need notes in the collection.
    """
    from tests.anki_oracle.synthetic_collection import SyntheticCollection

    coll = SyntheticCollection(settings.tt_collection_path)
    coll.set_deck(settings.anki_deck_name, 1)
    coll.add_notetype(CLOZE_NOTETYPE_MID, "Cloze", ("Text", "Back Extra"), template_count=1)
    coll.save()

    monkeypatch.setattr(settings, "anki_model_name", "Cloze")
    return coll


@pytest.fixture
def fake_driver(monkeypatch):
    """Replace ``_run_driver`` with canned responses so auth/sync legs complete.

    Mirrors :func:`_run_driver`'s real signature exactly
    ``(command: dict, timeout: int = 120) -> dict`` and reuses the file's
    existing response constants (``AUTH_RESPONSE``, ``NORMAL_SYNC``) so the
    fake stays honest if those shapes change.

    Yields the op log (a list of commands received) for assertion use.
    """
    import app.plugins.anki_sync.sync_orchestrator as so

    op_log: list[dict] = []

    def _fake(command: dict, timeout: int = 120) -> dict:
        op_log.append(command)
        op = command.get("op", "")
        if op == "login":
            return AUTH_RESPONSE
        if op == "sync":
            return NORMAL_SYNC
        if op == "media_pending":
            return {"pending": 0}
        return {"error": f"unknown op: {op}"}

    monkeypatch.setattr(so, "_run_driver", _fake)
    return op_log


class TestSociableSync:
    """peer_sync with a real on-disk collection — only the driver boundary is faked.

    ``_AUTH_CACHE`` is reset by the module-level ``_clear_auth_cache`` autouse
    fixture (lines 35–37), so ordering-dependent auth leakage between these
    sociable tests is prevented.
    """

    @pytest.mark.usefixtures("sociable_tt_collection", "fake_driver")
    def test_unlinked_cloze_item_gets_linked_and_written(self):
        """b0a4b8a guard: unlinked TT cloze collocation → real peer_sync →
        collocation has ``anki_note_id`` AND a notes row exists in the
        ``tt_collection`` file."""
        from app.srs.database import SRSDatabase

        db = SRSDatabase(settings.database_url)

        text = "Kava je dobra"
        unit = SyntacticUnit(
            text=text,
            translation="Coffee is good",
            word_count=3,
            difficulty=2,
            source="test",
            source_sentence="Kava je dobra, ampak čaj je boljši.",
            card_type="cloze",
        )
        db.add_collocation(unit, language_code="sl")

        assert peer_sync().tt_push_pull_exit == 0

        item = db.get_collocation(text)
        assert item is not None
        assert item.anki_note_id is not None, f"Expected anki_note_id for {text}, got None"

        from app.plugins.anki_sync.safety import safe_open
        from app.plugins.anki_sync.sync import OfflineReader

        with safe_open(settings.tt_collection_path, mode="ro") as ctx:
            reader = OfflineReader(ctx.conn, settings.anki_deck_name)
            records = reader.get_note_records()
            assert any(r.anki_note_id == item.anki_note_id for r in records), (
                f"No notes row with anki_note_id={item.anki_note_id} in tt_collection"
            )

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_full_bracket_with_pending_change(self, fake_driver):
        """Happy path via synthetic collection: login → pull → reconcile →
        push. The pending change (unlinked cloze) triggers the push leg."""
        from app.srs.database import SRSDatabase

        db = SRSDatabase(settings.database_url)

        text = "Dober dan"
        unit = SyntacticUnit(
            text=text,
            translation="Good day",
            word_count=2,
            difficulty=1,
            source="test",
            source_sentence="Dober dan, kako ste?",
            card_type="cloze",
        )
        db.add_collocation(unit, language_code="sl")

        report = peer_sync(dry_run=False)

        ops = [c["op"] for c in fake_driver]
        assert ops == ["login", "sync", "media_pending", "sync"], f"Expected login+pull+media_pending+push, got {ops}"

        assert report.tt_push_pull_exit == 0

        item = db.get_collocation(text)
        assert item is not None
        assert item.anki_note_id is not None
        from app.plugins.anki_sync.safety import safe_open
        from app.plugins.anki_sync.sync import OfflineReader

        with safe_open(settings.tt_collection_path, mode="ro") as ctx:
            reader = OfflineReader(ctx.conn, settings.anki_deck_name)
            records = reader.get_note_records()
            assert any(r.anki_note_id == item.anki_note_id for r in records)

    def test_language_code_reconciles_per_language_db_and_deck(self, tmp_path, monkeypatch, fake_driver):
        """peer_sync(language_code='no') reconciles the 'no' db against the Norwegian
        deck — proven through the real pipeline, only the driver boundary faked.

        Headline Phase-5 regression: the Sync button reconciled the .env default
        language regardless of the UI selection, so a Slovene grade pushed the
        Norwegian deck/db (and vice-versa). Here the pending unlinked collocation
        lives ONLY in the 'no' db and the collection has ONLY the Norwegian deck:
        if language threading works the push leg fires and the collocation links;
        if peer_sync fell back to the default db (the bug) nothing is pending, the
        push leg is skipped, and the collocation stays unlinked. No peer_sync patch.
        """
        from app.languages import get_deck_name
        from app.srs.database import SRSDatabase
        from tests.anki_oracle.synthetic_collection import SyntheticCollection

        no_deck = get_deck_name("no")
        coll = SyntheticCollection(settings.tt_collection_path)
        coll.set_deck(no_deck, 1)
        coll.add_notetype(CLOZE_NOTETYPE_MID, "Cloze", ("Text", "Back Extra"), template_count=1)
        coll.save()
        monkeypatch.setattr(settings, "anki_model_name", "Cloze")

        sl_db_path = tmp_path / "sl.db"
        no_db_path = tmp_path / "no.db"
        SRSDatabase(str(sl_db_path)).close()  # empty default-language db
        no_db = SRSDatabase(str(no_db_path))
        unit = SyntacticUnit(
            text="Takk skal du ha",
            translation="Thank you",
            word_count=4,
            difficulty=1,
            source="test",
            source_sentence="Takk skal du ha.",
            card_type="cloze",
        )
        no_db.add_collocation(unit, language_code="no")
        no_db.close()

        monkeypatch.setattr(
            settings,
            "database_urls",
            {"sl": f"sqlite:///{sl_db_path}", "no": f"sqlite:///{no_db_path}"},
        )

        report = peer_sync(language_code="no")

        assert report.tt_push_pull_exit == 0
        ops = [c["op"] for c in fake_driver]
        assert ops == ["login", "sync", "media_pending", "sync"], (
            f"push leg should fire for the 'no' db's pending change, got {ops} "
            "(login+sync only ⇒ peer_sync reconciled the wrong db)"
        )
        no_db2 = SRSDatabase(str(no_db_path))
        try:
            item = no_db2.get_collocation("Takk skal du ha")
        finally:
            no_db2.close()
        assert item is not None and item.anki_note_id is not None, (
            "the 'no' collocation must be linked by the reconcile against the 'no' db"
        )

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_dry_run_skips_push_leg_no_writes(self, fake_driver):
        """dry_run skips the push leg AND the reconcile must not write.

        The tt_collection file is byte-identical before/after, the seeded
        item stays unlinked, and the op log has no push-leg sync op.
        """
        from app.srs.database import SRSDatabase

        db = SRSDatabase(settings.database_url)

        text = "Lepa hiza"
        unit = SyntacticUnit(
            text=text,
            translation="Beautiful house",
            word_count=2,
            difficulty=1,
            source="test",
            source_sentence="To je lepa hiza.",
            card_type="cloze",
        )
        db.add_collocation(unit, language_code="sl")

        before_bytes = settings.tt_collection_path.read_bytes()

        peer_sync(dry_run=True)

        after_bytes = settings.tt_collection_path.read_bytes()
        assert before_bytes == after_bytes, "dry_run must not modify tt_collection"

        ops = [c["op"] for c in fake_driver]
        assert "login" in ops
        push_syncs = [c for c in ops if c == "sync"]
        assert len(push_syncs) >= 1  # pull leg runs
        assert "sync" not in ops[2:], "no push sync after reconcile"

        item = db.get_collocation(text)
        assert item is not None
        assert item.anki_note_id is None, "dry_run must not link item"

    # ═════════════════════════════════════════════════════════════════════
    # Batch B — failure-path tests
    # ═════════════════════════════════════════════════════════════════════

    @pytest.mark.parametrize("response", [FULL_SYNC, FULL_DOWNLOAD, FULL_UPLOAD])
    def test_pull_full_sync_variants_abort(self, monkeypatch, response):
        """Any full-sync-required code on pull aborts before TT reconcile."""
        import app.plugins.anki_sync.sync_orchestrator as so

        op_log: list[dict] = []

        def _fake(command: dict, timeout: int = 120) -> dict:
            op_log.append(command)
            if command.get("op") == "login":
                return AUTH_RESPONSE
            if command.get("op") == "sync":
                return response
            return {"error": f"unknown op: {command.get('op')}"}

        monkeypatch.setattr(so, "_run_driver", _fake)
        with pytest.raises(PeerSyncError, match="FULL_SYNC"):
            peer_sync(dry_run=False)
        assert [c["op"] for c in op_log] == ["login", "sync"]

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_push_full_sync_aborts(self, monkeypatch):
        """Full-sync on push aborts via PeerSyncError.

        Seeds a pending ``graves`` row so ``_has_pending_push`` returns
        True and the push leg is not skipped.
        """
        import sqlite3

        con = sqlite3.connect(settings.tt_collection_path)
        con.execute("INSERT INTO graves (oid, type, usn) VALUES (1, 0, -1)")
        con.commit()
        con.close()

        import app.plugins.anki_sync.sync_orchestrator as so

        op_log: list[dict] = []
        responses = iter([AUTH_RESPONSE, NORMAL_SYNC, {"pending": 0}, FULL_SYNC])

        def _fake(command: dict, timeout: int = 120) -> dict:
            op_log.append(command)
            return next(responses)

        monkeypatch.setattr(so, "_run_driver", _fake)
        with pytest.raises(PeerSyncError, match="FULL_SYNC"):
            peer_sync(dry_run=False)
        assert [c["op"] for c in op_log] == ["login", "sync", "media_pending", "sync"]

    def test_full_sync_error_message_is_actionable(self):
        """The FULL_SYNC abort names the cause and the exact bootstrap fix command."""
        import app.plugins.anki_sync.sync_orchestrator as so

        msg = str(so._full_sync_error("pull", 2, "please full-sync"))
        assert "FULL_SYNC" in msg  # kept so existing match= assertions still fire
        assert "pull" in msg  # the failing leg
        assert "--bootstrap" in msg  # the actual fix command, not the opaque "run bootstrap"
        assert "download-only" in msg  # reassurance it won't touch the desktop collection
        assert "please full-sync" in msg  # server message preserved

    def test_full_sync_error_omits_server_message_when_blank(self):
        """No dangling 'Server message:' when the server sent none."""
        import app.plugins.anki_sync.sync_orchestrator as so

        msg = str(so._full_sync_error("push", 4, ""))
        assert "Server message:" not in msg
        assert "push" in msg

    # ═════════════════════════════════════════════════════════════════════
    # Batch C — driver/login errors
    # ═════════════════════════════════════════════════════════════════════

    def test_driver_error_surfaces(self, monkeypatch):
        """Driver error surfaces as PeerSyncError."""
        import app.plugins.anki_sync.sync_orchestrator as so

        def _fake_driver(command: dict, timeout: int = 120) -> dict:
            raise PeerSyncError("Driver error: collection not found")

        monkeypatch.setattr(so, "_run_driver", _fake_driver)

        with pytest.raises(PeerSyncError, match="collection not found"):
            peer_sync(dry_run=False)

    def test_login_error(self, monkeypatch):
        """Login failure surfaces as PeerSyncError."""
        import app.plugins.anki_sync.sync_orchestrator as so

        def _fake(command: dict, timeout: int = 120) -> dict:
            if command.get("op") == "login":
                raise PeerSyncError("invalid credentials")
            return {"error": f"unknown op: {command.get('op')}"}

        monkeypatch.setattr(so, "_run_driver", _fake)
        with pytest.raises(PeerSyncError, match="Login failed"):
            peer_sync(dry_run=False)

    # ═════════════════════════════════════════════════════════════════════
    # Batch D — auth reuse / media enabled
    # ═════════════════════════════════════════════════════════════════════

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_auth_reused_across_syncs(self, fake_driver):
        """Same auth dict is passed to both pull and push sync calls."""
        from app.srs.database import SRSDatabase

        db = SRSDatabase(settings.database_url)
        db.add_collocation(
            SyntacticUnit(
                text="Dober dan",
                translation="Good day",
                word_count=2,
                difficulty=1,
                source="test",
                source_sentence="Dober dan, kako ste?",
                card_type="cloze",
            ),
            language_code="sl",
        )

        peer_sync(dry_run=False)

        sync_cmds = [c for c in fake_driver if c["op"] == "sync"]
        assert len(sync_cmds) >= 2
        assert sync_cmds[0]["auth"]["hkey"] == sync_cmds[1]["auth"]["hkey"]

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_pull_leg_is_media_enabled(self, fake_driver):
        """The pull (always-run) leg must sync media or files strand."""
        peer_sync(dry_run=False)

        sync_cmds = [c for c in fake_driver if c["op"] == "sync"]
        assert len(sync_cmds) >= 1
        assert sync_cmds[0].get("sync_media") is True

    # ═════════════════════════════════════════════════════════════════════
    # Batch E — auth cache
    # ═════════════════════════════════════════════════════════════════════

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_auth_cached_across_syncs(self, fake_driver):
        """A second peer_sync reuses the hkey — login runs once, not twice.

        The test uses a clean ``tmp_path`` collection, so after the first
        reconcile there are no pending server pushes and the push leg is
        skipped — the auth-reuse assertion is unaffected.
        """
        peer_sync(dry_run=False)
        peer_sync(dry_run=False)

        login_ops = [c for c in fake_driver if c["op"] == "login"]
        assert len(login_ops) == 1

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_stale_cached_auth_relogins_and_retries(self, monkeypatch):
        """A cached hkey the server rejects on the pull → re-login + retry once."""
        import app.plugins.anki_sync.sync_orchestrator as so

        so._AUTH_CACHE = {"hkey": "stale-key", "endpoint": "http://localhost:8080/"}

        responses = iter(
            [
                {"error": "auth failed"},
                AUTH_RESPONSE,
                NORMAL_SYNC,
            ]
        )
        op_log: list[dict] = []

        def _fake(command: dict, timeout: int = 120) -> dict:
            op_log.append(command)
            resp = next(responses)
            if "error" in resp:
                raise PeerSyncError(resp["error"])
            return resp

        monkeypatch.setattr(so, "_run_driver", _fake)
        report = peer_sync(dry_run=False)

        assert report.pull_required == 1
        # The retry flow: stale pull → re-login → retry pull
        ops = [c["op"] for c in op_log]
        assert ops[:3] == ["sync", "login", "sync"], f"got {ops}"

    def test_fresh_auth_failure_not_retried(self, monkeypatch):
        """A pull failure on a *fresh* (uncached) login isn't an expiry → no retry."""
        import app.plugins.anki_sync.sync_orchestrator as so

        op_log: list[dict] = []

        def _fake(command: dict, timeout: int = 120) -> dict:
            op_log.append(command)
            if command.get("op") == "login":
                return AUTH_RESPONSE
            if command.get("op") == "sync":
                raise PeerSyncError("network down")
            return {"error": f"unknown op: {command.get('op')}"}

        monkeypatch.setattr(so, "_run_driver", _fake)
        with pytest.raises(PeerSyncError, match="network down"):
            peer_sync(dry_run=False)
        assert [c["op"] for c in op_log] == ["login", "sync"]

    # ═════════════════════════════════════════════════════════════════════
    # Batch F — curDeck mirror wiring
    # ═════════════════════════════════════════════════════════════════════

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_peer_sync_mirrors_before_push(self, fake_driver):
        """peer_sync rewrites TT's stale curDeck to the user's real deck."""
        _make_collection_with_curdeck(settings.anki_collection_path, val=SLOVENE_DECK)

        import sqlite3

        con = sqlite3.connect(settings.tt_collection_path)
        con.execute(
            "INSERT OR REPLACE INTO config (key, usn, mtime_secs, val) VALUES ('curDeck', -1, 1, ?)",
            (NORWEGIAN_DECK,),
        )
        con.commit()
        con.close()

        peer_sync(dry_run=False)
        assert _read_curdeck_val(settings.tt_collection_path) == SLOVENE_DECK

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_dry_run_still_mirrors_before_pull(self, fake_driver):
        """dry_run skips the push but still mirrors curDeck before the pull."""
        _make_collection_with_curdeck(settings.anki_collection_path, val=SLOVENE_DECK)

        import sqlite3

        con = sqlite3.connect(settings.tt_collection_path)
        con.execute(
            "INSERT OR REPLACE INTO config (key, usn, mtime_secs, val) VALUES ('curDeck', -1, 1, ?)",
            (NORWEGIAN_DECK,),
        )
        con.commit()
        con.close()

        peer_sync(dry_run=True)
        assert _read_curdeck_val(settings.tt_collection_path) == SLOVENE_DECK

    # ═════════════════════════════════════════════════════════════════════
    # Batch G — skip-no-op push
    # ═════════════════════════════════════════════════════════════════════

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_skips_push_when_nothing_pending(self, fake_driver):
        """Clean tt_collection (no usn=-1 rows) → push leg is skipped."""
        report = peer_sync(dry_run=False)

        assert [c["op"] for c in fake_driver] == ["login", "sync"]
        assert report.push_required == 0
        assert report.push_message == "skipped: no local changes to push"

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_pushes_when_rows_pending(self, fake_driver):
        """A pending collocation triggers push leg after reconcile."""
        from app.srs.database import SRSDatabase

        db = SRSDatabase(settings.database_url)
        db.add_collocation(
            SyntacticUnit(
                text="Potisk",
                translation="Push",
                word_count=1,
                difficulty=1,
                source="test",
                source_sentence="Potisk test.",
                card_type="cloze",
            ),
            language_code="sl",
        )

        report = peer_sync(dry_run=False)

        assert [c["op"] for c in fake_driver] == ["login", "sync", "media_pending", "sync"]
        assert report.push_message is not None

    # ═════════════════════════════════════════════════════════════════════
    # Batch H — peer-sync timing
    # ═════════════════════════════════════════════════════════════════════

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_records_one_timing_per_leg(self, fake_driver):
        """A full (non-dry) sync times every leg plus the total.

        Seeds a collocation so the reconcile creates a pending push and the
        push-leg timings are populated.
        """
        from app.srs.database import SRSDatabase

        db = SRSDatabase(settings.database_url)
        db.add_collocation(
            SyntacticUnit(
                text="Timing test",
                translation="Timing",
                word_count=1,
                difficulty=1,
                source="test",
                source_sentence="Timing test.",
                card_type="cloze",
            ),
            language_code="sl",
        )

        report = peer_sync(dry_run=False)

        assert set(report.timings) == {
            "auth",
            "mirror_pre",
            "pull",
            "reconcile",
            "pending_check",
            "mirror_pre_push",
            "media_pending_check",
            "push",
            "total",
        }
        assert all(v >= 0 for v in report.timings.values())
        for label, secs in report.timings.items():
            if label != "total":
                assert report.timings["total"] >= secs - 1e-6

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_dry_run_skips_push_leg_timings(self, fake_driver):
        """dry_run has no pending-check / mirror_pre_push / push legs."""
        report = peer_sync(dry_run=True)

        assert set(report.timings) == {"auth", "mirror_pre", "pull", "reconcile", "total"}

    @pytest.mark.usefixtures("sociable_tt_collection")
    def test_writes_timing_log_line(self, fake_driver):
        """A greppable PEER_SYNC_TIMING line is appended to settings.sync_log."""
        from app.srs.database import SRSDatabase

        db = SRSDatabase(settings.database_url)
        db.add_collocation(
            SyntacticUnit(
                text="Log test",
                translation="Log",
                word_count=1,
                difficulty=1,
                source="test",
                source_sentence="Log test.",
                card_type="cloze",
            ),
            language_code="sl",
        )

        peer_sync(dry_run=False)

        log = settings.sync_log.read_text()
        assert "PEER_SYNC_TIMING" in log
        assert "dry_run=False" in log
        for field_name in ("auth=", "mirror_pre=", "pull=", "reconcile=", "push=", "total="):
            assert field_name in log, f"missing {field_name} in {log!r}"

    def test_no_timing_log_on_pull_abort(self, monkeypatch):
        """A full-sync abort raises before any timing line is written."""
        import app.plugins.anki_sync.sync_orchestrator as so

        def _fake(command: dict, timeout: int = 120) -> dict:
            if command.get("op") == "login":
                return AUTH_RESPONSE
            if command.get("op") == "sync":
                return FULL_SYNC
            return {"error": f"unknown op: {command.get('op')}"}

        monkeypatch.setattr(so, "_run_driver", _fake)
        with pytest.raises(PeerSyncError, match="FULL_SYNC"):
            peer_sync(dry_run=False)
        assert not settings.sync_log.exists()

    def test_tt_sync_failure_aborts_before_push(self, fake_driver):
        """Garbage tt_collection triggers reconcile failure — pull runs, no push.

        Write non-SQLite bytes to ``settings.tt_collection_path`` so ``safe_open``
        raises inside ``main()``, which returns exit-code 1. ``peer_sync`` catches
        the non-zero exit and raises ``PeerSyncError`` before the push leg.
        """
        settings.tt_collection_path.write_bytes(b"not a sqlite database")

        with pytest.raises(PeerSyncError, match="TT sync against tt_collection failed"):
            peer_sync(dry_run=False)

        ops = [c["op"] for c in fake_driver]
        assert ops == ["login", "sync"]


class TestMediaDirResolution:
    """Peer-path media dir resolution + the tt_collection.media → real symlink.

    "No duplicate library, use Anki's dir if it's around, ours if not" — see the
    media-sync design dialogue. The symlink is what lets our driver's media sync
    (which operates on tt_collection's own media dir) push from the real library.
    """

    def _cfg(self, monkeypatch, real, tt_col):
        from app.plugins.anki_sync import sync_orchestrator as so

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
