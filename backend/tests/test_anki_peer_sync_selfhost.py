"""Peer-sync integration gate (Phase 6).

Requires ``--run-peer-sync``. The server is **auto-started** (throwaway
credentials ``tt-test`` / ``tt-test-pw```, free port, temporary ``SYNC_BASE``)
via the session-scoped ``selfhost_sync_server`` fixture.

If you already set ``sync_endpoint``, ``sync_username``, ``sync_password`` (e.g.
via env vars pointing at a hand-started server), the fixture reuses it instead
of spawning — preserving the manual two-terminal workflow::

    # Terminal 1: start a self-host server:
    SYNC_USER1="$USER:$PASS" SYNC_HOST=127.0.0.1 SYNC_PORT=8080 \\
      uv run --isolated --no-project --python 3.14 --with anki python -m anki.syncserver

    # Terminal 2: run the gate (creds + endpoint via env):
    cd backend && sync_endpoint=http://127.0.0.1:8080/ sync_username="$USER" \\
      sync_password="$PASS" uv run pytest tests/test_anki_peer_sync_selfhost.py \\
      --run-peer-sync --no-cov -v

This is the **gate test** for option 2 (TT as an AnkiWeb sync peer). It seeds two
cards, grades a *different* card in each of two peers, syncs, and verifies each
peer receives the other's grade — real bidirectional convergence — with no
FULL_SYNC at any step. The TT side syncs through the actual ``peer_sync``
orchestrator (the production entry point); the second peer stands in for a
desktop/AnkiDroid device via the raw driver. If this passes, the architecture is
validated; if it fails, fall back to option-3-smoothed.

This file runs **serially** (one server per session, no cross-worker races).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.anki.sync_orchestrator import _driver_cmd, peer_sync
from app.config import settings

# The first isolated `uv run --with anki` may build the ephemeral env from a cold
# cache; give each driver call headroom.
_DRIVER_TIMEOUT_S = 180


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


@pytest.fixture(autouse=True)
def _auto_server(selfhost_sync_server, monkeypatch: pytest.MonkeyPatch):
    """Pin sync credentials from the session-scoped server fixture into settings.

    The session-scoped ``selfhost_sync_server`` fixture handles server lifecycle
    (spawn or reuse once per session).  This fixture reads the returned
    credentials and pins them function-scoped so every test sees the intended
    endpoint.  It runs **after** conftest's ``_settings_overrides`` (same
    function scope), so its override of ``sync_password`` wins over the generic
    dummy.
    """
    endpoint, username, password = selfhost_sync_server
    monkeypatch.setattr(settings, "sync_endpoint", endpoint)
    monkeypatch.setattr(settings, "sync_username", username)
    monkeypatch.setattr(settings, "sync_password", password)


@pytest.mark.peer_sync
class TestPeerSyncSelfHost:
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

    def test_tt_lapse_newer_than_remote_propagates(self, tmp_path: Path):
        """Layer 69 at the integration level: a TT lapse (Again → relearning) graded
        AFTER the card's last change on the server must propagate through peer_sync —
        not be discarded in favour of the server's graduated (review) state. This is
        the lapse-newer-than-remote case the fresh-grade convergence test missed.
        """
        import sqlite3
        from datetime import UTC, datetime, timedelta

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        tt_col = tmp_path / "tt_collection.anki2"
        peer2_col = tmp_path / "peer2.anki2"
        auth = _login()

        # Seed one card in the TT deck, then force it to GRADUATED (review) with a mod
        # one day in the past (older than the TT grade we make below).
        _driver({"op": "create_collection", "collection_path": str(tt_col)})
        add = _driver(
            {
                "op": "add_note",
                "collection_path": str(tt_col),
                "deck": settings.anki_deck_name,
                "fields": ["lapse front", "lapse back"],
            }
        )
        card_id = add["card_ids"][0]
        note_id = add["note_id"]
        old_mod = int(datetime.now(UTC).timestamp()) - 86400
        conn = sqlite3.connect(str(tt_col))
        conn.execute(
            "UPDATE cards SET type=2, queue=2, ivl=10, due=100, reps=12, lapses=0, mod=?, "
            'data=\'{"pos":1,"s":50.0,"d":5.0,"dr":0.9,"decay":0.5,"lrt":1}\' WHERE id=?',
            (old_mod, card_id),
        )
        conn.commit()
        conn.close()
        _driver({"op": "full_upload", "collection_path": str(tt_col), "auth": auth})

        _driver({"op": "create_collection", "collection_path": str(peer2_col)})
        _driver({"op": "full_download", "collection_path": str(peer2_col), "auth": auth})

        # In TT's DB: a collocation linked to that card, just graded "Again" (newer
        # than old_mod) → relearning + dirty.
        db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        try:
            db.add_collocation(
                SyntacticUnit(text="lapsetest", translation="lapse", word_count=1, difficulty=1, source="corpus")
            )
            guid = db.get_collocation("lapsetest").guid
            db.set_anki_ids(guid, note_id, {Direction.PRODUCTION: card_id})
            now = datetime.now(UTC)
            db.update_direction(
                guid,
                Direction.PRODUCTION,
                DirectionState(
                    direction=Direction.PRODUCTION,
                    state=SRSState.RELEARNING,
                    left=1001,
                    due_at=now + timedelta(minutes=10),
                    reps=12,
                    lapses=1,
                    anki_card_id=card_id,
                    dirty_fsrs=True,
                    last_rating=1,
                    last_review=now,
                    prior_state=SRSState.REVIEW,
                ),
            )
        finally:
            db.close()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "tt_collection_path", tt_col)
            report = peer_sync(dry_run=False)
        assert report.tt_push_pull_exit == 0
        _assert_incremental(report.pull_required, "lapse pull")
        _assert_incremental(report.push_required, "lapse push")

        # peer2 pulls; the card must now be RELEARNING (queue=1 / type=3), not the
        # stale review (queue=2) it would be if the lapse had been discarded.
        _driver({"op": "sync", "collection_path": str(peer2_col), "auth": auth})
        peer2_card = _driver({"op": "get_card", "collection_path": str(peer2_col), "card_id": card_id})
        assert peer2_card["queue"] == 1, f"lapse not propagated — peer2 card still {peer2_card}"
        assert peer2_card["type"] == 3

    def test_per_grade_revlog_round_trip(self, tmp_path: Path):
        """Layer 80 round-trip: two TT grades on one card (lapse + relearn step)
        pushed through peer_sync, then verify the server's revlog holds both rows
        at their exact tt ids. Run peer_sync a second time — still exactly two
        rows and reviews_today not inflated."""
        import sqlite3
        from datetime import UTC, datetime, timedelta

        from app.models.srs_item import Direction, DirectionState, RevlogRow, SRSState
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        tt_col = tmp_path / "tt_collection.anki2"
        peer2_col = tmp_path / "peer2.anki2"
        auth = _login()

        # Create a card and upload as baseline.
        _driver({"op": "create_collection", "collection_path": str(tt_col)})
        add = _driver(
            {
                "op": "add_note",
                "collection_path": str(tt_col),
                "deck": settings.anki_deck_name,
                "fields": ["pergrade front", "pergrade back"],
            }
        )
        card_id = add["card_ids"][0]
        note_id = add["note_id"]
        _driver({"op": "full_upload", "collection_path": str(tt_col), "auth": auth})

        _driver({"op": "create_collection", "collection_path": str(peer2_col)})
        _driver({"op": "full_download", "collection_path": str(peer2_col), "auth": auth})

        # Seed two tt_revlog rows (lapse + relearn step) and mark direction dirty.
        db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        try:
            db.add_collocation(
                SyntacticUnit(text="pergrade", translation="pg", word_count=1, difficulty=1, source="corpus")
            )
            guid = db.get_collocation("pergrade").guid
            db.set_anki_ids(guid, note_id, {Direction.PRODUCTION: card_id})
            coll_id = db.get_collocation_id_by_guid(guid)
            now = datetime.now(UTC)
            lapse_ms = int((now - timedelta(minutes=5)).timestamp() * 1000)
            relearn_ms = int(now.timestamp() * 1000)
            db.append_revlog(
                RevlogRow(
                    id=lapse_ms,
                    collocation_id=coll_id,
                    direction=Direction.PRODUCTION,
                    button_chosen=1,
                    interval=-600,
                    last_interval=10,
                    factor=0,
                    taken_millis=2500,
                    review_kind=1,
                    anki_card_id=card_id,
                )
            )
            db.append_revlog(
                RevlogRow(
                    id=relearn_ms,
                    collocation_id=coll_id,
                    direction=Direction.PRODUCTION,
                    button_chosen=3,
                    interval=-60,
                    last_interval=-600,
                    factor=0,
                    taken_millis=1800,
                    review_kind=2,
                    anki_card_id=card_id,
                )
            )
            db.update_direction(
                guid,
                Direction.PRODUCTION,
                DirectionState(
                    direction=Direction.PRODUCTION,
                    state=SRSState.RELEARNING,
                    left=1001,
                    due_at=now + timedelta(minutes=10),
                    reps=2,
                    lapses=1,
                    anki_card_id=card_id,
                    dirty_fsrs=True,
                    last_rating=3,
                    last_review=now,
                    prior_state=SRSState.REVIEW,
                ),
            )
        finally:
            db.close()

        # First peer_sync: pushes the two revlog rows.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "tt_collection_path", tt_col)
            report = peer_sync(dry_run=False)
        assert report.tt_push_pull_exit == 0

        # Check tt_col's revlog: both rows at their exact tt ids.
        conn = sqlite3.connect(str(tt_col))
        conn.row_factory = sqlite3.Row
        try:
            rows = sorted(
                conn.execute("SELECT id, ease, type FROM revlog WHERE cid=?", (card_id,)).fetchall(),
                key=lambda r: r["id"],
            )
        finally:
            conn.close()
        assert len(rows) == 2, f"expected 2 revlog rows, got {len(rows)}"
        assert rows[0]["id"] == lapse_ms
        assert rows[0]["ease"] == 1
        assert rows[0]["type"] == 1
        assert rows[1]["id"] == relearn_ms
        assert rows[1]["ease"] == 3
        assert rows[1]["type"] == 2

        # Second peer_sync: should not insert any new rows.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "tt_collection_path", tt_col)
            report2 = peer_sync(dry_run=False)
        assert report2.tt_push_pull_exit == 0

        conn = sqlite3.connect(str(tt_col))
        conn.row_factory = sqlite3.Row
        try:
            rows2 = conn.execute("SELECT id FROM revlog WHERE cid=? ORDER BY id", (card_id,)).fetchall()
        finally:
            conn.close()
        assert len(rows2) == 2, f"second sync inflated revlog: expected 2, got {len(rows2)}"
        assert [r["id"] for r in rows2] == [lapse_ms, relearn_ms]

    def test_tt_added_card_reaches_server_through_peer_sync(self, tmp_path: Path):
        """End-to-end guard for the b0a4b8a regression: a card *originated in TT*
        (no anki_note_id) must be minted into tt_collection by the peer-sync
        reconcile (peer_sync → main → run_full_sync → sync_create_new) and pushed
        to the server — reaching another device. Uses a cloze card so it rides
        Anki's built-in Cloze notetype (no custom-notetype provisioning needed);
        the create path through run_full_sync is identical regardless of type.
        """
        import sqlite3

        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        tt_col = tmp_path / "tt_collection.anki2"
        peer2_col = tmp_path / "peer2.anki2"
        auth = _login()

        # Baseline: create tt_col and a note in the TT deck (this also creates the
        # deck create_cloze_note needs), upload as the server baseline.
        _driver({"op": "create_collection", "collection_path": str(tt_col)})
        _driver(
            {
                "op": "add_note",
                "collection_path": str(tt_col),
                "deck": settings.anki_deck_name,
                "fields": ["baseline front", "baseline back"],
            }
        )
        _driver({"op": "full_upload", "collection_path": str(tt_col), "auth": auth})

        _driver({"op": "create_collection", "collection_path": str(peer2_col)})
        _driver({"op": "full_download", "collection_path": str(peer2_col), "auth": auth})

        # TT originates a cloze card — no Anki ids; sync_create_new must mint it.
        db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        try:
            db.add_collocation(
                SyntacticUnit(
                    text="bom",
                    translation="",
                    word_count=1,
                    difficulty=1,
                    source="llm",
                    lemma="bom",
                    source_sentence="Jutri bom šel domov.",
                    card_type="cloze",
                )
            )
            assert db.get_collocation("bom").anki_note_id is None
        finally:
            db.close()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "tt_collection_path", tt_col)
            report = peer_sync(dry_run=False)
        assert report.tt_push_pull_exit == 0
        _assert_incremental(report.pull_required, "create-new pull")
        _assert_incremental(report.push_required, "create-new push")

        # TT side: the collocation is now linked (sync_create_new wrote the ids).
        db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        try:
            assert db.get_collocation("bom").anki_note_id is not None, (
                "peer-sync reconcile did not mint the TT-added card (run_full_sync dropped create_new)"
            )
        finally:
            db.close()

        # Server side: another device pulls and now has the cloze note.
        _driver({"op": "sync", "collection_path": str(peer2_col), "auth": auth})
        peer2 = sqlite3.connect(str(peer2_col))
        try:
            flds = [r[0] for r in peer2.execute("SELECT flds FROM notes").fetchall()]
        finally:
            peer2.close()
        assert any("{{c1::" in f for f in flds), f"TT-added cloze note never reached the server — peer2 notes: {flds}"

    def test_tt_card_media_reaches_server_through_peer_sync(self, tmp_path: Path, monkeypatch):
        """End-to-end media: a TT-added cloze card's sentence audio must be copied
        into the collection's media dir by the reconcile and uploaded by the
        media-enabled push leg, reaching another device. Exercises the FULL
        peer_sync bracket carrying media (not just the driver)."""

        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        tt_col = tmp_path / "tt_collection.anki2"
        peer2_col = tmp_path / "peer2.anki2"
        auth = _login()

        # TT's source media dir holds the generated sentence audio.
        tt_media_src = tmp_path / "tt_media_src"
        tt_media_src.mkdir()
        (tt_media_src / "sentence_xyz.mp3").write_bytes(b"ID3-fake-audio-bytes")
        monkeypatch.setattr("app.anki.sync._MEDIA_DIR", tt_media_src)

        # Baseline (also creates the TT deck create_cloze_note needs), mirror to peer2.
        _driver({"op": "create_collection", "collection_path": str(tt_col)})
        _driver(
            {
                "op": "add_note",
                "collection_path": str(tt_col),
                "deck": settings.anki_deck_name,
                "fields": ["baseline front", "baseline back"],
            }
        )
        _driver({"op": "full_upload", "collection_path": str(tt_col), "auth": auth})
        _driver({"op": "create_collection", "collection_path": str(peer2_col)})
        _driver({"op": "full_download", "collection_path": str(peer2_col), "auth": auth})

        # TT originates a cloze card WITH a sentence-audio media row.
        db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        try:
            db.add_collocation(
                SyntacticUnit(
                    text="bom",
                    translation="",
                    word_count=1,
                    difficulty=1,
                    source="llm",
                    lemma="bom",
                    source_sentence="Jutri bom šel domov.",
                    card_type="cloze",
                )
            )
            coll_id = db.get_collocation_id_by_guid(db.get_collocation("bom").guid)
            db.add_media(
                coll_id,
                "audio_tts_sentence",
                "sentence_xyz.mp3",
                str(tt_media_src / "sentence_xyz.mp3"),
                "sentence_xyz.mp3",
                "deadbeef",
                20,
            )
        finally:
            db.close()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "tt_collection_path", tt_col)
            report = peer_sync(dry_run=False)
        assert report.tt_push_pull_exit == 0
        _assert_incremental(report.push_required, "media push")

        # Another device pulls WITH media → must physically have the audio file.
        _driver({"op": "sync", "collection_path": str(peer2_col), "auth": auth, "sync_media": True})
        present = _driver(
            {"op": "media_present", "collection_path": str(peer2_col), "media_filename": "sentence_xyz.mp3"}
        )
        assert present["present"], f"sentence audio did NOT reach peer2 through peer_sync — {present}"


@pytest.mark.peer_sync
class TestPeerSyncMediaDriver:
    """Driver-level media sync: `sync_collection(sync_media=True)` + `sync_media()`
    + poll moves a file peer → AnkiWeb → peer. The narrower layer beneath the full
    peer_sync media e2e (test_tt_card_media_reaches_server_through_peer_sync); kept
    as a focused regression guard for the driver's media trigger + poll loop.

    Gated on --run-peer-sync + a reachable THROWAWAY self-host server (never real
    AnkiWeb).
    """

    def test_media_round_trips_via_driver_sync(self, tmp_path: Path):
        tt_col = tmp_path / "tt.anki2"
        peer2_col = tmp_path / "peer2.anki2"
        auth = _login()

        # Baseline: A has one media note; upload + mirror to peer2.
        _driver({"op": "create_collection", "collection_path": str(tt_col)})
        _driver(
            {
                "op": "add_media_note",
                "collection_path": str(tt_col),
                "media_filename": "baseline.mp3",
                "media_hex": "01",
            }
        )
        _driver({"op": "full_upload", "collection_path": str(tt_col), "auth": auth})
        _driver({"op": "create_collection", "collection_path": str(peer2_col)})
        _driver({"op": "full_download", "collection_path": str(peer2_col), "auth": auth})

        # A adds a NEW media note → our media-enabled sync should push the file.
        _driver(
            {"op": "add_media_note", "collection_path": str(tt_col), "media_filename": "newcard.mp3", "media_hex": "02"}
        )
        push = _driver({"op": "sync", "collection_path": str(tt_col), "auth": auth, "sync_media": True})
        _assert_incremental(push.get("required"), "media push")
        assert push["media"]["completed"], f"media push errored: {push['media']}"
        assert push["media"]["saw_active"], (
            f"media sync never went active — sync_media()+poll is NOT the right trigger; block={push['media']}"
        )

        # peer2 pulls with media → must now physically have newcard.mp3.
        _driver({"op": "sync", "collection_path": str(peer2_col), "auth": auth, "sync_media": True})
        present = _driver({"op": "media_present", "collection_path": str(peer2_col), "media_filename": "newcard.mp3"})
        assert present["present"], "newcard.mp3 did NOT reach peer2 via media sync"

    def test_media_round_trip_parity(self, tmp_path: Path, monkeypatch):
        """Both directions of media convergence through the full peer_sync bracket.

        Direction 2 (server→TT, written first per TDD): a second peer swaps a
        note's media reference and syncs to the server; TT's peer_sync pulls the
        update and ``refresh_media_from_conn`` copies the new file into TT's
        ``_MEDIA_DIR`` + updates TT's media row.

        Direction 1 (TT→server): TT originates a media-bearing cloze card that
        reaches another device through peer_sync (partially covered by
        ``test_tt_card_media_reaches_server_through_peer_sync`` above; re-verified
        here as part of the round-trip).

        **Deck-filter dependency**: every note-creating driver op passes
        ``deck="0. Slovene"`` to match ``refresh_media_from_conn``'s
        ``find_deck_id(conn, settings.anki_deck_name)`` filter — the driver
        default ``"Default"`` would silently skip the media refresh (no error,
        just a no-op), producing a fake "Direction 2 red" that looks like a live
        bug. If Direction 2 does go red, check the deck filter first:
        ``SELECT did FROM cards WHERE nid=?`` in tt_collection against
        ``find_deck_id`` for ``"0. Slovene"``.
        """
        from app.models.srs_item import Direction
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        tt_col = tmp_path / "tt_collection.anki2"
        peer2_col = tmp_path / "peer2.anki2"
        auth = _login()

        # TT's source media dir — refresh_media_from_conn writes here.
        tt_media_src = tmp_path / "tt_media_src"
        tt_media_src.mkdir()
        monkeypatch.setattr("app.anki.sync._MEDIA_DIR", tt_media_src)

        # ── Shared setup ──────────────────────────────────────────────
        # Both media ops use deck="0. Slovene" (see class docstring).
        _driver({"op": "create_collection", "collection_path": str(tt_col)})
        add = _driver(
            {
                "op": "add_media_note",
                "collection_path": str(tt_col),
                "deck": "0. Slovene",
                "media_filename": "baseline.mp3",
                "media_hex": "424153454c494e45",  # "BASELINE"
            }
        )
        note_id = add["note_id"]
        card_id = add["card_ids"][0]
        baseline_stored = add["media_filename"]

        _driver({"op": "full_upload", "collection_path": str(tt_col), "auth": auth})
        _driver({"op": "create_collection", "collection_path": str(peer2_col)})
        _driver({"op": "full_download", "collection_path": str(peer2_col), "auth": auth})

        # Link the note in TT's DB so refresh_media_from_conn processes it.
        db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        try:
            db.add_collocation(
                SyntacticUnit(text="baseline", translation="baseline", word_count=1, difficulty=1, source="test")
            )
            guid = db.get_collocation("baseline").guid
            coll_id = db.get_collocation_id_by_guid(guid)
            db.set_anki_ids(guid, note_id, {Direction.RECOGNITION: card_id})
            db.add_media(
                coll_id,
                "audio_tts_sentence",
                baseline_stored,
                str(tt_media_src / baseline_stored),
                baseline_stored,
                "deadbeef",
                8,
            )
        finally:
            db.close()

        (tt_media_src / baseline_stored).write_bytes(b"BASELINE")

        # Settle: peer_sync uploads the media file (media-sync leg) and the
        # reconcile's refresh confirms it already exists in _MEDIA_DIR.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "tt_collection_path", tt_col)
            report = peer_sync(dry_run=False)
        assert report.tt_push_pull_exit == 0

        # ── Direction 2: server → TT ─────────────────────────────────
        # peer2 swaps the note's media reference and syncs to the server.
        _driver(
            {
                "op": "update_note_media",
                "collection_path": str(peer2_col),
                "note_id": note_id,
                "field_index": 1,  # back field (add_media_note writes [sound:…] here)
                "new_field_text": "[sound:swapped.mp3]",
                "media_filename": "swapped.mp3",
                "media_hex": "53574150504544",  # "SWAPPED"
            }
        )
        push = _driver(
            {
                "op": "sync",
                "collection_path": str(peer2_col),
                "auth": auth,
                "sync_media": True,
            }
        )
        _assert_incremental(push.get("required"), "peer2 media-swap push")
        assert push["media"]["completed"], f"peer2 media push errored: {push['media']}"

        # TT pulls the update — peer_sync's media-sync leg downloads
        # swapped.mp3 into tt_col's media dir, then refresh_media_from_conn
        # copies it into _MEDIA_DIR.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "tt_collection_path", tt_col)
            report2 = peer_sync(dry_run=False)
        assert report2.tt_push_pull_exit == 0

        # Assert the new file arrived in TT's frontend media dir.
        assert (tt_media_src / "swapped.mp3").read_bytes() == bytes.fromhex("53574150504544"), (
            "swapped.mp3 not found in _MEDIA_DIR after peer_sync pull"
        )

        # Assert TT's media row now references the swapped file.
        db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        try:
            swapped_row = db.find_media_by_anki_filename("swapped.mp3", collocation_id=coll_id)
            assert swapped_row is not None, "swapped.mp3 not found in TT media rows after pull"
        finally:
            db.close()

        # ── Direction 1: TT → server ─────────────────────────────────
        # TT originates a cloze card with sentence audio (no Anki note yet).
        db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
        try:
            db.add_collocation(
                SyntacticUnit(
                    text="novo",
                    translation="new",
                    word_count=1,
                    difficulty=1,
                    source="llm",
                    lemma="novo",
                    source_sentence="To je nov avto.",
                    card_type="cloze",
                )
            )
            coll_id2 = db.get_collocation_id_by_guid(db.get_collocation("novo").guid)
            assert db.get_collocation("novo").anki_note_id is None
            db.add_media(
                coll_id2,
                "audio_tts_sentence",
                "newcard.mp3",
                str(tt_media_src / "newcard.mp3"),
                "newcard.mp3",
                "f00d",
                7,
            )
        finally:
            db.close()

        (tt_media_src / "newcard.mp3").write_bytes(b"NEWCARD")

        # sync_create_new mints the cloze note, push leg carries the media.
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(settings, "tt_collection_path", tt_col)
            report3 = peer_sync(dry_run=False)
        assert report3.tt_push_pull_exit == 0
        _assert_incremental(report3.push_required, "Direction 1 push")

        # peer2 pulls with media → must now physically have newcard.mp3.
        _driver(
            {
                "op": "sync",
                "collection_path": str(peer2_col),
                "auth": auth,
                "sync_media": True,
            }
        )
        present = _driver(
            {
                "op": "media_present",
                "collection_path": str(peer2_col),
                "media_filename": "newcard.mp3",
            }
        )
        assert present["present"], f"newcard.mp3 did NOT reach peer2: {present}"
