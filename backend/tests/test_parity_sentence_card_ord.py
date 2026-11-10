"""Where a lesson line's card falls among the day's new cards (tunatale-h0q3.60, Layer 94).

Anki's TEMPLATE sort ranks the new cards it gathered by ``cards.ord``. A "TT
Sentence" note's cards have ords 0..N (the whole line 0, its blanks 1, 2, ..),
while TT keeps every one of them as a PRODUCTION direction and used to rank
PRODUCTION as ord 1. So a line's second blank (ord 2) came before a vocab
production card in TT and after it in Anki, and a whole-line card (ord 0) came
after every new recognition card in TT and among them in Anki. Found by review of
h0q3.55; on the live Cebuano deck 35 of 52 sentence cards sat at an ord other
than 1.

TT now stores the card's ord (``collocation_directions.anki_ord``, healed by
every pull) and ranks by it. Each case builds the same deck on both sides: one
line with one NEW card and two learned ones, a vocab production card, a vocab
recognition card.

The ord-1 case is the control: TT agreed with Anki there before the fix, by
coincidence, and must still agree.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime

import pytest

from app.common.guid import compute_guid
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.add_sentence_notetype import add_sentence_notetype
from app.srs.anki_mirror.queue_engine import _compute_live_main
from app.srs.database import SRSDatabase
from app.srs.sentence_note import sentence_note_fields
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import DEFAULT_DESIRED_RETENTION, SyntheticCollection
from tests.test_parity_sentence_sibling_bury import FSRS_WEIGHTS, LINE, NOTE_ID, NOW_MS

LANG = "ceb"
DUAL_MID = 1600000000002
M_NOTE, N_NOTE = 300, 400
#: Anki ord of each of LINE's rows (blanks first, the whole line last).
ORD_OF_ROW = (1, 2, 0)

SENTENCE = "sentence card"
PRODUCTION = "vocab production (ord 1)"
RECOGNITION = "vocab recognition (ord 0)"
NAMES = {
    **{NOTE_ID * 10 + ord_: SENTENCE for ord_ in ORD_OF_ROW},
    M_NOTE * 10 + 1: PRODUCTION,
    N_NOTE * 10: RECOGNITION,
}

SENTENCE_POSITION, PRODUCTION_POSITION, RECOGNITION_POSITION = 5, 10, 20
FUTURE_DUE_DAY = 50_000


def _anki_served(coll: SyntheticCollection, new_ord: int) -> list[str]:
    coll.enable_fsrs(weights=FSRS_WEIGHTS, retention=DEFAULT_DESIRED_RETENTION)
    coll.set_bury(bury_new=True, bury_reviews=True)
    coll.set_daily_limits(new=3, reviews=200)
    coll.set_new_card_display_order(sort_order=0, gather_priority=0)
    coll.add_notetype(DUAL_MID, "Dual", ("Front", "Back"), template_count=2)
    learned = dict(
        type=2,
        queue=2,
        due=FUTURE_DUE_DAY,
        ivl=30,
        reps=5,
        stability=30.0,
        difficulty=5.0,
        last_review_secs=int(time.time()) - 5 * 86400,
        desired_retention=DEFAULT_DESIRED_RETENTION,
    )
    fields = sentence_note_fields(LINE, cue_audio=None, answer_audio=None, source="Wolff 1 · line 4")
    coll.add_note(NOTE_ID, "sentence-guid", fields, mid=NOW_MS)
    for ord_ in ORD_OF_ROW:
        kw = dict(type=0, queue=0, due=SENTENCE_POSITION) if ord_ == new_ord else learned
        coll.add_card(id=NOTE_ID * 10 + ord_, note_id=NOTE_ID, ord=ord_, **kw)
    coll.add_note(id=M_NOTE, guid="g-m", fields=["matured", "back"], mid=DUAL_MID)
    coll.add_card(id=M_NOTE * 10, note_id=M_NOTE, ord=0, **learned)
    coll.add_card(id=M_NOTE * 10 + 1, note_id=M_NOTE, ord=1, type=0, queue=0, due=PRODUCTION_POSITION)
    coll.add_note(id=N_NOTE, guid="g-n", fields=["newword", "back"], mid=DUAL_MID)
    coll.add_card(id=N_NOTE * 10, note_id=N_NOTE, ord=0, type=0, queue=0, due=RECOGNITION_POSITION)
    coll.save()
    with sqlite3.connect(coll.path) as conn:
        assert add_sentence_notetype(conn, now_ms=NOW_MS) == "created"
    raw = run_oracle(coll.path, [{"op": "get_queue", "deck_id": 1, "fetch_limit": 50}]).raw()
    return [NAMES[c["card_id"]] for c in raw["get_queue_0"]["cards"]]


def _at_rollover(days: int) -> datetime:
    return datetime.combine(date.today() + timedelta(days=days), dtime(4, 0), tzinfo=UTC)


def _tt_served(new_ord: int) -> list[str]:
    """The same deck as a synced TT holds it: every direction carries its card's id, position and ord."""
    db = SRSDatabase(":memory:")
    for key, value in (
        ("daily_new_cap", "3"),
        ("daily_review_cap", "200"),
        ("bury_new", "True"),
        ("bury_review", "True"),
        ("new_card_sort_order", "0"),
        ("new_card_gather_priority", "0"),
    ):
        db.set_anki_state_cache(key, value)

    def _set(guid: str, direction: Direction, card_id: int, ord_: int, position: int | None) -> None:
        new = position is not None
        db.update_direction(
            guid,
            direction,
            DirectionState(
                direction=direction,
                state=SRSState.NEW if new else SRSState.REVIEW,
                due_at=_at_rollover(0 if new else 30),
                anki_card_id=card_id,
                anki_due=position if new else FUTURE_DUE_DAY,
                anki_ord=ord_,
                reps=0 if new else 5,
                stability=30.0,
                difficulty=5.0,
            ),
        )

    def _add(unit: SyntacticUnit) -> str:
        assert db.add_collocation(unit, language_code=LANG)
        return compute_guid(unit.text, LANG, unit.disambig_key)

    for card, ord_ in zip(LINE, ORD_OF_ROW, strict=True):
        _set(
            _add(card.unit()),
            Direction.PRODUCTION,
            NOTE_ID * 10 + ord_,
            ord_,
            SENTENCE_POSITION if ord_ == new_ord else None,
        )
    matured = _add(SyntacticUnit(text="matured", translation="matured", word_count=1, difficulty=1, source="corpus"))
    _set(matured, Direction.RECOGNITION, M_NOTE * 10, 0, None)
    _set(matured, Direction.PRODUCTION, M_NOTE * 10 + 1, 1, PRODUCTION_POSITION)
    newword = _add(SyntacticUnit(text="newword", translation="newword", word_count=1, difficulty=1, source="corpus"))
    _set(newword, Direction.RECOGNITION, N_NOTE * 10, 0, RECOGNITION_POSITION)
    with db._get_conn() as conn:  # linked, as after a sync
        conn.execute("UPDATE collocations SET anki_note_id = id + 1000")
        conn.commit()
    return [NAMES[item.directions[d].anki_card_id] for _row, item, _lang, d in _compute_live_main(db)]


@pytest.mark.oracle
@pytest.mark.parametrize(
    ("new_ord", "expected"),
    [
        pytest.param(1, [RECOGNITION, SENTENCE, PRODUCTION], id="first blank (ord 1): the control"),
        pytest.param(2, [RECOGNITION, PRODUCTION, SENTENCE], id="second blank (ord 2) ranks after production"),
        pytest.param(0, [SENTENCE, RECOGNITION, PRODUCTION], id="whole line (ord 0) ranks with recognition"),
    ],
)
def test_a_sentence_card_ranks_by_its_own_ord(
    synthetic_collection: SyntheticCollection, new_ord: int, expected: list[str]
) -> None:
    anki = _anki_served(synthetic_collection, new_ord)
    assert anki == expected, "the fixture no longer shows what it was built to show"
    assert _tt_served(new_ord) == anki
