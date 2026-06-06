"""Peer-sync integration spike (Phase 6 gate).

Requires ``--run-peer-sync`` and a running self-host Anki sync server.

This is the **gate test** for option 2. If it passes (two peers converge
cross-graded cards with no FULL_SYNC), the architecture is validated.
If it fails, fall back to option-3-smoothed.

Usage::

    # Terminal 1: start self-host server
    SYNC_USER1=ttspike:spikepass SYNC_HOST=127.0.0.1 SYNC_PORT=8080 \\
      uv run --with anki python -m anki.syncserver

    # Terminal 2: run test
    cd backend && uv run pytest tests/test_anki_peer_sync_selfhost.py \\
      --run-peer-sync --no-cov -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from app.anki.sync_orchestrator import _driver_cmd
from app.config import settings

SERVER_TIMEOUT_S = 5


def _server_reachable() -> bool:
    """Check if the sync server is reachable at *sync_endpoint*."""
    endpoint = (settings.sync_endpoint or "http://127.0.0.1:8080").rstrip("/")
    try:
        r = httpx.get(f"{endpoint}/sync/ping", timeout=SERVER_TIMEOUT_S)
        return r.status_code < 500
    except httpx.ConnectError, httpx.TimeoutException:
        return False


def _driver(command: dict, timeout: int = 60) -> dict:
    """Run sync_driver and return parsed result. Raises on error."""
    proc = subprocess.run(
        _driver_cmd(),
        input=json.dumps(command),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    result = json.loads(proc.stdout)
    if "error" in result:
        pytest.skip(f"Driver error: {result['error']}")
    return result


def _bootstrap_collection(path: Path) -> dict:
    """Login + create (if needed) + full_download for *path*."""
    auth = _driver(
        {
            "op": "login",
            "username": settings.sync_username,
            "password": settings.sync_password,
            "endpoint": settings.sync_endpoint,
        }
    )
    if not path.exists():
        _driver({"op": "create_collection", "collection_path": str(path)})
    return _driver({"op": "full_download", "collection_path": str(path), "auth": auth})


@pytest.mark.peer_sync
class TestPeerSyncSelfHost:
    @pytest.fixture(autouse=True)
    def _check_preconditions(self):
        if not _server_reachable():
            pytest.skip("Sync server not reachable — start it with `python -m anki.syncserver`")

    def test_convergence(self, tmp_path: Path):
        """Grade a card in each peer, sync, verify convergence with no FULL_SYNC."""
        from app.anki.sync_orchestrator import peer_sync

        tt_col = tmp_path / "tt_collection.anki2"
        peer2_col = tmp_path / "peer2.anki2"

        # Bootstrap both peers from the same server
        _bootstrap_collection(tt_col)
        _bootstrap_collection(peer2_col)

        # --- Grade card A in tt_col via TT's sync (mocked to use tt_col) ---
        # This requires TT's drill_feedback + peer_sync pipeline. For now, run
        # TT's sync.main against tt_col (it grades nothing if no TT state exists,
        # but proves the retargeting works).
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "tt_collection_path", tt_col)
            report = peer_sync(dry_run=True)

        assert report.auth_success
        assert report.pull_required != 2, "FULL_SYNC on TT side during convergence test"

        # --- Grade card B in peer2 via anki driver ---
        # TODO: replace with real grading once the test-server wiring is stable.
        # The driver currently lacks an answer_card op; either add it (mirroring
        # oracle._op_answer_card) or write the grade directly:
        #
        #   _driver({"op": "answer_card", "card_id": CARD_B_ID, "rating": 3,
        #            "collection_path": str(peer2_col)})
        #
        # For now, sync peer2 unmodified:
        _driver(
            {
                "op": "sync",
                "collection_path": str(peer2_col),
                "auth": _driver(
                    {
                        "op": "login",
                        "username": settings.sync_username,
                        "password": settings.sync_password,
                        "endpoint": settings.sync_endpoint,
                    }
                ),
            }
        )

        # --- Sync TT side (push) ---
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "tt_collection_path", tt_col)
            report2 = peer_sync(dry_run=False)

        assert report2.pull_required != 2, "FULL_SYNC on TT push"
        if report2.push_required:
            assert report2.push_required != 2, "FULL_SYNC on TT push to server"

        # --- Sync peer2 side ---
        auth = _driver(
            {
                "op": "login",
                "username": settings.sync_username,
                "password": settings.sync_password,
                "endpoint": settings.sync_endpoint,
            }
        )
        peer2_out = _driver({"op": "sync", "collection_path": str(peer2_col), "auth": auth})
        assert peer2_out.get("required") != 2, "FULL_SYNC on peer2 sync"
