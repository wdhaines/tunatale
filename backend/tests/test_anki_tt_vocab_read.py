"""TT's own vocab notes read back by field NAME (u8nz.7, tunatale-2qqr, 2026-09-26).

The first Cebuano mint ever (74 starter cards) failed the sync it ran in:
``sync_create_new`` minted the notes, then read the deck back and raised
"No L2 scorer for this language". A TT vocab note stores the headword as plain
text in its first field, with no L2 markup class. So the reader has to guess
which of ``mahal`` / ``expensive`` / ``[sound:…]`` is the Cebuano, and Cebuano,
like Tagalog, has no letters that tell it apart from English. The fix is not a
scorer that guesses: TT wrote the notetype, so the plugin names its fields.

The note shape is the one measured in the live tt_collection after that sync.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.cards.vocab_notetype import CEBUANO_VOCAB, TAGALOG_VOCAB, VocabNotetype
from app.plugins.anki_sync.sync import OfflineReader, OfflineWriter

MID = 1_700_000_000_001
DID = 21


# Tagalog hit the same crash the same day: its first sync after this fix was
# written minted 20 Tagalog Vocabulary notes, then raised on reading them back.
LANGUAGES = [
    pytest.param("ceb", CEBUANO_VOCAB, "3. Bisaya", ("mahal", "expensive"), id="ceb"),
    pytest.param("tl", TAGALOG_VOCAB, "2. Pimsleur Tagalog", ("pamilya", "family"), id="tl"),
]


def _collection(tmp_path: Path, notetype: VocabNotetype, root: str, word: tuple[str, str]) -> sqlite3.Connection:
    path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,
            dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT);
        CREATE TABLE notes (id INTEGER, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
            tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT);
        CREATE TABLE cards (id INTEGER, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
            usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, factor INTEGER,
            reps INTEGER, lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT);
        CREATE TABLE revlog (id INTEGER, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,
            lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER);
        CREATE TABLE decks (id INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB);
        CREATE TABLE notetypes (id INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
        CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB);
    """)
    conn.execute("INSERT INTO col VALUES (1,1704067200,0,0,11,0,0,0,'{}','{}','{}','{}','{}')")
    conn.execute("INSERT INTO decks VALUES (20, ?, 0, 0, '{}')", (root,))
    conn.execute("INSERT INTO decks VALUES (?, ?, 0, 0, '{}')", (DID, f"{root}\x1fTunaTale"))
    conn.execute("INSERT INTO notetypes VALUES (?, ?, 0, 0, NULL)", (MID, notetype.name))
    for ord_, name in enumerate(notetype.field_names):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, NULL)", (MID, ord_, name))
    l2, gloss = word
    flds = "\x1f".join([l2, gloss, f"[sound:tts_{l2}.mp3]", f'<img src="img_{gloss}.jpg">', "", "", ""])
    conn.execute("INSERT INTO notes VALUES (7001, 'tt_1', ?, 0, 0, 'tunatale', ?, ?, 0, 0, '')", (MID, flds, l2))
    for ord_ in (0, 1):
        conn.execute(
            "INSERT INTO cards VALUES (?, 7001, ?, ?, 0, 0, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (70010 + ord_, DID, ord_, 1 + ord_),
        )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


@pytest.mark.parametrize(("code", "notetype", "root", "word"), LANGUAGES)
def test_a_minted_vocab_note_reads_back_by_field_name(tmp_path, code, notetype, root, word):
    conn = _collection(tmp_path, notetype, root, word)
    [record] = OfflineReader(conn, root, language_code=code).get_note_records()
    assert (record.l2_text, record.translation) == word
    assert {c.anki_card_id: c.direction.value for c in record.cards} == {
        70010: "recognition",
        70011: "production",
    }


@pytest.mark.parametrize(("code", "notetype", "root", "word"), LANGUAGES)
def test_a_push_writes_each_role_into_its_own_field(tmp_path, code, notetype, root, word):
    conn = _collection(tmp_path, notetype, root, word)
    roles = OfflineWriter(conn).note_fields_by_role(7001, language_code=code)
    assert roles == {"text": notetype.l2_field, "translation": "English", "source_sentence": "Note", "image": "Image"}
