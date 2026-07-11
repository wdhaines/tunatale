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
``{"op": "get_today"}``
    Returns ``{"today": col.sched.today}`` — Anki's day index for today.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import time
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


def _op_answer_card(col: Any, op: dict) -> dict:
    """Grade a card with the given ease rating (1-4) and return its post-grade state.

    Calls the backend directly to avoid the timer-started requirement of the
    legacy ``answerCard`` API.
    """
    from anki.scheduler_pb2 import CardAnswer

    card_id = op["card_id"]
    ease = op["rating"]  # 1=AGAIN, 2=HARD, 3=GOOD, 4=EASY

    rating_map = {1: CardAnswer.AGAIN, 2: CardAnswer.HARD, 3: CardAnswer.GOOD, 4: CardAnswer.EASY}
    rating = rating_map[ease]

    states = col._backend.get_scheduling_states(card_id)

    if rating == CardAnswer.AGAIN:
        new_state = states.again
    elif rating == CardAnswer.HARD:
        new_state = states.hard
    elif rating == CardAnswer.GOOD:
        new_state = states.good
    else:
        new_state = states.easy

    ans = CardAnswer(
        card_id=card_id,
        current_state=states.current,
        new_state=new_state,
        rating=rating,
        answered_at_millis=int(time.time() * 1000),
        milliseconds_taken=0,
    )
    col._backend.answer_card_raw(ans.SerializeToString())
    # answer_card_raw persists the change; no explicit commit needed.
    card = col.get_card(card_id)
    ms = card.memory_state
    return {
        "card_id": card_id,
        "queue": card.queue,
        "type": card.type,
        "stability": round(ms.stability, 6) if ms else None,
        "difficulty": round(ms.difficulty, 6) if ms else None,
    }


def _op_scheduling_states(col: Any, op: dict) -> dict:
    """Build the study queue (populating the load balancer if enabled), then return
    the hard/good/easy review intervals Anki would schedule for a card.

    The queue build is what constructs ``card_queues.load_balancer`` from
    ``get_all_cards_due_in_range``; without it the balancer is absent and
    ``get_scheduling_states`` returns pure-fuzz intervals. Used by the Layer 53
    load-balancer parity test.
    """
    deck_id = op.get("deck_id", 1)
    col.decks.select(deck_id)
    col.sched.get_queued_cards(fetch_limit=op.get("fetch_limit", 1))
    states = col._backend.get_scheduling_states(op["card_id"])

    def _ivl(state: Any) -> int | None:
        normal = state.normal
        return normal.review.scheduled_days if normal.HasField("review") else None

    return {
        "hard": _ivl(states.hard),
        "good": _ivl(states.good),
        "easy": _ivl(states.easy),
        "today": col.sched.today,
    }


def _op_get_today(col: Any, op: dict) -> dict:
    """Return Anki's day index for today (col.sched.today)."""
    return {"today": col.sched.today}


def _op_get_card(col: Any, op: dict) -> dict:
    """Read a card's current state without modifying it."""
    card_id = op["card_id"]
    card = col.get_card(card_id)
    ms = card.memory_state
    return {
        "card_id": card_id,
        "queue": card.queue,
        "type": card.type,
        "ivl": card.ivl,
        "due": card.due,
        "reps": card.reps,
        "lapses": card.lapses,
        "stability": round(ms.stability, 6) if ms else None,
        "difficulty": round(ms.difficulty, 6) if ms else None,
    }


def _op_get_revlog(col: Any, op: dict) -> list[dict]:
    """Read revlog rows for a card, serialized like get_card for consistency."""
    card_id = op["card_id"]
    rows = col.db.all(
        "SELECT id, cid, ease, ivl, lastIvl, factor, time, type FROM revlog WHERE cid=?",
        card_id,
    )
    return [
        {
            "id": r[0],
            "cid": r[1],
            "ease": r[2],
            "ivl": r[3],
            "lastIvl": r[4],
            "factor": r[5],
            "time": r[6],
            "type": r[7],
        }
        for r in rows
    ]


def _op_add_review_cards(col: Any, op: dict) -> dict:
    """Add *count* overdue review cards (type=2, queue=2, due in the past) mid-session.

    Exists for the Layer-76 daily-cap test: Anki charges today's new-card intros
    against the review-per-day limit, but that only shows when reviews saturate
    the limit — which itself prevents new cards from being gathered/answered in a
    single fresh build (mutual exclusion). This op breaks the deadlock by adding
    reviews *after* new cards were answered, in the same process, so the follow-up
    ``get_queue`` sees ``review = review_limit - new_studied``. Uses the real
    ``add_note`` / ``update_card`` API (not raw SQL) so the backend invalidates
    the study queue and the next build gathers them. The cards carry no FSRS
    memory state (``data='{}'``) — irrelevant to the review *count* (a NULL-R card
    is still gathered, Layer 38), and this op only asserts on counts.
    """
    count = op["count"]
    deck_id = op.get("deck_id", 1)
    model = col.models.by_name("Basic") or col.models.all()[0]
    added = 0
    for i in range(count):
        note = col.new_note(model)
        note.fields[0] = f"rev-added-{i}"
        if len(note.fields) > 1:
            note.fields[1] = "back"
        col.add_note(note, deck_id)
        card = note.cards()[0]
        card.type = 2
        card.queue = 2
        card.due = 0
        card.ivl = 10
        card.factor = 2500
        card.reps = 5
        col.update_card(card)
        added += 1
    return {"added": added}


_OPERATIONS: dict[str, Any] = {
    "get_queue": _op_get_queue,
    "set_config": _op_set_config,
    "answer_card": _op_answer_card,
    "add_review_cards": _op_add_review_cards,
    "get_card": _op_get_card,
    "get_revlog": _op_get_revlog,
    "get_today": _op_get_today,
    "scheduling_states": _op_scheduling_states,
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
