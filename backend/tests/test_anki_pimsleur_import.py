"""Importing the Pimsleur Tagalog deck (tunatale-w4m7.8, design steps 3+4).

The deck's notetype, ``Basic (and reversed card) (genanki)`` (fields Front,
Back, Audio), puts the ENGLISH on Front and the Tagalog on Back, and its Card 1
(ord 0) is ``{{Front}}`` → ``{{Back}}{{Audio}}``: English prompt, Tagalog answer,
i.e. PRODUCTION. Card 2 (ord 1) is the recognition card. TT used to hardcode
``ord == 0 → RECOGNITION`` in three places, which would have landed every
Pimsleur card's FSRS state on the wrong direction.

Both ways a note enters TT are covered, because they are separate code: the
seed importer (``import_seed``) and the reverse-import pass that runs inside
every sync (``sync_create_new``, Layer 22). The fixture gives each note's two
cards DIFFERENT stabilities, so a swap cannot pass.

Field text and shapes are the measured ones (read-only safe_open of the live
mirror, 2026-09-23): 609 notes, all on this notetype; Back always ends in
``<br>`` (the only HTML in the deck, 644 tags); 16 one-word backs carry
punctuation (``Kumusta?``).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.config import settings
from app.models.srs_item import Direction
from app.plugins.anki_sync.sqlite_reader import fetch_cards_for_notes
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import get_lemmatizer

NOTETYPE = "Basic (and reversed card) (genanki)"
ROOT = "2. Pimsleur Tagalog"
LESSON = f"{ROOT}::Level 1::Lesson 01"
MID = 1_600_000_000_001
LESSON_DID = 12

# note id -> (Front, Back); cards are nid*10 (ord 0) and nid*10+1 (ord 1)
NOTES = {
    5001: ("to eat", "kumain<br>\n"),
    5002: ("How are you?", "Kumusta?<br>"),
    5003: ("Have you eaten?", "Kumain ka na ba?<br>"),
}
# ord 0 is PRODUCTION on this notetype; ord 1 is RECOGNITION.
ORD0_STABILITY = 40.0
ORD1_STABILITY = 4.0


def build_pimsleur_anki_db(tmp_path: Path) -> Path:
    """A collection shaped like the live Pimsleur deck: the genanki notetype,
    every card in a LESSON subdeck, the parent empty, two cards per note."""
    db_path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE col (
        id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,
        dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT,
        decks TEXT, dconf TEXT, tags TEXT)""")
    conn.execute("""CREATE TABLE notes (
        id INTEGER, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
        tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT)""")
    conn.execute("""CREATE TABLE cards (
        id INTEGER, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
        usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER,
        factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER,
        odue INTEGER, odid INTEGER, flags INTEGER, data TEXT)""")
    conn.execute("""CREATE TABLE revlog (
        id INTEGER, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,
        lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER)""")
    conn.execute("CREATE TABLE decks (id INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB)")
    conn.execute("CREATE TABLE notetypes (id INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB)")
    conn.execute("CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB)")

    conn.execute("INSERT INTO col VALUES (1,1704067200,0,0,11,0,0,0,'{}','{}','{}','{}','{}')")
    conn.execute("INSERT INTO decks VALUES (10, ?, 0, 0, '{}')", (ROOT,))
    conn.execute("INSERT INTO decks VALUES (?, ?, 0, 0, '{}')", (LESSON_DID, LESSON.replace("::", "\x1f")))
    conn.execute("INSERT INTO notetypes VALUES (?, ?, 0, 0, NULL)", (MID, NOTETYPE))
    for ord_, name in enumerate(["Front", "Back", "Audio"]):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, NULL)", (MID, ord_, name))

    for nid, (front, back) in NOTES.items():
        flds = "\x1f".join([front, back, f"[sound:pimsleur_{nid}.mp3]"])
        conn.execute(
            "INSERT INTO notes VALUES (?, ?, ?, 0, 0, '', ?, ?, 0, 0, '')", (nid, f"pims_{nid}", MID, flds, front)
        )
        for ord_, stability in ((0, ORD0_STABILITY), (1, ORD1_STABILITY)):
            conn.execute(
                "INSERT INTO cards VALUES (?, ?, ?, ?, 0, 0, 2, 2, 10, 21, 2500, 5, 0, 0, 0, 0, 0, ?)",
                (nid * 10 + ord_, nid, LESSON_DID, ord_, json.dumps({"s": stability, "d": 5.0})),
            )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def pimsleur_db(tmp_path):
    return build_pimsleur_anki_db(tmp_path)


@pytest.fixture
def table_lemmatizer(monkeypatch):
    """Production's lemmatizer setting (conftest pins the lowercase off-switch)."""
    monkeypatch.setattr(settings, "lemmatizer_type", "table")
    get_lemmatizer.cache_clear()
    yield
    get_lemmatizer("tl").close()
    get_lemmatizer.cache_clear()


def _import_seed(pimsleur_db: Path, tmp_path: Path, language_code: str = "tl") -> SRSDatabase:
    from app.plugins.anki_sync.import_seed import import_seed

    db_path = str(tmp_path / "tunatale_tl.db")
    import_seed(
        anki_collection_path=pimsleur_db,
        anki_backup_dir=tmp_path / "bak",
        anki_media_path=tmp_path / "fake_media",
        deck_name=ROOT,
        language_code=language_code,
        tunatale_db_path=db_path,
        media_dir=tmp_path / "media",
        fallback_log_path=tmp_path / "fallback.log",
    )
    return SRSDatabase(db_path)


async def _reverse_import(pimsleur_db: Path) -> SRSDatabase:
    from app.plugins.anki_sync.sync import AnkiSync, OfflineReader, OfflineWriter

    db = SRSDatabase(":memory:")
    conn = sqlite3.connect(str(pimsleur_db))
    conn.row_factory = sqlite3.Row
    try:
        report = await AnkiSync(
            db=db,
            _reader=OfflineReader(conn, ROOT, language_code="tl"),
            _writer=OfflineWriter(conn),
            language_code="tl",
        ).sync_create_new(deck_name=f"{ROOT}::TunaTale", model_name="Tagalog Vocabulary")
    finally:
        conn.close()
    assert report.notes_created_from_anki == len(NOTES)
    return db


def _assert_pimsleur_directions(db: SRSDatabase) -> None:
    item = db.get_collocation_by_anki_note_id(5001)
    assert item is not None
    prod = item.directions[Direction.PRODUCTION]
    rec = item.directions[Direction.RECOGNITION]
    # Card 1 (ord 0) is English → Tagalog: production gets ITS memory state.
    assert prod.anki_card_id == 50010
    assert prod.stability == ORD0_STABILITY
    assert rec.anki_card_id == 50011
    assert rec.stability == ORD1_STABILITY


def _assert_pimsleur_text(db: SRSDatabase) -> None:
    item = db.get_collocation_by_anki_note_id(5001)
    assert item is not None
    # The L2 is Back (not Front), with the trailing <br> gone.
    assert item.syntactic_unit.text == "kumain"
    assert item.syntactic_unit.translation == "to eat"


# ── the reader: direction comes from the notetype, scoped to the language ─────


def test_the_reader_maps_pimsleur_ord0_to_production_for_tagalog(pimsleur_db):
    with sqlite3.connect(str(pimsleur_db)) as conn:
        cards = fetch_cards_for_notes(conn, [5001], language_code="tl")
    by_ord = {c.ord: c for c in cards}
    assert by_ord[0].direction == Direction.PRODUCTION
    assert by_ord[0].fsrs_state.direction == Direction.PRODUCTION
    assert by_ord[1].direction == Direction.RECOGNITION
    assert by_ord[1].fsrs_state.direction == Direction.RECOGNITION


def test_the_pimsleur_profile_does_not_capture_another_languages_genanki_deck(pimsleur_db):
    # The notetype name is generic (any genanki export); the profile is Tagalog's.
    with sqlite3.connect(str(pimsleur_db)) as conn:
        cards = fetch_cards_for_notes(conn, [5001], language_code="sl")
    assert {c.ord: c.direction for c in cards} == {0: Direction.RECOGNITION, 1: Direction.PRODUCTION}


def test_the_sync_reader_carries_each_cards_direction(pimsleur_db):
    from app.plugins.anki_sync.sync_reader import OfflineReader

    with sqlite3.connect(str(pimsleur_db)) as conn:
        conn.row_factory = sqlite3.Row
        records = OfflineReader(conn, ROOT, language_code="tl").get_note_records()
    rec = next(r for r in records if r.anki_note_id == 5001)
    assert rec.l2_text == "kumain"
    assert rec.translation == "to eat"
    assert {c.ord: c.direction for c in rec.cards} == {0: Direction.PRODUCTION, 1: Direction.RECOGNITION}


# ── path 1: the seed importer ─────────────────────────────────────────────────


def test_import_seed_lands_each_cards_state_on_its_own_direction(pimsleur_db, tmp_path):
    _assert_pimsleur_directions(_import_seed(pimsleur_db, tmp_path))


def test_import_seed_reads_back_as_the_l2_without_its_br(pimsleur_db, tmp_path):
    _assert_pimsleur_text(_import_seed(pimsleur_db, tmp_path))


def test_import_seed_keys_a_verb_on_its_root(pimsleur_db, tmp_path, table_lemmatizer):
    db = _import_seed(pimsleur_db, tmp_path)
    assert db.get_collocation_by_anki_note_id(5001).syntactic_unit.lemma == "kain"


def test_import_seed_lemmatizes_a_one_word_back_without_its_punctuation(pimsleur_db, tmp_path, table_lemmatizer):
    db = _import_seed(pimsleur_db, tmp_path)
    item = db.get_collocation_by_anki_note_id(5002)
    assert item.syntactic_unit.text == "Kumusta?"  # the card text is untouched
    assert item.syntactic_unit.lemma == "kumusta"


def test_import_seed_leaves_a_sentence_unlemmatized(pimsleur_db, tmp_path, table_lemmatizer):
    db = _import_seed(pimsleur_db, tmp_path)
    assert db.get_collocation_by_anki_note_id(5003).syntactic_unit.lemma is None


# ── path 2: the reverse-import inside every sync ──────────────────────────────


async def test_the_reverse_import_lands_each_cards_state_on_its_own_direction(pimsleur_db):
    _assert_pimsleur_directions(await _reverse_import(pimsleur_db))


async def test_the_reverse_import_reads_back_as_the_l2_without_its_br(pimsleur_db):
    _assert_pimsleur_text(await _reverse_import(pimsleur_db))


async def test_the_reverse_import_keys_a_verb_on_its_root(pimsleur_db, table_lemmatizer):
    db = await _reverse_import(pimsleur_db)
    assert db.get_collocation_by_anki_note_id(5001).syntactic_unit.lemma == "kain"
    assert db.get_collocation_by_anki_note_id(5002).syntactic_unit.lemma == "kumusta"


# ── path 3: an ordinary pull maps Anki's cards onto TT's directions ───────────


def test_sync_pull_updates_the_direction_the_card_belongs_to(pimsleur_db, tmp_path):
    from app.plugins.anki_sync.sync import AnkiSync, OfflineReader, OfflineWriter

    db = _import_seed(pimsleur_db, tmp_path)
    # Anki moves ONLY the ord-0 (production) card; the pull must not hand its
    # new state to recognition.
    with sqlite3.connect(str(pimsleur_db)) as c:
        c.execute(
            "UPDATE cards SET data = ?, reps = 9, mod = 99 WHERE id = 50010", (json.dumps({"s": 77.0, "d": 5.0}),)
        )
    conn = sqlite3.connect(str(pimsleur_db))
    conn.row_factory = sqlite3.Row
    try:
        AnkiSync(
            db=db,
            _reader=OfflineReader(conn, ROOT, language_code="tl"),
            _writer=OfflineWriter(conn),
            language_code="tl",
        ).sync_pull()
    finally:
        conn.close()
    item = db.get_collocation_by_anki_note_id(5001)
    assert item.directions[Direction.PRODUCTION].stability == 77.0
    assert item.directions[Direction.RECOGNITION].stability == ORD1_STABILITY
