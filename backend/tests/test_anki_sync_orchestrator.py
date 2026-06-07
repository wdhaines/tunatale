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
        mock_tt.assert_called_once_with(argv=[], _settings=ANY)
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
        mock_tt.assert_called_once_with(argv=["--dry-run"], _settings=ANY)
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

    def test_tt_settings_retargets_path(self):
        """_tt_settings clones settings with anki_collection_path = tt_collection_path."""
        from app.anki.sync_orchestrator import _tt_settings

        s = _tt_settings()
        assert s.anki_collection_path == settings.tt_collection_path
        assert s.anki_collection_path != settings.anki_collection_path

    def test_anki_with_spec(self):
        """Empty version → bare 'anki' (never a malformed 'anki=='); set → pinned."""
        from app.anki.sync_orchestrator import _anki_with_spec

        with patch.object(settings, "anki_pkg_version", ""):
            assert _anki_with_spec() == "anki"
        with patch.object(settings, "anki_pkg_version", "25.02"):
            assert _anki_with_spec() == "anki==25.02"


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
