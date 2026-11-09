"""Sibling bury for a lesson line's cards on one "TT Sentence" note (tunatale-h0q3.55 phase 2).

A line's blanks and its whole-line card are clozes of ONE note, so Anki buries
them as siblings: with ``bury_new`` a second new card of the note is buried once
one is gathered, and with ``bury_reviews`` a second due review likewise. TT keeps
one collocation per card (design B), so its sibling identity can no longer be the
row id. The rows of one line share ``collocations.sibling_group``, and every TT
bury test keys on ``COALESCE(sibling_group, row id)``.

Each test builds the same line on both sides — three cards of one note in Anki,
three rows from ``SentenceCard.unit()`` in TT — and compares Anki's ``counts``
with TT's badge counters and its served queue (``_compute_live_main``).

The control is the shape that exists today: rows with no sibling group (the
separate Cloze notes of h0q3.42) are not siblings, and TT counts each one.

Not covered here: the grade-time bury Anki writes into the collection
(``bury_siblings`` works on note ids, so a shared note needs nothing new) and
the create/pull/push of the shared note (``test_anki_sentence_note_sync.py``).
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime

import pytest

from app.common.guid import compute_guid
from app.models.srs_item import Direction, DirectionState, SRSState
from app.plugins.anki_sync.add_sentence_notetype import add_sentence_notetype
from app.srs.anki_mirror.queue_engine import _compute_live_main
from app.srs.database import SRSDatabase
from app.srs.fsrs import DEFAULT_FSRS5_PARAMS
from app.srs.sentence_card import Cue, Gloss, SentenceCard, blank_span
from app.srs.sentence_note import sentence_note_fields
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import DEFAULT_DESIRED_RETENTION, SyntheticCollection

FSRS_WEIGHTS = DEFAULT_FSRS5_PARAMS.weights
#: add_sentence_notetype takes max(now_ms, max id + 1); the fixture's notetype ids are small.
NOW_MS = 1790000000000
NOTE_ID = 1790000000500

_BASE = SentenceCard(
    lesson_id="wolff-1",
    line_index=4,
    speaker="Ínting",
    text="Nâa ba si Místir Abáya?",
    spoken="Nâa ba si Místir Abáya?",
    english="Is Mr. Abaya there?",
    glosses=(Gloss("ba", "question particle"),),
    cue=Cue(speaker="Inday", text="Únsa may átò?", spoken="Únsa may átò?", english="What can I do for you?"),
)
#: In TT's row-id order: blanks first, the whole line last. Anki ords: whole = 0, blanks 1, 2.
LINE = [
    _BASE.with_blank(blank_span(_BASE.text, "nâa"), "there (near you)"),
    _BASE.with_blank(blank_span(_BASE.text, "ba"), "yes/no question particle"),
    _BASE,
]

NEW, LEARNING, REVIEW_DUE = "new", "learning", "review_due"


def _anki_counts(coll: SyntheticCollection, states: dict[int, str]) -> dict[str, int]:
    """Anki's post-bury counts for one TT Sentence note whose card at ord N is in ``states[N]``."""
    coll.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    coll.set_bury(bury_new=True, bury_reviews=True)
    fields = sentence_note_fields(LINE, cue_audio=None, answer_audio=None, source="Wolff 1 · line 4")
    coll.add_note(NOTE_ID, "sentence-guid", fields, mid=NOW_MS)
    now_secs = int(time.time())
    for ord_, state in states.items():
        kw: dict = {}
        if state == REVIEW_DUE:
            kw = dict(type=2, queue=2, due=0, ivl=10, reps=5, last_review_secs=now_secs - 5 * 86400)
        elif state == LEARNING:
            kw = dict(type=1, queue=1, due=now_secs + 60, reps=1, left=1001, last_review_secs=now_secs - 600)
        else:
            kw = dict(type=0, queue=0, due=ord_ + 1)
        if state != NEW:
            kw.update(stability=10.0, difficulty=5.0, desired_retention=DEFAULT_DESIRED_RETENTION)
        coll.add_card(id=NOTE_ID * 10 + ord_, note_id=NOTE_ID, ord=ord_, **kw)
    coll.save()
    with sqlite3.connect(coll.path) as conn:
        assert add_sentence_notetype(conn, now_ms=NOW_MS) == "created"
    raw = run_oracle(coll.path, [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}]).raw()
    return raw["get_queue_0"]["counts"]


def _tt_line(states: dict[int, str], *, grouped: bool = True) -> SRSDatabase:
    """TT's side of the same line: one row per card, ``states`` keyed by Anki ord (whole = 0)."""
    db = SRSDatabase(":memory:")
    db.set_anki_state_cache("daily_new_cap", "20")
    db.set_anki_state_cache("daily_review_cap", "200")
    db.set_anki_state_cache("bury_new", "True")
    db.set_anki_state_cache("bury_review", "True")
    ord_of = {LINE[0].key: 1, LINE[1].key: 2, LINE[2].key: 0}
    today = date.today()
    for card in LINE:
        unit = card.unit()
        assert db.add_collocation(unit, language_code="ceb")
        item = db.get_collocation_by_guid(compute_guid(unit.text, "ceb", unit.disambig_key))
        assert item is not None
        state = states[ord_of[card.key]]
        srs_state = {NEW: SRSState.NEW, LEARNING: SRSState.LEARNING, REVIEW_DUE: SRSState.REVIEW}[state]
        db.update_direction(
            item.guid,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                due_at=datetime.combine(today, dtime(4, 0), tzinfo=UTC) - timedelta(days=1 if state != NEW else 0),
                stability=10.0,
                difficulty=5.0,
                reps=0 if state == NEW else 1,
                lapses=0,
                state=srs_state,
            ),
        )
    if not grouped:
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET sibling_group = NULL")
    return db


def _tt_counts(db: SRSDatabase) -> dict[str, int]:
    today = date.today()
    return {
        "new": db.count_new_available_collocations(today),
        "review": db.count_review_due_collocations(today),
    }


def _served(db: SRSDatabase) -> int:
    return len(_compute_live_main(db))


@pytest.mark.oracle
def test_new_cards_of_one_line_collapse_to_one(synthetic_collection: SyntheticCollection) -> None:
    states = {0: NEW, 1: NEW, 2: NEW}
    anki = _anki_counts(synthetic_collection, states)
    assert anki["new"] == 1, f"Anki buries the 2nd and 3rd new sibling of one note; counts={anki}"

    db = _tt_line(states)
    assert _tt_counts(db)["new"] == anki["new"]
    assert _served(db) == 1, "TT serves one card of the line, as Anki gathers one"


@pytest.mark.oracle
def test_due_review_blank_buries_its_siblings(synthetic_collection: SyntheticCollection) -> None:
    """Two blanks due as reviews and the whole line new: Anki gathers one review and
    buries the other review and the new card."""
    states = {0: NEW, 1: REVIEW_DUE, 2: REVIEW_DUE}
    anki = _anki_counts(synthetic_collection, states)
    assert (anki["new"], anki["review"]) == (0, 1), f"counts={anki}"

    db = _tt_line(states)
    assert _tt_counts(db) == {"new": anki["new"], "review": anki["review"]}
    assert _served(db) == 1


@pytest.mark.oracle
def test_learning_blank_buries_new_and_review_siblings(synthetic_collection: SyntheticCollection) -> None:
    states = {0: NEW, 1: LEARNING, 2: REVIEW_DUE}
    anki = _anki_counts(synthetic_collection, states)
    assert (anki["new"], anki["review"]) == (0, 0), f"counts={anki}"

    db = _tt_line(states)
    assert _tt_counts(db) == {"new": anki["new"], "review": anki["review"]}
    assert _served(db) == 0, "the learning card is served from the learning queue, not live_main"


def test_rows_without_a_sibling_group_are_not_siblings() -> None:
    """Control: today's separate Cloze notes (sibling_group NULL) are each counted, as Anki
    counts separate notes. If this went to 1 the grouping would be keyed on something else."""
    db = _tt_line({0: NEW, 1: NEW, 2: NEW}, grouped=False)
    assert _tt_counts(db)["new"] == 3
    assert _served(db) == 3


def test_a_line_rows_share_one_sibling_group() -> None:
    db = _tt_line({0: NEW, 1: NEW, 2: NEW})
    with db._get_conn() as conn:
        groups = {r[0] for r in conn.execute("SELECT sibling_group FROM collocations")}
    assert groups == {"sent:wolff-1:4"}


def test_upsert_by_guid_groups_a_sentence_row_and_leaves_vocab_ungrouped() -> None:
    from app.models.syntactic_unit import SyntacticUnit

    db = SRSDatabase(":memory:")
    now = datetime.now(UTC)
    prod = {Direction.PRODUCTION: DirectionState(direction=Direction.PRODUCTION, due_at=now)}
    db.upsert_by_guid(LINE[0].unit(), "ceb", prod)
    vocab = SyntacticUnit(text="balay", translation="house", word_count=1, difficulty=1, source="user")
    db.upsert_by_guid(vocab, "ceb", prod)
    with db._get_conn() as conn:
        groups = dict(conn.execute("SELECT text, sibling_group FROM collocations").fetchall())
    assert groups == {LINE[0].text: "sent:wolff-1:4", "balay": None}


@pytest.mark.oracle
async def test_a_line_minted_by_sync_is_a_note_anki_keeps_as_written(synthetic_collection: SyntheticCollection) -> None:
    """TT's own create path, then Anki: Check Database passes and generates or removes no card
    (TT's cards are exactly the ords Anki derives from Text), the cards are one note's siblings
    (one new card gathered), and after a row's deletion is pushed the graved card stays gone
    and the rest still check clean.

    The hand-built notes above pin the bury; this pins that sync writes that note."""
    from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
    from tests._helpers.anki_sync_create_new import FakeReader

    synthetic_collection.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    synthetic_collection.set_bury(bury_new=True, bury_reviews=True)
    synthetic_collection.save()
    db = _tt_line({0: NEW, 1: NEW, 2: NEW})
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.row_factory = sqlite3.Row
        assert add_sentence_notetype(conn, now_ms=NOW_MS) == "created"
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(conn), language_code="ceb")
        await sync.sync_create_new(deck_name=synthetic_collection.deck_name, model_name="unused")
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1

    raw = run_oracle(
        synthetic_collection.path,
        [{"op": "check_database"}, {"op": "note_ords"}, {"op": "get_queue", "deck_id": 1, "fetch_limit": 50}],
    ).raw()
    assert raw["check_database_0"]["ok"], raw["check_database_0"]["report"]
    [ords] = raw["note_ords_1"]["ords"].values()
    assert ords == [0, 1, 2]
    assert raw["get_queue_2"]["counts"]["new"] == 1

    with db._get_conn() as tt:
        ba_id = tt.execute("SELECT id FROM collocations WHERE disambig_key = ?", (LINE[1].key,)).fetchone()[0]
    db.delete_collocation(ba_id)
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.row_factory = sqlite3.Row
        AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(conn), language_code="ceb").sync_push()

    raw = run_oracle(synthetic_collection.path, [{"op": "check_database"}, {"op": "note_ords"}]).raw()
    assert raw["check_database_0"]["ok"], raw["check_database_0"]["report"]
    [ords] = raw["note_ords_1"]["ords"].values()
    assert ords == [0, 1], "the blank whose row was deleted (cloze 3, ord 2) is graved and not regenerated"
