"""A drill row never reaches Anki before its note is built (tunatale-5i1p.4, audit I1).

A drill card's ``card_data`` is not a ``SentenceCard``, so ``sentence_card()``
reads it as ``None``; without a hold, ``sync_create_new``'s mint loop would take
a ``sent:`` cloze row with no sentence card down the plain-cloze branch and mint
it on Anki's built-in Cloze notetype: a wrong-notetype note that the
"TT Sentence" phase (5i1p.5) would then have to undo. Until 5i1p.5 builds a
drill note's fields, the mint holds every drill row unlinked, NEW and
studiable in TT, and says so in the log.

Real ``OfflineWriter`` on an in-memory collection carrying both the built-in
Cloze notetype and the real "TT Sentence" notetype (``add_sentence_notetype``).
"""

from __future__ import annotations

import logging

import pytest

from app.common.guid import compute_guid
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
from app.srs.database import SRSDatabase
from app.srs.lesson_card_mint import mint_lesson_cards
from tests._helpers.anki_sync_create_new import FakeReader
from tests.test_anki_sentence_note_sync import _conn
from tests.test_drill_card import DRILLS
from tests.test_lesson_drill_cards import EXPECTED_KEYS, _cards

LANG = "no"
DECK = "0. Slovene"  # the helper collection's deck


@pytest.fixture
def no_tts(monkeypatch):
    """The TTS boundary: the plain cloze's audio fails open, as it does with Azure down."""
    import app.audio.cloze_tts as cloze_tts_mod

    async def _down(text, voice=None):
        raise RuntimeError("TTS down")

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _down)


def _plain_cloze(db: SRSDatabase) -> str:
    unit = SyntacticUnit(
        text="er",
        translation="is",
        word_count=1,
        difficulty=1,
        source="test",
        card_type="cloze",
        source_sentence="Han {{c1::er}} her.",
    )
    assert db.add_collocation(unit, LANG)
    return compute_guid(unit.text, LANG, unit.disambig_key)


def _notes_by_notetype(anki_conn) -> dict[str, int]:
    rows = anki_conn.execute(
        "SELECT nt.name, COUNT(*) FROM notes n JOIN notetypes nt ON nt.id = n.mid GROUP BY nt.name"
    ).fetchall()
    return {name: count for name, count in rows}


async def _mint(db: SRSDatabase, anki_conn):
    writer = OfflineWriter(anki_conn)
    sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer, language_code=LANG)
    return await sync.sync_create_new(deck_name=DECK, model_name="Slovene Vocabulary")


async def test_i1_a_drill_row_mints_no_note_while_a_plain_cloze_does(no_tts, caplog):
    db = SRSDatabase(":memory:")
    minted = mint_lesson_cards(db, LANG, _cards(), drills=DRILLS)
    cloze_guid = _plain_cloze(db)
    anki = _conn()

    with caplog.at_level(logging.INFO):
        await _mint(db, anki)

    # control: the plain cloze row in the same run did reach Anki, on the Cloze notetype
    assert _notes_by_notetype(anki) == {"Cloze": 1}
    assert db.get_collocation_by_guid(cloze_guid).anki_note_id is not None
    # no drill row has a note of any notetype, and each is still NEW and unlinked
    for cid, _card in minted:
        _, item, _ = db.get_collocation_by_id(cid)
        assert item.anki_note_id is None
        prod = item.directions[Direction.PRODUCTION]
        assert (prod.anki_card_id, prod.state) == (None, SRSState.NEW)
    for key in EXPECTED_KEYS:
        assert f"DRILL_CARD_HELD key={key}" in caplog.text


async def test_a_dry_run_does_not_count_a_held_drill_row():
    db = SRSDatabase(":memory:")
    mint_lesson_cards(db, LANG, _cards(), drills=DRILLS)
    _plain_cloze(db)
    sync = AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(_conn()), language_code=LANG)
    report = await sync.sync_create_new(deck_name=DECK, model_name="Slovene Vocabulary", dry_run=True)
    assert report.count == 1  # the plain cloze only


async def test_a_second_sync_holds_it_again(no_tts):
    db = SRSDatabase(":memory:")
    mint_lesson_cards(db, LANG, _cards(), drills=DRILLS)
    anki = _conn()
    await _mint(db, anki)
    await _mint(db, anki)
    assert _notes_by_notetype(anki) == {}


def test_i1_push_a_flagged_drill_row_has_no_note_to_write():
    """The push side: a drill row flagged dirty (its audio attached) has no Anki note, so nothing is written."""
    db = SRSDatabase(":memory:")
    minted = mint_lesson_cards(db, LANG, _cards(), drills=DRILLS)
    for cid, _ in minted:
        db.add_dirty_field_by_id(cid, "audio")
    anki = _conn()
    before = anki.execute("SELECT COUNT(*) FROM notes").fetchone()[0]

    report = AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(anki), language_code=LANG).sync_push()

    assert report.no_anki_id == len(minted)
    assert anki.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == before == 0
