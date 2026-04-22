"""Tests for S3.7: sync mode auto-detection + CLI wrapper."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

import pytest

from app.anki.anki_connect import AnkiConnectUnavailable
from app.anki.safety import AnkiContext
from app.anki.sync import (
    AnkiUnavailableError,
    detect_mode,
    main,
)

# ── Fakes ──────────────────────────────────────────────────────────────────────


class FakeAnkiConnectClient:
    """Tracks calls; returns empty results by default."""

    def __init__(self, *, ping_raises=None) -> None:
        self._ping_raises = ping_raises
        self.ping_called = False
        self.calls: list[tuple] = []

    def ping(self) -> int:
        self.ping_called = True
        if self._ping_raises is not None:
            raise self._ping_raises
        return 6

    def find_notes(self, query: str) -> list[int]:
        self.calls.append(("find_notes", query))
        return []

    def notes_info(self, notes: list[int]) -> list[dict]:
        return []

    def cards_info(self, cards: list[int]) -> list[dict]:
        return []


class FakeSettings:
    anki_connect_url: str = "http://127.0.0.1:8765"
    anki_deck_name: str = "0. Slovene"
    anki_collection_path: Path = Path("/fake/collection.anki2")
    anki_backup_dir: Path = Path("/fake/backups")
    database_url: str = "sqlite:///:memory:"


def _minimal_anki_conn() -> sqlite3.Connection:
    """Return an in-memory SQLite connection shaped like a minimal Anki collection."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE col (id INTEGER, ver INTEGER, decks TEXT)")
    conn.execute("INSERT INTO col VALUES (1, 18, '{}')")
    return conn


@contextlib.contextmanager
def _fake_safe_open(path, backup_dir=None, mode="ro"):
    conn = _minimal_anki_conn()
    ctx = AnkiContext(conn=conn, backup_path=Path("/fake/backup"), source_sha256="abc")
    try:
        yield ctx
    finally:
        conn.close()


def _raises_if_called(path, backup_dir=None, mode="ro"):
    raise AssertionError("safe_open should not have been called")
    yield  # make it a generator


# ── TestDetectMode ─────────────────────────────────────────────────────────────


class TestDetectMode:
    def test_returns_online_when_ping_succeeds(self):
        client = FakeAnkiConnectClient()
        result = detect_mode(client, Path("/fake/collection.anki2"), _probe_lock=lambda p: None)
        assert result == "online"

    def test_does_not_probe_lock_when_online(self):
        probed = []
        client = FakeAnkiConnectClient()
        detect_mode(client, Path("/fake/collection.anki2"), _probe_lock=lambda p: probed.append(p))
        assert probed == []

    def test_returns_offline_when_ping_refused_and_probe_succeeds(self):
        client = FakeAnkiConnectClient(ping_raises=AnkiConnectUnavailable("refused"))
        result = detect_mode(client, Path("/fake/collection.anki2"), _probe_lock=lambda p: None)
        assert result == "offline"

    def test_raises_unavailable_when_ping_refused_and_probe_locked(self):
        client = FakeAnkiConnectClient(ping_raises=AnkiConnectUnavailable("refused"))

        def locked(p):
            raise RuntimeError("collection is locked")

        with pytest.raises(AnkiUnavailableError):
            detect_mode(client, Path("/fake/collection.anki2"), _probe_lock=locked)

    def test_error_message_mentions_anki_connect(self):
        client = FakeAnkiConnectClient(ping_raises=AnkiConnectUnavailable("refused"))

        def locked(p):
            raise RuntimeError("locked")

        with pytest.raises(AnkiUnavailableError, match="(?i)anki"):
            detect_mode(client, Path("/fake/collection.anki2"), _probe_lock=locked)


# ── TestMainOnlineMode ─────────────────────────────────────────────────────────


class TestMainOnlineMode:
    def test_mode_online_succeeds_when_reachable(self):
        client = FakeAnkiConnectClient()
        result = main(
            ["--mode", "online"],
            _settings=FakeSettings(),
            _client=client,
            _safe_open_fn=_raises_if_called,
        )
        assert result == 0

    def test_mode_online_exits_nonzero_when_unreachable(self):
        client = FakeAnkiConnectClient(ping_raises=AnkiConnectUnavailable("refused"))
        result = main(
            ["--mode", "online"],
            _settings=FakeSettings(),
            _client=client,
            _safe_open_fn=_raises_if_called,
        )
        assert result != 0

    def test_mode_online_does_not_fall_back_to_offline(self):
        """--mode online must not silently fall back; safe_open must never be called."""
        called = []

        @contextlib.contextmanager
        def tracking_open(path, backup_dir=None, mode="ro"):
            called.append(True)
            yield _minimal_anki_conn()  # pragma: no cover

        client = FakeAnkiConnectClient(ping_raises=AnkiConnectUnavailable("refused"))
        main(
            ["--mode", "online"],
            _settings=FakeSettings(),
            _client=client,
            _safe_open_fn=tracking_open,
        )
        assert called == []


# ── TestMainOfflineMode ────────────────────────────────────────────────────────


class TestMainOfflineMode:
    def test_mode_offline_bypasses_anki_connect_probe(self):
        """--mode offline must never call client.ping()."""
        client = FakeAnkiConnectClient()
        result = main(
            ["--mode", "offline"],
            _settings=FakeSettings(),
            _client=client,
            _safe_open_fn=_fake_safe_open,
        )
        assert result == 0
        assert not client.ping_called

    def test_mode_offline_succeeds(self):
        client = FakeAnkiConnectClient()
        result = main(
            ["--mode", "offline"],
            _settings=FakeSettings(),
            _client=client,
            _safe_open_fn=_fake_safe_open,
        )
        assert result == 0

    def test_mode_offline_exits_nonzero_when_collection_locked(self):
        @contextlib.contextmanager
        def locked_open(path, backup_dir=None, mode="ro"):
            raise RuntimeError("collection is locked")
            yield  # pragma: no cover

        client = FakeAnkiConnectClient()
        result = main(
            ["--mode", "offline"],
            _settings=FakeSettings(),
            _client=client,
            _safe_open_fn=locked_open,
        )
        assert result != 0


# ── TestMainAutoMode ───────────────────────────────────────────────────────────


class TestMainAutoMode:
    def test_auto_uses_online_when_reachable(self):
        client = FakeAnkiConnectClient()
        result = main(
            ["--mode", "auto"],
            _settings=FakeSettings(),
            _client=client,
            _safe_open_fn=_raises_if_called,
        )
        assert result == 0
        assert client.ping_called

    def test_auto_uses_offline_when_anki_unreachable(self):
        client = FakeAnkiConnectClient(ping_raises=AnkiConnectUnavailable("refused"))
        result = main(
            ["--mode", "auto"],
            _settings=FakeSettings(),
            _client=client,
            _safe_open_fn=_fake_safe_open,
            _probe_lock=lambda p: None,  # collection is unlocked
        )
        assert result == 0

    def test_auto_exits_nonzero_when_both_fail(self):
        client = FakeAnkiConnectClient(ping_raises=AnkiConnectUnavailable("refused"))

        def _locked(p):
            raise RuntimeError("locked")

        result = main(
            ["--mode", "auto"],
            _settings=FakeSettings(),
            _client=client,
            _safe_open_fn=_fake_safe_open,
            _probe_lock=_locked,
        )
        assert result != 0


# ── TestMainCliFlags ───────────────────────────────────────────────────────────


class TestMainCliFlags:
    def test_default_mode_is_auto(self):
        """Passing no --mode should behave the same as --mode auto."""
        client = FakeAnkiConnectClient()
        result = main(
            [],
            _settings=FakeSettings(),
            _client=client,
            _safe_open_fn=_raises_if_called,
        )
        assert result == 0

    def test_dry_run_flag_accepted(self):
        client = FakeAnkiConnectClient()
        result = main(
            ["--dry-run"],
            _settings=FakeSettings(),
            _client=client,
            _safe_open_fn=_raises_if_called,
        )
        assert result == 0

    def test_force_fsrs_without_ack_file_exits_nonzero(self, tmp_path, monkeypatch):
        """--force-fsrs without ack file and non-interactive stdin must fail."""
        monkeypatch.setattr("sys.stdin", type("FakeStdin", (), {"isatty": lambda self: False})())
        client = FakeAnkiConnectClient()
        result = main(
            ["--force-fsrs"],
            _settings=FakeSettings(),
            _client=client,
            _safe_open_fn=_raises_if_called,
            _force_fsrs_ack_path=tmp_path / "no_ack.txt",
        )
        assert result != 0

    def test_force_fsrs_with_ack_file_proceeds(self, tmp_path):
        ack = tmp_path / "ack.txt"
        ack.write_text("acknowledged at 2026-04-21T12:00:00\n")
        client = FakeAnkiConnectClient()
        result = main(
            ["--force-fsrs"],
            _settings=FakeSettings(),
            _client=client,
            _safe_open_fn=_raises_if_called,
            _force_fsrs_ack_path=ack,
        )
        assert result == 0
