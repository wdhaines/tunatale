"""Peer-sync integration gate (Phase 6).

Requires ``--run-peer-sync`` and a running self-host Anki sync server.

This is the **gate test** for option 2 (TT as an AnkiWeb sync peer). It seeds two
cards, grades a *different* card in each of two peers, syncs, and verifies each
peer receives the other's grade — real bidirectional convergence — with no
FULL_SYNC at any step. The TT side syncs through the actual ``peer_sync``
orchestrator (the production entry point); the second peer stands in for a
desktop/AnkiDroid device via the raw driver. If this passes, the architecture is
validated; if it fails, fall back to option-3-smoothed.

Usage::

    # Terminal 1: start a self-host server (isolated 3.14 — anki's protobuf can't
    # import under the project env; see app/anki/sync_driver.py).
    SYNC_USER1=ttspike:spikepass SYNC_HOST=127.0.0.1 SYNC_PORT=8080 \\
      uv run --isolated --no-project --python 3.14 --with anki python -m anki.syncserver

    # Terminal 2: run the gate (creds + endpoint via env → Settings)
    cd backend && sync_endpoint=http://127.0.0.1:8080/ sync_username=ttspike \\
      sync_password=spikepass uv run pytest tests/test_anki_peer_sync_selfhost.py \\
      --run-peer-sync --no-cov -v
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import httpx
import pytest

from app.anki.sync_orchestrator import _driver_cmd, peer_sync
from app.config import settings

SERVER_TIMEOUT_S = 5
# The first isolated `uv run --with anki` may build the ephemeral env from a cold
# cache; give each driver call headroom.
_DRIVER_TIMEOUT_S = 180


def _server_reachable() -> bool:
    """Check if the sync server is reachable at *sync_endpoint*."""
    endpoint = (settings.sync_endpoint or "http://127.0.0.1:8080").rstrip("/")
    try:
        r = httpx.get(f"{endpoint}/sync/ping", timeout=SERVER_TIMEOUT_S)
        return r.status_code < 500
    except httpx.ConnectError, httpx.TimeoutException:
        return False


def _driver(command: dict, timeout: int = _DRIVER_TIMEOUT_S) -> dict:
    """Run sync_driver and return its parsed result.

    Fails (never skips) on a driver error or unparseable output: under
    --run-peer-sync the server is up and the driver is expected to work, so a
    failure here is a real failure, not something to silently skip past.
    """
    proc = subprocess.run(
        _driver_cmd(),
        input=json.dumps(command),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError:
        pytest.fail(
            f"driver produced no valid JSON for op={command.get('op')!r}\n"
            f"stdout: {proc.stdout!r}\nstderr: {proc.stderr}"
        )
    if "error" in result:
        pytest.fail(f"driver error for op={command.get('op')!r}: {result['error']}")
    return result


def _login() -> dict:
    return _driver(
        {
            "op": "login",
            "username": settings.sync_username,
            "password": settings.sync_password,
            "endpoint": settings.sync_endpoint,
        }
    )


def _assert_incremental(required: int | None, where: str) -> None:
    """A sync that actually merged returns NO_CHANGES (0) or NORMAL_SYNC (1);
    2/3/4 (FULL_SYNC/DOWNLOAD/UPLOAD) mean the incremental sync did not happen."""
    assert required in (0, 1), f"{where}: expected incremental sync, got required={required}"


@pytest.mark.peer_sync
class TestPeerSyncSelfHost:
    @pytest.fixture(autouse=True)
    def _check_preconditions(self):
        # Legitimate external-infra gate: the integration test cannot run without a
        # server. (The dangerous silent skip — a *driver error* masquerading as a
        # pass — is handled by _driver, which fails.)
        if not _server_reachable():
            pytest.skip("Sync server not reachable — start it with `python -m anki.syncserver`")

    def test_bidirectional_convergence(self, tmp_path: Path):
        """Grade a different card in each peer; verify each receives the other's
        grade with no FULL_SYNC. TT side goes through the real peer_sync bracket."""
        tt_col = tmp_path / "tt_collection.anki2"
        peer2_col = tmp_path / "peer2.anki2"
        auth = _login()

        # --- Seed: TT creates two cards (each in its own deck so it is the sole
        # queue head — anki's answer_card requires grading the top card) and
        # uploads them as the server baseline ---
        _driver({"op": "create_collection", "collection_path": str(tt_col)})
        card_a = _driver(
            {"op": "add_note", "collection_path": str(tt_col), "deck": "PeerSyncA", "fields": ["A front", "A back"]}
        )["card_ids"][0]
        card_b = _driver(
            {"op": "add_note", "collection_path": str(tt_col), "deck": "PeerSyncB", "fields": ["B front", "B back"]}
        )["card_ids"][0]
        _driver({"op": "full_upload", "collection_path": str(tt_col), "auth": auth})

        # --- peer2 (stand-in desktop/mobile device) mirrors the server ---
        _driver({"op": "create_collection", "collection_path": str(peer2_col)})
        _driver({"op": "full_download", "collection_path": str(peer2_col), "auth": auth})

        # --- Grade A in TT, push via the real peer_sync orchestrator ---
        _driver(
            {"op": "answer_card", "collection_path": str(tt_col), "deck": "PeerSyncA", "card_id": card_a, "rating": 3}
        )
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "tt_collection_path", tt_col)
            report = peer_sync(dry_run=False)
        assert report.auth_success
        assert report.tt_push_pull_exit == 0
        _assert_incremental(report.pull_required, "peer_sync pull (A)")
        _assert_incremental(report.push_required, "peer_sync push (A)")

        # --- Grade B in peer2, push (bidirectional → also pulls A) ---
        _driver(
            {
                "op": "answer_card",
                "collection_path": str(peer2_col),
                "deck": "PeerSyncB",
                "card_id": card_b,
                "rating": 3,
            }
        )
        _assert_incremental(
            _driver({"op": "sync", "collection_path": str(peer2_col), "auth": auth})["required"],
            "peer2 sync (B)",
        )

        # --- TT pulls B via peer_sync ---
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "tt_collection_path", tt_col)
            report2 = peer_sync(dry_run=False)
        _assert_incremental(report2.pull_required, "peer_sync pull (B)")

        # --- Convergence: each peer has the other's grade ---
        tt_b = _driver({"op": "get_card", "collection_path": str(tt_col), "card_id": card_b})
        peer2_a = _driver({"op": "get_card", "collection_path": str(peer2_col), "card_id": card_a})
        assert tt_b["reps"] > 0, "TT did not receive peer2's grade of card B"
        assert peer2_a["reps"] > 0, "peer2 did not receive TT's grade of card A"
