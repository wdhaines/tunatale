"""Anki sync driver subprocess.

Invoked via::

    uv run --with anki python -m app.anki.sync_driver

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
    print(json.dumps(result), file=_REAL_STDOUT)


def _serialize_sync_auth(auth: Any) -> dict:
    return {"hkey": auth.hkey, "endpoint": auth.endpoint}


def _serialize_sync_output(output: Any) -> dict:
    return {
        "required": output.required,
        "server_message": output.server_message,
    }


def _make_auth(auth_dict: dict) -> Any:
    """Build a SyncAuth protobuf from a dict."""
    from anki.sync_pb2 import SyncAuth

    return SyncAuth(hkey=auth_dict["hkey"], endpoint=auth_dict.get("endpoint", ""))


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


def _op_sync(op: dict) -> dict:
    collection_path = op["collection_path"]
    auth = _make_auth(op["auth"])
    col = Collection(str(collection_path))
    try:
        output = col.sync_collection(auth, sync_media=False)
        return _serialize_sync_output(output)
    finally:
        col.close()


def _op_full_download(op: dict) -> dict:
    collection_path = op["collection_path"]
    auth = _make_auth(op["auth"])
    col = Collection(str(collection_path))
    try:
        col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
        return {"ok": True}
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


_OPERATIONS: dict[str, Any] = {
    "login": _op_login,
    "sync": _op_sync,
    "create_collection": _op_create_collection,
    "full_download": _op_full_download,
    "full_upload": _op_full_upload,
    "add_note": _op_add_note,
    "answer_card": _op_answer_card,
    "get_card": _op_get_card,
}


def main() -> None:
    if _IMPORT_ERROR:
        _emit({"error": f"anki not available: {_IMPORT_ERROR}"})
        return

    try:
        command = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError) as e:
        _emit({"error": f"Failed to read command: {e}"})
        return

    op_name = command.get("op", "")
    handler = _OPERATIONS.get(op_name)
    if handler is None:
        _emit({"error": f"Unknown operation: {op_name}"})
        return

    try:
        result = handler(command)
        _emit(result)
    except Exception as e:
        import traceback

        _emit({"error": str(e), "traceback": traceback.format_exc()})


if __name__ == "__main__":
    main()
