"""Anki oracle subprocess.

Invoked via::

    uv run --with anki python oracle.py <collection_path>

Reads a JSON array of operations from stdin and writes a JSON object of
results to stdout.  A single subprocess invocation can batch multiple
operations on the same collection in one shot.

Operations
----------

``{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}``
    Returns Anki's ordered queue head and ``counts()`` dict.
``{"op": "set_config", "key": "fsrs", "value": true}``
    Calls ``col.set_config(key, value)`` for setup.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_IMPORT_ERROR: str | None = None
try:
    from anki.collection import Collection
except ImportError as e:
    Collection = None  # type: ignore[assignment,misc]
    _IMPORT_ERROR = str(e)


def _serialize_scheduling_state(state: Any) -> dict | None:
    """Flatten a ``SchedulingState`` protobuf to a JSON-safe dict.

    SchedulingState is a one-of-many message — current Anki may report
    ``normal.review``, ``normal.learning``, ``normal.relearning``, etc. This
    helper exposes whatever fields are present in a flat shape so test
    assertions don't have to walk the variant chain.
    """
    if state is None:
        return None
    normal = state.normal if state.HasField("normal") else None
    if normal is None:
        return {"kind": "filtered_or_unknown"}

    if normal.HasField("review"):
        rv = normal.review
        memory_state = rv.memory_state if rv.HasField("memory_state") else None
        return {
            "kind": "review",
            "scheduled_days": rv.scheduled_days,
            "elapsed_days": rv.elapsed_days,
            "stability": memory_state.stability if memory_state else None,
            "difficulty": memory_state.difficulty if memory_state else None,
        }
    if normal.HasField("learning"):
        lr = normal.learning
        memory_state = lr.memory_state if lr.HasField("memory_state") else None
        return {
            "kind": "learning",
            "scheduled_secs": lr.scheduled_secs,
            "elapsed_secs": lr.elapsed_secs,
            "remaining_steps": lr.remaining_steps,
            "stability": memory_state.stability if memory_state else None,
            "difficulty": memory_state.difficulty if memory_state else None,
        }
    if normal.HasField("relearning"):
        rl = normal.relearning
        memory_state = rl.review.memory_state if rl.review.HasField("memory_state") else None
        return {
            "kind": "relearning",
            "review_scheduled_days": rl.review.scheduled_days,
            "learning_scheduled_secs": rl.learning.scheduled_secs,
            "lapses": rl.review.lapses,
            "stability": memory_state.stability if memory_state else None,
            "difficulty": memory_state.difficulty if memory_state else None,
        }
    if normal.HasField("new"):
        return {"kind": "new", "position": normal.new.position}
    return {"kind": "normal_empty"}


def _serialize_states(states: Any) -> dict | None:
    """Flatten a ``SchedulingStates`` (current + 4 next) to JSON-safe nested dict."""
    if states is None:
        return None
    return {
        "current": _serialize_scheduling_state(states.current),
        "again": _serialize_scheduling_state(states.again),
        "hard": _serialize_scheduling_state(states.hard),
        "good": _serialize_scheduling_state(states.good),
        "easy": _serialize_scheduling_state(states.easy),
    }


def _serialize_card(card: Any, col: Any) -> dict:
    """Serialize a single queued card to a plain JSON-safe dict.

    ``card`` is a ``QueuedCard`` proto whose ``.card`` is a protobuf ``Card``
    message — the field names differ from the Python ``anki.cards.Card`` class:
    ``ctype``/``interval``/``remaining_steps`` instead of ``type``/``ivl``/``left``.
    The output dict normalizes back to the Python-class names so test
    assertions can match TT's terminology.
    """
    c = card.card
    memory_state = c.memory_state if c.HasField("memory_state") else None
    last_review = c.last_review_time_secs if c.HasField("last_review_time_secs") else None
    try:
        sfld = col.get_note(c.note_id).sfld
    except Exception:
        sfld = ""
    result: dict[str, Any] = {
        "card_id": c.id,
        "note_id": c.note_id,
        "queue": c.queue,
        "type": c.ctype,
        "due": c.due,
        "ivl": c.interval,
        "reps": c.reps,
        "lapses": c.lapses,
        "left": c.remaining_steps,
    }
    if memory_state:
        result["memory_state"] = {
            "stability": round(memory_state.stability, 4),
            "difficulty": round(memory_state.difficulty, 4),
        }
    else:
        result["memory_state"] = None
    result["last_review"] = last_review
    result["sfld"] = sfld
    result["states"] = _serialize_states(card.states) if card.HasField("states") else None
    return result


def _op_get_queue(col: Any, op: dict) -> dict:
    deck_id = op.get("deck_id", 1)
    col.decks.select(deck_id)
    fetch_limit = op.get("fetch_limit", 50)
    result = col.sched.get_queued_cards(fetch_limit=fetch_limit)
    new_count, learning_count, review_count = col.sched.counts()
    return {
        "cards": [_serialize_card(qc, col) for qc in result.cards],
        "counts": {
            "new": new_count,
            "learning": learning_count,
            "review": review_count,
        },
    }


def _op_set_config(col: Any, op: dict) -> dict:
    col.set_config(op["key"], op["value"])
    return {"ok": True}


_OPERATIONS: dict[str, Any] = {
    "get_queue": _op_get_queue,
    "set_config": _op_set_config,
}


def _run_operations(collection_path: Path) -> None:
    """Execute operations from stdin against *collection_path* and print results."""
    work_path = collection_path.parent / ".oracle_work.anki2"
    shutil.copy(str(collection_path), str(work_path))

    col: Any = None
    try:
        col = Collection(str(work_path))
    except Exception as e:
        print(json.dumps({"error": f"Failed to open collection: {e}"}))
        _cleanup(work_path)
        return

    # Force V3 scheduler. SyntheticCollection writes schedVer=2 in col.conf so
    # this is a one-line flip via Anki's own API (which knows the protobuf-enum
    # → config-key mapping for SCHED_2021).
    try:
        if not col.v3_scheduler():
            col.set_v3_scheduler(True)
    except Exception as e:
        print(json.dumps({"error": f"Failed to enable V3 scheduler: {e}"}))
        with contextlib.suppress(Exception):
            col.close()
        _cleanup(work_path)
        return

    try:
        try:
            operations = json.loads(sys.stdin.read())
        except (json.JSONDecodeError, OSError) as e:
            print(json.dumps({"error": f"Failed to read operations: {e}"}))
            return

        import traceback

        results: dict[str, Any] = {}
        for idx, operation in enumerate(operations):
            op_name = operation.get("op", "?")
            handler = _OPERATIONS.get(op_name)
            if handler is None:
                results[f"{op_name}_{idx}"] = {"error": f"Unknown operation: {op_name}"}
            else:
                try:
                    results[f"{op_name}_{idx}"] = handler(col, operation)
                except Exception as e:
                    results[f"{op_name}_{idx}"] = {
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                    }
        print(json.dumps(results))
    finally:
        with contextlib.suppress(Exception):
            col.close()
        _cleanup(work_path)


def _cleanup(work_path: Path) -> None:
    if work_path.exists():
        work_path.unlink()
    wal = work_path.with_suffix(".anki2-wal")
    if wal.exists():
        wal.unlink()
    shm = work_path.with_suffix(".anki2-shm")
    if shm.exists():
        shm.unlink()


def main() -> None:
    if _IMPORT_ERROR:
        print(json.dumps({"error": f"anki not available: {_IMPORT_ERROR}"}))
        sys.exit(0)

    if len(sys.argv) < 2:
        print(json.dumps({"error": "Usage: oracle.py <collection_path>"}))
        sys.exit(0)

    collection_path = Path(sys.argv[1])
    if not collection_path.exists():
        print(json.dumps({"error": f"Collection not found: {collection_path}"}))
        sys.exit(0)

    try:
        _run_operations(collection_path)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(0)


if __name__ == "__main__":
    main()
