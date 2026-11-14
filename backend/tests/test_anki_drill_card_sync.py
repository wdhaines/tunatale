"""Drill cards in Anki (tunatale-5i1p.5): the note holds the step the card's ``reps`` call for.

Design: ``.beads-tasks/briefs/design-rotating-drill-card-2026-11.md`` § Build 3,
§ Oracle (S1-S9 here), and the audit checks J1-J7 on the bead.

- **Mint** (S1, J1): a drill row becomes one "TT Sentence" note, one card (ord
  0), through the existing line mint. It never reaches Anki's built-in Cloze
  notetype; the hold PR #421 put in front of it is gone, not kept beside this.
- **Rotation** (S2-S8): after the pull, each drill note is rewritten if its
  stored fields differ from what its row's ``reps`` call for. From state, not
  from a flag: a phone edit that puts an old step back is repaired at the next
  sync, and a sync with nothing changed writes nothing.

Real ``OfflineWriter``/``OfflineReader`` on an in-memory collection carrying the
built-in Cloze notetype and the real "TT Sentence" notetype. The sync helper runs
the phases in ``run_full_sync``'s order (create, push, pull, rotate);
``TestRunFullSync`` pins that order on the real runner.
"""

from __future__ import annotations

import json
import logging
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.guid import compute_guid
from app.main import app
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.sync import AnkiSync, OfflineReader, OfflineWriter
from app.srs.database import SRSDatabase
from app.srs.drill_card import drill_card
from app.srs.lesson_card_mint import mint_lesson_cards
from tests._helpers.anki_sync_create_new import FakeReader
from tests._helpers.anki_sync_pull import FakeReader as RecordsReader
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests.test_anki_sentence_note_sync import _conn
from tests.test_api_drill_card import _install
from tests.test_drill_card import DRILLS
from tests.test_lesson_drill_cards import EXPECTED_KEYS, _cards, _story

LANG = "no"
DECK = "0. Slovene"  # the helper collection's deck
SUB = "sent:test-01:drill-1"  # substitution, 4 steps
QA = "sent:test-01:drill-4:2"  # a question-and-answer step card: does not rotate


@pytest.fixture
def no_tts(monkeypatch):
    """The TTS boundary: the plain cloze's audio fails open, as it does with Azure down."""
    import app.audio.cloze_tts as cloze_tts_mod

    async def _down(text, voice=None):
        raise RuntimeError("TTS down")

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _down)


def _plain_cloze(db: SRSDatabase) -> str:
    unit = SyntacticUnit(
        text="er",
        translation="is",
        word_count=1,
        difficulty=1,
        source="test",
        card_type="cloze",
        source_sentence="Han {{c1::er}} her.",
    )
    assert db.add_collocation(unit, LANG)
    return compute_guid(unit.text, LANG, unit.disambig_key)


def _notes_by_notetype(anki) -> dict[str, int]:
    rows = anki.execute(
        "SELECT nt.name, COUNT(*) FROM notes n JOIN notetypes nt ON nt.id = n.mid GROUP BY nt.name"
    ).fetchall()
    return {name: count for name, count in rows}


def _minted(db: SRSDatabase) -> dict[str, int]:
    """Row id by key, for the lesson's drill rows."""
    return {card.unit.disambig_key: cid for cid, card in mint_lesson_cards(db, LANG, _cards(), drills=DRILLS)}


def _row(db: SRSDatabase, cid: int):
    _, item, _ = db.get_collocation_by_id(cid)
    return item


def _reps(db: SRSDatabase, cid: int) -> int:
    return _row(db, cid).directions[Direction.PRODUCTION].reps


def _expected(db: SRSDatabase, cid: int, reps: int | None = None, media: dict[str, str] | None = None) -> str:
    from app.srs.sentence_note import drill_note_fields, drill_source_label

    card = drill_card(_row(db, cid).syntactic_unit)
    view = card.view(_reps(db, cid) if reps is None else reps)
    return "\x1f".join(drill_note_fields(view, media=media or {}, source=drill_source_label(card)))


def _note(anki, db: SRSDatabase, cid: int) -> dict:
    nid = _row(db, cid).anki_note_id
    r = anki.execute("SELECT id, flds, sfld, mod, usn, tags FROM notes WHERE id = ?", (nid,)).fetchone()
    return dict(r)


def _card(anki, db: SRSDatabase, cid: int) -> dict:
    nid = _row(db, cid).anki_note_id
    return dict(anki.execute("SELECT * FROM cards WHERE nid = ?", (nid,)).fetchone())


async def _mint(db: SRSDatabase, anki, *, dry_run: bool = False):
    sync = AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(anki), language_code=LANG)
    return await sync.sync_create_new(deck_name=DECK, model_name="Slovene Vocabulary", dry_run=dry_run)


def _rotate(db: SRSDatabase, anki, *, dry_run: bool = False) -> int:
    sync = AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(anki), language_code=LANG)
    return sync.rotate_drill_notes(dry_run=dry_run)


async def _sync(db: SRSDatabase, anki) -> int:
    """One sync, phases in ``run_full_sync``'s order: create, push, pull, rotate."""
    writer = OfflineWriter(anki)
    await AnkiSync(db=db, _reader=FakeReader(), _writer=writer, language_code=LANG).sync_create_new(
        deck_name=DECK, model_name="Slovene Vocabulary"
    )
    AnkiSync(db=db, _reader=FakeReader(), _writer=writer, language_code=LANG).sync_push()
    records = OfflineReader(anki, DECK, language_code=LANG).get_note_records()
    puller = AnkiSync(db=db, _reader=RecordsReader(records), _writer=writer, language_code=LANG)
    puller.sync_pull()
    return puller.rotate_drill_notes()


def _review_in_anki(anki, card_id: int, reps: int) -> None:
    """A review made on the phone: the card's count moves, the note does not."""
    anki.execute(
        "UPDATE cards SET type = 2, queue = 2, due = 900, ivl = 10, reps = ?, mod = ?, usn = -1, data = ? WHERE id = ?",
        (reps, int(time.time()), json.dumps({"s": 10.0, "d": 5.0, "lrt": int(time.time()) - 86400}), card_id),
    )
    anki.commit()


def _forget_in_anki(anki, card_id: int) -> None:
    """Anki's Forget: the card back to new, its count reset."""
    anki.execute(
        "UPDATE cards SET type = 0, queue = 0, due = 5, ivl = 0, reps = 0, lapses = 0, mod = ?, usn = -1, data = '' "
        "WHERE id = ?",
        (int(time.time()) + 1, card_id),
    )
    anki.commit()


def _col(anki) -> tuple[int, int]:
    return tuple(anki.execute("SELECT scm, usn FROM col").fetchone())


def _graves(anki) -> list:
    return [tuple(r) for r in anki.execute("SELECT oid, type FROM graves")]


def _clean_note(anki, nid: int) -> None:
    """As an AnkiWeb sync leaves a note: no longer dirty."""
    anki.execute("UPDATE notes SET usn = 7 WHERE id = ?", (nid,))
    anki.commit()


# --------------------------------------------------------------------------
# S1 / J1: the mint, the hold replaced
# --------------------------------------------------------------------------


class TestMint:
    async def test_s1_each_drill_row_is_one_tt_sentence_note_and_the_plain_cloze_keeps_its_cloze_note(self, no_tts):
        db = SRSDatabase(":memory:")
        minted = _minted(db)
        cloze_guid = _plain_cloze(db)
        anki = _conn()

        await _mint(db, anki)

        # control: the plain cloze in the same run reached Anki on the Cloze notetype
        assert _notes_by_notetype(anki) == {"Cloze": 1, "TT Sentence": len(EXPECTED_KEYS)}
        assert db.get_collocation_by_guid(cloze_guid).anki_note_id is not None
        for key in EXPECTED_KEYS:
            cid = minted[key]
            item = _row(db, cid)
            cards = anki.execute("SELECT id, ord FROM cards WHERE nid = ?", (item.anki_note_id,)).fetchall()
            assert [c["ord"] for c in cards] == [0]
            prod = item.directions[Direction.PRODUCTION]
            assert (prod.anki_card_id, prod.state) == (cards[0]["id"], SRSState.NEW)
            note = _note(anki, db, cid)
            assert note["flds"] == _expected(db, cid, reps=0)
            assert note["tags"].split() == ["tunatale", "sentence", "drill", "tt-course"]

    async def test_a_dry_run_counts_the_drill_rows(self):
        db = SRSDatabase(":memory:")
        _minted(db)
        _plain_cloze(db)
        anki = _conn()
        report = await _mint(db, anki, dry_run=True)
        assert report.count == 1 + len(EXPECTED_KEYS)
        assert _notes_by_notetype(anki) == {}

    async def test_a_second_mint_adds_nothing(self, no_tts):
        db = SRSDatabase(":memory:")
        _minted(db)
        anki = _conn()
        await _mint(db, anki)
        await _mint(db, anki)
        assert _notes_by_notetype(anki) == {"TT Sentence": len(EXPECTED_KEYS)}

    async def test_the_hold_is_gone(self, no_tts, caplog):
        db = SRSDatabase(":memory:")
        _minted(db)
        with caplog.at_level(logging.INFO):
            await _mint(db, _conn())
        assert "DRILL_CARD_HELD" not in caplog.text

    async def test_a_drill_row_whose_card_data_is_unreadable_is_held_loudly(self, no_tts, caplog):
        """Never down the plain-cloze branch onto the Cloze notetype (the I1 class, audit J5)."""
        db = SRSDatabase(":memory:")
        minted = _minted(db)
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET card_data = '{}' WHERE id = ?", (minted[SUB],))
            conn.commit()
        anki = _conn()

        with caplog.at_level(logging.INFO):
            await _mint(db, anki)

        assert _row(db, minted[SUB]).anki_note_id is None
        assert f"DRILL_CARD_UNREADABLE key={SUB}" in caplog.text
        # control: the other drill rows in the same run have their notes
        assert _notes_by_notetype(anki) == {"TT Sentence": len(EXPECTED_KEYS) - 1}

    async def test_f1_a_linked_unreadable_drill_row_is_held_by_the_rotation_and_the_rest_rotate(self, no_tts, caplog):
        """Audit F1: a linked drill row whose card_data stopped reading as a drill card (a new
        required DrillCard field would do it to every existing row) must not raise out of the sync."""
        db = SRSDatabase(":memory:")
        minted = _minted(db)
        anki = _conn()
        await _sync(db, anki)
        bad, good = minted[SUB], minted["sent:test-01:drill-2"]
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET card_data = '{}' WHERE id = ?", (bad,))
            conn.commit()
        for cid in (bad, good):
            anki.execute("UPDATE notes SET flds = 'x' || flds WHERE id = ?", (_row(db, cid).anki_note_id,))
        anki.commit()
        bad_before = _note(anki, db, bad)

        with caplog.at_level(logging.INFO):
            assert _rotate(db, anki) == 1

        assert f"DRILL_CARD_UNREADABLE key={SUB}" in caplog.text
        assert _note(anki, db, bad) == bad_before
        assert _note(anki, db, good)["flds"] == _expected(db, good)  # control: the other drill is written

    async def test_f1_a_flagged_linked_unreadable_drill_row_keeps_its_flag_and_the_push_goes_on(self, no_tts, caplog):
        db = SRSDatabase(":memory:")
        cid = _minted(db)[SUB]
        anki = _conn()
        await _sync(db, anki)
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET card_data = '{}' WHERE id = ?", (cid,))
            conn.commit()
        db.add_dirty_field_by_id(cid, "audio")
        before = _note(anki, db, cid)

        with caplog.at_level(logging.INFO):
            report = AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(anki), language_code=LANG).sync_push()

        assert report.write_noop == 1
        assert db.get_dirty_fields(_row(db, cid).guid) == "audio"
        assert _note(anki, db, cid) == before
        assert f"DRILL_CARD_UNREADABLE key={SUB}" in caplog.text

    def test_a_flagged_drill_row_with_no_note_has_nothing_to_write(self):
        db = SRSDatabase(":memory:")
        minted = _minted(db)
        for cid in minted.values():
            db.add_dirty_field_by_id(cid, "audio")
        anki = _conn()

        report = AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(anki), language_code=LANG).sync_push()

        assert report.no_anki_id == len(minted)
        assert anki.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0

    async def test_a_flagged_drill_row_rebuilds_its_note_at_its_step(self, no_tts):
        db = SRSDatabase(":memory:")
        cid = _minted(db)[SUB]
        anki = _conn()
        await _mint(db, anki)
        nid = _row(db, cid).anki_note_id
        anki.execute("UPDATE notes SET flds = 'x' || flds WHERE id = ?", (nid,))
        db.add_dirty_field_by_id(cid, "audio")

        report = AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(anki), language_code=LANG).sync_push()

        assert report.notes_pushed == 1
        assert _note(anki, db, cid)["flds"] == _expected(db, cid)
        assert db.get_dirty_fields(_row(db, cid).guid) == ""


# --------------------------------------------------------------------------
# S2-S5, J2-J4: rotation from state, after the pull
# --------------------------------------------------------------------------


async def _grade_in_tt(cid: int, times: int) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        for _ in range(times):
            r = await c.post(f"/api/srs/items/{cid}/direction/production/feedback", json={"rating": "good"})
            assert r.status_code == 200


class TestRotation:
    async def test_s2_a_tt_grade_rotates_the_note_data_only(self, no_tts):
        db = _install(_story())
        cid = _minted(db)[SUB]
        anki = _conn()
        await _sync(db, anki)
        scm = _col(anki)[0]
        card_id = _card(anki, db, cid)["id"]

        await _grade_in_tt(cid, 1)
        await _sync(db, anki)

        assert _reps(db, cid) == 1
        note = _note(anki, db, cid)
        assert note["flds"] == _expected(db, cid, reps=1) != _expected(db, cid, reps=0)
        assert note["usn"] == -1 and note["sfld"] == note["flds"].split("\x1f")[0]
        assert _col(anki)[0] == scm
        assert _graves(anki) == []
        assert [r[0] for r in anki.execute("SELECT id FROM cards WHERE nid = ?", (note["id"],))] == [card_id]

    async def test_j2_a_row_graded_in_tt_while_held_mints_at_its_step_with_its_schedule(self, no_tts):
        db = _install(_story())
        cid = _minted(db)[SUB]
        await _grade_in_tt(cid, 2)
        tt = _row(db, cid).directions[Direction.PRODUCTION]
        assert (tt.reps, tt.anki_card_id) == (2, None)
        anki = _conn()

        await _sync(db, anki)

        assert _note(anki, db, cid)["flds"] == _expected(db, cid, reps=2)
        card = _card(anki, db, cid)
        assert card["reps"] == 2 and card["type"] != 0
        assert _reps(db, cid) == 2
        # S5 on this row: a second sync writes nothing to the note
        nid = _note(anki, db, cid)["id"]
        _clean_note(anki, nid)
        before = _note(anki, db, cid)
        assert await _sync(db, anki) == 0
        assert _note(anki, db, cid) == before

    async def test_s3_a_review_made_in_anki_rotates_the_note_in_one_sync(self, no_tts):
        db = SRSDatabase(":memory:")
        cid = _minted(db)[SUB]
        anki = _conn()
        await _sync(db, anki)
        _review_in_anki(anki, _card(anki, db, cid)["id"], reps=3)

        assert await _sync(db, anki) == 1

        assert _reps(db, cid) == 3
        assert _note(anki, db, cid)["flds"] == _expected(db, cid, reps=3)

    async def test_s4_an_old_step_put_back_on_the_phone_is_repaired_in_one_sync(self, no_tts):
        db = SRSDatabase(":memory:")
        cid = _minted(db)[SUB]
        anki = _conn()
        await _sync(db, anki)
        nid = _note(anki, db, cid)["id"]
        anki.execute("UPDATE notes SET flds = ?, usn = 7 WHERE id = ?", (_expected(db, cid, reps=2), nid))
        anki.commit()
        assert db.get_dirty_fields(_row(db, cid).guid) == ""  # nothing flagged in TT

        assert await _sync(db, anki) == 1

        assert _note(anki, db, cid)["flds"] == _expected(db, cid, reps=0)

    async def test_j4_an_anki_forget_puts_the_note_back_to_step_0_in_one_sync(self, no_tts):
        db = SRSDatabase(":memory:")
        cid = _minted(db)[SUB]
        anki = _conn()
        await _sync(db, anki)
        card_id = _card(anki, db, cid)["id"]
        _review_in_anki(anki, card_id, reps=3)
        await _sync(db, anki)
        assert _note(anki, db, cid)["flds"] == _expected(db, cid, reps=3)
        _forget_in_anki(anki, card_id)

        await _sync(db, anki)

        assert _reps(db, cid) == 0
        assert _note(anki, db, cid)["flds"] == _expected(db, cid, reps=0)

    async def test_s5_a_sync_with_nothing_changed_writes_nothing(self, no_tts):
        db = SRSDatabase(":memory:")
        minted = _minted(db)
        anki = _conn()
        await _sync(db, anki)
        for cid in minted.values():
            _clean_note(anki, _row(db, cid).anki_note_id)
        before = [_note(anki, db, cid) for cid in minted.values()]
        col_mod = anki.execute("SELECT mod FROM col").fetchone()[0]

        assert await _sync(db, anki) == 0

        assert [_note(anki, db, cid) for cid in minted.values()] == before
        assert anki.execute("SELECT mod FROM col").fetchone()[0] == col_mod

    async def test_j3_a_question_and_answer_step_card_never_rotates(self, no_tts):
        db = SRSDatabase(":memory:")
        cid = _minted(db)[QA]
        anki = _conn()
        await _sync(db, anki)
        nid = _row(db, cid).anki_note_id
        _clean_note(anki, nid)
        before = _note(anki, db, cid)
        assert _rotate(db, anki) == 0  # reps 0
        _review_in_anki(anki, _card(anki, db, cid)["id"], reps=3)

        await _sync(db, anki)

        assert _reps(db, cid) == 3
        assert _note(anki, db, cid) == before

    async def test_s6_a_row_that_lost_its_card_link_is_relinked_without_reading_text(self, no_tts, caplog):
        db = SRSDatabase(":memory:")
        cid = _minted(db)[SUB]
        anki = _conn()
        await _sync(db, anki)
        card_id = _card(anki, db, cid)["id"]
        nid = _row(db, cid).anki_note_id
        anki.execute("UPDATE notes SET flds = ? WHERE id = ?", (_expected(db, cid, reps=2), nid))
        anki.commit()
        with db._get_conn() as conn:
            conn.execute("UPDATE collocation_directions SET anki_card_id = NULL WHERE collocation_id = ?", (cid,))
            conn.commit()

        with caplog.at_level(logging.INFO):
            await _sync(db, anki)

        assert _row(db, cid).directions[Direction.PRODUCTION].anki_card_id == card_id
        assert _note(anki, db, cid)["flds"] == _expected(db, cid, reps=0)
        assert "SENTENCE_NOTE_HELD" not in caplog.text
        assert _graves(anki) == []

    async def test_s7_a_dry_run_counts_and_writes_nothing(self, no_tts):
        db = SRSDatabase(":memory:")
        cid = _minted(db)[SUB]
        anki = _conn()
        await _sync(db, anki)
        _review_in_anki(anki, _card(anki, db, cid)["id"], reps=3)
        records = OfflineReader(anki, DECK, language_code=LANG).get_note_records()
        AnkiSync(db=db, _reader=RecordsReader(records), _writer=OfflineWriter(anki), language_code=LANG).sync_pull()
        before = _note(anki, db, cid)

        assert _rotate(db, anki, dry_run=True) == 1

        assert _note(anki, db, cid) == before
        assert _rotate(db, anki) == 1  # control: the same state, not dry, writes
        assert _note(anki, db, cid)["flds"] == _expected(db, cid, reps=3)

    async def test_s8_a_note_not_in_this_collection_is_skipped(self, no_tts):
        db = SRSDatabase(":memory:")
        minted = _minted(db)
        anki = _conn()
        await _sync(db, anki)
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET anki_note_id = 999 WHERE id = ?", (minted[SUB],))
            conn.commit()
        other = minted["sent:test-01:drill-2"]
        anki.execute("UPDATE notes SET flds = 'x' || flds WHERE id = ?", (_row(db, other).anki_note_id,))
        anki.commit()

        assert _rotate(db, anki) == 1  # control: the drill whose note is here is written

        assert _note(anki, db, other)["flds"] == _expected(db, other)

    def test_a_drill_row_with_no_note_yet_is_left_to_the_mint(self):
        db = SRSDatabase(":memory:")
        _minted(db)
        anki = _conn()
        assert _rotate(db, anki) == 0
        assert anki.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 0

    async def test_a_drill_whose_note_cannot_be_built_is_held_and_the_rest_rotate(self, no_tts, caplog):
        db = SRSDatabase(":memory:")
        minted = _minted(db)
        anki = _conn()
        await _sync(db, anki)
        bad, good = minted[SUB], minted["sent:test-01:drill-2"]
        with db._get_conn() as conn:
            conn.execute(
                "UPDATE collocations SET card_data = replace(card_data, 'Hun er her.', '{{c2::Hun}} er her.') "
                "WHERE id = ?",
                (bad,),
            )
            conn.commit()
        for cid in (bad, good):
            anki.execute("UPDATE notes SET flds = 'x' || flds WHERE id = ?", (_row(db, cid).anki_note_id,))
        anki.commit()
        bad_before = _note(anki, db, bad)

        with caplog.at_level(logging.INFO):
            assert _rotate(db, anki) == 1

        assert _note(anki, db, bad) == bad_before
        assert f"SENTENCE_NOTE_HELD line={SUB}" in caplog.text and "cloze syntax" in caplog.text
        assert _note(anki, db, good)["flds"] == _expected(db, good)  # control: the rest of the phase ran

    async def test_each_write_is_logged(self, no_tts, caplog):
        db = SRSDatabase(":memory:")
        cid = _minted(db)[SUB]
        anki = _conn()
        await _sync(db, anki)
        _review_in_anki(anki, _card(anki, db, cid)["id"], reps=5)

        with caplog.at_level(logging.INFO):
            await _sync(db, anki)

        assert f"DRILL_ROTATED key={SUB} step=1" in caplog.text


# --------------------------------------------------------------------------
# J6: media per write
# --------------------------------------------------------------------------


class TestMedia:
    def _attach(self, db: SRSDatabase, cid: int, kinds: list[str], on_disk: set[str]) -> None:
        import app.plugins.anki_sync.sync as sync_mod

        for kind in kinds:
            name = f"{kind}.mp3"
            if kind in on_disk:
                (sync_mod._MEDIA_DIR / name).write_bytes(name.encode())
            db.add_media(
                collocation_id=cid,
                kind=kind,
                filename=name,
                path=f"media/{name}",
                anki_filename=name,
                sha256="x",
                size_bytes=1,
            )

    async def test_the_steps_takes_reach_the_note_and_the_media_folder(self, tmp_path, no_tts):
        anki_media = tmp_path / "anki-media"
        anki_media.mkdir()
        db = SRSDatabase(":memory:")
        cid = _minted(db)[SUB]
        kinds = ["drill_model", "drill_cue_0", "drill_answer_0"]
        self._attach(db, cid, kinds, set(kinds))
        anki = _conn()

        await AnkiSync(
            db=db, _reader=FakeReader(), _writer=OfflineWriter(anki, media_dir=anki_media), language_code=LANG
        ).sync_create_new(deck_name=DECK, model_name="Slovene Vocabulary")

        assert _note(anki, db, cid)["flds"] == _expected(db, cid, media={k: f"{k}.mp3" for k in kinds})
        assert sorted(p.name for p in anki_media.iterdir()) == sorted(f"{k}.mp3" for k in kinds)

    async def test_j6_a_clip_missing_from_disk_writes_the_fields_without_it_and_the_sync_goes_on(
        self, tmp_path, no_tts, caplog
    ):
        anki_media = tmp_path / "anki-media"
        anki_media.mkdir()
        db = SRSDatabase(":memory:")
        minted = _minted(db)
        cid = minted[SUB]
        kinds = ["drill_model", "drill_cue_0", "drill_answer_0"]
        self._attach(db, cid, kinds, {"drill_model", "drill_answer_0"})
        anki = _conn()

        with caplog.at_level(logging.INFO):
            await AnkiSync(
                db=db, _reader=FakeReader(), _writer=OfflineWriter(anki, media_dir=anki_media), language_code=LANG
            ).sync_create_new(deck_name=DECK, model_name="Slovene Vocabulary")

        media = {"drill_model": "drill_model.mp3", "drill_answer_0": "drill_answer_0.mp3"}
        assert _note(anki, db, cid)["flds"] == _expected(db, cid, media=media)
        assert f"DRILL_CARD_NO_AUDIO key={SUB} part=drill_cue_0" in caplog.text
        # the sync went on: every other drill row has its note too
        assert all(_row(db, c).anki_note_id is not None for c in minted.values())

    async def test_rotation_copies_the_new_steps_takes(self, tmp_path, no_tts):
        anki_media = tmp_path / "anki-media"
        anki_media.mkdir()
        db = SRSDatabase(":memory:")
        cid = _minted(db)[SUB]
        kinds = ["drill_model", "drill_answer_0", "drill_cue_1", "drill_answer_1"]
        self._attach(db, cid, kinds, set(kinds))
        anki = _conn()
        await _sync(db, anki)
        _review_in_anki(anki, _card(anki, db, cid)["id"], reps=1)
        records = OfflineReader(anki, DECK, language_code=LANG).get_note_records()
        AnkiSync(db=db, _reader=RecordsReader(records), _writer=OfflineWriter(anki), language_code=LANG).sync_pull()

        n = AnkiSync(
            db=db, _reader=FakeReader(), _writer=OfflineWriter(anki, media_dir=anki_media), language_code=LANG
        ).rotate_drill_notes()

        assert n == 1
        assert sorted(p.name for p in anki_media.iterdir()) == [
            "drill_answer_0.mp3",
            "drill_answer_1.mp3",
            "drill_cue_1.mp3",
        ]
