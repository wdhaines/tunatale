"""Phase F end-to-end: listen → sync creates cloze note → verify Anki state."""

from __future__ import annotations

import io

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore


class FakeReaderE2E:
    def get_note_records(self):
        return []

    def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
        return []


def _make_dual_collection_conn():
    import sqlite3

    from app.cards.vocab_notetype import SLOVENE_VOCAB

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
        (SLOVENE_VOCAB.name,),
    )
    conn.executemany(
        "INSERT INTO fields VALUES (?, ?, ?, x'')",
        [(1000001, i, name) for i, name in enumerate(list(SLOVENE_VOCAB.field_names))],
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
    """Full round-trip: /listen → sync_create_new → verify Anki state."""

    async def test_listen_then_sync_creates_cloze_and_vocab(self):
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

        # ── 1. Listen ─────────────────────────────────────────────────────
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        assert db.count_collocations() == 3  # 2 cloze + 1 vocab

        kje = db.get_collocation_by_lemma("kje")
        assert kje is not None
        assert kje.syntactic_unit.card_type == "cloze"
        assert kje.syntactic_unit.source_sentence == "{{c1::Kje}} je banka?"

        je = db.get_collocation_by_lemma("je")
        assert je is not None
        assert je.syntactic_unit.card_type == "cloze"
        assert je.syntactic_unit.source_sentence == "Kje {{c1::je}} banka?"

        # banka is a content word → created as vocab
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

        assert report.created == 3  # 2 cloze + 1 vocab
        assert report.skipped == 0
        assert report.linked == 0

        # ── 3. Verify Anki state ──────────────────────────────────────────
        notes = anki_conn.execute("SELECT n.id, n.mid, n.flds, n.sfld, n.tags FROM notes n ORDER BY n.id").fetchall()
        assert len(notes) == 3

        cloze_notes = [n for n in notes if n["mid"] == 1000002]
        vocab_notes = [n for n in notes if n["mid"] == 1000001]
        assert len(cloze_notes) == 2
        assert len(vocab_notes) == 1

        for note in cloze_notes:
            assert "tunatale" in note["tags"]
            flds = note["flds"].split("\x1f")
            assert "{{c1::" in flds[0]
            assert "[sound:tts_sentence_" in flds[1]

        # ── 4. Verify each cloze note has exactly one card ────────────────
        for note in cloze_notes:
            cards = anki_conn.execute("SELECT id, ord, type, queue FROM cards WHERE nid = ?", (note["id"],)).fetchall()
            assert len(cards) == 1
            assert cards[0]["ord"] == 0

    async def test_capped_listen_created_rows_sync_via_sync_create_new(self):
        """Budget-capped creation (plan Step 3) must leave rows in exactly the
        shape ``sync_create_new`` consumes: the capped subset — and only it —
        reaches Anki as real notes, matched by guid. Guards the contract that
        the staged-creation pass keeps state NEW / anki ids None; a regression
        there would make listen-created cards silently never reach Anki."""
        db = SRSDatabase(":memory:")

        store = ContentStore(":memory:")
        # Occurrence counts: banka 3, center 2, hotel 1 — all content words
        # (vocab), so with daily_new_cap=2 the ranked creation pass takes
        # banka + center and leaves hotel as a remaining candidate.
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text=t, voice_id="female-1", language_code="sl", role="female-1")
                        for t in ["banka center hotel", "banka center", "banka"]
                    ],
                )
            ],
            key_phrases=[],
        )
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        app.state.srs_db = db
        app.state.content_store = store
        db.set_anki_state_cache("daily_new_cap", "2")

        # ── 1. Capped listen ──────────────────────────────────────────────
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        data = response.json()
        assert data["created"] == 2
        assert data["remaining_candidates"] == 1
        assert db.count_collocations() == 2

        banka = db.get_collocation_by_lemma("banka")
        center = db.get_collocation_by_lemma("center")
        assert banka is not None and center is not None
        assert db.get_collocation_by_lemma("hotel") is None

        # ── 2. Sync create new ────────────────────────────────────────────
        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)

        report = await AnkiSync(db=db, _reader=FakeReaderE2E(), _writer=writer).sync_create_new(
            deck_name="0. Slovene",
            model_name="Slovene Vocabulary",
        )

        # ── 3. Exactly the capped subset reaches Anki, matched by guid ────
        assert report.created == 2
        assert report.skipped == 0
        assert report.linked == 0
        note_guids = {r["guid"] for r in anki_conn.execute("SELECT guid FROM notes").fetchall()}
        assert note_guids == {banka.guid, center.guid}


class TestImageEndpointToSyncSeam:
    """Sociable seam: the real image-upload endpoint sets exactly the state the
    real sync_push consumes, all the way into a real Anki note's Image field.

    The endpoint (``test_srs_image_endpoints``) and the push
    (``test_anki_sync_push::TestSyncPushImage``) are each green in isolation; the
    contract *between* them is only a ``dirty_fields`` value plus a stored TT
    media row. This drives HTTP upload → ``sync_push`` → ``OfflineWriter``
    end-to-end against a real in-memory collection, so a drift in that contract
    (filename convention, dirty-flag name, media ``kind``) can't hide in the gap
    between two green halves — the b0a4b8a failure shape.
    """

    _JPG = b"\xff\xd8\xff" + b"\x00" * 64  # minimal JPEG magic + padding

    async def test_upload_endpoint_edit_reaches_anki_note_via_push(self, tmp_path, monkeypatch):
        import app.cards.media.vocab_media as vocab_media
        import app.plugins.anki_sync.sync as sync_mod

        # Both _MEDIA_DIR constants resolve to backend/media independently. Point
        # both at one tmp dir so the endpoint's write and the push's read share it
        # (and the real media tree is untouched).
        media_dir = tmp_path / "tt_media"
        media_dir.mkdir()
        monkeypatch.setattr(vocab_media, "_MEDIA_DIR", media_dir)
        monkeypatch.setattr(sync_mod, "_MEDIA_DIR", media_dir)

        # ── TT side: a vocab collocation linked to a pre-existing Anki note ──
        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(text="voda", translation="water", word_count=1, difficulty=1, source="corpus")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("voda")
        assert item is not None
        guid = item.guid
        coll_id = db.get_collocation_id_by_guid(guid)
        note_id, rec_cid, prod_cid = 5001, 50010, 50011
        db.set_anki_ids(guid, note_id, {Direction.RECOGNITION: rec_cid, Direction.PRODUCTION: prod_cid})

        # ── Anki side: real collection holding that linked vocab note, Image empty ──
        anki_conn = _make_dual_collection_conn()
        flds = "\x1f".join(["voda", "water", "", "", "", "", ""])  # 7 vocab fields; Image=ord 3
        anki_conn.execute(
            "INSERT INTO notes VALUES (?, ?, 1000001, 0, 0, '', ?, 'voda', 0, 0, '')",
            (note_id, guid, flds),
        )
        anki_conn.commit()
        anki_media = tmp_path / "collection.media"
        anki_media.mkdir()
        writer = OfflineWriter(anki_conn, media_dir=anki_media)

        # ── 1. Real HTTP upload → stores TT media + stamps dirty_fields="image" ──
        app.state.srs_db = db
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.put(
                f"/api/srs/items/{coll_id}/image/upload",
                files={"file": ("photo.jpg", io.BytesIO(self._JPG), "image/jpeg")},
            )
        assert resp.status_code == 200
        fname = db.get_image_filename(coll_id)
        assert fname is not None and fname.endswith(".jpg")
        assert db.get_dirty_fields(guid) == "image"

        # ── 2. Real sync_push consumes exactly that state ──
        AnkiSync(db=db, _reader=FakeReaderE2E(), _writer=writer).sync_push()

        # ── 3. The Anki note's Image field + media file reflect the upload ──
        row = anki_conn.execute("SELECT flds, usn FROM notes WHERE id = ?", (note_id,)).fetchone()
        assert row["flds"].split("\x1f")[3] == f'<img src="{fname}">'
        assert row["usn"] == -1  # dirty → pushed to AnkiWeb on next sync
        assert (anki_media / fname).read_bytes() == self._JPG  # bytes copied into collection.media
        assert db.get_dirty_fields(guid) == ""  # flag cleared by the push
