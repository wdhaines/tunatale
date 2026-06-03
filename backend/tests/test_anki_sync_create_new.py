"""Tests for S3.9: sync_create_new (addNote + media)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time

import httpx
import pytest

from app.anki.anki_connect import AnkiConnectClient
from app.anki.media.pipeline import MediaResult
from app.anki.sync import (
    AnkiSync,
    CardRecord,
    DuplicateNoteError,
    NoteRecord,
    OfflineWriter,
    _safe_stem,
)
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_db() -> SRSDatabase:
    return SRSDatabase(":memory:")


def _make_collection_conn():
    """Build minimal in-memory collection.anki2 for OfflineWriter.create_note tests."""
    import sqlite3

    from app.anki.notetype import SLOVENE_VOCAB_FIELD_NAMES, SLOVENE_VOCAB_NOTETYPE_NAME

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
    conn.execute("INSERT INTO decks VALUES (12345, '0. Slovene', 0, 0, x'')")
    conn.execute(
        "INSERT INTO notetypes VALUES (1000001, ?, 0, 0, x'')",
        (SLOVENE_VOCAB_NOTETYPE_NAME,),
    )
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?, ?, x'')",
        [(1000001, i, name) for i, name in enumerate(SLOVENE_VOCAB_FIELD_NAMES)],
    )
    conn.executemany(
        "INSERT INTO templates VALUES (?, ?, ?, 0, 0, x'')",
        [(1000001, 0, "Recognition"), (1000001, 1, "Production")],
    )
    conn.commit()
    return conn


def _add_item(db: SRSDatabase, text: str, translation: str) -> str:
    """Add a collocation with no Anki IDs. Returns guid."""
    unit = SyntacticUnit(text=text, translation=translation, word_count=1, difficulty=1, source="corpus")
    db.add_collocation(unit)
    return db.get_collocation(text).guid


def _add_item_with_anki_ids(db: SRSDatabase, text: str, translation: str, note_id: int = 9001) -> str:
    """Add a collocation WITH an Anki note_id already set."""
    guid = _add_item(db, text, translation)
    db.set_anki_ids(guid, note_id, {Direction.RECOGNITION: note_id * 10})
    return guid


def _add_cloze_item(db: SRSDatabase, text: str, sentence: str) -> str:
    """Add a cloze collocation with no Anki IDs. Returns guid."""
    unit = SyntacticUnit(
        text=text,
        translation="",
        word_count=1,
        difficulty=1,
        source="cloze",
        lemma=text.casefold(),
        source_sentence=sentence,
        card_type="cloze",
    )
    db.add_collocation(unit)
    return db.get_collocation(text).guid


class FakeReader:
    def get_note_records(self):
        return []

    def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
        return []


class FakeCreateWriter:
    """Tracks calls for sync_create_new assertions."""

    def __init__(
        self,
        new_note_id: int = 5001,
        cards_by_ord: dict[int, int] | None = None,
    ) -> None:
        self.calls: list[tuple] = []
        self._new_note_id = new_note_id
        self._cards_by_ord = cards_by_ord if cards_by_ord is not None else {0: 50010, 1: 50011}

    def create_note(self, deck_name: str, model_name: str, fields: dict, tags: list) -> int:
        self.calls.append(("create_note", deck_name, model_name, dict(fields), list(tags)))
        return self._new_note_id

    def store_media_file(self, filename: str, data: bytes) -> None:
        self.calls.append(("store_media_file", filename, len(data)))

    def get_cards_for_note(self, note_id: int) -> dict[int, int]:
        self.calls.append(("get_cards_for_note", note_id))
        return self._cards_by_ord

    # Stubs for the push path (not used in create_new tests)
    def update_note_fields(self, note_id, fields):
        pass

    def suspend(self, card_ids):
        pass

    def unsuspend(self, card_ids):
        pass

    def set_due_date(self, card_ids, days):
        pass

    def write_revlog(self, **kw):
        pass

    def set_specific_value_of_card(self, card_id, keys, new_values):
        pass

    def action_names(self) -> list[str]:
        return [c[0] for c in self.calls]


async def _no_media(word: str, english: str, *, used_image_urls: set[str]) -> MediaResult | None:
    return None


async def _forvo_media(word: str, english: str, *, used_image_urls: set[str]) -> MediaResult | None:
    return MediaResult(audio_bytes=b"mp3_data", audio_source="forvo")


async def _tts_media(word: str, english: str, *, used_image_urls: set[str]) -> MediaResult | None:
    return MediaResult(
        audio_bytes=b"tts_data",
        audio_source="tts",
        image_bytes=b"img_data",
        image_ext="jpg",
    )


async def _full_media(word: str, english: str, *, used_image_urls: set[str]) -> MediaResult | None:
    url = f"https://cdn.pixabay.com/{english}.jpg"
    used_image_urls.add(url)
    return MediaResult(
        audio_bytes=b"mp3_data",
        audio_source="forvo",
        image_bytes=b"img_data",
        image_ext="jpg",
        image_url=url,
    )


class FlexTransport(httpx.BaseTransport):
    """Returns per-action results."""

    def __init__(self, results: dict) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._results = results

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        action = body["action"]
        params = body.get("params", {})
        self.calls.append((action, params))
        result = self._results.get(action, None)
        return httpx.Response(200, json={"result": result, "error": None})


def _flex_client(results: dict) -> tuple[AnkiConnectClient, FlexTransport]:
    transport = FlexTransport(results)
    client = AnkiConnectClient(http_client=httpx.Client(transport=transport))
    return client, transport


def _make_cloze_collection_conn():
    """Build minimal in-memory collection.anki2 with the Cloze notetype pre-seeded."""
    import sqlite3

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
    conn.execute("INSERT INTO decks VALUES (12345, '0. Slovene', 0, 0, x'')")
    conn.execute("INSERT INTO notetypes VALUES (1000001, 'Cloze', 0, 0, x'')")
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?, ?, x'')",
        [(1000001, i, name) for i, name in enumerate(["Text", "Back Extra"])],
    )
    conn.executemany(
        "INSERT INTO templates VALUES (?, ?, ?, 0, 0, x'')",
        [(1000001, 0, "Cloze")],
    )
    conn.commit()
    return conn


# ── TestClozeNote ─────────────────────────────────────────────────────────────


class TestClozeNote:
    def test_create_cloze_note_inserts_cloze_note_with_single_card(self):
        """create_cloze_note inserts note with correct notetype, usn=-1, single card."""
        anki_conn = _make_cloze_collection_conn()
        writer = OfflineWriter(anki_conn)
        cloze_text = "knjiga, {{c1::ki}} je tam"

        note_id = writer.create_cloze_note(
            deck_name="0. Slovene",
            cloze_text=cloze_text,
            tags=["tunatale", "cloze"],
        )

        note = anki_conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        assert note is not None
        assert note["mid"] == 1000001  # Cloze notetype
        assert note["usn"] == -1
        assert note["mod"] > 0
        assert "tunatale" in note["tags"]
        assert "cloze" in note["tags"]

        cards = anki_conn.execute("SELECT * FROM cards WHERE nid = ?", (note_id,)).fetchall()
        assert len(cards) == 1
        card = cards[0]
        assert card["type"] == 0
        assert card["queue"] == 0
        assert card["ord"] == 0
        assert card["usn"] == -1

    def test_create_cloze_note_raises_if_cloze_notetype_missing(self):
        """Missing Cloze notetype raises ValueError."""
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT);
            CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT UNIQUE, mid INTEGER, mod INTEGER, usn INTEGER, tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT);
            CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT);
            CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER, lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER);
            CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
            CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB, PRIMARY KEY (ntid, ord));
            CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord));
            CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB);
        """)
        conn.execute("INSERT INTO col VALUES (1, 1704067200, 0, 1000, 18, 0, 0, 0, '{}', '{}', '{}', '{}', '{}')")
        conn.execute("INSERT INTO decks VALUES (12345, '0. Slovene', 0, 0, x'')")
        conn.commit()

        writer = OfflineWriter(conn)
        with pytest.raises(ValueError, match="Cloze notetype not found"):
            writer.create_cloze_note(deck_name="0. Slovene", cloze_text="test")

    def test_create_cloze_note_raises_if_deck_missing(self):
        """Missing deck name raises ValueError."""
        anki_conn = _make_cloze_collection_conn()
        writer = OfflineWriter(anki_conn)
        with pytest.raises(ValueError, match="not found"):
            writer.create_cloze_note(deck_name="Nonexistent Deck", cloze_text="test")

    def test_create_cloze_note_duplicate_guid_raises(self):
        """Same cloze_text called twice raises DuplicateNoteError."""
        anki_conn = _make_cloze_collection_conn()
        writer = OfflineWriter(anki_conn)
        cloze_text = "knjiga, {{c1::ki}} je tam"

        writer.create_cloze_note(deck_name="0. Slovene", cloze_text=cloze_text)
        with pytest.raises(DuplicateNoteError):
            writer.create_cloze_note(deck_name="0. Slovene", cloze_text=cloze_text)

    def test_create_cloze_note_creates_card_with_max_due_plus_one(self):
        """Cloze card gets MAX(due)+1 allocator same as create_note."""
        anki_conn = _make_cloze_collection_conn()
        # Pre-populate with an existing card at due=5
        existing_id = 9001
        guid = "aabbccdd00112233"
        anki_conn.execute(
            "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
            "VALUES (?, ?, 1000001, 0, 0, '', 'existing', 'existing', 0, 0, '')",
            (existing_id, guid),
        )
        anki_conn.execute(
            "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, left, odue, odid, flags, data) "
            "VALUES (?, 9001, 12345, 0, 0, 0, 0, 0, 5, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (existing_id * 10,),
        )
        anki_conn.commit()

        writer = OfflineWriter(anki_conn)
        note_id = writer.create_cloze_note(
            deck_name="0. Slovene",
            cloze_text="knjiga, {{c1::je}} tam",
        )
        due = anki_conn.execute("SELECT due FROM cards WHERE nid = ?", (note_id,)).fetchone()
        assert due is not None
        assert due["due"] >= 6  # MAX(5) + 1 = 6


def _make_dual_collection_conn():
    """In-memory collection.anki2 with both Slovene Vocabulary and Cloze notetypes."""
    import sqlite3

    from app.anki.notetype import SLOVENE_VOCAB_FIELD_NAMES, SLOVENE_VOCAB_NOTETYPE_NAME

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
    conn.execute("INSERT INTO decks VALUES (12345, '0. Slovene', 0, 0, x'')")
    # Slovene Vocabulary notetype
    conn.execute(
        "INSERT INTO notetypes VALUES (1000001, ?, 0, 0, x'')",
        (SLOVENE_VOCAB_NOTETYPE_NAME,),
    )
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?, ?, x'')",
        [(1000001, i, name) for i, name in enumerate(SLOVENE_VOCAB_FIELD_NAMES)],
    )
    conn.executemany(
        "INSERT INTO templates VALUES (?, ?, ?, 0, 0, x'')",
        [(1000001, 0, "Recognition"), (1000001, 1, "Production")],
    )
    # Cloze notetype
    conn.execute("INSERT INTO notetypes VALUES (1000002, 'Cloze', 0, 0, x'')")
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?, ?, x'')",
        [(1000002, i, name) for i, name in enumerate(["Text", "Back Extra"])],
    )
    conn.executemany(
        "INSERT INTO templates VALUES (?, ?, ?, 0, 0, x'')",
        [(1000002, 0, "Cloze")],
    )
    conn.commit()
    return conn


# ── TestSyncCreateNewRouting ──────────────────────────────────────────────────


class TestSyncCreateNewRouting:
    async def test_sync_create_new_routes_cloze_items_to_create_cloze_note(self):
        """Cloze items create Anki notes with Cloze notetype and {{c1::word}} text."""
        db = _make_db()
        _add_cloze_item(db, "ki", "knjiga, ki je tam")

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        notes = anki_conn.execute("SELECT n.id, n.mid, n.flds, n.tags FROM notes n").fetchall()
        assert len(notes) == 1
        note = notes[0]
        assert note["mid"] == 1000002  # Cloze notetype
        assert "tunatale" in note["tags"]
        assert "cloze" in note["tags"]
        flds = note["flds"].split("\x1f")
        assert flds[0] == "knjiga, {{c1::ki}} je tam"

        # Cloze direction maps to PRODUCTION (not RECOGNITION)
        guid = db.get_collocation("ki").guid
        item = db.get_collocation_by_guid(guid)
        assert Direction.PRODUCTION in item.directions
        assert Direction.RECOGNITION not in item.directions

    async def test_sync_create_new_cloze_includes_sentence_translation_in_back_extra(self):
        """Cloze notes with sentence_translation get <span class='st'> in back_extra."""
        db = _make_db()
        unit = SyntacticUnit(
            text="vsak",
            translation="every",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="vsak",
            source_sentence="Odprto je vsak dan",
            source_sentence_translation="It is open every day",
            card_type="cloze",
        )
        db.add_collocation(unit)
        item = db.get_collocation("vsak")
        assert item is not None

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        notes = anki_conn.execute("SELECT n.flds FROM notes n").fetchall()
        assert len(notes) == 1
        flds = notes[0][0].split("\x1f")
        assert "<i>every</i>" in flds[1]
        assert '<span class="st">It is open every day</span>' in flds[1]

    async def test_sync_create_new_cloze_with_audio_writes_sound_tag_and_copies_mp3(self, tmp_path):
        """Cloze with sentence audio: back_extra has [sound:...] and media file is stored."""
        db = _make_db()
        unit = SyntacticUnit(
            text="še",
            translation="yet",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="še",
            source_sentence="Ja, še nisem videl.",
            card_type="cloze",
        )
        db.add_collocation(unit)
        item = db.get_collocation_by_lemma("še")
        assert item is not None
        coll_id = db.get_collocation_by_lemma_with_id("še")[0]
        # Seed audio media row + file
        (tmp_path / "tts_sentence_abc.mp3").write_bytes(b"fake-mp3")
        db.add_media(
            collocation_id=coll_id,
            kind="audio_tts_sentence",
            filename="tts_sentence_abc.mp3",
            path=str(tmp_path / "tts_sentence_abc.mp3"),
            anki_filename="",
            sha256="abc",
            size_bytes=8,
        )

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn, media_dir=tmp_path)
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        notes = anki_conn.execute("SELECT n.flds FROM notes n").fetchall()
        assert len(notes) == 1
        flds = notes[0][0].split("\x1f")
        assert "[sound:tts_sentence_abc.mp3]" in flds[1]

    async def test_sync_create_new_routes_vocab_items_to_create_note(self):
        """Vocab items go through existing create_note path."""
        db = _make_db()
        _add_item(db, "voda", "water")

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        notes = anki_conn.execute("SELECT n.id, n.mid, n.flds FROM notes n").fetchall()
        assert len(notes) == 1
        note = notes[0]
        assert note["mid"] == 1000001  # Slovene Vocabulary notetype
        flds = note["flds"].split("\x1f")
        assert flds[0] == "voda"

    async def test_sync_create_new_mixed_batch(self):
        """One vocab + one cloze in the same batch: both land correctly."""
        db = _make_db()
        vocab_guid = _add_item(db, "voda", "water")
        cloze_guid = _add_cloze_item(db, "ki", "knjiga, ki je tam")

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        notes = anki_conn.execute("SELECT n.id, n.mid, n.guid, n.tags FROM notes n ORDER BY n.id").fetchall()
        assert len(notes) == 2

        # Cloze note
        cloze_notes = [n for n in notes if "cloze" in n["tags"]]
        assert len(cloze_notes) == 1
        assert cloze_notes[0]["mid"] == 1000002

        # Vocab note
        vocab_notes = [n for n in notes if "cloze" not in n["tags"]]
        assert len(vocab_notes) == 1
        assert vocab_notes[0]["mid"] == 1000001

        # Both items have anki_note_id set
        assert db.get_collocation_by_guid(cloze_guid).anki_note_id is not None
        assert db.get_collocation_by_guid(vocab_guid).anki_note_id is not None

    async def test_sync_create_new_uses_slovene_voc_for_source_llm(self):
        """LingQ /listen pushes TT rows with source='llm'; sync_create_new must
        create them as Slovene Vocabulary notes (not Basic) with 2 cards (Recognition + Production)."""
        db = _make_db()
        unit = SyntacticUnit(text="nič", translation="nothing", word_count=1, difficulty=1, source="llm")
        db.add_collocation(unit)
        guid = db.get_collocation("nič").guid

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        notes = anki_conn.execute("SELECT id, mid, flds FROM notes").fetchall()
        assert len(notes) == 1
        assert notes[0]["mid"] == 1000001  # Slovene Vocabulary notetype
        # Both Recognition + Production cards were created
        cards = anki_conn.execute("SELECT ord FROM cards WHERE nid = ? ORDER BY ord", (notes[0]["id"],)).fetchall()
        assert [c["ord"] for c in cards] == [0, 1]
        # TT collocation has both directions populated with the new Anki card_ids
        item = db.get_collocation_by_guid(guid)
        rec = item.directions[Direction.RECOGNITION]
        prod = item.directions[Direction.PRODUCTION]
        assert rec.anki_card_id is not None
        assert prod.anki_card_id is not None
        assert rec.anki_card_id != prod.anki_card_id

    async def test_sync_create_new_vocab_duplicate_guid_links_not_creates(self):
        """If an Anki note with the matching guid already exists, sync_create_new
        catches DuplicateNoteError and links the TT row to the existing note rather
        than creating a duplicate. This is the spec the buggy LingQ importer violated.
        """
        db = _make_db()
        guid = _add_item(db, "trgovina", "shop")

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        # Clear anki_note_id to simulate "the same word is being re-added".
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET anki_note_id = NULL WHERE guid = ?", (guid,))
            db._commit(conn)

        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        # Second pass: linked (1), not created (0). No duplicate note in Anki.
        assert report.created == 0
        assert report.linked == 1
        note_count = anki_conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        assert note_count == 1

    async def test_sync_create_new_cloze_duplicate_guid(self):
        """Cloze item whose text already exists as an Anki note uses DuplicateNoteError path."""
        db = _make_db()
        cloze_guid = _add_cloze_item(db, "ki", "knjiga, ki je tam")

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)

        # First sync creates the cloze note
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        # Clear anki_note_id so the item appears unsynced again
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET anki_note_id = NULL WHERE guid = ?", (cloze_guid,))
            db._commit(conn)

        # Second sync hits DuplicateNoteError → linked (+1), not created
        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        assert report.created == 0
        assert report.linked == 1

        # anki_note_id is still set from the linked path
        item = db.get_collocation_by_guid(cloze_guid)
        assert item.anki_note_id is not None
        # Cloze still has only PRODUCTION direction
        assert Direction.PRODUCTION in item.directions
        assert Direction.RECOGNITION not in item.directions

    async def test_sync_create_new_case_cloze_with_grammar_hint(self):
        """Morphology cloze writes plain {{c1::surface}} to Anki Text field, grammar hint to Back Extra."""
        db = _make_db()
        from app.srs.function_words import format_morphology_hint, make_morphology_cloze_text

        morph_cloze_sentence = make_morphology_cloze_text(
            "Ljubljano",
            "ljubljana",
            "noun:acc:sg",
            "Grem v Ljubljano s prijateljem.",
        )
        grammar_hint = format_morphology_hint("ljubljana", "noun:acc:sg")
        unit = SyntacticUnit(
            text="Ljubljano",
            translation="Ljubljana",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="ljubljana",
            disambig_key="morph:noun-acc-sg",
            card_type="cloze",
            source_sentence=morph_cloze_sentence,
            source_sentence_translation="I'm going to Ljubljana with a friend.",
            grammar=grammar_hint,
        )
        db.add_collocation(unit)

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        notes = anki_conn.execute("SELECT n.id, n.mid, n.flds, n.tags, n.guid FROM notes n").fetchall()
        assert len(notes) == 1
        note = notes[0]
        assert note["mid"] == 1000002  # Cloze notetype
        flds = note["flds"].split("\x1f")
        assert flds[0] == "Grem v Ljubljan{{c1::o}} s prijateljem."

        # Back Extra contains translation, sentence translation, and grammar hint
        assert "Ljubljana" in flds[1]
        assert "I'm going to Ljubljana with a friend." in flds[1]
        assert "ljubljana, accusative singular" in flds[1]
        assert 'class="grammar"' in flds[1]

        # Verify GUID stability — re-sync with cleared anki_note_id should link, not duplicate
        guid = db.get_collocation("Ljubljano").guid
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET anki_note_id = NULL WHERE guid = ?", (guid,))
            db._commit(conn)

        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        assert report.created == 0
        assert report.linked == 1


# ── TestListItemsWithoutAnkiNote ──────────────────────────────────────────────


class TestListItemsWithoutAnkiNote:
    def test_returns_item_without_anki_note(self):
        db = _make_db()
        guid = _add_item(db, "voda", "water")
        rows = db.list_items_without_anki_note()
        assert len(rows) == 1
        assert rows[0][0] == guid
        assert rows[0][1].syntactic_unit.text == "voda"

    def test_excludes_item_with_anki_note(self):
        db = _make_db()
        _add_item_with_anki_ids(db, "voda", "water")
        assert db.list_items_without_anki_note() == []

    def test_returns_empty_when_db_empty(self):
        db = _make_db()
        assert db.list_items_without_anki_note() == []

    def test_returns_only_items_without_note(self):
        db = _make_db()
        _add_item_with_anki_ids(db, "voda", "water")
        guid2 = _add_item(db, "miza", "table")
        rows = db.list_items_without_anki_note()
        assert len(rows) == 1
        assert rows[0][0] == guid2


# ── TestSafeStem ──────────────────────────────────────────────────────────────


class TestSafeStem:
    def test_basic_ascii(self):
        assert _safe_stem("voda", "sl") == "sl_voda"

    def test_spaces_become_underscores(self):
        assert _safe_stem("letni čas", "sl") == "sl_letni_čas"

    def test_strips_special_chars(self):
        assert _safe_stem("hello!", "tts") == "tts_hello"

    def test_prefix_applied(self):
        assert _safe_stem("table", "img").startswith("img_")


# ── TestSyncCreateNew ─────────────────────────────────────────────────────────


class TestSyncCreateNew:
    async def test_creates_note_for_item_without_anki_id(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        assert report.count == 1
        assert "create_note" in writer.action_names()

    async def test_skips_item_with_existing_anki_id(self):
        db = _make_db()
        _add_item_with_anki_ids(db, "voda", "water")
        writer = FakeCreateWriter()
        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        assert report.count == 0
        assert "create_note" not in writer.action_names()

    async def test_returns_zero_when_no_new_items(self):
        db = _make_db()
        writer = FakeCreateWriter()
        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        assert report.count == 0

    async def test_dry_run_counts_but_does_not_write(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", dry_run=True
        )
        assert report.count == 1
        assert "create_note" not in writer.action_names()
        # DB not updated
        assert db.list_items_without_anki_note()[0][0] is not None

    async def test_no_media_fn_creates_note_with_empty_media_fields(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=None
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        fields = call[3]
        assert fields["Audio"] == ""
        assert fields["Image"] == ""

    async def test_forvo_audio_uses_sl_prefix(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=_forvo_media
        )
        stored = [c for c in writer.calls if c[0] == "store_media_file"]
        assert len(stored) == 1
        assert stored[0][1].startswith("sl_")

    async def test_tts_audio_uses_tts_prefix(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=_tts_media
        )
        stored = [c for c in writer.calls if c[0] == "store_media_file"]
        assert len(stored) == 2  # audio + image
        audio_files = [s for s in stored if s[1].startswith("tts_")]
        assert len(audio_files) == 1
        assert audio_files[0][1] == "tts_voda.mp3"

    async def test_audio_field_contains_sound_tag(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=_forvo_media
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        assert "[sound:" in call[3]["Audio"]

    async def test_image_field_contains_img_tag(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=_tts_media
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        assert '<img src="' in call[3]["Image"]

    async def test_source_sentence_written_to_note_field(self):
        """Item with source_sentence should have it in the Note field."""
        db = _make_db()
        # Add item with source context
        unit = SyntacticUnit(
            text="kako si",
            translation="how are you",
            word_count=2,
            difficulty=1,
            source="user",
            source_sentence="Kako si? Jaz sem dobro.",
        )
        db.add_collocation(unit)
        _ = db.get_collocation("kako si").guid  # Ensure item is created

        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        fields = call[3]
        assert fields["Note"] == "Kako si? Jaz sem dobro."

    async def test_empty_source_sentence_gives_empty_note_field(self):
        """Item without source_sentence should have empty Note field."""
        db = _make_db()
        _add_item(db, "voda", "water")  # No source_sentence
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        fields = call[3]
        assert fields["Note"] == ""

    async def test_image_stored_with_img_prefix(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=_full_media
        )
        stored = [c for c in writer.calls if c[0] == "store_media_file"]
        filenames = [c[1] for c in stored]
        assert any(f.startswith("img_") for f in filenames)

    async def test_media_fn_returning_none_stores_no_media(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", _media_fn=_no_media
        )
        assert "store_media_file" not in writer.action_names()

    async def test_updates_db_with_note_id(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter(new_note_id=5001)
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        item = db.get_collocation("voda")
        assert item.anki_note_id == 5001

    async def test_updates_db_with_card_ids(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter(new_note_id=5001, cards_by_ord={0: 50010, 1: 50011})
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        item = db.get_collocation("voda")
        rec = item.directions.get(Direction.RECOGNITION)
        prod = item.directions.get(Direction.PRODUCTION)
        assert rec is not None and rec.anki_card_id == 50010
        assert prod is not None and prod.anki_card_id == 50011

    async def test_handles_note_with_only_one_card(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter(new_note_id=5001, cards_by_ord={0: 50010})
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        item = db.get_collocation("voda")
        assert item.anki_note_id == 5001
        assert item.directions[Direction.RECOGNITION].anki_card_id == 50010

    async def test_handles_note_with_no_cards(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter(new_note_id=5001, cards_by_ord={})
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        # note_id still stored even if no cards
        item = db.get_collocation("voda")
        assert item.anki_note_id == 5001

    async def test_creates_multiple_notes(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        _add_item(db, "miza", "table")
        writer = FakeCreateWriter()
        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        assert report.count == 2
        assert len([c for c in writer.calls if c[0] == "create_note"]) == 2

    async def test_deduplicates_images_via_used_image_urls(self):
        """used_image_urls accumulates across items so second item sees first URL."""
        db = _make_db()
        _add_item(db, "voda", "water")
        _add_item(db, "miza", "table")

        received_used_urls: list[frozenset] = []

        async def tracking_media(word, english, *, used_image_urls):
            received_used_urls.append(frozenset(used_image_urls))
            url = f"https://cdn.pixabay.com/{english}.jpg"
            used_image_urls.add(url)
            return MediaResult(audio_bytes=b"x", audio_source="forvo")

        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
            _media_fn=tracking_media,
        )
        # First item saw empty set; second item saw first URL
        assert received_used_urls[0] == frozenset()
        assert len(received_used_urls[1]) == 1

    async def test_note_fields_include_slovene_and_english(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        fields = call[3]
        assert fields["Slovene"] == "voda"
        assert fields["English"] == "water"

    async def test_note_has_tunatale_tag(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        call = next(c for c in writer.calls if c[0] == "create_note")
        assert "tunatale" in call[4]  # tags

    async def test_dry_run_does_not_update_db(self):
        db = _make_db()
        _add_item(db, "voda", "water")
        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary", dry_run=True
        )
        item = db.get_collocation("voda")
        assert item.anki_note_id is None

    async def test_duplicate_note_error_links_offline(self):
        """DuplicateNoteError from OfflineWriter links without calling find_notes."""
        from app.anki.sync import DuplicateNoteError

        db = _make_db()
        _add_item(db, "voda", "water")

        class OfflineDupWriter(FakeCreateWriter):
            def create_note(self, deck, model, fields, tags):
                raise DuplicateNoteError(note_id=8888)

        writer = OfflineDupWriter()
        report = await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        assert report.linked == 1
        assert report.created == 0
        assert db.get_collocation("voda").anki_note_id == 8888
        # find_notes must NOT be called (offline path knows the ID from the exception)
        assert not any(c[0] == "find_notes" for c in writer.calls)

    async def test_creates_notes_with_higher_due_for_newer_items(self):
        """sync_create_new with real OfflineWriter: newer items get higher cards.due."""
        from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME
        from app.anki.sync import OfflineWriter

        db = _make_db()
        guid_old = _add_item(db, "staro", "old")
        guid_new = _add_item(db, "novo", "new")
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET created_at = '2026-01-01 00:00:00' WHERE guid = ?", (guid_old,))
            conn.execute("UPDATE collocations SET created_at = '2026-06-01 00:00:00' WHERE guid = ?", (guid_new,))
            conn.commit()

        anki_conn = _make_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name=SLOVENE_VOCAB_NOTETYPE_NAME
        )
        rows = anki_conn.execute(
            "SELECT n.guid, c.due FROM notes n JOIN cards c ON c.nid = n.id WHERE c.type = 0 ORDER BY c.due ASC"
        ).fetchall()
        assert len(rows) == 4  # 2 notes × 2 cards each (rec + prod)
        guids = [r["guid"] for r in rows]
        # Oldest TT item (staro) should have lowest cards.due → appears first
        assert guids[0] == guid_old
        assert guids[2] == guid_new  # newer TT item starts at position 3 (after both staro cards)

    async def test_preserves_existing_anki_due(self):
        """sync_create_new doesn't touch existing cards' due values."""
        from app.anki.notetype import SLOVENE_VOCAB_NOTETYPE_NAME
        from app.anki.sync import OfflineWriter

        db = _make_db()
        _add_item_with_anki_ids(db, "obstojeca", "existing", note_id=9999)
        guid_new = _add_item(db, "nova", "new")

        anki_conn = _make_collection_conn()
        writer = OfflineWriter(anki_conn)
        # Pre-populate with existing cards at due=1,2,3
        anki_conn.execute(
            "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
            "VALUES (9999, 'existing', 1000001, 0, 0, '', 'existing', 'existing', 0, 0, '')"
        )
        for due_val in (1, 2, 3):
            anki_conn.execute(
                "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, left, odue, odid, flags, data) "
                "VALUES (?, 9999, 12345, ?, 0, 0, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
                (9000 + due_val, due_val - 1, due_val),
            )
        anki_conn.commit()

        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name=SLOVENE_VOCAB_NOTETYPE_NAME
        )
        unchanged = anki_conn.execute("SELECT due FROM cards WHERE id IN (9001, 9002, 9003) ORDER BY id").fetchall()
        assert [r["due"] for r in unchanged] == [1, 2, 3]

        new_due = anki_conn.execute(
            "SELECT c.due FROM cards c JOIN notes n ON c.nid = n.id WHERE n.guid = ? LIMIT 1",
            (guid_new,),
        ).fetchone()
        assert new_due is not None
        assert new_due["due"] >= 4  # MAX(existing due) + 1 = 3 + 1 = 4

    async def test_sorts_by_created_at_asc_before_creating_notes(self):
        """sync_create_new sorts oldest-first so MAX(due)+1 gives newer items higher due."""
        db = _make_db()
        guid_new = _add_item(db, "new_word", "new")
        guid_old = _add_item(db, "old_word", "old")
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET created_at = '2026-01-01 00:00:00' WHERE guid = ?", (guid_old,))
            conn.execute("UPDATE collocations SET created_at = '2026-06-01 00:00:00' WHERE guid = ?", (guid_new,))
            conn.commit()

        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        create_calls = [c for c in writer.calls if c[0] == "create_note"]
        assert len(create_calls) == 2
        assert create_calls[0][3]["Slovene"] == "old_word"
        assert create_calls[1][3]["Slovene"] == "new_word"

    async def test_same_second_created_at_does_not_crash(self):
        """Multiple items with identical created_at produce no crash; all 3 created."""
        db = _make_db()
        _add_item(db, "word_a", "a")
        _add_item(db, "word_b", "b")
        _add_item(db, "word_c", "c")

        writer = FakeCreateWriter()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        create_calls = [c for c in writer.calls if c[0] == "create_note"]
        assert len(create_calls) == 3


# ── Reverse-import tests (Layer 22: Anki→TT) ──────────────────────────────────


class _ReverseFakeReader:
    """Fake OfflineReader.get_note_records that returns pre-built NoteRecords."""

    def __init__(self, records: list[NoteRecord]) -> None:
        self._records = records

    def get_note_records(self) -> list[NoteRecord]:
        return self._records


class _ReverseFakeWriter:
    """Writer stub for sync_create_new reverse-import tests."""

    def create_note(self, deck_name, model_name, fields, tags):
        return 5001

    def create_cloze_note(self, deck_name, cloze_text, back_extra, tags):
        return 5001

    def get_cards_for_note(self, note_id):
        return {0: 50010, 1: 50011}

    def store_media_file(self, fn, data):
        pass

    def update_note_fields(self, *a):
        pass

    def suspend(self, *a):
        pass

    def unsuspend(self, *a):
        pass

    def set_due_date(self, *a):
        pass

    def write_revlog(self, **kw):
        pass

    def set_specific_value_of_card(self, *a):
        pass

    def find_notes(self, q):
        return []


class TestReverseImportLayer22:
    """sync_create_new reverse-import pass mints TT rows from Anki-only notes."""

    async def test_reverse_imports_anki_only_notes(self):
        """An Anki-only note with no TT row gets imported as a new collocation."""
        db = _make_db()
        reader = _ReverseFakeReader(
            [
                NoteRecord(
                    anki_note_id=1001,
                    anki_guid="test-guid-1",
                    l2_text="voda",
                    translation="water",
                    note="",
                    disambig_key="",
                    mod=0,
                    cards=[
                        CardRecord(
                            anki_card_id=90010,
                            ord=0,
                            queue=2,
                            reps=3,
                            lapses=0,
                            stability=5.0,
                            difficulty=4.0,
                            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                            anki_due=10,
                            anki_card_mod=100,
                        ),
                    ],
                ),
            ]
        )
        writer = _ReverseFakeWriter()
        report = await AnkiSync(db=db, _reader=reader, _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        assert report.notes_created_from_anki == 1
        item = db.get_collocation("voda")
        assert item is not None
        assert item.anki_note_id == 1001

        rec_dir = item.directions[Direction.RECOGNITION]
        assert rec_dir.state == SRSState.REVIEW
        assert rec_dir.stability == 5.0
        assert rec_dir.reps == 3

    async def test_reverse_import_handles_queue_2_reps_0(self):
        """Anki card with (queue=2, reps=0) reverse-imports as REVIEW."""
        db = _make_db()
        reader = _ReverseFakeReader(
            [
                NoteRecord(
                    anki_note_id=1002,
                    anki_guid="test-guid-2",
                    l2_text="banka",
                    translation="bank",
                    note="",
                    disambig_key="",
                    mod=0,
                    cards=[
                        CardRecord(
                            anki_card_id=90020,
                            ord=0,
                            queue=2,
                            reps=0,
                            lapses=0,
                            stability=0.0,
                            difficulty=0.0,
                            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                            anki_due=10,
                            anki_card_mod=0,
                        ),
                    ],
                ),
            ]
        )
        writer = _ReverseFakeWriter()
        await AnkiSync(db=db, _reader=reader, _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        item = db.get_collocation("banka")
        assert item is not None
        rec_dir = item.directions[Direction.RECOGNITION]
        assert rec_dir.state == SRSState.REVIEW

    async def test_reverse_import_skips_already_linked(self):
        """An Anki note already linked in TT is not re-imported."""
        db = _make_db()
        guid = _add_item_with_anki_ids(db, "voda", "water", note_id=1001)

        reader = _ReverseFakeReader(
            [
                NoteRecord(
                    anki_note_id=1001,
                    anki_guid=guid,
                    l2_text="voda",
                    translation="water",
                    note="",
                    disambig_key="",
                    mod=0,
                    cards=[
                        CardRecord(
                            anki_card_id=90010,
                            ord=0,
                            queue=2,
                            reps=3,
                            lapses=0,
                            stability=5.0,
                            difficulty=4.0,
                            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                            anki_due=10,
                        ),
                    ],
                ),
            ]
        )
        writer = _ReverseFakeWriter()
        report = await AnkiSync(db=db, _reader=reader, _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        assert report.notes_created_from_anki == 0

    async def test_reverse_import_handles_cloze(self):
        """A cloze Anki note reverse-imports as a single PRODUCTION direction."""
        db = _make_db()
        reader = _ReverseFakeReader(
            [
                NoteRecord(
                    anki_note_id=1003,
                    anki_guid="cloze-guid-1",
                    l2_text="ki",
                    translation="which",
                    note="knjiga, ki je tam",
                    disambig_key="",
                    mod=0,
                    cards=[
                        CardRecord(
                            anki_card_id=90030,
                            ord=0,
                            queue=0,
                            reps=0,
                            lapses=0,
                            stability=1.0,
                            difficulty=5.0,
                            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                            anki_due=0,
                        ),
                    ],
                    is_cloze=True,
                ),
            ]
        )
        writer = _ReverseFakeWriter()
        report = await AnkiSync(db=db, _reader=reader, _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        assert report.notes_created_from_anki == 1
        item = db.get_collocation("ki")
        assert item is not None
        assert item.anki_note_id == 1003
        assert item.syntactic_unit.card_type == "cloze"
        assert Direction.PRODUCTION in item.directions
        assert Direction.RECOGNITION not in item.directions

    async def test_reverse_import_multi_cloze_picks_first_card(self):
        """Multi-deletion cloze (c1/c2/c3) uses only the first card (ord=0)."""
        db = _make_db()
        reader = _ReverseFakeReader(
            [
                NoteRecord(
                    anki_note_id=1005,
                    anki_guid="multi-cloze-guid",
                    l2_text="ki",
                    translation="which",
                    note="knjiga, {{c1::ki}} je {{c2::tam}}",
                    disambig_key="",
                    mod=0,
                    is_cloze=True,
                    cards=[
                        CardRecord(
                            anki_card_id=90050,
                            ord=0,
                            queue=2,
                            reps=5,
                            lapses=0,
                            stability=5.0,
                            difficulty=4.0,
                            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                            anki_due=10,
                            anki_card_mod=100,
                        ),
                        CardRecord(
                            anki_card_id=90051,
                            ord=1,
                            queue=2,
                            reps=3,
                            lapses=1,
                            stability=3.0,
                            difficulty=5.0,
                            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                            anki_due=20,
                            anki_card_mod=101,
                        ),
                        CardRecord(
                            anki_card_id=90052,
                            ord=2,
                            queue=0,
                            reps=0,
                            lapses=0,
                            stability=1.0,
                            difficulty=5.0,
                            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
                            anki_due=0,
                            anki_card_mod=102,
                        ),
                    ],
                ),
            ]
        )
        writer = _ReverseFakeWriter()
        report = await AnkiSync(db=db, _reader=reader, _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        assert report.notes_created_from_anki == 1
        item = db.get_collocation("ki")
        assert item is not None
        directions = item.directions
        assert Direction.PRODUCTION in directions
        assert Direction.RECOGNITION not in directions
        prod = directions[Direction.PRODUCTION]
        assert prod.anki_card_id == 90050
        assert prod.reps == 5
        assert prod.stability == 5.0

    async def test_reverse_import_skips_note_with_no_cards(self):
        """An Anki note with zero cards produces no TT row (directions is empty)."""
        db = _make_db()
        reader = _ReverseFakeReader(
            [
                NoteRecord(
                    anki_note_id=1004,
                    anki_guid="no-cards-guid",
                    l2_text="missing",
                    translation="missing",
                    note="",
                    disambig_key="",
                    mod=0,
                    cards=[],
                ),
            ]
        )
        writer = _ReverseFakeWriter()
        report = await AnkiSync(db=db, _reader=reader, _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        assert report.notes_created_from_anki == 0
        assert db.get_collocation("missing") is None
