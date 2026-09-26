"""TT's own Cebuano Vocabulary notes read back by field NAME (u8nz.7, 2026-09-26).

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

from app.cards.vocab_notetype import CEBUANO_VOCAB
from app.plugins.anki_sync.sync import OfflineReader, OfflineWriter

MID = 1_700_000_000_001
DID = 21


def _collection(tmp_path: Path) -> sqlite3.Connection:
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
    conn.execute("INSERT INTO decks VALUES (20, '3. Bisaya', 0, 0, '{}')")
    conn.execute("INSERT INTO decks VALUES (?, '3. Bisaya\x1fTunaTale', 0, 0, '{}')", (DID,))
    conn.execute("INSERT INTO notetypes VALUES (?, ?, 0, 0, NULL)", (MID, CEBUANO_VOCAB.name))
    for ord_, name in enumerate(CEBUANO_VOCAB.field_names):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, NULL)", (MID, ord_, name))
    flds = "\x1f".join(["mahal", "expensive", "[sound:tts_mahal.mp3]", '<img src="img_expensive.jpg">', "", "", ""])
    conn.execute("INSERT INTO notes VALUES (7001, 'ceb_1', ?, 0, 0, 'tunatale', ?, 'mahal', 0, 0, '')", (MID, flds))
    for ord_ in (0, 1):
        conn.execute(
            "INSERT INTO cards VALUES (?, 7001, ?, ?, 0, 0, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (70010 + ord_, DID, ord_, 1 + ord_),
        )
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def test_a_minted_cebuano_note_reads_back_by_field_name(tmp_path):
    conn = _collection(tmp_path)
    [record] = OfflineReader(conn, "3. Bisaya", language_code="ceb").get_note_records()
    assert (record.l2_text, record.translation) == ("mahal", "expensive")
    assert {c.anki_card_id: c.direction.value for c in record.cards} == {
        70010: "recognition",
        70011: "production",
    }


def test_a_push_writes_each_role_into_its_own_field(tmp_path):
    conn = _collection(tmp_path)
    roles = OfflineWriter(conn).note_fields_by_role(7001, language_code="ceb")
    assert roles == {"text": "Cebuano", "translation": "English", "source_sentence": "Note", "image": "Image"}
