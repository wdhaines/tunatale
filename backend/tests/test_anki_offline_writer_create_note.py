"""Tests for OfflineWriter.create_note, get_cards_for_note, and store_media_file."""

from __future__ import annotations

import hashlib
import re
import sqlite3

import pytest

from app.cards.notetype import SLOVENE_VOCAB_FIELD_NAMES, SLOVENE_VOCAB_NOTETYPE_NAME
from app.common.guid import compute_guid
from app.plugins.anki_sync.sync import DuplicateNoteError, OfflineWriter

_SVNT_MID = 1000001
_DECK_ID = 12345
_DECK_NAME = "0. Slovene"


def _make_collection_conn() -> sqlite3.Connection:
    """Build minimal in-memory collection.anki2 for OfflineWriter.create_note tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE col (
            id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,
            dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT,
            decks TEXT, dconf TEXT, tags TEXT
        );
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY, guid TEXT UNIQUE, mid INTEGER, mod INTEGER,
            usn INTEGER, tags TEXT, flds TEXT, sfld TEXT, csum INTEGER,
            flags INTEGER, data TEXT
        );
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER,
            mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER, due INTEGER,
            ivl INTEGER, factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER,
            odue INTEGER, odid INTEGER, flags INTEGER, data TEXT
        );
        CREATE TABLE revlog (
            id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER,
            ivl INTEGER, lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER
        );
        CREATE TABLE notetypes (
            id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER,
            usn INTEGER, config BLOB
        );
        CREATE TABLE templates (
            ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER,
            usn INTEGER, config BLOB, PRIMARY KEY (ntid, ord)
        );
        CREATE TABLE fields (
            ntid INTEGER, ord INTEGER, name TEXT, config BLOB,
            PRIMARY KEY (ntid, ord)
        );
        CREATE TABLE decks (
            id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER,
            usn INTEGER, common BLOB
        );
    """)
    conn.execute("INSERT INTO col VALUES (1, 1704067200, 0, 1000, 18, 0, 0, 0, '{}', '{}', '{}', '{}', '{}')")
    conn.execute("INSERT INTO decks VALUES (?, ?, 0, 0, x'')", (_DECK_ID, _DECK_NAME))
    conn.execute(
        "INSERT INTO notetypes VALUES (?, ?, 0, 0, x'')",
        (_SVNT_MID, SLOVENE_VOCAB_NOTETYPE_NAME),
    )
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?, ?, x'')",
        [(_SVNT_MID, i, name) for i, name in enumerate(SLOVENE_VOCAB_FIELD_NAMES)],
    )
    conn.executemany(
        "INSERT INTO templates VALUES (?, ?, ?, 0, 0, x'')",
        [(_SVNT_MID, 0, "Recognition"), (_SVNT_MID, 1, "Production")],
    )
    conn.commit()
    return conn


def _make_fields(word: str = "banka", english: str = "bank", disambig: str = "") -> dict:
    return {
        "Slovene": word,
        "English": english,
        "Audio": "",
        "Image": "",
        "Grammar": "",
        "Note": "",
        "DisambigKey": disambig,
    }


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


class TestOfflineWriterCreateNote:
    def test_inserts_note_with_correct_guid(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        note_id = writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields(), ["tunatale"])

        row = conn.execute("SELECT guid, mid, usn, flds, sfld FROM notes WHERE id = ?", (note_id,)).fetchone()
        assert row is not None
        expected_guid = compute_guid("banka", "sl", "")
        assert row["guid"] == expected_guid
        assert row["mid"] == _SVNT_MID
        assert row["usn"] == -1

    def test_inserts_note_with_correct_flds_and_sfld(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        fields = _make_fields("hiša", "house", "B1")
        note_id = writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, fields, ["tunatale"])

        row = conn.execute("SELECT flds, sfld FROM notes WHERE id = ?", (note_id,)).fetchone()
        expected_flds = "\x1f".join(fields.get(name, "") for name in SLOVENE_VOCAB_FIELD_NAMES)
        assert row["flds"] == expected_flds
        assert row["sfld"] == "hiša"

    def test_inserts_note_with_correct_csum(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        note_id = writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields(), ["tunatale"])

        row = conn.execute("SELECT csum FROM notes WHERE id = ?", (note_id,)).fetchone()
        expected_csum = int(hashlib.sha1(b"banka").hexdigest()[:8], 16)
        assert row["csum"] == expected_csum

    def test_inserts_note_with_usn_minus_one_and_mod_set(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        note_id = writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields(), ["tunatale"])

        row = conn.execute("SELECT usn, mod FROM notes WHERE id = ?", (note_id,)).fetchone()
        assert row["usn"] == -1
        assert row["mod"] > 0

    def test_inserts_two_cards_for_two_template_notetype(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        note_id = writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields(), ["tunatale"])

        cards = conn.execute(
            "SELECT ord, usn, type, queue FROM cards WHERE nid = ? ORDER BY ord", (note_id,)
        ).fetchall()
        assert len(cards) == 2
        assert {c["ord"] for c in cards} == {0, 1}
        assert all(c["usn"] == -1 for c in cards)
        assert all(c["type"] == 0 for c in cards)  # new card
        assert all(c["queue"] == 0 for c in cards)  # new queue

    def test_cards_have_correct_deck_id(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        note_id = writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields(), ["tunatale"])

        cards = conn.execute("SELECT did FROM cards WHERE nid = ?", (note_id,)).fetchall()
        assert all(c["did"] == _DECK_ID for c in cards)

    def test_bumps_col_mod_preserves_usn(self):
        conn = _make_collection_conn()
        conn.execute("UPDATE col SET mod = 100, usn = 5")
        conn.commit()

        writer = OfflineWriter(conn)
        writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields(), ["tunatale"])

        row = conn.execute("SELECT mod, usn FROM col").fetchone()
        assert row["mod"] > 100
        # col.usn is the sync ANCHOR (server's last value), not a dirty flag — preserve it.
        # Clobbering it to -1 forced an AnkiWeb full sync whenever another device advanced
        # the server (Layer 61). Content rows carry their own usn=-1 to push.
        assert row["usn"] == 5

    def test_does_not_change_col_scm(self):
        conn = _make_collection_conn()
        scm_before = conn.execute("SELECT scm FROM col").fetchone()["scm"]

        writer = OfflineWriter(conn)
        writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields(), ["tunatale"])

        scm_after = conn.execute("SELECT scm FROM col").fetchone()["scm"]
        assert scm_after == scm_before

    def test_returns_positive_note_id(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        note_id = writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields(), ["tunatale"])

        assert note_id > 0
        assert conn.execute("SELECT id FROM notes WHERE id = ?", (note_id,)).fetchone() is not None

    def test_duplicate_guid_raises_duplicate_note_error(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        note_id = writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields(), ["tunatale"])

        with pytest.raises(DuplicateNoteError) as exc_info:
            writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields(), ["tunatale"])

        assert exc_info.value.note_id == note_id

    def test_different_words_create_different_notes(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        id1 = writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields("banka", "bank"), ["tunatale"])
        id2 = writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields("hiša", "house"), ["tunatale"])

        assert id1 != id2
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 2

    def test_disambig_included_in_guid(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        id1 = writer.create_note(
            _DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields("barva", "color", "B1"), ["tunatale"]
        )
        id2 = writer.create_note(
            _DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields("barva", "paint", "B2"), ["tunatale"]
        )
        assert id1 != id2
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 2

    def test_unknown_notetype_raises_value_error(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        with pytest.raises(ValueError, match="not found in notetypes table"):
            writer.create_note(_DECK_NAME, "No Such Model", _make_fields(), ["tunatale"])

    def test_unknown_deck_raises_value_error(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        with pytest.raises(ValueError, match="not found"):
            writer.create_note("No Such Deck", SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields(), ["tunatale"])


class TestOfflineWriterGetCardsForNote:
    def test_returns_ord_to_card_id_dict(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        note_id = writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields(), ["tunatale"])

        cards = writer.get_cards_for_note(note_id)
        assert set(cards.keys()) == {0, 1}
        assert all(isinstance(v, int) for v in cards.values())

    def test_returns_empty_for_unknown_note(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        assert writer.get_cards_for_note(99999) == {}

    def test_card_ids_match_inserted_cards(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        note_id = writer.create_note(_DECK_NAME, SLOVENE_VOCAB_NOTETYPE_NAME, _make_fields(), ["tunatale"])

        cards_from_method = writer.get_cards_for_note(note_id)
        cards_from_db = {
            r["ord"]: r["id"]
            for r in conn.execute("SELECT ord, id FROM cards WHERE nid = ? ORDER BY ord", (note_id,)).fetchall()
        }
        assert cards_from_method == cards_from_db


class TestOfflineWriterStoreMediaFile:
    def test_writes_file_to_media_dir(self, tmp_path):
        media_dir = tmp_path / "collection.media"
        media_dir.mkdir()
        conn = _make_collection_conn()
        writer = OfflineWriter(conn, media_dir=media_dir)
        writer.store_media_file("test.mp3", b"audio_data")

        assert (media_dir / "test.mp3").read_bytes() == b"audio_data"

    def test_no_error_without_media_dir(self):
        conn = _make_collection_conn()
        writer = OfflineWriter(conn)
        writer.store_media_file("test.mp3", b"audio_data")  # should not raise

    def test_registers_in_media_db_when_present(self, tmp_path):
        media_dir = tmp_path / "collection.media"
        media_dir.mkdir()
        # Create a minimal collection.media.db
        media_db_path = tmp_path / "collection.media.db"
        media_conn = sqlite3.connect(str(media_db_path))
        media_conn.execute("CREATE TABLE media (fname TEXT PRIMARY KEY, csum TEXT, mtime INTEGER, dirty INTEGER)")
        media_conn.commit()
        media_conn.close()

        conn = _make_collection_conn()
        writer = OfflineWriter(conn, media_dir=media_dir, media_db_path=media_db_path)
        writer.store_media_file("sl_banka.mp3", b"audio_data")

        row = (
            sqlite3.connect(str(media_db_path))
            .execute("SELECT dirty FROM media WHERE fname = ?", ("sl_banka.mp3",))
            .fetchone()
        )
        assert row is not None
        assert row[0] == 1  # dirty=1 so AnkiWeb will pick it up

    def test_store_media_file_silent_on_bad_media_db(self, tmp_path):
        """sqlite3.Error during media.db update is non-fatal — file still written."""
        media_dir = tmp_path / "collection.media"
        media_dir.mkdir()
        # Create a media.db with the wrong schema (missing 'dirty' column) to trigger error
        media_db_path = tmp_path / "collection.media.db"
        bad_conn = sqlite3.connect(str(media_db_path))
        bad_conn.execute("CREATE TABLE media (fname TEXT PRIMARY KEY, broken TEXT)")
        bad_conn.commit()
        bad_conn.close()

        conn = _make_collection_conn()
        writer = OfflineWriter(conn, media_dir=media_dir, media_db_path=media_db_path)
        writer.store_media_file("test.mp3", b"audio_data")  # must not raise

        assert (media_dir / "test.mp3").read_bytes() == b"audio_data"

    def test_probes_db2_before_db_when_no_explicit_path(self, tmp_path):
        """Modern Anki uses collection.media.db2; writer must prefer it over .db."""
        media_dir = tmp_path / "collection.media"
        media_dir.mkdir()
        # Only db2 exists — no db1
        media_db2 = tmp_path / "collection.media.db2"
        mconn = sqlite3.connect(str(media_db2))
        mconn.execute("CREATE TABLE media (fname TEXT PRIMARY KEY, csum TEXT, mtime INTEGER, dirty INTEGER)")
        mconn.commit()
        mconn.close()

        conn = _make_collection_conn()
        writer = OfflineWriter(conn, media_dir=media_dir)  # no explicit media_db_path
        writer.store_media_file("sl_test.mp3", b"data")

        row = sqlite3.connect(str(media_db2)).execute("SELECT dirty FROM media WHERE fname='sl_test.mp3'").fetchone()
        assert row is not None and row[0] == 1

    def test_falls_back_to_db1_when_db2_absent(self, tmp_path):
        """Falls back to collection.media.db when db2 doesn't exist."""
        media_dir = tmp_path / "collection.media"
        media_dir.mkdir()
        # Only db1 exists
        media_db1 = tmp_path / "collection.media.db"
        mconn = sqlite3.connect(str(media_db1))
        mconn.execute("CREATE TABLE media (fname TEXT PRIMARY KEY, csum TEXT, mtime INTEGER, dirty INTEGER)")
        mconn.commit()
        mconn.close()

        conn = _make_collection_conn()
        writer = OfflineWriter(conn, media_dir=media_dir)  # no explicit media_db_path
        writer.store_media_file("sl_test.mp3", b"data")

        row = sqlite3.connect(str(media_db1)).execute("SELECT dirty FROM media WHERE fname='sl_test.mp3'").fetchone()
        assert row is not None and row[0] == 1

    def test_db2_wins_when_both_exist(self, tmp_path):
        """When both db1 and db2 exist, db2 wins."""
        media_dir = tmp_path / "collection.media"
        media_dir.mkdir()
        for suffix in ("collection.media.db", "collection.media.db2"):
            mconn = sqlite3.connect(str(tmp_path / suffix))
            mconn.execute("CREATE TABLE media (fname TEXT PRIMARY KEY, csum TEXT, mtime INTEGER, dirty INTEGER)")
            mconn.commit()
            mconn.close()

        conn = _make_collection_conn()
        writer = OfflineWriter(conn, media_dir=media_dir)
        writer.store_media_file("sl_test.mp3", b"data")

        row2 = (
            sqlite3.connect(str(tmp_path / "collection.media.db2"))
            .execute("SELECT dirty FROM media WHERE fname='sl_test.mp3'")
            .fetchone()
        )
        row1 = (
            sqlite3.connect(str(tmp_path / "collection.media.db"))
            .execute("SELECT dirty FROM media WHERE fname='sl_test.mp3'")
            .fetchone()
        )
        assert row2 is not None and row2[0] == 1  # db2 was written
        assert row1 is None  # db1 was NOT written


# ── Norwegian Vocabulary notetype (Phase 3 production write-back) ──────────

_NO_MID = 1000099
_NO_DECK_NAME = "0. 6000 Most Frequent Norwegian Words [Part 1]"
_NO_DECK_ID = 54321


def _make_norwegian_collection_conn() -> sqlite3.Connection:
    """In-memory collection with a Norwegian Vocabulary notetype (2 templates)."""
    from app.cards.vocab_notetype import NORWEGIAN_VOCAB

    conn = _make_collection_conn()
    conn.execute("INSERT INTO decks VALUES (?, ?, 0, 0, x'')", (_NO_DECK_ID, _NO_DECK_NAME))
    conn.execute("INSERT INTO notetypes VALUES (?, ?, 0, 0, x'')", (_NO_MID, NORWEGIAN_VOCAB.name))
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?, ?, x'')",
        [(_NO_MID, i, name) for i, name in enumerate(NORWEGIAN_VOCAB.field_names)],
    )
    conn.executemany(
        "INSERT INTO templates VALUES (?, ?, ?, 0, 0, x'')",
        [(_NO_MID, 0, "Recognition"), (_NO_MID, 1, "Production")],
    )
    conn.commit()
    return conn


def _make_norwegian_fields(word: str = "snakke", english: str = "to speak") -> dict:
    return {
        "Norwegian": word,
        "English": english,
        "Audio": "",
        "Image": "",
        "Grammar": "",
        "Note": "",
        "DisambigKey": "",
    }


class TestGetSortFieldName:
    def test_returns_l2_field_for_slovene(self):
        conn = _make_collection_conn()
        assert OfflineWriter(conn).get_sort_field_name(SLOVENE_VOCAB_NOTETYPE_NAME) == "Slovene"

    def test_returns_l2_field_for_norwegian(self):
        conn = _make_norwegian_collection_conn()
        assert OfflineWriter(conn).get_sort_field_name("Norwegian Vocabulary") == "Norwegian"

    def test_raises_for_unknown_notetype(self):
        conn = _make_collection_conn()
        with pytest.raises(ValueError, match="has no fields"):
            OfflineWriter(conn).get_sort_field_name("Nonexistent Notetype")


class TestNorwegianCreateNote:
    def test_minted_into_norwegian_notetype_with_norwegian_guid(self):
        conn = _make_norwegian_collection_conn()
        writer = OfflineWriter(conn)
        note_id = writer.create_note(
            _NO_DECK_NAME, "Norwegian Vocabulary", _make_norwegian_fields(), ["tunatale"], language_code="no"
        )
        row = conn.execute("SELECT guid, mid, sfld, flds FROM notes WHERE id = ?", (note_id,)).fetchone()
        # GUID folds the "no" language code (distinct from a Slovene "snakke").
        assert row["guid"] == compute_guid("snakke", "no", "")
        assert row["guid"] != compute_guid("snakke", "sl", "")
        assert row["mid"] == _NO_MID
        assert row["sfld"] == "snakke"
        # The L2 word lands in field ord 0 ("Norwegian").
        assert row["flds"].split("\x1f")[0] == "snakke"

    def test_mints_recognition_and_production_cards(self):
        conn = _make_norwegian_collection_conn()
        writer = OfflineWriter(conn)
        note_id = writer.create_note(
            _NO_DECK_NAME, "Norwegian Vocabulary", _make_norwegian_fields(), ["tunatale"], language_code="no"
        )
        cards = conn.execute("SELECT ord, did FROM cards WHERE nid = ? ORDER BY ord", (note_id,)).fetchall()
        assert [c["ord"] for c in cards] == [0, 1]  # recognition + production
        assert all(c["did"] == _NO_DECK_ID for c in cards)

    def test_fields_serialize_in_norwegian_field_order(self):
        conn = _make_norwegian_collection_conn()
        writer = OfflineWriter(conn)
        fields = _make_norwegian_fields("bilen", "the car")
        fields["English"] = "the car"
        note_id = writer.create_note(_NO_DECK_NAME, "Norwegian Vocabulary", fields, ["tunatale"], language_code="no")
        from app.cards.vocab_notetype import NORWEGIAN_VOCAB

        row = conn.execute("SELECT flds FROM notes WHERE id = ?", (note_id,)).fetchone()
        expected = "\x1f".join(fields.get(name, "") for name in NORWEGIAN_VOCAB.field_names)
        assert row["flds"] == expected

    def test_image_lands_in_norwegian_image_field(self):
        from app.cards.vocab_notetype import NORWEGIAN_VOCAB

        conn = _make_norwegian_collection_conn()
        writer = OfflineWriter(conn)
        fields = _make_norwegian_fields("bil", "car")
        fields["Image"] = '<img src="img_car.jpg">'
        note_id = writer.create_note(_NO_DECK_NAME, "Norwegian Vocabulary", fields, ["tunatale"], language_code="no")
        img_idx = NORWEGIAN_VOCAB.field_names.index("Image")
        parts = conn.execute("SELECT flds FROM notes WHERE id = ?", (note_id,)).fetchone()["flds"].split("\x1f")
        assert parts[img_idx] == '<img src="img_car.jpg">'
