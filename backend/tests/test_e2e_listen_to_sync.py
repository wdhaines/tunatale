"""Phase F end-to-end: listen → sync creates cloze note → verify Anki state."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.anki.sync import AnkiSync, OfflineWriter
from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore


class FakeReaderE2E:
    def get_note_records(self):
        return []


def _make_dual_collection_conn():
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


@pytest.fixture(autouse=True)
def _clean_app_state():
    yield
    for attr in ("srs_db", "content_store"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


class TestListenToSyncRoundTrip:
    """Full round-trip: /listen → sync_create_new → verify Anki Cloze note."""

    async def test_listen_then_sync_creates_cloze_note(self):
        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)

        store = ContentStore(":memory:")
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(
                            text="Kje je banka?",
                            voice_id="female-1",
                            language_code="sl",
                            role="female-1",
                        ),
                    ],
                )
            ],
            key_phrases=[],
        )
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        app.state.srs_db = db
        app.state.content_store = store

        # ── 1. Listen ─────────────────────────────────────────────────────
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        assert db.count_collocations() == 3

        kje = db.get_collocation_by_lemma("kje")
        assert kje is not None
        assert kje.syntactic_unit.card_type == "cloze"
        assert kje.syntactic_unit.source_sentence == "Kje je banka?"

        je = db.get_collocation_by_lemma("je")
        assert je is not None
        assert je.syntactic_unit.card_type == "cloze"
        assert je.syntactic_unit.source_sentence == "Kje je banka?"

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        assert banka.syntactic_unit.card_type == "vocab"

        # ── 2. Sync create new ────────────────────────────────────────────
        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)

        report = await AnkiSync(db=db, _reader=FakeReaderE2E(), _writer=writer).sync_create_new(
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
        )

        assert report.created == 3
        assert report.skipped == 0
        assert report.linked == 0

        # ── 3. Verify Anki state ──────────────────────────────────────────
        notes = anki_conn.execute("SELECT n.id, n.mid, n.flds, n.sfld, n.tags FROM notes n ORDER BY n.id").fetchall()
        assert len(notes) == 3

        # Find the cloze notes (they have "cloze" tag)
        cloze_notes = [n for n in notes if "cloze" in n["tags"]]
        assert len(cloze_notes) == 2

        for note in cloze_notes:
            assert note["mid"] == 1000002  # Cloze notetype
            assert "tunatale" in note["tags"]
            flds = note["flds"].split("\x1f")
            assert "{{c1::" in flds[0]
            assert flds[1] == ""  # Back Extra empty

        # Find the vocab note
        vocab_notes = [n for n in notes if "cloze" not in n["tags"]]
        assert len(vocab_notes) == 1
        assert vocab_notes[0]["mid"] == 1000001  # Slovene Vocabulary notetype

        # ── 4. Verify each cloze note has exactly one card ────────────────
        for note in cloze_notes:
            cards = anki_conn.execute("SELECT id, ord, type, queue FROM cards WHERE nid = ?", (note["id"],)).fetchall()
            assert len(cards) == 1
            assert cards[0]["ord"] == 0

    async def test_listen_then_sync_with_cloze_disabled_creates_vocab_only(self):
        """With cloze disabled, all items including function words are vocab."""
        db = SRSDatabase(":memory:")

        store = ContentStore(":memory:")
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(
                            text="Kje je banka?",
                            voice_id="female-1",
                            language_code="sl",
                            role="female-1",
                        ),
                    ],
                )
            ],
            key_phrases=[],
        )
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200

        for lemma in ("kje", "je", "banka"):
            item = db.get_collocation_by_lemma(lemma)
            assert item is not None
            assert item.syntactic_unit.card_type == "vocab"
            assert item.syntactic_unit.source_sentence == ""
