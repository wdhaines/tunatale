"""Anki sync driver subprocess.

Invoked via::

    uv run --with anki python -m app.plugins.anki_sync.sync_driver

Reads one JSON command from stdin, writes one JSON result to stdout.
This is the ONLY module in ``app/`` that imports ``anki``.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# anki and its backend emit diagnostics to stdout (e.g. a "blocked main thread"
# watchdog stack trace during a network sync). That corrupts our single-JSON-line
# stdout contract, so reserve the real stdout for the result and route everything
# else — including anything printed at anki import time — to stderr.
_REAL_STDOUT = sys.stdout
sys.stdout = sys.stderr

_IMPORT_ERROR: str | None = None
try:
    from anki.collection import Collection
except ImportError as e:
    Collection = None  # type: ignore[assignment,misc]
    _IMPORT_ERROR = str(e)


def _emit(result: dict) -> None:
    """Write the one-and-only JSON result line to the real stdout."""
    print(json.dumps(result), file=_REAL_STDOUT, flush=True)


def _serialize_sync_auth(auth: Any) -> dict:
    return {"hkey": auth.hkey, "endpoint": auth.endpoint}


def _serialize_sync_output(output: Any) -> dict:
    return {
        "required": output.required,
        "server_message": output.server_message,
    }


def _make_auth(auth_dict: dict) -> Any:
    """Build a SyncAuth protobuf from a dict.

    An empty endpoint is left UNSET (not ""): Anki treats endpoint="" as an invalid
    *custom* server ("Invalid sync server specified"), whereas an unset endpoint means
    "use the default AnkiWeb server" (and lets Anki resolve its shard). A non-empty
    endpoint (self-host) is passed through.
    """
    from anki.sync_pb2 import SyncAuth

    endpoint = auth_dict.get("endpoint") or ""
    if endpoint:
        return SyncAuth(hkey=auth_dict["hkey"], endpoint=endpoint)
    return SyncAuth(hkey=auth_dict["hkey"])


def _op_login(op: dict) -> dict:
    username = op["username"]
    password = op["password"]
    endpoint = op.get("endpoint", "")
    with tempfile.TemporaryDirectory() as td:
        col_path = str(Path(td) / "tmp_col.anki2")
        col = Collection(col_path)
        try:
            auth = col.sync_login(username, password, endpoint=endpoint or None)
            return _serialize_sync_auth(auth)
        finally:
            col.close()


def _await_media_sync(col, timeout_s: float = 120.0, poll_s: float = 0.05) -> dict:
    """Poll media_sync_status().active until the background media sync finishes.

    Starts at *poll_s* (default 50ms) and backs off ×1.5 per poll, capped at
    0.2s.  Returns observability for the spike: whether we ever saw active=True
    (proves the sync actually started), poll count, elapsed seconds, and any
    error (the status call throws if media sync failed). Does NOT close col —
    caller owns it.
    """
    start = time.time()
    deadline = start + timeout_s
    polls = 0
    saw_active = False
    error: str | None = None
    current_poll = poll_s
    while time.time() < deadline:
        try:
            status = col.media_sync_status()
        except Exception as e:  # media_sync_status throws on a failed media sync
            error = str(e)
            break
        polls += 1
        if status.active:
            saw_active = True
        elif saw_active:
            break  # was active, now done
        elif polls > 5:
            break  # never went active after a few polls → nothing to sync (or didn't start)
        time.sleep(current_poll)
        current_poll = min(current_poll * 1.5, 0.2)
    return {
        "completed": error is None,
        "saw_active": saw_active,
        "polls": polls,
        "elapsed_s": round(time.time() - start, 3),
        "error": error,
        "timed_out": time.time() >= deadline,
    }


def _op_sync(op: dict) -> dict:
    collection_path = op["collection_path"]
    auth = _make_auth(op["auth"])
    sync_media = op.get("sync_media", False)
    col = Collection(str(collection_path))
    try:
        output = col.sync_collection(auth, sync_media=sync_media)
        result = _serialize_sync_output(output)
        if sync_media:
            # The sync_media flag on sync_collection negotiates the media handshake;
            # the actual file transfer is the explicit sync_media() call, which runs
            # in a background thread we poll to completion. (Spike hypothesis — the
            # observability in _await_media_sync tells us if this is the right trigger.)
            auth_media = auth
            if output.new_endpoint:
                from anki.sync_pb2 import SyncAuth

                auth_media = SyncAuth(hkey=auth.hkey, endpoint=output.new_endpoint)
            col.sync_media(auth_media)
            result["media"] = _await_media_sync(col)
        return result
    finally:
        col.close()


def _op_full_download(op: dict) -> dict:
    collection_path = op["collection_path"]
    auth = _make_auth(op["auth"])
    col = Collection(str(collection_path))
    try:
        # Anki's real flow (qt/aqt/sync.py) runs the normal sync handshake FIRST: it
        # negotiates server state and, for AnkiWeb, returns the sharded `new_endpoint`.
        # Skipping it works on the lenient built-in server but AnkiWeb returns
        # HTTP 400 "missing original size". On an empty local vs a populated server the
        # handshake reports FULL_DOWNLOAD without pushing; we then force the download.
        out = col.sync_collection(auth, sync_media=False)
        if out.new_endpoint:
            from anki.sync_pb2 import SyncAuth

            auth = SyncAuth(hkey=auth.hkey, endpoint=out.new_endpoint)
        # media disabled → server_usn=None (matches Anki: server_media_usn only when
        # media syncing is enabled). upload=False is forced: we only ever pull here.
        col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
        return {"ok": True, "required": out.required}
    finally:
        col.close()


def _op_full_upload(op: dict) -> dict:
    collection_path = op["collection_path"]
    auth = _make_auth(op["auth"])
    col = Collection(str(collection_path))
    try:
        col.full_upload_or_download(auth=auth, server_usn=None, upload=True)
        return {"ok": True}
    finally:
        col.close()


def _op_add_note(op: dict) -> dict:
    """Add a note (default Basic notetype + Default deck) and return its card ids."""
    collection_path = op["collection_path"]
    col = Collection(str(collection_path))
    try:
        notetype = col.models.by_name(op.get("notetype", "Basic"))
        note = col.new_note(notetype)
        for i, value in enumerate(op["fields"]):
            note.fields[i] = value
        deck_id = col.decks.id(op.get("deck", "Default"))
        col.add_note(note, deck_id)
        return {"note_id": note.id, "card_ids": [c.id for c in note.cards()]}
    finally:
        col.close()


def _op_answer_card(op: dict) -> dict:
    """Grade a card (rating 1-4) via the backend, returning post-grade reps/queue.

    Mirrors tests/anki_oracle/oracle.py: the queue is built first so the
    scheduler/today are initialized, then ``answer_card_raw`` avoids the
    timer-started requirement of the legacy ``answerCard`` API.
    """
    from anki.scheduler_pb2 import CardAnswer

    collection_path = op["collection_path"]
    card_id = op["card_id"]
    ease = op["rating"]  # 1=AGAIN, 2=HARD, 3=GOOD, 4=EASY
    col = Collection(str(collection_path))
    try:
        col.decks.select(col.decks.id(op.get("deck", "Default")))
        col.sched.get_queued_cards(fetch_limit=50)
        states = col._backend.get_scheduling_states(card_id)
        rating_map = {1: CardAnswer.AGAIN, 2: CardAnswer.HARD, 3: CardAnswer.GOOD, 4: CardAnswer.EASY}
        new_state = {1: states.again, 2: states.hard, 3: states.good, 4: states.easy}[ease]
        ans = CardAnswer(
            card_id=card_id,
            current_state=states.current,
            new_state=new_state,
            rating=rating_map[ease],
            answered_at_millis=int(time.time() * 1000),
            milliseconds_taken=0,
        )
        col._backend.answer_card_raw(ans.SerializeToString())
        card = col.get_card(card_id)
        return {"card_id": card_id, "queue": card.queue, "reps": card.reps}
    finally:
        col.close()


def _op_get_card(op: dict) -> dict:
    """Read a card's state without modifying it."""
    collection_path = op["collection_path"]
    card_id = op["card_id"]
    col = Collection(str(collection_path))
    try:
        card = col.get_card(card_id)
        return {
            "card_id": card_id,
            "queue": card.queue,
            "type": card.type,
            "ivl": card.ivl,
            "due": card.due,
            "reps": card.reps,
            "lapses": card.lapses,
        }
    finally:
        col.close()


def _op_add_media_note(op: dict) -> dict:
    """Add a note that references a media file, registering the file via the
    proper media API (so anki marks it for upload). Spike helper."""
    collection_path = op["collection_path"]
    col = Collection(str(collection_path))
    try:
        fname = op["media_filename"]
        data = bytes.fromhex(op.get("media_hex", "deadbeef"))
        stored = col.media.write_data(fname, data)  # registers + returns final name
        notetype = col.models.by_name(op.get("notetype", "Basic"))
        note = col.new_note(notetype)
        note.fields[0] = op.get("front", "media front")
        note.fields[1] = op.get("back", f"[sound:{stored}]")
        deck_id = col.decks.id(op.get("deck", "Default"))
        col.add_note(note, deck_id)
        return {
            "note_id": note.id,
            "card_ids": [c.id for c in note.cards()],
            "media_filename": stored,
            "media_dir": col.media.dir(),
        }
    finally:
        col.close()


def _op_media_present(op: dict) -> dict:
    """Report whether a media filename exists locally + total media count. Spike helper."""
    collection_path = op["collection_path"]
    col = Collection(str(collection_path))
    try:
        fname = op["media_filename"]
        media_dir = Path(col.media.dir())
        count = sum(1 for _ in media_dir.iterdir()) if media_dir.exists() else 0
        return {"present": col.media.have(fname), "media_count": count, "media_dir": str(media_dir)}
    finally:
        col.close()


def _op_update_note_media(op: dict) -> dict:
    """Register a new media file and update an existing note's field to reference it.

    Used by Phase 4's server→TT media round-trip parity test to simulate an image/audio
    swap on a second peer. Registers via ``col.media.write_data`` (the production path)
    and writes the note field via ``col.update_note`` (not the deprecated ``note.flush()``).
    """
    collection_path = op["collection_path"]
    note_id = op["note_id"]
    field_index = op["field_index"]
    new_field_text = op["new_field_text"]
    media_filename = op["media_filename"]
    media_data = bytes.fromhex(op["media_hex"])
    col = Collection(str(collection_path))
    try:
        stored = col.media.write_data(media_filename, media_data)
        note = col.get_note(note_id)
        note.fields[field_index] = new_field_text
        col.update_note(note)
        return {"ok": True, "stored_filename": stored, "media_dir": col.media.dir()}
    finally:
        col.close()


def _op_create_collection(op: dict) -> dict:
    """Create a minimal empty collection at the given path."""
    collection_path = Path(op["collection_path"])
    collection_path.parent.mkdir(parents=True, exist_ok=True)
    col = Collection(str(collection_path))
    try:
        col.close()
        return {"ok": True}
    except Exception as e:
        return {"error": str(e)}


def _op_media_pending(op: dict) -> dict:
    """Count media files needing upload.

    Checks two sources:
    1. Dirty entries in ``<collection>.media.db2`` (``dirty = 1``).
    2. Files present in the media directory but absent from the sidecar
       (untracked files placed by the offline reconcile).

    Returns ``{"pending": <count>}`` or ``{"pending": -1}`` when unreadable/missing.
    """
    collection_path = Path(op["collection_path"])
    media_db = Path(str(collection_path).replace(".anki2", ".media.db2"))
    media_dir = Path(str(collection_path).replace(".anki2", ".media"))

    if not media_db.exists():
        return {"pending": -1}
    try:
        import sqlite3

        con = sqlite3.connect(f"file:{media_db}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT COUNT(*) FROM media WHERE dirty = 1").fetchone()
            dirty_count = row[0] if row else 0
            if dirty_count > 0:
                return {"pending": dirty_count}

            if media_dir.is_dir():
                tracked = {r[0] for r in con.execute("SELECT fname FROM media").fetchall()}
                untracked = sum(1 for f in media_dir.iterdir() if f.is_file() and f.name not in tracked)
                if untracked > 0:
                    return {"pending": untracked}

            return {"pending": 0}
        finally:
            con.close()
    except Exception:
        return {"pending": -1}


def _op_shutdown(_op: dict) -> dict:
    """Graceful shutdown — emit ok and signal the loop to exit."""
    return {"ok": True}


_OPERATIONS: dict[str, Any] = {
    "login": _op_login,
    "sync": _op_sync,
    "create_collection": _op_create_collection,
    "full_download": _op_full_download,
    "full_upload": _op_full_upload,
    "add_note": _op_add_note,
    "add_media_note": _op_add_media_note,
    "media_present": _op_media_present,
    "media_pending": _op_media_pending,
    "update_note_media": _op_update_note_media,
    "answer_card": _op_answer_card,
    "get_card": _op_get_card,
    "shutdown": _op_shutdown,
}


_NOANKI_OPS = {"shutdown", "media_pending"}


def _dispatch(command: dict) -> dict:
    """Dispatch a single command and return the result dict."""
    op_name = command.get("op", "")

    if _IMPORT_ERROR and op_name not in _NOANKI_OPS:
        return {"error": f"anki not available: {_IMPORT_ERROR}"}

    handler = _OPERATIONS.get(op_name)
    if handler is None:
        return {"error": f"Unknown operation: {op_name}"}

    try:
        return handler(command)
    except Exception as e:
        import traceback

        return {"error": str(e), "traceback": traceback.format_exc()}


def main() -> None:
    """Persistent loop: read one JSON command per line from stdin, dispatch,
    emit exactly one JSON result line to stdout, repeat. Exit on stdin EOF
    or ``shutdown`` op."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            command = json.loads(line)
        except (json.JSONDecodeError, OSError) as e:
            _emit({"error": f"Failed to read command: {e}"})
            continue

        result = _dispatch(command)
        _emit(result)

        if command.get("op") == "shutdown":
            break


if __name__ == "__main__":
    main()
