"""The re-mint against Anki itself: a line's separate Cloze notes become one TT Sentence note (tunatale-h0q3.55 phase 3).

What this covers:
- After ``remint_sentence_notes`` and the sync that follows (orphans, then
  create), Anki's own Check Database accepts the collection, finds exactly one
  note for the line with a card per cloze (ords 0, 1, 2), and none of the old
  Cloze notes. Each TT row is linked to a card Anki agrees exists.

What this does NOT cover (owned by ``test_anki_remint_sentence_notes.py``):
- The refusals, the capture file, the resume path and the commit order.
- Sibling bury on the minted note (``test_parity_sentence_sibling_bury.py``).
"""

from __future__ import annotations

import sqlite3

import pytest

from app.models.srs_item import Direction
from app.plugins.anki_sync import remint_sentence_notes as remint
from app.plugins.anki_sync.add_sentence_notetype import add_sentence_notetype
from app.plugins.anki_sync.sync import AnkiSync, OfflineReader, OfflineWriter
from app.srs.database import SRSDatabase
from app.srs.sentence_card import Cue, Gloss, SentenceCard, blank_span
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import SyntheticCollection

LANG = "ceb"
CLOZE_MID = 1700000000000
#: add_sentence_notetype takes max(now_ms, max id + 1).
NOW_MS = 1790000000000
OLD_NOTES = (1794017364351, 1794017364362, 1794017364372)

_BASE = SentenceCard(
    lesson_id="wolff-01b",
    line_index=4,
    speaker="Ínting",
    text="Nâa ba si Místir Abáya?",
    spoken="Nâa ba si Místir Abáya?",
    english="Is Mr. Abaya there?",
    glosses=(Gloss("ba", "question particle"),),
    cue=Cue(speaker="Inday", text="Únsa may átò?", spoken="Únsa may átò?", english="What can I do for you?"),
)
LINE = [
    _BASE.with_blank(blank_span(_BASE.text, "nâa"), "there (near you)"),
    _BASE.with_blank(blank_span(_BASE.text, "ba"), "yes/no question particle"),
    _BASE,
]


@pytest.fixture
def no_tts(monkeypatch):
    import app.audio.cloze_tts as cloze_tts_mod

    async def _boom(text, voice=None):
        raise AssertionError(f"re-mint synthesized {text!r}")

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _boom)


@pytest.mark.oracle
async def test_the_re_minted_line_is_one_note_anki_accepts(synthetic_collection: SyntheticCollection, tmp_path, no_tts):
    coll = synthetic_collection
    coll.add_notetype(CLOZE_MID, "Cloze", ("Text", "Back Extra"))
    for note_id, card in zip(OLD_NOTES, LINE, strict=True):
        coll.add_note(note_id, f"old-{note_id}", [card.cloze(), ""], mid=CLOZE_MID)
        coll.add_card(note_id, note_id, type=2, queue=2, due=1, ivl=5, reps=3)
    coll.save()
    with sqlite3.connect(coll.path) as conn:
        assert add_sentence_notetype(conn, now_ms=NOW_MS) == "created"

    db = SRSDatabase(f"sqlite:///{tmp_path / 'tt.db'}")
    for note_id, card in zip(OLD_NOTES, LINE, strict=True):
        unit = card.unit()
        assert db.add_collocation(unit, LANG)
        with db._get_conn() as tt:
            guid = tt.execute("SELECT guid FROM collocations WHERE disambig_key = ?", (card.key,)).fetchone()[0]
            tt.execute("UPDATE collocations SET sibling_group = NULL WHERE guid = ?", (guid,))
        db.set_anki_ids(guid, note_id, {Direction.PRODUCTION: note_id})

    assert (
        remint.run(
            db,
            collection_path=coll.path,
            backup_dir=tmp_path / "backups",
            capture_path=tmp_path / "capture.json",
            lines=[_BASE.line_key],
            lessons=[],
            apply=True,
        )
        == 0
    )
    conn = sqlite3.connect(coll.path)
    sync = AnkiSync(
        db=db,
        _reader=OfflineReader(conn, coll.deck_name, language_code=LANG),
        _writer=OfflineWriter(conn),
        language_code=LANG,
    )
    sync.detect_and_reset_orphans()
    await sync.sync_create_new(deck_name=coll.deck_name, model_name="Basic")
    conn.commit()
    conn.close()

    raw = run_oracle(coll.path, [{"op": "check_database"}, {"op": "note_ords"}]).raw()

    assert raw["check_database_0"]["ok"], raw["check_database_0"]["report"]
    ords = raw["note_ords_1"]["ords"]
    assert len(ords) == 1, f"one note for the line, none of the old ones: {ords}"
    [(sfld, note_ords)] = ords.items()
    assert note_ords == [0, 1, 2]
    anki_cards = set(raw["note_ords_1"]["card_ids"][sfld].values())
    tt_cards = {db.get_collocation_by_guid(g).directions[Direction.PRODUCTION].anki_card_id for g in _guids(db)}
    assert tt_cards == anki_cards


def _guids(db: SRSDatabase) -> list[str]:
    with db._get_conn() as tt:
        return [r[0] for r in tt.execute("SELECT guid FROM collocations WHERE disambig_key LIKE 'sent:%'")]
