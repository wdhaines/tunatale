"""Unit tests for sync server fixture helpers (``tests/_helpers/sync_server.py``).

These test the pure functions — port pick, env construction, reuse-decision —
without spawning an actual subprocess. They run in the normal suite (no
``--run-peer-sync`` flag needed).
"""

from __future__ import annotations

from pathlib import Path

from tests._helpers.sync_server import find_free_port, ping, server_cmd, server_env


class TestFindFreePort:
    def test_returns_valid_ephemeral_port(self) -> None:
        port = find_free_port()
        assert isinstance(port, int)
        assert 1024 <= port <= 65535

    def test_consecutive_calls_return_different_ports(self) -> None:
        ports = {find_free_port() for _ in range(5)}
        assert len(ports) > 1, "All ports were identical (bind may not be releasing?)"


class TestServerCmd:
    def test_includes_anki_syncserver(self) -> None:
        cmd = server_cmd()
        assert cmd[0] == "uv"
        assert cmd[1:4] == ["run", "--isolated", "--no-project"]
        assert cmd[-2:] == ["-m", "anki.syncserver"]

    def test_return_value_is_list_of_strings(self) -> None:
        cmd = server_cmd()
        assert isinstance(cmd, list)
        assert all(isinstance(part, str) for part in cmd)


class TestServerEnv:
    def test_includes_all_required_vars(self) -> None:
        env = server_env(port=18080, base=Path("/tmp/sync-base"))
        assert env["SYNC_USER1"] == "tt-test:tt-test-pw"
        assert env["SYNC_HOST"] == "127.0.0.1"
        assert env["SYNC_PORT"] == "18080"
        assert env["SYNC_BASE"] == "/tmp/sync-base"

    def test_preserves_existing_environ(self) -> None:
        env = server_env(port=18080, base=Path("/tmp/sync-base"))
        assert "PATH" in env

    def test_sets_qt_offscreen(self) -> None:
        env = server_env(port=18080, base=Path("/tmp/sync-base"))
        assert env["QT_QPA_PLATFORM"] == "offscreen"


class TestPing:
    def test_unreachable_host_returns_false(self) -> None:
        assert not ping("http://127.0.0.1:1")

    def test_unreachable_invalid_url_returns_false(self) -> None:
        assert not ping("http://127.0.0.1:9")
