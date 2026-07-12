"""Tests for persistent driver process (Fix A) and driver loop protocol.

Test 1 — Driver loop unit tests: exercise sync_driver.py's main() loop
directly by feeding it JSON lines via subprocess stdin.

Test 2 — Fake driver (process boundary): exercise the orchestrator's
persistent driver behavior — PID reuse, kill/respawn, timeout, stderr drain,
double-failure → PeerSyncError.

Test 3 — Push-leg media gating (Fix B): verify sync_media is conditional on
media_pending results.

Test 4 — _await_media_sync backoff: verify the returned observability dict.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.anki.sync_orchestrator import PeerSyncError

_DRIVER_PATH = str(Path(__file__).resolve().parent.parent / "app" / "anki" / "sync_driver.py")
_FAKE_DRIVER = str(Path(__file__).resolve().parent / "_fake_driver.py")


def _read_lines(proc: subprocess.Popen, count: int, timeout: float = 10) -> list[dict]:
    """Read *count* JSON result lines from *proc*'s stdout within *timeout*."""
    import threading

    results: list[dict] = []
    error: list[Exception] = []

    def _reader():
        try:
            assert proc.stdout is not None
            for _ in range(count):
                line = proc.stdout.readline()
                if not line:
                    break
                results.append(json.loads(line.strip()))
        except Exception as e:
            error.append(e)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        pytest.fail(f"Timed out waiting for {count} lines (got {len(results)})")
    if error:
        pytest.fail(f"Reader error: {error[0]}")
    if len(results) < count:
        pytest.fail(f"EOF after {len(results)} lines (expected {count})")
    return results


# ══════════════════════════════════════════════════════════════════════════════
# Driver loop unit tests (sync_driver.py's own main)
# ══════════════════════════════════════════════════════════════════════════════


class TestDriverLoop:
    """Exercise the persistent main() loop by running the fake driver script
    (speaks the same line protocol, no anki dependency)."""

    def _spawn_driver(self, timeout: float = 10) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, _FAKE_DRIVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )

    @staticmethod
    def _close_proc(proc: subprocess.Popen) -> None:
        """Close all pipe handles so GC doesn't emit ResourceWarning."""
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.close()

    def test_multiple_commands_produce_one_result_each(self):
        """Two echo commands → two result lines, in order."""
        proc = self._spawn_driver()
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"op": "echo", "payload": "first"}) + "\n")
        proc.stdin.write(json.dumps({"op": "echo", "payload": "second"}) + "\n")
        proc.stdin.flush()

        results = _read_lines(proc, 2)
        assert results[0]["echo"] == "first"
        assert results[1]["echo"] == "second"
        assert results[0]["count"] < results[1]["count"]

        proc.stdin.close()
        proc.wait(timeout=5)
        self._close_proc(proc)

    def test_eof_exits_cleanly(self):
        """Closing stdin (EOF) makes the driver exit 0."""
        proc = self._spawn_driver()
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"op": "echo", "payload": "hi"}) + "\n")
        proc.stdin.flush()
        _read_lines(proc, 1)
        proc.stdin.close()
        rc = proc.wait(timeout=5)
        assert rc == 0
        self._close_proc(proc)

    def test_shutdown_op_exits(self):
        """The shutdown op emits ok and exits."""
        proc = self._spawn_driver()
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"op": "echo", "payload": "before"}) + "\n")
        proc.stdin.write(json.dumps({"op": "shutdown"}) + "\n")
        proc.stdin.flush()

        results = _read_lines(proc, 2)
        assert results[0]["echo"] == "before"
        assert results[1] == {"ok": True}
        rc = proc.wait(timeout=5)
        assert rc == 0
        self._close_proc(proc)

    def test_unknown_op_returns_error_and_continues(self):
        """Unknown op returns error dict, loop continues."""
        proc = self._spawn_driver()
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"op": "unknown_thing"}) + "\n")
        proc.stdin.write(json.dumps({"op": "echo", "payload": "after"}) + "\n")
        proc.stdin.flush()

        results = _read_lines(proc, 2)
        assert "error" in results[0]
        assert results[1]["echo"] == "after"
        proc.stdin.close()
        proc.wait(timeout=5)
        self._close_proc(proc)

    def test_bad_json_returns_error_and_continues(self):
        """Malformed input returns error dict, loop continues."""
        proc = self._spawn_driver()
        assert proc.stdin is not None
        proc.stdin.write("NOT JSON\n")
        proc.stdin.write(json.dumps({"op": "echo", "payload": "ok"}) + "\n")
        proc.stdin.flush()

        results = _read_lines(proc, 2)
        assert "error" in results[0]
        assert results[1]["echo"] == "ok"
        proc.stdin.close()
        proc.wait(timeout=5)
        self._close_proc(proc)

    def test_blank_lines_skipped(self):
        """Blank lines between commands are silently skipped."""
        proc = self._spawn_driver()
        assert proc.stdin is not None
        proc.stdin.write("\n\n\n")
        proc.stdin.write(json.dumps({"op": "echo", "payload": "x"}) + "\n")
        proc.stdin.flush()

        results = _read_lines(proc, 1)
        assert results[0]["echo"] == "x"
        proc.stdin.close()
        proc.wait(timeout=5)
        self._close_proc(proc)


# ══════════════════════════════════════════════════════════════════════════════
# Persistent driver process (orchestrator's _run_driver)
# ══════════════════════════════════════════════════════════════════════════════


def _spawn_fake_driver_proc() -> subprocess.Popen:
    """Start the fake driver (no anki) as a persistent process."""
    return subprocess.Popen(
        [sys.executable, _FAKE_DRIVER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )


class TestPersistentDriver:
    """Orchestrator's persistent driver: PID reuse, kill/respawn, stderr drain."""

    def _patch_driver_cmd(self, monkeypatch):
        """Make _driver_cmd return the fake driver invocation."""
        import app.anki.sync_orchestrator as so

        monkeypatch.setattr(so, "_driver_cmd", lambda: [sys.executable, _FAKE_DRIVER])

    def _cleanup_driver(self):
        import app.anki.sync_orchestrator as so

        with so._DRIVER_LOCK:
            so._kill_driver()

    def test_two_sequential_calls_reuse_pid(self, monkeypatch):
        """Two _run_driver calls in sequence reuse the same PID."""
        import app.anki.sync_orchestrator as so

        self._patch_driver_cmd(monkeypatch)
        try:
            r1 = so._run_driver({"op": "echo", "payload": "a"})
            pid1 = so._DRIVER_PROC.pid
            r2 = so._run_driver({"op": "echo", "payload": "b"})
            pid2 = so._DRIVER_PROC.pid
            assert r1["echo"] == "a"
            assert r2["echo"] == "b"
            assert pid1 == pid2, "must reuse the same process"
        finally:
            self._cleanup_driver()

    def test_kill_between_calls_respawns_and_retries(self, monkeypatch):
        """Kill the driver between calls; the second call spawns a new process."""
        import app.anki.sync_orchestrator as so

        self._patch_driver_cmd(monkeypatch)
        try:
            so._run_driver({"op": "echo", "payload": "a"})
            pid1 = so._DRIVER_PROC.pid
            # Kill the process externally
            killed = so._DRIVER_PROC
            killed.kill()
            killed.wait()
            for stream in (killed.stdin, killed.stdout, killed.stderr):
                if stream is not None:
                    with contextlib.suppress(OSError):
                        stream.close()
            so._DRIVER_PROC = None

            r2 = so._run_driver({"op": "echo", "payload": "b"})
            pid2 = so._DRIVER_PROC.pid
            assert r2["echo"] == "b"
            assert pid1 != pid2, "must respawn a new process"
        finally:
            self._cleanup_driver()

    def test_hanging_driver_is_killed_and_retried(self, monkeypatch):
        """A driver that hangs past timeout → kill/respawn/retry succeeds."""
        import app.anki.sync_orchestrator as so

        self._patch_driver_cmd(monkeypatch)
        try:
            # The slow op takes 30s but timeout is 0.5s → transport failure.
            # After kill/respawn, the next attempt on a fresh process also times out
            # because we're still sending the slow op.
            with pytest.raises(PeerSyncError, match="Driver failed after retry"):
                so._run_driver({"op": "slow", "delay_s": 30}, timeout=0.5)
        finally:
            self._cleanup_driver()

    def test_driver_transport_fails_twice_raises(self, monkeypatch):
        """Driver transport failure on both attempts → PeerSyncError after retry."""
        import app.anki.sync_orchestrator as so

        # Patch _driver_cmd to return a script that exits immediately (EOF on stdout).
        monkeypatch.setattr(
            so,
            "_driver_cmd",
            lambda: [sys.executable, "-c", "import sys; sys.stdout = sys.stderr"],
        )
        try:
            with pytest.raises(PeerSyncError, match="Driver failed after retry"):
                so._run_driver({"op": "echo", "payload": "x"}, timeout=5)
        finally:
            self._cleanup_driver()

    def test_stderr_chatter_doesnt_deadlock(self, monkeypatch):
        """A driver that floods stderr doesn't block a large response."""
        import app.anki.sync_orchestrator as so

        self._patch_driver_cmd(monkeypatch)
        try:
            result = so._run_driver({"op": "stderr_flood", "lines": 500})
            assert result["ok"] is True
        finally:
            self._cleanup_driver()

    def test_transport_failure_message_includes_driver_stderr(self, monkeypatch):
        """A driver that dies mid-command leaves its stderr in the error message.

        Regression: the pre-fix code looked stderr up via the _DRIVER_PROC global
        AFTER _kill_driver had cleared it, so every transport error shipped with
        an empty stderr section — the one diagnostic that explains WHY the driver
        died was always missing.
        """
        import app.anki.sync_orchestrator as so

        self._patch_driver_cmd(monkeypatch)
        try:
            with pytest.raises(PeerSyncError, match="Driver failed after retry") as exc_info:
                so._run_driver({"op": "die", "last_words": "GROQ_MARKER_out_of_disk"}, timeout=5)
            assert "GROQ_MARKER_out_of_disk" in str(exc_info.value)
        finally:
            self._cleanup_driver()

    def test_atexit_noop_without_driver(self):
        """The atexit handler is a no-op when no driver is running."""
        import app.anki.sync_orchestrator as so

        self._cleanup_driver()  # deterministic: no live driver
        assert so._DRIVER_PROC is None
        so._atexit_shutdown_driver()  # must not raise

    def test_atexit_closes_stdin(self, monkeypatch):
        """The atexit handler closes driver stdin so it exits on interpreter shutdown."""
        import app.anki.sync_orchestrator as so

        self._patch_driver_cmd(monkeypatch)
        try:
            so._run_driver({"op": "echo", "payload": "x"})
            assert so._DRIVER_PROC is not None
            assert so._DRIVER_PROC.poll() is None
            so._atexit_shutdown_driver()
            # After atexit, the process should exit (stdin closed → EOF → loop exits)
            rc = so._DRIVER_PROC.wait(timeout=5)
            assert rc == 0
        finally:
            self._cleanup_driver()


# ══════════════════════════════════════════════════════════════════════════════
# Coverage gaps in sync_orchestrator.py
# ══════════════════════════════════════════════════════════════════════════════


class TestOrchestratorCoverage:
    """Targeted tests for uncovered branches in sync_orchestrator.py."""

    def test_driver_cmd_returns_list(self):
        """_driver_cmd() returns a non-empty command list (line 268)."""
        from app.anki.sync_orchestrator import _driver_cmd

        cmd = _driver_cmd()
        assert isinstance(cmd, list)
        assert len(cmd) > 0
        assert cmd[0] == "uv"

    def test_drain_stderr_catches_pipe_close(self):
        """_drain_stderr handles ValueError when the stderr pipe is closed (lines 59-60)."""
        import collections
        import threading
        from types import SimpleNamespace

        from app.anki.sync_orchestrator import _drain_stderr

        class BrokenStderr:
            """Iterating raises ValueError (pipe closed under us)."""

            def __iter__(self):
                raise ValueError("I/O operation on closed file")
                yield  # make it an iterator  # pragma: no cover

        buf: collections.deque[str] = collections.deque(maxlen=200)
        proc = SimpleNamespace(stderr=BrokenStderr())
        t = threading.Thread(target=_drain_stderr, args=(proc, buf))
        t.start()
        t.join(timeout=5)
        assert list(buf) == []

    def test_kill_driver_with_none_streams(self):
        """_kill_driver tolerates a process with None streams (branch 104→103)."""
        import app.anki.sync_orchestrator as so

        class FakeProc:
            stdin = None
            stdout = None
            stderr = None

            def kill(self):
                pass

            def wait(self, **kw):
                pass

        with so._DRIVER_LOCK:
            so._DRIVER_PROC = FakeProc()  # type: ignore[assignment]
            result = so._kill_driver()
        assert result == ""

    def test_kill_driver_with_no_stderr_thread(self):
        """_kill_driver tolerates a process without _stderr_thread (branch 108→110)."""
        import app.anki.sync_orchestrator as so

        class FakeStream:
            def close(self):
                pass

        class FakeProc:
            stdin = FakeStream()
            stdout = FakeStream()
            stderr = FakeStream()
            # No _stderr_thread attribute

            def kill(self):
                pass

            def wait(self, **kw):
                pass

        with so._DRIVER_LOCK:
            so._DRIVER_PROC = FakeProc()  # type: ignore[assignment]
            result = so._kill_driver()
        assert result == ""

    def test_run_driver_error_result_raises(self, monkeypatch):
        """_run_driver_locked raises PeerSyncError when the driver returns an error (line 340)."""
        import app.anki.sync_orchestrator as so

        monkeypatch.setattr(so, "_driver_cmd", lambda: [sys.executable, _FAKE_DRIVER])
        try:
            with pytest.raises(PeerSyncError, match="deliberate error"):
                so._run_driver({"op": "error", "msg": "deliberate error"})
        finally:
            with so._DRIVER_LOCK:
                so._kill_driver()


# ══════════════════════════════════════════════════════════════════════════════
# Push-leg media gating (Fix B)
# ══════════════════════════════════════════════════════════════════════════════


class TestPushLegMediaGating:
    """Verify that the push leg's sync_media depends on media_pending."""

    @pytest.fixture(autouse=True)
    def _clear_auth(self):
        import app.anki.sync_orchestrator as so

        so._AUTH_CACHE = None
        yield
        so._AUTH_CACHE = None

    def _make_fake_driver_with_media_pending(self, monkeypatch, pending_count: int):
        """Create a fake _run_driver that tracks ops and returns media_pending count."""
        import app.anki.sync_orchestrator as so

        op_log: list[dict] = []

        def _fake(command: dict, timeout: int = 120) -> dict:
            op_log.append(command)
            op = command.get("op", "")
            if op == "login":
                return {"hkey": "fake-hkey", "endpoint": "http://localhost/"}
            if op == "sync":
                return {"required": 1, "server_message": "OK"}
            if op == "media_pending":
                return {"pending": pending_count}
            return {"error": f"unknown op: {op}"}

        monkeypatch.setattr(so, "_run_driver", _fake)
        return op_log

    def test_media_pending_zero_skips_media_on_push(self, monkeypatch):
        """media_pending → 0: push leg has sync_media=False; pull leg always True."""
        from app.config import settings
        from tests.anki_oracle.synthetic_collection import SyntheticCollection

        coll = SyntheticCollection(settings.tt_collection_path)
        coll.set_deck(settings.anki_deck_name, 1)
        coll.add_notetype(1704067201, "Cloze", ("Text", "Back Extra"), template_count=1)
        coll.save()
        monkeypatch.setattr(settings, "anki_model_name", "Cloze")

        op_log = self._make_fake_driver_with_media_pending(monkeypatch, pending_count=0)

        from app.srs.database import SRSDatabase

        db = SRSDatabase(settings.database_url)
        db.add_collocation(
            __import__("app.models.syntactic_unit", fromlist=["SyntacticUnit"]).SyntacticUnit(
                text="MediaGating",
                translation="test",
                word_count=1,
                difficulty=1,
                source="test",
                source_sentence="MediaGating test.",
                card_type="cloze",
            ),
            language_code="sl",
        )

        from app.anki.sync_orchestrator import peer_sync

        report = peer_sync(dry_run=False)
        assert report.tt_push_pull_exit == 0

        sync_cmds = [c for c in op_log if c["op"] == "sync"]
        # Pull leg always has sync_media=True
        assert sync_cmds[0]["sync_media"] is True
        # Push leg: media_pending=0 → sync_media=False
        assert sync_cmds[1]["sync_media"] is False

    def test_media_pending_positive_enables_media_on_push(self, monkeypatch):
        """media_pending → 3: push leg has sync_media=True."""
        from app.config import settings
        from tests.anki_oracle.synthetic_collection import SyntheticCollection

        coll = SyntheticCollection(settings.tt_collection_path)
        coll.set_deck(settings.anki_deck_name, 1)
        coll.add_notetype(1704067201, "Cloze", ("Text", "Back Extra"), template_count=1)
        coll.save()
        monkeypatch.setattr(settings, "anki_model_name", "Cloze")

        op_log = self._make_fake_driver_with_media_pending(monkeypatch, pending_count=3)

        from app.srs.database import SRSDatabase

        db = SRSDatabase(settings.database_url)
        db.add_collocation(
            __import__("app.models.syntactic_unit", fromlist=["SyntacticUnit"]).SyntacticUnit(
                text="MediaGating2",
                translation="test",
                word_count=1,
                difficulty=1,
                source="test",
                source_sentence="MediaGating2 test.",
                card_type="cloze",
            ),
            language_code="sl",
        )

        from app.anki.sync_orchestrator import peer_sync

        report = peer_sync(dry_run=False)
        assert report.tt_push_pull_exit == 0

        sync_cmds = [c for c in op_log if c["op"] == "sync"]
        assert sync_cmds[0]["sync_media"] is True  # pull always True
        assert sync_cmds[1]["sync_media"] is True  # push: pending=3 → True

    def test_media_pending_unknown_enables_media_on_push(self, monkeypatch):
        """media_pending → -1 (unknown): push leg has sync_media=True (safe default)."""
        from app.config import settings
        from tests.anki_oracle.synthetic_collection import SyntheticCollection

        coll = SyntheticCollection(settings.tt_collection_path)
        coll.set_deck(settings.anki_deck_name, 1)
        coll.add_notetype(1704067201, "Cloze", ("Text", "Back Extra"), template_count=1)
        coll.save()
        monkeypatch.setattr(settings, "anki_model_name", "Cloze")

        op_log = self._make_fake_driver_with_media_pending(monkeypatch, pending_count=-1)

        from app.srs.database import SRSDatabase

        db = SRSDatabase(settings.database_url)
        db.add_collocation(
            __import__("app.models.syntactic_unit", fromlist=["SyntacticUnit"]).SyntacticUnit(
                text="MediaGating3",
                translation="test",
                word_count=1,
                difficulty=1,
                source="test",
                source_sentence="MediaGating3 test.",
                card_type="cloze",
            ),
            language_code="sl",
        )

        from app.anki.sync_orchestrator import peer_sync

        report = peer_sync(dry_run=False)
        assert report.tt_push_pull_exit == 0

        sync_cmds = [c for c in op_log if c["op"] == "sync"]
        assert sync_cmds[0]["sync_media"] is True  # pull always True
        assert sync_cmds[1]["sync_media"] is True  # push: pending=-1 (unknown) → True

    def test_pull_leg_always_media_enabled(self, monkeypatch):
        """The pull leg is always sync_media=True regardless of media_pending."""
        from app.config import settings
        from tests.anki_oracle.synthetic_collection import SyntheticCollection

        coll = SyntheticCollection(settings.tt_collection_path)
        coll.set_deck(settings.anki_deck_name, 1)
        coll.add_notetype(1704067201, "Cloze", ("Text", "Back Extra"), template_count=1)
        coll.save()
        monkeypatch.setattr(settings, "anki_model_name", "Cloze")

        op_log = self._make_fake_driver_with_media_pending(monkeypatch, pending_count=0)

        from app.anki.sync_orchestrator import peer_sync

        report = peer_sync(dry_run=False)
        assert report.tt_push_pull_exit == 0

        sync_cmds = [c for c in op_log if c["op"] == "sync"]
        # Pull leg (first sync) must always have sync_media=True
        assert sync_cmds[0]["sync_media"] is True

    def test_media_pending_op_called_before_push(self, monkeypatch):
        """media_pending is called between pending_check and push sync."""
        from app.config import settings
        from tests.anki_oracle.synthetic_collection import SyntheticCollection

        coll = SyntheticCollection(settings.tt_collection_path)
        coll.set_deck(settings.anki_deck_name, 1)
        coll.add_notetype(1704067201, "Cloze", ("Text", "Back Extra"), template_count=1)
        coll.save()
        monkeypatch.setattr(settings, "anki_model_name", "Cloze")

        op_log = self._make_fake_driver_with_media_pending(monkeypatch, pending_count=0)

        from app.srs.database import SRSDatabase

        db = SRSDatabase(settings.database_url)
        db.add_collocation(
            __import__("app.models.syntactic_unit", fromlist=["SyntacticUnit"]).SyntacticUnit(
                text="MediaGating4",
                translation="test",
                word_count=1,
                difficulty=1,
                source="test",
                source_sentence="MediaGating4 test.",
                card_type="cloze",
            ),
            language_code="sl",
        )

        from app.anki.sync_orchestrator import peer_sync

        report = peer_sync(dry_run=False)
        assert report.tt_push_pull_exit == 0

        op_names = [c["op"] for c in op_log]
        mp_idx = op_names.index("media_pending")
        push_idx = op_names.index("sync", mp_idx)
        assert mp_idx < push_idx, "media_pending must be called before the push sync"
        # And media_pending_check must be in the timings
        assert "media_pending_check" in report.timings

    def test_push_timing_excludes_media_pending_check(self, monkeypatch):
        """timings["push"] measures only the push leg, not the pending check.

        Regression: the pre-fix code reused the same t0 for both, so a slow
        media_pending check silently inflated the push timing in
        PEER_SYNC_TIMING — the log line whose whole purpose is attributing
        slowness to the right leg.
        """
        import time as _time

        from app.config import settings
        from tests.anki_oracle.synthetic_collection import SyntheticCollection

        coll = SyntheticCollection(settings.tt_collection_path)
        coll.set_deck(settings.anki_deck_name, 1)
        coll.add_notetype(1704067201, "Cloze", ("Text", "Back Extra"), template_count=1)
        coll.save()
        monkeypatch.setattr(settings, "anki_model_name", "Cloze")

        import app.anki.sync_orchestrator as so

        op_log: list[dict] = []

        def _fake(command: dict, timeout: int = 120) -> dict:
            op_log.append(command)
            op = command.get("op", "")
            if op == "login":
                return {"hkey": "fake-hkey", "endpoint": "http://localhost/"}
            if op == "sync":
                return {"required": 1, "server_message": "OK"}
            if op == "media_pending":
                _time.sleep(0.5)  # slow pending check must not leak into "push"
                return {"pending": 0}
            return {"error": f"unknown op: {op}"}

        monkeypatch.setattr(so, "_run_driver", _fake)

        from app.srs.database import SRSDatabase

        db = SRSDatabase(settings.database_url)
        db.add_collocation(
            __import__("app.models.syntactic_unit", fromlist=["SyntacticUnit"]).SyntacticUnit(
                text="MediaGating5",
                translation="test",
                word_count=1,
                difficulty=1,
                source="test",
                source_sentence="MediaGating5 test.",
                card_type="cloze",
            ),
            language_code="sl",
        )

        from app.anki.sync_orchestrator import peer_sync

        report = peer_sync(dry_run=False)
        assert report.tt_push_pull_exit == 0
        assert report.timings["media_pending_check"] >= 0.5
        assert report.timings["push"] < 0.4, (
            f"push timing {report.timings['push']:.2f}s includes the media_pending check"
        )


# ══════════════════════════════════════════════════════════════════════════════
# _await_media_sync backoff
# ══════════════════════════════════════════════════════════════════════════════


class TestAwaitMediaSyncBackoff:
    """Verify the returned observability dict for _await_media_sync backoff."""

    def test_idle_case_bails_after_few_polls(self):
        """When media never goes active, polls bail after 5 polls."""
        from app.anki.sync_driver import _await_media_sync

        class FakeStatus:
            active = False

        class FakeCol:
            def media_sync_status(self):
                return FakeStatus()

        result = _await_media_sync(FakeCol(), timeout_s=5.0)
        assert result["completed"] is True
        assert result["saw_active"] is False
        assert result["polls"] <= 6  # up to 5 polls + maybe one more
        assert result["timed_out"] is False

    def test_active_then_done(self):
        """When media goes active then finishes, the dict reflects it."""
        from app.anki.sync_driver import _await_media_sync

        call_count = 0

        class FakeStatus:
            active = False

        class FakeCol:
            def media_sync_status(self):
                nonlocal call_count
                call_count += 1
                s = FakeStatus()
                if call_count <= 2:
                    s.active = True
                return s

        result = _await_media_sync(FakeCol(), timeout_s=5.0)
        assert result["completed"] is True
        assert result["saw_active"] is True
        assert result["timed_out"] is False

    def test_exception_recorded(self):
        """A media_sync_status() exception is captured in the dict."""
        from app.anki.sync_driver import _await_media_sync

        class FakeCol:
            def media_sync_status(self):
                raise RuntimeError("media sync failed")

        result = _await_media_sync(FakeCol(), timeout_s=5.0)
        assert result["completed"] is False
        assert "media sync failed" in result["error"]

    def test_backoff_timing(self):
        """Poll intervals back off ×1.5, capped at 0.2s. Verify via elapsed time."""
        from app.anki.sync_driver import _await_media_sync

        class FakeStatus:
            active = False

        class FakeCol:
            def media_sync_status(self):
                return FakeStatus()

        # With poll_s=0.05 and 5 polls:
        # poll1: sleep 0.05, poll2: sleep 0.075, poll3: sleep 0.1125, poll4: sleep 0.16875, poll5: sleep 0.2
        # Total sleep ≈ 0.05 + 0.075 + 0.1125 + 0.16875 + 0.2 = 0.60625
        result = _await_media_sync(FakeCol(), timeout_s=5.0, poll_s=0.05)
        # Should complete in under 1s (the old 0.2 fixed would take 1.0s for 5 polls)
        assert result["elapsed_s"] < 1.0
        assert result["polls"] <= 6
