"""A lesson line's cards through sync as ONE "TT Sentence" note (tunatale-h0q3.55 phase 2).

TT keeps one row per card; the rows of a line share ``sibling_group`` and become
the clozes of one note, so Anki buries them as siblings. What sync must do:

- **Create**: one note per group, one card per row, each row linked to its own
  card (cloze N = ord N-1: the whole line is cloze 1, blanks 2.. in row order).
  A row joining a line whose note exists adds its card to that note and
  rewrites the note's fields; the cards already there keep their ids and ords.
- **Pull**: a shared note is read card by card, each resolved by its card id,
  so one row's schedule never lands on a sibling.
- **Push**: any flag on any row of a line rebuilds the whole note from the line.
- **Delete**: deleting a row drops its cloze from the note and graves its card
  (the note stays while siblings live).

Real ``OfflineWriter``/``OfflineReader`` on an in-memory collection carrying the
real notetype (``add_sentence_notetype``).
"""

from __future__ import annotations

import logging

import pytest

from app.cards.sentence_notetype import NAME
from app.common.guid import compute_guid
from app.models.srs_item import Direction, SRSState
from app.plugins.anki_sync.add_sentence_notetype import add_sentence_notetype
from app.plugins.anki_sync.sync import AnkiSync, OfflineReader, OfflineWriter
from app.srs.database import SRSDatabase
from app.srs.sentence_card import Cue, Gloss, SentenceCard, blank_span
from app.srs.sentence_note import sentence_note_fields, source_label
from tests._helpers.anki_sync_create_new import FakeReader, _make_dual_collection_conn
from tests._helpers.anki_sync_pull import FakeReader as RecordsReader

LANG = "ceb"
DECK = "0. Slovene"
_BASE = SentenceCard(
    lesson_id="wolff-1",
    line_index=4,
    speaker="Ínting",
    text="Nâa ba si Místir Abáya?",
    spoken="Nâa ba si Místir Abáya?",
    english="Is Mr. Abaya there?",
    glosses=(Gloss("ba", "question particle"),),
    cue=Cue(speaker="Inday", text="Únsa may átò?", spoken="Únsa may átò?", english="What can I do for you?"),
)
NAA = _BASE.with_blank(blank_span(_BASE.text, "nâa"), "there (near you)")
BA = _BASE.with_blank(blank_span(_BASE.text, "ba"), "yes/no question particle")
SI = _BASE.with_blank(blank_span(_BASE.text, "si"), "particle preceding names")
WHOLE = _BASE
NEXT_LINE = SentenceCard(
    lesson_id="wolff-1",
    line_index=5,
    speaker="Inday",
    text="Wâ siya dínhi.",
    spoken="Wâ siya dínhi.",
    english="He's not here.",
    glosses=(),
    cue=Cue(speaker="Ínting", text=_BASE.text, spoken=_BASE.spoken, english=_BASE.english),
)


@pytest.fixture
def no_tts(monkeypatch):
    """The TTS boundary: a sentence card must never reach it at mint."""
    import app.audio.cloze_tts as cloze_tts_mod

    async def _boom(text, voice=None):
        raise AssertionError(f"sentence card mint synthesized {text!r}")

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _boom)


def _conn(*, notetype: bool = True):
    conn = _make_dual_collection_conn()
    conn.execute(
        "CREATE TABLE graves (oid INTEGER NOT NULL, type INTEGER NOT NULL, usn INTEGER NOT NULL, "
        "PRIMARY KEY (oid, type))"
    )
    if notetype:
        assert add_sentence_notetype(conn) == "created"
    return conn


def _add(db: SRSDatabase, card: SentenceCard) -> str:
    unit = card.unit()
    assert db.add_collocation(unit, LANG)
    return compute_guid(unit.text, LANG, unit.disambig_key)


def _row_id(db: SRSDatabase, guid: str) -> int:
    with db._get_conn() as conn:
        return conn.execute("SELECT id FROM collocations WHERE guid = ?", (guid,)).fetchone()["id"]


async def _mint(db, anki_conn, media_dir=None):
    writer = OfflineWriter(anki_conn, media_dir=media_dir)
    sync = AnkiSync(db=db, _reader=FakeReader(), _writer=writer, language_code=LANG)
    await sync.sync_create_new(deck_name=DECK, model_name="Slovene Vocabulary")
    return writer


def _notes(anki_conn) -> list[tuple]:
    return [tuple(r) for r in anki_conn.execute("SELECT id, mid, guid, flds, sfld, usn FROM notes ORDER BY id")]


def _cards(anki_conn) -> dict[int, tuple[int, int]]:
    """ord → (card id, due)."""
    return {r["ord"]: (r["id"], r["due"]) for r in anki_conn.execute("SELECT id, ord, due FROM cards")}


def _card_of(db, guid) -> int:
    return db.get_collocation_by_guid(guid).directions[Direction.PRODUCTION].anki_card_id


def _fields(cards, ords=None, cue=None, answer=None):
    return "\x1f".join(
        sentence_note_fields(cards, cue_audio=cue, answer_audio=answer, source="Wolff 1 · line 4", ords=ords)
    )


def _sentence_mid(anki_conn) -> int:
    return anki_conn.execute("SELECT id FROM notetypes WHERE name = ?", (NAME,)).fetchone()[0]


class TestCreate:
    async def test_a_line_mints_one_note_and_links_each_row_to_its_own_card(self, no_tts):
        db = SRSDatabase(":memory:")
        naa, ba, whole = (_add(db, c) for c in (NAA, BA, WHOLE))
        anki_conn = _conn()

        await _mint(db, anki_conn)

        [(note_id, mid, guid, flds, sfld, usn)] = _notes(anki_conn)
        assert mid == _sentence_mid(anki_conn)
        assert guid == compute_guid(WHOLE.line_key, LANG, NAME)
        assert flds == _fields([NAA, BA, WHOLE])
        assert sfld == flds.split("\x1f")[0] and usn == -1
        cards = _cards(anki_conn)
        assert set(cards) == {0, 1, 2}
        # Cloze N is ord N-1: the whole line is cloze 1, the blanks 2, 3 in row order.
        assert (_card_of(db, whole), _card_of(db, naa), _card_of(db, ba)) == (cards[0][0], cards[1][0], cards[2][0])
        for g in (naa, ba, whole):
            assert db.get_collocation_by_guid(g).anki_note_id == note_id

    async def test_cards_take_course_positions_in_row_order(self, no_tts):
        """One unknown at a time: the blanks reach the deck before the whole line."""
        db = SRSDatabase(":memory:")
        for c in (NAA, BA, WHOLE):
            _add(db, c)
        anki_conn = _conn()

        await _mint(db, anki_conn)

        cards = _cards(anki_conn)
        assert cards[1][1] < cards[2][1] < cards[0][1]

    async def test_two_lines_are_two_notes(self, no_tts):
        db = SRSDatabase(":memory:")
        for c in (NAA, WHOLE, NEXT_LINE):
            _add(db, c)
        anki_conn = _conn()

        await _mint(db, anki_conn)

        assert len(_notes(anki_conn)) == 2

    async def test_a_row_joining_a_minted_line_adds_its_card_to_that_note(self, no_tts):
        db = SRSDatabase(":memory:")
        naa = _add(db, NAA)
        whole = _add(db, WHOLE)
        anki_conn = _conn()
        await _mint(db, anki_conn)
        before = _cards(anki_conn)
        [(note_id, *_)] = _notes(anki_conn)

        ba = _add(db, BA)
        await _mint(db, anki_conn)

        [(same_id, _mid, _guid, flds, sfld, usn)] = _notes(anki_conn)
        assert same_id == note_id
        after = _cards(anki_conn)
        assert after[0] == before[0] and after[1] == before[1], "cards already minted keep their ids and ords"
        assert set(after) == {0, 1, 2}
        assert (
            _card_of(db, ba) == after[2][0]
            and _card_of(db, naa) == before[1][0]
            and _card_of(db, whole) == before[0][0]
        )
        assert flds == _fields([NAA, BA, WHOLE], ords={WHOLE.key: 1, NAA.key: 2, BA.key: 3})
        assert sfld == flds.split("\x1f")[0] and usn == -1

    async def test_held_unlinked_while_the_notetype_is_missing(self, no_tts, caplog):
        db = SRSDatabase(":memory:")
        guid = _add(db, NAA)
        anki_conn = _conn(notetype=False)

        with caplog.at_level(logging.WARNING):
            await _mint(db, anki_conn)

        assert _notes(anki_conn) == []
        assert db.get_collocation_by_guid(guid).anki_note_id is None
        assert "SENTENCE_NOTE_NO_NOTETYPE" in caplog.text

    async def test_a_suspended_row_stays_out_of_the_note(self, no_tts):
        db = SRSDatabase(":memory:")
        _add(db, NAA)
        ba = _add(db, BA)
        db.set_suspended(_row_id(db, ba), True)
        anki_conn = _conn()

        await _mint(db, anki_conn)

        assert set(_cards(anki_conn)) == {1}
        assert db.get_collocation_by_guid(ba).anki_note_id is None

    async def test_an_ungrouped_unlinked_row_is_held_loudly(self, no_tts, caplog):
        """Every sentence row is grouped at insert (and the v50 migration grouped the unlinked
        ones), so an ungrouped unlinked one is a bug to see, not a card to mint alone."""
        db = SRSDatabase(":memory:")
        guid = _add(db, NAA)
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET sibling_group = NULL")
        anki_conn = _conn()

        with caplog.at_level(logging.WARNING):
            await _mint(db, anki_conn)

        assert _notes(anki_conn) == []
        assert db.get_collocation_by_guid(guid).anki_note_id is None
        assert "SENTENCE_CARD_NO_GROUP" in caplog.text

    async def test_the_lines_takes_reach_the_note_and_the_media_folder(self, tmp_path, no_tts):
        import app.plugins.anki_sync.sync as sync_mod

        db = SRSDatabase(":memory:")
        guid = _add(db, NAA)
        _add(db, WHOLE)
        coll_id = _row_id(db, guid)
        for kind, name in (("audio_cue", "cue_take.mp3"), ("audio_tts_sentence", "answer_take.mp3")):
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
        anki_conn = _conn()

        await _mint(db, anki_conn, media_dir=tmp_path)

        [(_id, _mid, _guid, flds, *_)] = _notes(anki_conn)
        assert flds == _fields([NAA, WHOLE], cue="cue_take.mp3", answer="answer_take.mp3")
        assert (tmp_path / "cue_take.mp3").read_bytes() == b"cue_take.mp3"
        assert (tmp_path / "answer_take.mp3").read_bytes() == b"answer_take.mp3"

    async def test_an_openers_picture_reaches_the_note_and_the_media_folder(self, tmp_path, no_tts):
        """Without the file in collection.media the front is a broken image."""
        import app.plugins.anki_sync.sync as sync_mod

        opener = SentenceCard(
            "wolff-1", 1, "Ínting", "Maáyong búntag.", "Maáyong búntag.", "Good morning.", (), picture="opener_door.svg"
        )
        (sync_mod._MEDIA_DIR / "opener_door.svg").write_bytes(b"<svg/>")
        db = SRSDatabase(":memory:")
        _add(db, opener)
        anki_conn = _conn()

        await _mint(db, anki_conn, media_dir=tmp_path)

        [(_id, _mid, _guid, flds, *_)] = _notes(anki_conn)
        assert '<img src="opener_door.svg">' in flds.split("\x1f")
        assert (tmp_path / "opener_door.svg").read_bytes() == b"<svg/>"

    async def test_a_note_written_but_never_linked_is_adopted(self, no_tts):
        """A crash between writing the note and linking its rows: the next mint finds the note
        by its line's guid and links to it rather than writing a second one."""
        db = SRSDatabase(":memory:")
        naa = _add(db, NAA)
        anki_conn = _conn()
        guid = compute_guid(NAA.line_key, LANG, NAME)
        note_id = OfflineWriter(anki_conn).create_sentence_note(
            DECK, _fields([NAA]).split("\x1f"), [1], guid=guid, tags=[]
        )

        await _mint(db, anki_conn)

        [(same_id, _mid, _guid, flds, *_)] = _notes(anki_conn)
        assert same_id == note_id and flds == _fields([NAA])
        assert _card_of(db, naa) == _cards(anki_conn)[1][0]

    async def test_a_line_on_two_notes_is_held(self, no_tts, caplog):
        """Two rows of one line linked to two notes (a legacy pair grouped without re-minting)
        cannot take a third row: which note would it join? Held, loudly."""
        db = SRSDatabase(":memory:")
        naa, ba, whole = (_add(db, c) for c in (NAA, BA, WHOLE))
        anki_conn = _conn()
        writer = OfflineWriter(anki_conn)
        for g, text in ((naa, "{{c1::Nâa}}"), (ba, "{{c1::ba}}")):
            nid = writer.create_cloze_note(DECK, text, language_code=LANG)
            db.set_anki_ids(g, nid, {Direction.PRODUCTION: writer.get_cards_for_note(nid)[0]})

        with caplog.at_level(logging.WARNING):
            await _mint(db, anki_conn)

        assert db.get_collocation_by_guid(whole).anki_note_id is None
        assert "SENTENCE_NOTE_SPLIT" in caplog.text

    async def test_dry_run_writes_nothing(self, no_tts):
        db = SRSDatabase(":memory:")
        _add(db, NAA)
        anki_conn = _conn()
        sync = AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(anki_conn), language_code=LANG)

        report = await sync.sync_create_new(deck_name=DECK, model_name="Slovene Vocabulary", dry_run=True)

        assert report.count == 1 and _notes(anki_conn) == []


def test_source_label() -> None:
    assert source_label(NAA) == "Wolff 1 · line 4"
    assert source_label(SentenceCard(**{**NAA.__dict__, "lesson_id": "wolff-2a"})) == "Wolff 2a · line 4"


def _reader(anki_conn) -> OfflineReader:
    return OfflineReader(anki_conn, DECK, language_code=LANG)


def _review_in_anki(anki_conn, card_id: int) -> None:
    """Grade a card to review in Anki itself: what the phone does between syncs."""
    import json
    import time

    anki_conn.execute(
        "UPDATE cards SET type = 2, queue = 2, due = 900, ivl = 10, reps = 3, mod = ?, data = ? WHERE id = ?",
        (int(time.time()), json.dumps({"s": 10.0, "d": 5.0, "lrt": int(time.time()) - 86400}), card_id),
    )
    anki_conn.commit()


class TestPull:
    async def test_each_card_of_the_note_lands_on_its_own_row(self, no_tts):
        db = SRSDatabase(":memory:")
        naa, ba, whole = (_add(db, c) for c in (NAA, BA, WHOLE))
        anki_conn = _conn()
        writer = await _mint(db, anki_conn)
        _review_in_anki(anki_conn, _card_of(db, ba))

        AnkiSync(
            db=db, _reader=RecordsReader(_reader(anki_conn).get_note_records()), _writer=writer, language_code=LANG
        ).sync_pull()

        state = {g: db.get_collocation_by_guid(g).directions[Direction.PRODUCTION] for g in (naa, ba, whole)}
        assert (state[ba].state, state[ba].reps) == (SRSState.REVIEW, 3)
        assert state[naa].state == SRSState.NEW and state[whole].state == SRSState.NEW
        for g, card in ((naa, NAA), (ba, BA), (whole, WHOLE)):
            unit = db.get_collocation_by_guid(g).syntactic_unit
            assert (unit.translation, unit.note) == (card.english, ""), "Anki gives a sentence card its schedule only"

    async def test_a_card_with_no_row_is_left_alone(self, no_tts):
        db = SRSDatabase(":memory:")
        naa = _add(db, NAA)
        ba = _add(db, BA)
        anki_conn = _conn()
        writer = await _mint(db, anki_conn)
        db.delete_collocation(_row_id(db, ba))

        report = AnkiSync(
            db=db, _reader=RecordsReader(_reader(anki_conn).get_note_records()), _writer=writer, language_code=LANG
        ).sync_pull()

        assert db.get_collocation_by_guid(naa).directions[Direction.PRODUCTION].state == SRSState.NEW
        assert report.skipped_unknown_guid == 1

    async def test_an_unlinked_sentence_note_is_never_imported(self, no_tts):
        """TT authors these notes and keeps their content; a note TT holds no row for (its rows
        deleted) must not come back as a row built from the note's Text."""
        db = SRSDatabase(":memory:")
        guid = _add(db, NAA)
        anki_conn = _conn()
        writer = await _mint(db, anki_conn)
        db.delete_collocation(_row_id(db, guid))

        await AnkiSync(db=db, _reader=_reader(anki_conn), _writer=writer, language_code=LANG).sync_create_new(
            deck_name=DECK, model_name="Slovene Vocabulary"
        )

        assert db.count_collocations() == 0

    async def test_the_guid_collision_tripwire_ignores_sentence_notes(self, no_tts):
        db = SRSDatabase(":memory:")
        for c in (NAA, NEXT_LINE):
            _add(db, c)
        anki_conn = _conn()
        writer = await _mint(db, anki_conn)

        sync = AnkiSync(db=db, _reader=_reader(anki_conn), _writer=writer, language_code=LANG)

        assert sync.warn_if_guid_collisions() == 0


def _push(db, anki_conn, *, dry_run=False):
    writer = OfflineWriter(anki_conn)
    return AnkiSync(db=db, _reader=FakeReader(), _writer=writer, language_code=LANG).sync_push(dry_run=dry_run)


class TestPush:
    async def test_an_edit_to_one_row_rebuilds_the_whole_note(self, no_tts):
        db = SRSDatabase(":memory:")
        naa, ba, whole = (_add(db, c) for c in (NAA, BA, WHOLE))
        anki_conn = _conn()
        await _mint(db, anki_conn)
        cards_before = _cards(anki_conn)
        edited = BA.with_blank(BA.blank, "question particle (yes/no)")
        db.update_sentence_card(ba, edited)

        report = _push(db, anki_conn)

        [(_id, _mid, _guid, flds, sfld, usn)] = _notes(anki_conn)
        assert flds == _fields([NAA, edited, WHOLE])
        assert sfld == flds.split("\x1f")[0] and usn == -1
        assert _cards(anki_conn) == cards_before
        assert [db.get_dirty_fields(g) for g in (naa, ba, whole)] == ["", "", ""]
        assert report.notes_pushed == 1

    async def test_the_lines_takes_reach_the_media_folder_on_push(self, tmp_path, no_tts):
        import app.plugins.anki_sync.sync as sync_mod

        db = SRSDatabase(":memory:")
        naa = _add(db, NAA)
        anki_conn = _conn()
        await _mint(db, anki_conn)
        (sync_mod._MEDIA_DIR / "answer_take.mp3").write_bytes(b"take")
        db.add_media(
            collocation_id=_row_id(db, naa),
            kind="audio_tts_sentence",
            filename="answer_take.mp3",
            path="media/answer_take.mp3",
            anki_filename="answer_take.mp3",
            sha256="x",
            size_bytes=1,
        )
        db.add_dirty_field(naa, "audio")

        AnkiSync(
            db=db, _reader=FakeReader(), _writer=OfflineWriter(anki_conn, media_dir=tmp_path), language_code=LANG
        ).sync_push()

        assert _notes(anki_conn)[0][3] == _fields([NAA], answer="answer_take.mp3")
        assert (tmp_path / "answer_take.mp3").read_bytes() == b"take"

    async def test_flags_on_several_rows_of_a_line_write_its_note_once(self, no_tts):
        db = SRSDatabase(":memory:")
        naa, ba = _add(db, NAA), _add(db, BA)
        anki_conn = _conn()
        await _mint(db, anki_conn)
        db.set_dirty_fields(naa, "note")
        db.set_dirty_fields(ba, "audio")

        report = _push(db, anki_conn)

        assert report.notes_pushed == 1
        assert [db.get_dirty_fields(g) for g in (naa, ba)] == ["", ""]

    async def test_deleting_a_row_drops_its_cloze_and_graves_its_card(self, no_tts):
        """The note stays while siblings live: one CARD grave, no note grave, col.usn untouched."""
        db = SRSDatabase(":memory:")
        naa, ba, whole = (_add(db, c) for c in (NAA, BA, WHOLE))
        anki_conn = _conn()
        await _mint(db, anki_conn)
        cards_before = _cards(anki_conn)
        col_usn = anki_conn.execute("SELECT usn FROM col").fetchone()[0]
        ba_card = _card_of(db, ba)

        db.delete_collocation(_row_id(db, ba))
        _push(db, anki_conn)

        [(_id, _mid, _guid, flds, *_)] = _notes(anki_conn)
        assert flds == _fields([NAA, WHOLE], ords={WHOLE.key: 1, NAA.key: 2})
        assert _cards(anki_conn) == {0: cards_before[0], 1: cards_before[1]}
        assert [tuple(r) for r in anki_conn.execute("SELECT oid, type, usn FROM graves")] == [(ba_card, 0, -1)]
        assert anki_conn.execute("SELECT usn FROM col").fetchone()[0] == col_usn
        assert [db.get_dirty_fields(g) for g in (naa, whole)] == ["", ""]

    async def test_bulk_delete_marks_the_line_too(self, no_tts):
        db = SRSDatabase(":memory:")
        naa, ba, whole = (_add(db, c) for c in (NAA, BA, WHOLE))
        anki_conn = _conn()
        await _mint(db, anki_conn)

        db.delete_collocations([_row_id(db, naa), _row_id(db, ba)])

        assert db.get_dirty_fields(whole) == "card_data"

    async def test_a_note_missing_from_the_collection_keeps_the_flags(self, no_tts):
        """During peer-sync the collection is tt_collection; nothing written, nothing cleared."""
        db = SRSDatabase(":memory:")
        naa = _add(db, NAA)
        anki_conn = _conn()
        await _mint(db, anki_conn)
        anki_conn.execute("DELETE FROM notes")
        db.set_dirty_fields(naa, "note")

        report = _push(db, anki_conn)

        assert db.get_dirty_fields(naa) == "note"
        assert report.write_noop == 1 and report.notes_pushed == 0

    async def test_dry_run_writes_nothing(self, no_tts):
        db = SRSDatabase(":memory:")
        ba = _add(db, BA)
        anki_conn = _conn()
        await _mint(db, anki_conn)
        before = _notes(anki_conn)
        db.update_sentence_card(ba, BA.with_blank(BA.blank, "changed"))

        _push(db, anki_conn, dry_run=True)

        assert _notes(anki_conn) == before
        assert db.get_dirty_fields(ba) == "card_data"


class TestWriter:
    """The writer's refusals: each is a state the engine guards against, so it is reached only here."""

    def test_create_without_the_notetype_is_refused(self):
        with pytest.raises(ValueError, match="not in the collection"):
            OfflineWriter(_conn(notetype=False)).create_sentence_note(DECK, ["x"] * 12, [0], guid="g", tags=[])

    def test_create_into_a_missing_deck_is_refused(self):
        with pytest.raises(ValueError, match="not found"):
            OfflineWriter(_conn()).create_sentence_note("No such deck", ["x"] * 12, [0], guid="g", tags=[])

    def test_rewriting_another_kind_of_note_is_refused(self):
        conn = _conn()
        writer = OfflineWriter(conn)
        nid = writer.create_cloze_note(DECK, "{{c1::x}}", language_code=LANG)
        with pytest.raises(ValueError, match="not a 'TT Sentence' note"):
            writer.rewrite_sentence_note(nid, ["x"] * 12, [0])

    def test_a_note_left_with_no_card_takes_the_deck_it_is_given(self):
        conn = _conn()
        writer = OfflineWriter(conn)
        nid = writer.create_sentence_note(DECK, ["x"] * 12, [0], guid="g", tags=[])
        conn.execute("DELETE FROM cards")
        with pytest.raises(ValueError, match="no card to take a deck from"):
            writer.rewrite_sentence_note(nid, ["x"] * 12, [0])
        assert writer.rewrite_sentence_note(nid, ["x"] * 12, [0], deck_name=DECK)
        assert set(writer.get_cards_for_note(nid)) == {0}


def _graves(anki_conn) -> list[tuple[int, int]]:
    return [tuple(r) for r in anki_conn.execute("SELECT oid, type FROM graves ORDER BY oid")]


def _unlink_all(db: SRSDatabase) -> None:
    """Every row loses its Anki pointers: rows deleted and made again, a restored TT database."""
    with db._get_conn() as conn:
        conn.execute("UPDATE collocations SET anki_note_id = NULL")
        conn.execute("UPDATE collocation_directions SET anki_card_id = NULL")
        conn.commit()


class TestACardStaysWithItsBlank:
    """A cloze number names a card, and the card holds the review history (tunatale-h0q3.60).

    Found by review of h0q3.55: a line was numbered from its rows alone, so
    whenever the rows and the note disagreed (a freed number, a row with no link)
    a blank was given another blank's card and the rest were graved.
    """

    async def test_a_relinked_line_with_a_freed_number_keeps_each_card(self, no_tts):
        """Numbers 1 and 3 in use (2 was deleted). Numbered from scratch the surviving blank
        became 2: its card at ord 2 was graved and a new one made, its history gone."""
        db = SRSDatabase(":memory:")
        naa, ba, whole = (_add(db, c) for c in (NAA, BA, WHOLE))
        anki_conn = _conn()
        await _mint(db, anki_conn)
        ba_card, whole_card = _card_of(db, ba), _card_of(db, whole)
        db.delete_collocation(_row_id(db, naa))
        _push(db, anki_conn)
        graves = _graves(anki_conn)

        _unlink_all(db)
        await _mint(db, anki_conn)

        assert (_card_of(db, ba), _card_of(db, whole)) == (ba_card, whole_card)
        assert _graves(anki_conn) == graves, "no card graved by the relink"
        assert set(_cards(anki_conn)) == {0, 2}

    async def test_a_relinked_line_without_a_gap_keeps_each_card(self, no_tts):
        """The control: this held before the fix, by the coincidence of fresh numbers matching."""
        db = SRSDatabase(":memory:")
        guids = [_add(db, c) for c in (NAA, BA, WHOLE)]
        anki_conn = _conn()
        await _mint(db, anki_conn)
        before = [_card_of(db, g) for g in guids]

        _unlink_all(db)
        await _mint(db, anki_conn)

        assert [_card_of(db, g) for g in guids] == before and _graves(anki_conn) == []

    async def test_a_new_blank_never_takes_the_card_of_one_deleted_and_not_yet_pushed(self, no_tts):
        """Delete a blank and add another before one sync. The mint runs before the push, so
        the deleted blank's card is still on the note; the next number above the ROWS' own
        was that card's, and the new blank inherited its history."""
        db = SRSDatabase(":memory:")
        _naa, ba, _whole = (_add(db, c) for c in (NAA, BA, WHOLE))
        anki_conn = _conn()
        await _mint(db, anki_conn)
        ba_card = _card_of(db, ba)

        db.delete_collocation(_row_id(db, ba))
        si = _add(db, SI)
        await _mint(db, anki_conn)

        assert _card_of(db, si) != ba_card
        assert set(_cards(anki_conn)) == {0, 1, 3}, "BA's card (ord 2) is gone and its number stays unused"
        assert (ba_card, 0) in _graves(anki_conn)

    async def test_a_blank_removed_and_put_back_before_a_sync_keeps_its_card(self, no_tts):
        db = SRSDatabase(":memory:")
        _naa, ba, _whole = (_add(db, c) for c in (NAA, BA, WHOLE))
        anki_conn = _conn()
        await _mint(db, anki_conn)
        ba_card = _card_of(db, ba)

        db.delete_collocation(_row_id(db, ba))
        ba = _add(db, BA)
        await _mint(db, anki_conn)

        assert _card_of(db, ba) == ba_card and _graves(anki_conn) == []

    async def test_a_card_deleted_in_anki_is_put_back_at_its_number(self, no_tts):
        """One card gone, its note alive: the row is not unlinked, so no mint reaches it, and
        nothing was flagged, so no push did. It sat cardless, still served by TT."""
        from dataclasses import replace

        db = SRSDatabase(":memory:")
        naa, ba, whole = (_add(db, c) for c in (NAA, BA, WHOLE))
        for k in range(10, 18):  # other lines: one lost card stays under the 25% orphan threshold
            _add(db, replace(WHOLE, line_index=k, text=f"Linya {k}.", spoken=f"Linya {k}.", cue=None))
        anki_conn = _conn()
        await _mint(db, anki_conn)
        ba_card, others = _card_of(db, ba), (_card_of(db, naa), _card_of(db, whole))
        note_id = db.get_collocation_by_guid(ba).anki_note_id
        anki_conn.execute("INSERT INTO graves (oid, type, usn) VALUES (?, 0, -1)", (ba_card,))
        anki_conn.execute("DELETE FROM cards WHERE id = ?", (ba_card,))
        anki_conn.commit()

        sync = AnkiSync(db=db, _reader=_reader(anki_conn), _writer=OfflineWriter(anki_conn), language_code=LANG)
        sync.detect_and_reset_orphans()
        assert db.get_dirty_fields(ba) == "card_data"
        sync.sync_push()

        ords = {r[0]: r[1] for r in anki_conn.execute("SELECT ord, id FROM cards WHERE nid = ?", (note_id,))}
        assert set(ords) == {0, 1, 2}
        assert _card_of(db, ba) == ords[2] and (_card_of(db, naa), _card_of(db, whole)) == others
        assert db.get_dirty_fields(ba) == ""


class TestHeld:
    """A line that cannot be written safely is held, loudly, and the sync goes on (tunatale-h0q3.60)."""

    async def test_a_row_joining_a_note_whose_text_is_not_the_lines_is_held(self, no_tts, caplog):
        db = SRSDatabase(":memory:")
        naa = _add(db, NAA)
        anki_conn = _conn()
        await _mint(db, anki_conn)
        anki_conn.execute("UPDATE notes SET flds = 'edited by hand' || substr(flds, instr(flds, char(31)))")
        anki_conn.commit()
        before = (_notes(anki_conn), _cards(anki_conn))

        ba = _add(db, BA)
        with caplog.at_level(logging.WARNING):
            await _mint(db, anki_conn)

        assert (_notes(anki_conn), _cards(anki_conn)) == before
        assert db.get_collocation_by_guid(ba).anki_note_id is None
        assert db.get_collocation_by_guid(naa).anki_note_id is not None
        assert "SENTENCE_NOTE_HELD line=sent:wolff-1:4" in caplog.text

    async def test_a_note_with_another_text_is_not_adopted(self, no_tts, caplog):
        db = SRSDatabase(":memory:")
        naa = _add(db, NAA)
        anki_conn = _conn()
        guid = compute_guid(NAA.line_key, LANG, NAME)
        OfflineWriter(anki_conn).create_sentence_note(DECK, ["x"] * 12, [1], guid=guid, tags=[])
        before = (_notes(anki_conn), _cards(anki_conn))

        with caplog.at_level(logging.WARNING):
            await _mint(db, anki_conn)

        assert (_notes(anki_conn), _cards(anki_conn)) == before and _graves(anki_conn) == []
        assert db.get_collocation_by_guid(naa).anki_note_id is None
        assert "SENTENCE_NOTE_HELD" in caplog.text

    async def test_overlapping_blanks_hold_their_line_and_the_next_line_still_mints(self, no_tts, caplog):
        """This raised out of sync_create_new, after the lines ahead of it had been written."""
        db = SRSDatabase(":memory:")
        naa = _add(db, NAA)
        _add(db, _BASE.with_blank((0, 6), "overlaps nâa"))
        nxt = _add(db, NEXT_LINE)
        anki_conn = _conn()

        with caplog.at_level(logging.WARNING):
            await _mint(db, anki_conn)

        assert db.get_collocation_by_guid(naa).anki_note_id is None
        assert db.get_collocation_by_guid(nxt).anki_note_id is not None
        assert len(_notes(anki_conn)) == 1
        assert "SENTENCE_NOTE_HELD line=sent:wolff-1:4" in caplog.text and "overlap" in caplog.text

    async def test_a_push_that_cannot_build_the_note_keeps_the_flags(self, no_tts, caplog):
        db = SRSDatabase(":memory:")
        naa = _add(db, NAA)
        anki_conn = _conn()
        await _mint(db, anki_conn)
        before = _notes(anki_conn)
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET card_data = replace(card_data, 'Abáya?', 'Abáya::?')")
            conn.commit()
        db.set_dirty_fields(naa, "card_data")

        with caplog.at_level(logging.WARNING):
            report = _push(db, anki_conn)

        assert _notes(anki_conn) == before
        assert db.get_dirty_fields(naa) == "card_data"
        assert report.write_noop == 1 and report.notes_pushed == 0
        assert "SENTENCE_NOTE_HELD" in caplog.text


class TestPullStoresTheCardsOrd:
    """Anki sorts new cards by ``cards.ord``; the pull is where TT learns it (tunatale-h0q3.60)."""

    async def test_each_row_learns_its_cards_ord_with_nothing_else_to_write(self, no_tts):
        db = SRSDatabase(":memory:")
        naa, ba, whole = (_add(db, c) for c in (NAA, BA, WHOLE))
        anki_conn = _conn()
        writer = await _mint(db, anki_conn)

        def _pull(*, dry_run=False):
            return AnkiSync(
                db=db, _reader=RecordsReader(_reader(anki_conn).get_note_records()), _writer=writer, language_code=LANG
            ).sync_pull(dry_run=dry_run)

        def _ords():
            return [db.get_collocation_by_guid(g).directions[Direction.PRODUCTION].anki_ord for g in (naa, ba, whole)]

        _pull(dry_run=True)
        assert _ords() == [None, None, None], "a dry run writes nothing"
        _pull()
        assert _ords() == [1, 2, 0]

    async def test_a_card_graded_in_anki_keeps_its_ord_through_the_merge_write(self, no_tts):
        db = SRSDatabase(":memory:")
        _naa, ba, _whole = (_add(db, c) for c in (NAA, BA, WHOLE))
        anki_conn = _conn()
        writer = await _mint(db, anki_conn)
        _review_in_anki(anki_conn, _card_of(db, ba))

        AnkiSync(
            db=db, _reader=RecordsReader(_reader(anki_conn).get_note_records()), _writer=writer, language_code=LANG
        ).sync_pull()

        state = db.get_collocation_by_guid(ba).directions[Direction.PRODUCTION]
        assert (state.state, state.anki_ord) == (SRSState.REVIEW, 2)
