"""A reset pushed to Anki is Anki's own Forget — oracle parity (Layer 90).

A reset in TunaTale reaches Anki through ``OfflineWriter.forget_card``. These
tests run Anki's Forget on a card (with "reset repetition and lapse counts"
ticked, which is what a TunaTale reset means) and compare what it leaves behind
with what ``forget_card`` leaves on the same collection.

The part that was missing is the revlog row. Forget logs ``type = 4`` with
``factor = 0``, and that row is how Anki's FSRS code knows to ignore the reviews
before it (``RevlogEntry::is_reset``). Without it the old reviews still count in
any Anki-side recompute from the revlog.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime

import pytest

from app.plugins.anki_sync.sync import OfflineWriter
from app.srs.anki_mirror.protobuf_wire import anki_today_col_day
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import COL_CRT, SyntheticCollection

_REVIEW, _RELEARNING = 10010, 10020
_SAME_IN_BOTH = ("type", "queue", "ivl", "factor", "reps", "lapses", "left", "odue", "odid")
_ROW = ("ease", "ivl", "lastIvl", "factor", "time", "type")


def _seed(coll: SyntheticCollection) -> None:
    coll.enable_fsrs(weights=DEFAULT_FSRS5_PARAMS.weights, retention=0.9)
    today = anki_today_col_day(COL_CRT, datetime.now(UTC))
    now = int(time.time())
    common = {
        "ord": 0,
        "ivl": 10,
        "reps": 5,
        "lapses": 1,
        "factor": 2300,
        "stability": 10.0,
        "difficulty": 5.5,
        "last_review_secs": now - 7 * 86400,
        "desired_retention": 0.9,
    }
    coll.add_note(id=1001, guid="g-forget-review", fields=["review", "back"])
    coll.add_card(id=_REVIEW, note_id=1001, type=2, queue=2, due=today + 3, **common)
    coll.add_note(id=1002, guid="g-forget-relearn", fields=["relearn", "back"])
    coll.add_card(id=_RELEARNING, note_id=1002, type=3, queue=1, due=now + 600, left=1001, **common)
    coll.save()


@pytest.mark.oracle
@pytest.mark.parametrize("card_id", [_REVIEW, _RELEARNING], ids=["review", "relearning"])
def test_forget_card_leaves_what_anki_forget_leaves(synthetic_collection: SyntheticCollection, card_id: int) -> None:
    """``forget_card`` == Anki's Forget with the counts reset: the card and the revlog row.

    What this covers:
    - the scheduling columns of the card, for a review card and a relearning one
    - the reset row, column for column, including ``lastIvl`` = the old interval

    What this does NOT cover, because the two differ on purpose:
    - ``due``: Anki takes the collection's next new-card position; ``forget_card``
      puts the card after the last new card
    - ``data``: Anki keeps ``dr`` and ``lrt`` and drops the memory state;
      ``forget_card`` writes ``{}``. Both leave the card with no memory state,
      which is asserted here.
    """
    _seed(synthetic_collection)

    raw = run_oracle(
        synthetic_collection.path,
        [
            {"op": "forget_cards", "card_ids": [card_id], "reset_counts": True},
            {"op": "get_card_row", "card_id": card_id},
            {"op": "get_revlog", "card_id": card_id},
        ],
    ).raw()
    anki_card = raw["get_card_row_1"]
    (anki_row,) = raw["get_revlog_2"]
    assert anki_card["type"] == 0  # the control: Anki forgot the card

    # The oracle works on a copy, so the seeded file is still untouched here.
    conn = sqlite3.connect(str(synthetic_collection.path))
    conn.row_factory = sqlite3.Row
    try:
        assert conn.execute("SELECT type FROM cards WHERE id = ?", (card_id,)).fetchone()[0] != 0  # the control
        started_ms = int(time.time() * 1000)
        OfflineWriter(conn).forget_card(card_id)
        tt_card = dict(conn.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone())
        tt_rows = [dict(r) for r in conn.execute("SELECT * FROM revlog WHERE cid = ?", (card_id,))]
    finally:
        conn.close()

    assert {k: tt_card[k] for k in _SAME_IN_BOTH} == {k: anki_card[k] for k in _SAME_IN_BOTH}
    assert "s" not in json.loads(anki_card["data"]) and "s" not in json.loads(tt_card["data"])
    assert len(tt_rows) == 1, f"one reset, one row; got {tt_rows}"
    (tt_row,) = tt_rows
    assert {k: tt_row[k] for k in _ROW} == {k: anki_row[k] for k in _ROW}
    assert {k: tt_row[k] for k in _ROW} == {"ease": 0, "ivl": 0, "lastIvl": 10, "factor": 0, "time": 0, "type": 4}
    assert tt_row["usn"] == -1
    assert tt_row["id"] >= started_ms
