"""Where the AnkiWeb password comes from (tunatale-pkk).

``sync_orchestrator`` used to hardcode two steps: the ``sync_password`` setting,
then the macOS ``security`` binary. On Linux that binary does not exist, so the
lookup returned ``None`` and the run died pointing at a command the box cannot
run. These tests pin the three oracles the bead names:

1. On macOS with nothing configured, resolution is UNCHANGED — the Keychain is
   still consulted and the error text still names ``security add-generic-password``.
2. On Linux with a file source configured, the password resolves without the
   Keychain being touched at all.
3. No secret reaches a log or an exception message, at any level.

⚠️ ``platform`` is threaded as a PARAMETER rather than read from ``sys.platform``
inside the code under test, so none of this patches a module global. The seam is
the signature — see ``.claude/rules/testing.md`` on testing through the seam.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.plugins.anki_sync.secrets import (
    FileSecretSource,
    KeychainSecretSource,
    SecretRequest,
    StaticSecretSource,
    build_secret_sources,
    resolve_secret,
)

REQUEST = SecretRequest(service="tunatale-ankiweb", account="someone@example.com")
SECRET = "correct-horse-battery-staple"


class TestTheChainForThisMachine:
    def test_macos_keeps_the_keychain_last(self):
        """Oracle 1, structural half: darwin still ends at the Keychain."""
        chain = build_secret_sources(static_value="", file_path=None, platform="darwin")

        assert [s.name for s in chain] == ["sync_password setting", "sync_password_file", "macOS Keychain"]
        assert isinstance(chain[-1], KeychainSecretSource)

    def test_linux_has_no_keychain_source_at_all(self):
        """Not merely 'the Keychain returns None on Linux' — it is ABSENT.

        Leaving it in would spend one guaranteed-None subprocess attempt per sync
        looking for a binary that cannot exist, and would let the darwin error
        text advise a command the box cannot run.
        """
        chain = build_secret_sources(static_value="", file_path=None, platform="linux")

        assert [s.name for s in chain] == ["sync_password setting", "sync_password_file"]
        assert not any(isinstance(s, KeychainSecretSource) for s in chain)

    def test_the_setting_outranks_the_file(self):
        """Order is the documented override precedence, not an accident."""
        chain = build_secret_sources(static_value="from-env", file_path=Path("/nonexistent"), platform="linux")

        assert resolve_secret(REQUEST, chain) == "from-env"


class TestIndividualSources:
    def test_an_empty_setting_is_not_configured_rather_than_empty_password(self):
        """A blank .env line must not shadow the source behind it."""
        assert StaticSecretSource("").lookup(REQUEST) is None

    def test_the_file_source_strips_the_trailing_newline(self, tmp_path: Path):
        """Every way of writing such a file adds one, and a password carrying a
        stray \\n fails auth in a way indistinguishable from a wrong password."""
        secret_file = tmp_path / "pw"
        secret_file.write_text(SECRET + "\n")

        assert FileSecretSource(secret_file).lookup(REQUEST) == SECRET

    def test_an_unreadable_file_declines_instead_of_raising(self, tmp_path: Path):
        """The chain must keep walking: a machine using the Keychain has no file."""
        assert FileSecretSource(tmp_path / "absent").lookup(REQUEST) is None

    def test_no_configured_file_declines(self):
        assert FileSecretSource(None).lookup(REQUEST) is None

    def test_a_whitespace_only_file_is_not_configured(self, tmp_path: Path):
        secret_file = tmp_path / "pw"
        secret_file.write_text("   \n")

        assert FileSecretSource(secret_file).lookup(REQUEST) is None

    def test_resolve_returns_none_when_every_source_declines(self):
        assert resolve_secret(REQUEST, [StaticSecretSource(""), FileSecretSource(None)]) is None


class TestNoSecretEverLeaks:
    """Oracle 3. The password is the one value in this repo that must not reach
    ``sync.log``; a leak here is silent and permanent."""

    def test_the_file_source_logs_the_path_but_never_the_contents(self, tmp_path: Path, caplog):
        secret_file = tmp_path / "pw"
        secret_file.write_text(SECRET)

        with caplog.at_level(logging.DEBUG):
            assert FileSecretSource(secret_file).lookup(REQUEST) == SECRET

        assert SECRET not in caplog.text

    def test_a_declined_lookup_logs_no_secret_either(self, tmp_path: Path, caplog):
        with caplog.at_level(logging.DEBUG):
            FileSecretSource(tmp_path / "absent").lookup(REQUEST)

        assert SECRET not in caplog.text


class TestResolveSyncPassword:
    """The wiring, through ``sync_orchestrator._resolve_sync_password``."""

    @pytest.fixture(autouse=True)
    def _no_ambient_credentials(self, monkeypatch):
        """conftest pins a non-empty sync_password so tests never shell out. That
        short-circuit is exactly what this class needs to defeat."""
        from app.config import settings

        monkeypatch.setattr(settings, "sync_password", "", raising=False)
        monkeypatch.setattr(settings, "sync_password_file", "", raising=False)
        monkeypatch.setattr(settings, "sync_username", "someone@example.com", raising=False)
        monkeypatch.setattr(settings, "sync_keychain_service", "tunatale-ankiweb", raising=False)

    def test_macos_with_nothing_configured_still_names_the_security_command(self):
        """ORACLE 1. This exact text is what the user's setup instructions rest
        on, so it is pinned verbatim rather than by keyword."""
        from unittest.mock import patch

        from app.plugins.anki_sync.sync_orchestrator import PeerSyncError, _resolve_sync_password

        with (
            patch("app.plugins.anki_sync.sync_orchestrator._keychain_password", return_value=None) as keychain,
            pytest.raises(PeerSyncError) as exc,
        ):
            _resolve_sync_password(platform="darwin")

        keychain.assert_called_once_with("tunatale-ankiweb", "someone@example.com")
        assert "security add-generic-password -s tunatale-ankiweb -a someone@example.com -w" in str(exc.value)
        assert "Store it in the macOS Keychain" in str(exc.value)

    def test_macos_still_reads_the_keychain_when_it_has_the_password(self):
        """ORACLE 1, the success half — resolution is unchanged, not just its error."""
        from unittest.mock import patch

        from app.plugins.anki_sync.sync_orchestrator import _resolve_sync_password

        with patch("app.plugins.anki_sync.sync_orchestrator._keychain_password", return_value=SECRET):
            assert _resolve_sync_password(platform="darwin") == SECRET

    def test_linux_reads_the_file_without_touching_the_keychain(self, tmp_path: Path, monkeypatch):
        """ORACLE 2. `assert_not_called` is the point: on Linux `security` does not
        exist, so 'it happened to return None' would not be the same claim."""
        from unittest.mock import patch

        from app.config import settings
        from app.plugins.anki_sync.sync_orchestrator import _resolve_sync_password

        secret_file = tmp_path / "ankiweb-password"
        secret_file.write_text(SECRET + "\n")
        monkeypatch.setattr(settings, "sync_password_file", str(secret_file), raising=False)

        with patch("app.plugins.anki_sync.sync_orchestrator._keychain_password") as keychain:
            assert _resolve_sync_password(platform="linux") == SECRET

        keychain.assert_not_called()

    def test_linux_with_nothing_configured_does_not_advise_a_macos_command(self):
        """The actual bug: the old message sent a Linux operator to `security`."""
        from app.plugins.anki_sync.sync_orchestrator import PeerSyncError, _resolve_sync_password

        with pytest.raises(PeerSyncError) as exc:
            _resolve_sync_password(platform="linux")

        message = str(exc.value)
        assert "security add-generic-password" not in message
        assert "Keychain is not available on this platform (linux)" in message
        assert "sync_password_file" in message

    def test_the_setting_still_wins_on_either_platform(self, monkeypatch):
        from app.config import settings
        from app.plugins.anki_sync.sync_orchestrator import _resolve_sync_password

        monkeypatch.setattr(settings, "sync_password", SECRET, raising=False)

        assert _resolve_sync_password(platform="darwin") == SECRET
        assert _resolve_sync_password(platform="linux") == SECRET

    def test_the_raised_error_never_contains_the_password(self, tmp_path: Path, monkeypatch):
        """ORACLE 3 at the wiring level: a configured-but-wrong setup must not
        echo whatever it did find into the exception."""
        from app.config import settings
        from app.plugins.anki_sync.sync_orchestrator import PeerSyncError, _resolve_sync_password

        empty = tmp_path / "empty"
        empty.write_text("\n")
        monkeypatch.setattr(settings, "sync_password_file", str(empty), raising=False)

        with pytest.raises(PeerSyncError) as exc:
            _resolve_sync_password(platform="linux")

        assert SECRET not in str(exc.value)
        assert str(empty) not in str(exc.value) or "sync_password_file" in str(exc.value)
