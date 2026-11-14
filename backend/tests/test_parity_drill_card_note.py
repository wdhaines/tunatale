"""A rotated drill note as Anki itself reads it (tunatale-5i1p.5, oracle P1).

TT mints a drill card with its own create path, Anki reviews the card, TT pulls
the review and rotates the note to the next step with its own rotation phase.
Then Anki is asked, through the pinned engine:

- Check Database passes, and the card row is byte-identical to what Anki's own
  review left: the rotation wrote the note only. (The oracle grades a throwaway
  copy of the file, so the review is carried into the collection as a sync from
  the phone would carry it: the card row Anki produced, written in place.) Control: the same note cut to
  11 fields is reported ("Fixed"), so a clean check is not a check that cannot
  fail.
- The front shows the new step (the sentence before, ``+ cue``) and hides its
  answer; the back shows the answer.

Measured once by hand on 2026-11-13 (``briefs/tool-rotating-card-probe-2026-11.py``,
case 1); this keeps it measured through TT's own code.
"""

from __future__ import annotations

import re
import shutil
import sqlite3

import pytest

from app.models.srs_item import Direction
from app.plugins.anki_sync.add_sentence_notetype import add_sentence_notetype
from app.plugins.anki_sync.sync import AnkiSync, OfflineReader, OfflineWriter
from app.srs.database import SRSDatabase
from app.srs.lesson_card_mint import mint_lesson_cards
from tests._helpers.anki_sync_create_new import FakeReader
from tests._helpers.anki_sync_pull import FakeReader as RecordsReader
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import SyntheticCollection
from tests.test_drill_card import SUBSTITUTION

LANG = "no"
NOW_MS = 1790000000000
CARDS = {"set": "test-01", "items": [{"kind": "drill", "drill": 1, "role": "male-1"}]}


def _connect(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


@pytest.mark.oracle
async def test_p1_a_reviewed_drill_note_rotated_by_tt_is_one_anki_keeps(
    synthetic_collection: SyntheticCollection, tmp_path
) -> None:
    deck = synthetic_collection.deck_name
    db = SRSDatabase(":memory:")
    [(cid, _card)] = mint_lesson_cards(db, LANG, CARDS, drills=[SUBSTITUTION])
    with _connect(synthetic_collection.path) as conn:
        assert add_sentence_notetype(conn, now_ms=NOW_MS) == "created"
        await AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(conn), language_code=LANG).sync_create_new(
            deck_name=deck, model_name="unused"
        )
    card_id = db.get_collocation_by_id(cid)[1].directions[Direction.PRODUCTION].anki_card_id

    reviewed = run_oracle(
        synthetic_collection.path,
        [{"op": "answer_card", "card_id": card_id, "rating": 3}, {"op": "get_card_row", "card_id": card_id}],
    ).raw()["get_card_row_1"]
    assert reviewed["reps"] == 1
    # The oracle grades a throwaway copy, so the review reaches the file as a phone's
    # would through a sync: the card row Anki's own review produced, written in place.
    with _connect(synthetic_collection.path) as conn:
        cols = ", ".join(f"{k} = ?" for k in reviewed)
        conn.execute(
            f"UPDATE cards SET {cols}, mod = mod + 1, usn = -1 WHERE id = ?",  # noqa: S608 - column names are the oracle's
            (*reviewed.values(), card_id),
        )

    with _connect(synthetic_collection.path) as conn:
        records = OfflineReader(conn, deck, language_code=LANG).get_note_records()
        sync = AnkiSync(db=db, _reader=RecordsReader(records), _writer=OfflineWriter(conn), language_code=LANG)
        sync.sync_pull()
        assert db.get_collocation_by_id(cid)[1].directions[Direction.PRODUCTION].reps == 1
        assert sync.rotate_drill_notes() == 1
        [nid] = [r[0] for r in conn.execute("SELECT id FROM notes")]

    # the control, on a copy: a note with a field missing IS reported
    bad = tmp_path / "bad.anki2"
    shutil.copy(synthetic_collection.path, bad)
    with _connect(bad) as conn:
        flds = conn.execute("SELECT flds FROM notes WHERE id = ?", (nid,)).fetchone()[0]
        conn.execute("UPDATE notes SET flds = ? WHERE id = ?", ("\x1f".join(flds.split("\x1f")[:11]), nid))
    control = run_oracle(bad, [{"op": "check_database"}]).raw()["check_database_0"]
    assert not control["ok"] and "Fixed" in control["report"], control["report"]

    raw = run_oracle(
        synthetic_collection.path,
        [{"op": "check_database"}, {"op": "get_card_row", "card_id": card_id}, {"op": "render_cards"}],
    ).raw()

    assert raw["check_database_0"]["ok"], raw["check_database_0"]["report"]
    assert raw["get_card_row_1"] == reviewed, "the rotation wrote the note only"
    [card] = raw["render_cards_2"].values()
    front = re.sub(r'data-cloze="[^"]*"', "", card["question"])
    assert "Hun er her." in front and "+ de" in front  # step 1: step 0's answer, then the cue
    assert "De er her." not in front
    assert "De er her." in card["answer"]
