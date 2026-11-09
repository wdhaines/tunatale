"""A LEGACY sentence card through sync (tunatale-h0q3.42): pull and push of a separate Cloze note.

Since h0q3.55 phase 2 a new sentence card mints into its line's "TT Sentence"
note (``test_anki_sentence_note_sync.py``). The cards minted before that — Wolff
1's 52 separate c1 Cloze notes — keep ``sibling_group`` NULL until phase 3
re-mints them, and sync must keep reading and writing them as they are. Each
test here builds that shape: an ungrouped row linked to its own Cloze note.

The card's content is TT's: both note fields are built from ``card_data`` on the
way out, and nothing on the way back may rewrite it from Anki's Back Extra
(which ``extract_cloze_translation`` and friends would otherwise parse into the
row's translation). Audio is the lesson's own take, attached as media rows;
the sync never synthesizes for a sentence card (a metered voice, rendered once
for the lesson already).

Real ``OfflineWriter`` on an in-memory collection; the reader for the pull is
the real ``OfflineReader``'s records, replayed.
"""

from __future__ import annotations

import pytest

from app.common.guid import compute_guid
from app.models.srs_item import Direction
from app.plugins.anki_sync.sync import AnkiSync, OfflineReader, OfflineWriter
from app.srs.database import SRSDatabase
from app.srs.sentence_card import Cue, Gloss, SentenceCard, blank_span, sentence_card
from tests._helpers.anki_sync_create_new import FakeReader, _make_dual_collection_conn
from tests._helpers.anki_sync_pull import FakeReader as RecordsReader

DECK = "0. Slovene"
CARD = SentenceCard(
    lesson_id="a-visit-1234",
    line_index=3,
    speaker="Per",
    text="Jeg bor i Oslo.",
    spoken="Jeg bor i Oslo.",
    english="I live in Oslo.",
    glosses=(Gloss("jeg", "I"), Gloss("bor", "live(s)")),
    cue=Cue("Kari", "Hvor bor du?", "Hvor bor du?", "Where do you live?", (Gloss("hvor", "where?"),)),
)


@pytest.fixture
def no_tts(monkeypatch):
    """The TTS boundary: a sentence card must never reach it at mint."""
    import app.audio.cloze_tts as cloze_tts_mod

    async def _boom(text, voice=None):
        raise AssertionError(f"sentence card mint synthesized {text!r}")

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _boom)


def _add(db: SRSDatabase, card: SentenceCard) -> tuple[str, int]:
    unit = card.unit()
    db.add_collocation(unit, "sl")
    guid = compute_guid(unit.text, "sl", unit.disambig_key)
    with db._get_conn() as conn:
        coll_id = conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()["id"]
    return guid, coll_id


def _media(db, coll_id, kind, name):
    import app.plugins.anki_sync.sync as sync_mod

    (sync_mod._MEDIA_DIR / name).write_bytes(name.encode())
    db.add_media(
        collocation_id=coll_id,
        kind=kind,
        filename=name,
        path=f"media/{name}",
        anki_filename=name,
        sha256="x",
        size_bytes=1,
    )


async def _mint(db, anki_conn, media_dir=None):
    """Build the legacy shape: each ungrouped row on its own c1 Cloze note, as h0q3.42 minted it."""
    writer = OfflineWriter(anki_conn, media_dir=media_dir)
    with db._get_conn() as conn:
        rows = conn.execute("SELECT id, guid FROM collocations WHERE anki_note_id IS NULL ORDER BY id").fetchall()
        conn.execute("UPDATE collocations SET sibling_group = NULL")
    for row in rows:
        card = sentence_card(db.get_collocation_by_guid(row["guid"]).syntactic_unit)
        note_id = writer.create_cloze_note(
            DECK, card.note_text(None), back_extra=card.back_extra(None), language_code="sl"
        )
        db.set_anki_ids(row["guid"], note_id, {Direction.PRODUCTION: writer.get_cards_for_note(note_id)[0]})
    return writer


def _flds(anki_conn) -> list[list[str]]:
    return [r[0].split("\x1f") for r in anki_conn.execute("SELECT flds FROM notes ORDER BY id")]


class TestPull:
    async def test_pull_leaves_the_cards_content_alone(self, no_tts):
        db = SRSDatabase(":memory:")
        guid, _ = _add(db, CARD)
        anki_conn = _make_dual_collection_conn()
        writer = await _mint(db, anki_conn)
        records = OfflineReader(anki_conn, DECK, language_code="sl").get_note_records()
        assert records, "precondition: the reader sees the minted note"

        AnkiSync(db=db, _reader=RecordsReader(records), _writer=writer).sync_pull()

        unit = db.get_collocation_by_guid(guid).syntactic_unit
        assert unit.translation == CARD.english
        assert unit.source_sentence_translation == ""
        assert unit.note == ""
        assert sentence_card(unit) == CARD


class TestPush:
    async def test_a_picture_added_later_reaches_anki_media_on_push(self, tmp_path, no_tts):
        """Wolff 1's opener was minted with no picture; giving it one is a card edit, pushed like any other."""
        import app.plugins.anki_sync.sync as sync_mod

        opener = SentenceCard("a-visit-1234", 1, "Per", "Hei.", "Hei.", "Hello.", ())
        db = SRSDatabase(":memory:")
        guid, _ = _add(db, opener)
        anki_conn = _make_dual_collection_conn()
        await _mint(db, anki_conn)

        pictured = SentenceCard(**{**opener.__dict__, "picture": "opener_door.svg"})
        (sync_mod._MEDIA_DIR / "opener_door.svg").write_bytes(b"<svg/>")
        db.update_sentence_card(guid, pictured)
        writer = OfflineWriter(anki_conn, media_dir=tmp_path)
        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert _flds(anki_conn)[0][0] == pictured.note_text(None)
        assert (tmp_path / "opener_door.svg").read_bytes() == b"<svg/>"

    async def test_card_edit_rewrites_both_fields(self, no_tts):
        db = SRSDatabase(":memory:")
        guid, coll_id = _add(db, CARD)
        anki_conn = _make_dual_collection_conn()
        writer = await _mint(db, anki_conn)

        edited = CARD.with_blank(blank_span(CARD.text, "i"), "in")
        edited = SentenceCard(**{**edited.__dict__, "blank": None, "blank_gloss": "", "english": "I live in Oslo!"})
        db.update_sentence_card(guid, edited)
        _media(db, coll_id, "audio_cue", "cue_take.mp3")
        assert "card_data" in db.get_dirty_fields(guid).split(",")

        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert _flds(anki_conn) == [[edited.note_text("cue_take.mp3"), edited.back_extra(None)]]
        assert db.get_dirty_fields(guid) == ""
        assert sentence_card(db.get_collocation_by_guid(guid).syntactic_unit) == edited

    async def test_any_flag_rebuilds_both_fields_and_clears(self, no_tts):
        """Every field of this note comes from the card, so no flag can be left with nothing to write."""
        db = SRSDatabase(":memory:")
        guid, coll_id = _add(db, CARD)
        anki_conn = _make_dual_collection_conn()
        writer = await _mint(db, anki_conn)
        _media(db, coll_id, "audio_tts_sentence", "answer_take.mp3")
        db.set_dirty_fields(guid, "note")

        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push()

        assert _flds(anki_conn) == [[CARD.note_text(None), CARD.back_extra("answer_take.mp3")]]
        assert db.get_dirty_fields(guid) == ""

    async def test_dry_run_writes_nothing(self, tmp_path, no_tts):
        db = SRSDatabase(":memory:")
        guid, coll_id = _add(db, CARD)
        anki_conn = _make_dual_collection_conn()
        await _mint(db, anki_conn)
        _media(db, coll_id, "audio_tts_sentence", "answer_take.mp3")
        db.add_dirty_field(guid, "audio")
        writer = OfflineWriter(anki_conn, media_dir=tmp_path)

        AnkiSync(db=db, _reader=FakeReader(), _writer=writer).sync_push(dry_run=True)

        assert _flds(anki_conn) == [[CARD.note_text(None), CARD.back_extra(None)]]
        assert not (tmp_path / "answer_take.mp3").exists()
        assert db.get_dirty_fields(guid) == "audio"
