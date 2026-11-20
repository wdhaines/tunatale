"""A corrected lesson card is updated in place, never minted twice (tunatale-adxy, steps 2-3).

Design: the bead's design field (Fable, 2026-11-16), amendments A1-A5 and
oracle rows E1, E6-E10, E13, E14 here; U7 is the unchanged re-mint.

A lesson card's identity is its key (``sent:<set>:...``), so the mint finds an
existing card by key. A sentence line is updated as one unit: every row of its
sibling group, the blanks the learner mined included, in one transaction or
none. A row's text, translation, sentence and ``card_data`` follow the card; its
guid stays as minted. Anything the update cannot place holds the whole line:
nothing written, one loud line, the rest of the mint goes on.
"""

from __future__ import annotations

import copy
import logging

import pytest

from app.srs.database import SRSDatabase
from app.srs.drill_card import drill_card
from app.srs.lesson_card_mint import mint_lesson_cards
from app.srs.sentence_card import sentence_card
from app.srs.sentence_mining import add_blank
from tests.test_anki_drill_card_sync import _col, _conn, _expected, _graves, _note, _row, _sync
from tests.test_drill_card import DRILLS
from tests.test_lesson_card_list import _cards as _sentence_cards
from tests.test_lesson_drill_cards import _cards as _drill_cards

LANG = "no"
LINE2 = "sent:test-01:2"
SUB = "sent:test-01:drill-1"


@pytest.fixture
def no_tts(monkeypatch):
    """The TTS boundary: a plain cloze's audio fails open, as it does with Azure down. Lesson cards never reach it."""
    import app.audio.cloze_tts as cloze_tts_mod

    async def _down(text, voice=None):
        raise RuntimeError("TTS down")

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _down)


def _rows(db: SRSDatabase) -> list[dict]:
    with db._get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM collocations ORDER BY id")]


def _by_key(db: SRSDatabase) -> dict[str, dict]:
    return {r["disambig_key"]: r for r in _rows(db)}


def _line2(cards: dict, **changes) -> dict:
    """*cards* with line 2's item changed."""
    out = copy.deepcopy(cards)
    item = next(i for i in out["items"] if i.get("line") == 2)
    item.update(changes)
    return out


def _sentence(row: dict):
    from app.srs.sentence_card import SentenceCard

    return SentenceCard.from_json(row["card_data"])


# --------------------------------------------------------------------------
# U7: an unchanged re-mint writes nothing
# --------------------------------------------------------------------------


class TestUnchanged:
    def test_u7_the_same_list_again_writes_nothing(self, caplog):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        before = _rows(db)
        with caplog.at_level(logging.INFO):
            assert mint_lesson_cards(db, LANG, _sentence_cards()) == []
        assert _rows(db) == before
        assert "LESSON_CARD_UPDATED" not in caplog.text

    def test_u7_the_same_drills_again_write_nothing(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _drill_cards(), drills=DRILLS)
        before = _rows(db)
        assert mint_lesson_cards(db, LANG, _drill_cards(), drills=DRILLS) == []
        assert _rows(db) == before


# --------------------------------------------------------------------------
# E1, A2: a drill corrected in the lesson
# --------------------------------------------------------------------------


def _drills(**sub_changes) -> list[dict]:
    out = copy.deepcopy(DRILLS)
    out[0].update(sub_changes)
    return out


class TestDrillCorrected:
    def test_e1_a_corrected_model_updates_the_one_row_and_keeps_its_guid(self, caplog):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _drill_cards(), drills=DRILLS)
        before = _by_key(db)[SUB]

        with caplog.at_level(logging.INFO):
            assert mint_lesson_cards(db, LANG, _drill_cards(), drills=_drills(model="Han er her nå.")) == []

        rows = [r for r in _rows(db) if r["disambig_key"] == SUB]
        assert len(rows) == 1
        after = rows[0]
        assert (after["id"], after["guid"]) == (before["id"], before["guid"])
        from app.srs.drill_card import DrillCard

        card = DrillCard.from_json(after["card_data"])
        assert card.view(0).before == "Han er her nå."
        assert after["text"] == "Han er her nå."  # A2: the row's text follows the card
        assert after["source_sentence"] == "{{c1::Han er her nå.}}"
        assert f"LESSON_CARD_UPDATED key={SUB}" in caplog.text

    def test_a_corrected_step_answer_reaches_the_row(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _drill_cards(), drills=DRILLS)
        steps = copy.deepcopy(DRILLS[0]["steps"])
        steps[0]["answer"] = "Hun er her nå."
        mint_lesson_cards(db, LANG, _drill_cards(), drills=_drills(steps=steps))
        from app.srs.drill_card import DrillCard

        assert DrillCard.from_json(_by_key(db)[SUB]["card_data"]).view(0).answer == "Hun er her nå."

    def test_an_unlinked_drill_row_is_not_flagged(self):
        """Unlinked: the next sync mints from the corrected card_data; nothing to flag (A4)."""
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _drill_cards(), drills=DRILLS)
        mint_lesson_cards(db, LANG, _drill_cards(), drills=_drills(model="Han er her nå."))
        assert (_by_key(db)[SUB]["dirty_fields"] or "") == ""

    def test_e13_a_drill_that_loses_its_last_step_drops_that_steps_clips(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _drill_cards(), drills=DRILLS)
        cid = _by_key(db)[SUB]["id"]
        for kind in ("drill_model", "drill_cue_0", "drill_answer_0", "drill_cue_3", "drill_answer_3"):
            db.add_media(cid, kind, f"{kind}.mp3", f"media/{kind}.mp3", f"{kind}.mp3", "x", 1)

        mint_lesson_cards(db, LANG, _drill_cards(), drills=_drills(steps=copy.deepcopy(DRILLS[0]["steps"][:3])))

        card = drill_card(db.get_collocation_by_id(cid)[1].syntactic_unit)
        assert len(card.steps) == 3
        assert card.view(3).step == 0  # in_review mod 3: a valid step, nothing stored
        assert not db.has_media_row(cid, "drill_cue_3")
        assert not db.has_media_row(cid, "drill_answer_3")
        # control: a clip whose text did not change stays
        assert db.has_media_row(cid, "drill_model")
        assert db.has_media_row(cid, "drill_cue_0")

    def test_a_clip_whose_text_changed_is_dropped(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _drill_cards(), drills=DRILLS)
        cid = _by_key(db)[SUB]["id"]
        for kind in ("drill_model", "drill_cue_0"):
            db.add_media(cid, kind, f"{kind}.mp3", f"media/{kind}.mp3", f"{kind}.mp3", "x", 1)
        mint_lesson_cards(db, LANG, _drill_cards(), drills=_drills(model="Han er her nå."))
        assert not db.has_media_row(cid, "drill_model")
        assert db.has_media_row(cid, "drill_cue_0")

    def test_e14_a_question_and_answer_drill_that_loses_its_last_step_orphans_that_row(self, caplog):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _drill_cards(), drills=DRILLS)
        before = _by_key(db)
        qa = copy.deepcopy(DRILLS)
        qa[3]["steps"] = qa[3]["steps"][:2]
        qa[3]["steps"][0]["answer"] = "Han er her nå."

        with caplog.at_level(logging.INFO):
            mint_lesson_cards(db, LANG, _drill_cards(), drills=qa)

        after = _by_key(db)
        assert after["sent:test-01:drill-4:3"] == before["sent:test-01:drill-4:3"]
        assert caplog.text.count("LESSON_CARD_ORPHANED key=sent:test-01:drill-4:3") == 1
        from app.srs.drill_card import DrillCard

        for key in ("sent:test-01:drill-4:1", "sent:test-01:drill-4:2"):
            card = DrillCard.from_json(after[key]["card_data"])
            assert len(card.steps) == 2
            assert card.steps[0].answer == "Han er her nå."
        assert len(_rows(db)) == len(before)

    def test_an_unreadable_drill_row_holds_its_key(self, caplog):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _drill_cards(), drills=DRILLS)
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET card_data = '{}' WHERE disambig_key = ?", (SUB,))
            conn.commit()
        before = _rows(db)
        with caplog.at_level(logging.INFO):
            mint_lesson_cards(db, LANG, _drill_cards(), drills=_drills(model="Han er her nå."))
        assert _rows(db) == before
        assert f"LESSON_LINE_UPDATE_HELD line={SUB} reason=the row's card_data is not a drill card" in caplog.text

    def test_a_drill_key_already_duplicated_is_left_alone(self, caplog):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _drill_cards(), drills=DRILLS)
        with db._get_conn() as conn:
            conn.execute("CREATE TEMP TABLE dup AS SELECT * FROM collocations WHERE disambig_key = ?", (SUB,))
            conn.execute("UPDATE dup SET id = NULL, guid = 'dup', text = text || ' (old)'")
            conn.execute("INSERT INTO collocations SELECT * FROM dup")
            conn.commit()
        before = _rows(db)
        with caplog.at_level(logging.INFO):
            mint_lesson_cards(db, LANG, _drill_cards(), drills=_drills(model="Han er her nå."))
        assert _rows(db) == before
        assert f"LESSON_LINE_UPDATE_HELD line={SUB}" in caplog.text


# --------------------------------------------------------------------------
# A1, A2, E6-E9: a sentence line corrected in the lesson
# --------------------------------------------------------------------------


class TestSentenceCorrected:
    def test_a2_an_english_correction_reaches_every_row_of_the_line(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        before = _by_key(db)
        mint_lesson_cards(db, LANG, _line2(_sentence_cards(), english="Hi, how is it going?"))
        after = _by_key(db)
        for key in (LINE2, "sent:test-01:2:13-16"):
            assert after[key]["translation"] == "Hi, how is it going?"
            assert _sentence(after[key]).english == "Hi, how is it going?"
            assert (after[key]["id"], after[key]["guid"]) == (before[key]["id"], before[key]["guid"])
        # control: another line is untouched by a byte
        assert after["sent:test-01:3:5-9"] == before["sent:test-01:3:5-9"]

    def test_e6_a_word_before_a_blank_corrected_moves_the_blank_with_it(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        before = _by_key(db)
        old_blank = before["sent:test-01:2:13-16"]

        mint_lesson_cards(db, LANG, _line2(_sentence_cards(), text="Hei du, hvordan går det?"))

        after = _by_key(db)
        assert len(_rows(db)) == len(before)
        moved = after["sent:test-01:2:16-19"]
        assert moved["id"] == old_blank["id"]
        assert moved["guid"] == old_blank["guid"]
        card = _sentence(moved)
        assert card.text[card.blank[0] : card.blank[1]] == "går"
        assert moved["text"] == "Hei du, hvordan går det?"
        assert moved["source_sentence"] == "Hei du, hvordan {{c1::går}} det?"
        assert after[LINE2]["text"] == "Hei du, hvordan går det?"

    def test_e7_a_mined_blank_is_re_anchored(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        mined = add_blank(db, LANG, LINE2, (17, 20), "it")  # "det"

        mint_lesson_cards(db, LANG, _line2(_sentence_cards(), text="Hei du, hvordan går det?"))

        row = next(r for r in _rows(db) if r["id"] == mined)
        card = _sentence(row)
        assert row["disambig_key"] == "sent:test-01:2:20-23"
        assert card.text[card.blank[0] : card.blank[1]] == "det"
        assert card.blank_gloss == "it"
        assert row["source"] == "user"  # a mined blank keeps its place at the front

    def test_e8_the_blanked_word_itself_corrected_keeps_its_row(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        old = _by_key(db)["sent:test-01:2:13-16"]

        mint_lesson_cards(
            db,
            LANG,
            _line2(_sentence_cards(), text="Hei, hvordan står det?", blanks=[{"form": "står", "gloss": "stands"}]),
        )

        row = next(r for r in _rows(db) if r["id"] == old["id"])
        card = _sentence(row)
        assert row["disambig_key"] == "sent:test-01:2:13-17"
        assert card.text[card.blank[0] : card.blank[1]] == "står"
        assert card.blank_gloss == "stands"
        assert len([r for r in _rows(db) if (r["sibling_group"] or "") == LINE2]) == 2

    def test_e9_a_mined_blank_whose_word_is_gone_holds_the_line(self, caplog):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        add_blank(db, LANG, LINE2, (17, 20), "it")  # "det"
        before = _rows(db)
        cards = _line2(_sentence_cards(), text="Hei, hvordan går du?")
        line3 = next(i for i in cards["items"] if i.get("line") == 3)
        line3["english"] = "Good, thanks, and you?"

        with caplog.at_level(logging.INFO):
            assert mint_lesson_cards(db, LANG, cards) == []

        line2_before = [r for r in before if r["sibling_group"] == LINE2]
        line2_after = [r for r in _rows(db) if r["sibling_group"] == LINE2]
        assert line2_after == line2_before
        assert f"LESSON_LINE_UPDATE_HELD line={LINE2}" in caplog.text
        # control: another line of the same lesson still updates
        assert _by_key(db)["sent:test-01:3:5-9"]["translation"] == "Good, thanks, and you?"

    def test_a_list_blank_with_no_counterpart_holds_the_line(self, caplog):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        before = _rows(db)
        cards = _line2(_sentence_cards(), blanks=[])  # the blank dropped from the list
        with caplog.at_level(logging.INFO):
            mint_lesson_cards(db, LANG, cards)
        assert _rows(db) == before
        assert f"LESSON_LINE_UPDATE_HELD line={LINE2}" in caplog.text

    def test_a_new_blank_in_the_list_is_minted_after_the_update(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        cards = _line2(
            _sentence_cards(),
            english="Hi, how is it going?",
            blanks=[{"form": "går", "gloss": "_goes_ (å gå)"}, {"form": "det", "gloss": "it"}],
        )
        minted = mint_lesson_cards(db, LANG, cards)
        assert [c.unit.disambig_key for _, c in minted] == ["sent:test-01:2:17-20"]
        new = _by_key(db)["sent:test-01:2:17-20"]
        assert new["translation"] == "Hi, how is it going?"
        assert _by_key(db)[LINE2]["translation"] == "Hi, how is it going?"

    def test_a_legacy_row_holds_its_line(self, caplog):
        """A sent: row with no sibling group is on the old one-note-per-card shape: not a line the update can see."""
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET sibling_group = NULL WHERE disambig_key = ?", (LINE2,))
            conn.commit()
        before = _rows(db)
        with caplog.at_level(logging.INFO):
            assert mint_lesson_cards(db, LANG, _line2(_sentence_cards(), english="Hi, how is it going?")) == []
        assert _rows(db) == before
        assert f"LESSON_LINE_UPDATE_HELD line={LINE2}" in caplog.text

    def test_a_line_with_an_unreadable_row_is_held(self, caplog):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET card_data = '{}' WHERE disambig_key = ?", (LINE2,))
            conn.commit()
        before = _rows(db)
        with caplog.at_level(logging.INFO):
            mint_lesson_cards(db, LANG, _line2(_sentence_cards(), english="Hi, how is it going?"))
        assert _rows(db) == before
        assert f"LESSON_LINE_UPDATE_HELD line={LINE2} reason=a row of the line is not a sentence card" in caplog.text

    def test_e10_a_line_partly_linked_is_held(self, caplog):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        whole = _by_key(db)[LINE2]
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET anki_note_id = 77 WHERE id = ?", (whole["id"],))
            conn.commit()
        before = _rows(db)
        with caplog.at_level(logging.INFO):
            mint_lesson_cards(db, LANG, _line2(_sentence_cards(), english="Hi, how is it going?"))
        assert _rows(db) == before
        assert f"LESSON_LINE_UPDATE_HELD line={LINE2}" in caplog.text

    def test_a_linked_line_is_flagged_for_the_push(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET anki_note_id = 77 WHERE sibling_group = ?", (LINE2,))
            conn.commit()
        mint_lesson_cards(db, LANG, _line2(_sentence_cards(), english="Hi, how is it going?"))
        for r in _rows(db):
            if r["sibling_group"] == LINE2:
                assert "card_data" in (r["dirty_fields"] or "").split(",")

    def test_a_spoken_correction_drops_the_lines_answer_take_on_every_row(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        mined = add_blank(db, LANG, LINE2, (17, 20), "it")
        ids = [r["id"] for r in _rows(db) if r["sibling_group"] == LINE2]
        for cid in ids:
            db.add_media(cid, "audio_tts_sentence", "a.mp3", "media/a.mp3", "a.mp3", "x", 1)
            db.add_media(cid, "audio_cue", "c.mp3", "media/c.mp3", "c.mp3", "x", 1)
        assert mined in ids

        mint_lesson_cards(db, LANG, _line2(_sentence_cards(), spoken="Hei, hvordan gaar det?"))

        for cid in ids:
            assert not db.has_media_row(cid, "audio_tts_sentence")
            assert db.has_media_row(cid, "audio_cue")  # control: the cue did not change

    def test_an_english_only_correction_keeps_the_takes(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        cid = _by_key(db)[LINE2]["id"]
        db.add_media(cid, "audio_tts_sentence", "a.mp3", "media/a.mp3", "a.mp3", "x", 1)
        mint_lesson_cards(db, LANG, _line2(_sentence_cards(), english="Hi, how is it going?"))
        assert db.has_media_row(cid, "audio_tts_sentence")

    def test_a_cue_correction_drops_the_cue_take(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        cid = _by_key(db)[LINE2]["id"]
        db.add_media(cid, "audio_tts_sentence", "a.mp3", "media/a.mp3", "a.mp3", "x", 1)
        db.add_media(cid, "audio_cue", "c.mp3", "media/c.mp3", "c.mp3", "x", 1)
        cards = _sentence_cards()
        item = next(i for i in cards["items"] if i.get("line") == 2)
        item["cue"]["spoken"] = "Hei hei!"
        mint_lesson_cards(db, LANG, cards)
        assert not db.has_media_row(cid, "audio_cue")
        assert db.has_media_row(cid, "audio_tts_sentence")


def test_a_sentence_card_parses_from_its_row():
    """Guard for the helper above: the row's card_data is a sentence card."""
    db = SRSDatabase(":memory:")
    mint_lesson_cards(db, LANG, _sentence_cards())
    assert sentence_card(db.get_collocation_by_id(_by_key(db)[LINE2]["id"])[1].syntactic_unit) is not None


# --------------------------------------------------------------------------
# A3, E11, E12: the takes follow the corrected line
# --------------------------------------------------------------------------


def _lesson(cache, line2: str = "Hei, hvordan gar det?", *, write: bool = True, story: dict | None = None):
    """The story's lines as a rendered lesson says them (Kari = NB_B, Per = NB_A); takes written when *write*."""
    from app.models.lesson import Lesson, Phrase, Section, SectionType
    from tests.test_lesson_take import NB_A, NB_B

    lesson = Lesson(
        title="Hei",
        language_code="no",
        sections=[
            Section(
                SectionType.NATURAL_SPEED,
                [
                    Phrase("Hei!", NB_B, "no", role="female-1"),
                    Phrase(line2, NB_A, "no", role="male-1"),
                    Phrase("Bra, takk, og du?", NB_B, "no", role="female-1"),
                ],
            )
        ],
        generation_metadata={"story": story} if story is not None else {},
    )
    if write:
        _write_takes(lesson, cache)
    return lesson


def _write_takes(lesson, cache) -> None:
    from tests.test_lesson_take import _instrument_paths

    cache.mkdir(parents=True, exist_ok=True)
    for (text, voice, enunciated), path in _instrument_paths(lesson, cache).items():
        if not enunciated:
            path.write_bytes(f"{text}|{voice}".encode())


def _attach(db, cards, lesson, cache, media, voice_map) -> None:
    from app.srs.lesson_card_mint import attach_lesson_card_audio, lesson_card_rows

    attach_lesson_card_audio(db, lesson_card_rows(db, cards), lesson, voice_map, cache_dir=cache, media_dir=media)


NEW_SPOKEN = "Hei, hvordan gar det naa?"


class TestTakes:
    def _setup(self, tmp_path, language_no):
        db = SRSDatabase(":memory:")
        cache, media = tmp_path / "cache", tmp_path / "media"
        media.mkdir()
        mint_lesson_cards(db, LANG, _sentence_cards())
        mined = add_blank(db, LANG, LINE2, (17, 20), "it")
        _attach(db, _sentence_cards(), _lesson(cache), cache, media, language_no.tts_voice_map)
        return db, cache, media, mined

    def test_a_mined_blank_gets_the_lines_takes(self, tmp_path, language_no):
        db, _cache, media, mined = self._setup(tmp_path, language_no)
        whole = _by_key(db)[LINE2]["id"]
        assert db.get_sentence_audio_filename(mined) == db.get_sentence_audio_filename(whole) is not None
        assert db.get_media_filename(mined, "audio_cue") == db.get_media_filename(whole, "audio_cue") is not None

    def test_e11_a_corrected_spoken_line_gets_its_new_take_on_every_row(self, tmp_path, language_no):
        db, cache, media, mined = self._setup(tmp_path, language_no)
        ids = [r["id"] for r in _rows(db) if r["sibling_group"] == LINE2]
        old = db.get_sentence_audio_filename(ids[0])
        cue = db.get_media_filename(ids[0], "audio_cue")
        cards = _line2(_sentence_cards(), spoken=NEW_SPOKEN)

        mint_lesson_cards(db, LANG, cards)
        _attach(db, cards, _lesson(cache, NEW_SPOKEN), cache, media, language_no.tts_voice_map)

        for cid in ids:
            name = db.get_sentence_audio_filename(cid)
            assert name != old
            assert (media / name).read_bytes().startswith(NEW_SPOKEN.encode())
            assert db.get_media_filename(cid, "audio_cue") == cue  # the cue did not change
        with db._get_conn() as conn:  # line 3's cue is line 2's old line, so only line 2's answers are checked
            stale = conn.execute(
                f"SELECT COUNT(*) FROM media WHERE filename = ? AND kind = 'audio_tts_sentence' "
                f"AND collocation_id IN ({','.join('?' * len(ids))})",
                (old, *ids),
            ).fetchone()[0]
        assert stale == 0
        assert mined in ids

    def test_e12_no_take_yet_leaves_the_card_silent_until_one_arrives(self, tmp_path, language_no, caplog):
        db, cache, media, _mined = self._setup(tmp_path, language_no)
        whole = _by_key(db)[LINE2]["id"]
        cards = _line2(_sentence_cards(), spoken=NEW_SPOKEN)
        mint_lesson_cards(db, LANG, cards)

        with caplog.at_level(logging.INFO):
            _attach(db, cards, _lesson(cache), cache, media, language_no.tts_voice_map)  # not rendered yet

        assert db.get_sentence_audio_filename(whole) is None
        assert "SENTENCE_CARD_NO_TAKE key=sent:test-01:2" in caplog.text

        _attach(db, cards, _lesson(cache, NEW_SPOKEN), cache, media, language_no.tts_voice_map)
        assert (media / db.get_sentence_audio_filename(whole)).read_bytes().startswith(NEW_SPOKEN.encode())

    def test_lesson_card_rows_names_every_row_the_list_has(self):
        from app.srs.lesson_card_mint import lesson_card_rows

        db = SRSDatabase(":memory:")
        minted = mint_lesson_cards(db, LANG, _sentence_cards())
        sentence_rows = [(cid, c) for cid, c in minted if c.unit.disambig_key]
        assert [cid for cid, _ in lesson_card_rows(db, _sentence_cards())] == [cid for cid, _ in sentence_rows]


class TestListenAttachesEveryLessonRow:
    """A3's unconfirmed claim, run: a card minted before its lesson was rendered got no
    take from any later listen, because the listen attached only the rows it added."""

    async def test_a_card_minted_before_its_render_gets_its_take_at_the_next_listen(self):
        from httpx import ASGITransport, AsyncClient

        import app.api.srs as srs_api
        from app.config import settings
        from app.main import app
        from app.storage.store import ContentStore
        from tests.test_lesson_card_list import _story

        lesson = _lesson(settings.tts_cache_dir, write=False, story=_story())
        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"content_id": "lesson-1"})
            whole = _by_key(db)[LINE2]["id"]
            assert db.get_sentence_audio_filename(whole) is None  # not rendered yet

            _write_takes(lesson, settings.tts_cache_dir)  # the render
            resp = await client.post("/api/srs/listen", json={"content_id": "lesson-1"})

        assert resp.json()["created"] == 0
        name = db.get_sentence_audio_filename(whole)
        assert name is not None
        assert (srs_api._MEDIA_DIR / name).exists()


# --------------------------------------------------------------------------
# Step 5: through sync (E2, E3, E5, E6, E8, E10, E11) and on the Cards list (E15)
# --------------------------------------------------------------------------


def _flds(anki, db, cid) -> str:
    return _note(anki, db, cid)["flds"]


def _ord(anki, card_id: int) -> int:
    return anki.execute("SELECT ord FROM cards WHERE id = ?", (card_id,)).fetchone()[0]


def _card_id(db, cid) -> int:
    from app.models.srs_item import Direction

    return _row(db, cid).directions[Direction.PRODUCTION].anki_card_id


class TestThroughSync:
    async def test_e2_e5_a_corrected_step_reaches_its_note_and_a_second_sync_writes_nothing(self, no_tts):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _drill_cards(), drills=DRILLS)
        anki = _conn()
        await _sync(db, anki)
        cid = _by_key(db)[SUB]["id"]
        nid = _row(db, cid).anki_note_id
        scm = _col(anki)[0]
        steps = copy.deepcopy(DRILLS[0]["steps"])
        steps[0]["answer"] = "Hun er her nå."

        mint_lesson_cards(db, LANG, _drill_cards(), drills=_drills(steps=steps))
        await _sync(db, anki)

        note = _note(anki, db, cid)
        assert note["id"] == nid
        assert note["flds"] == _expected(db, cid)
        assert "Hun er her nå." in note["flds"]
        assert note["usn"] == -1
        assert _col(anki)[0] == scm
        assert _graves(anki) == []

        # E5: nothing changed, nothing written
        assert await _sync(db, anki) == 0
        assert _note(anki, db, cid) == note

    async def test_e3_a_corrected_line_reaches_its_note_with_no_hold(self, no_tts, caplog):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        anki = _conn()
        await _sync(db, anki)
        whole = _by_key(db)[LINE2]["id"]
        nid = _row(db, whole).anki_note_id

        with caplog.at_level(logging.INFO):
            mint_lesson_cards(db, LANG, _line2(_sentence_cards(), english="Hi, how is it going?"))
            await _sync(db, anki)

        assert [r["disambig_key"] for r in _rows(db)].count(LINE2) == 1
        assert _row(db, whole).anki_note_id == nid
        assert "Hi, how is it going?" in _flds(anki, db, whole)
        assert "Hi, how are you?" not in _flds(anki, db, whole)
        assert "SENTENCE_NOTE_HELD" not in caplog.text
        assert _graves(anki) == []
        assert (_by_key(db)[LINE2]["dirty_fields"] or "") == ""

    async def test_e6_a_moved_blank_keeps_its_card_and_blanks_the_same_word(self, no_tts):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        anki = _conn()
        await _sync(db, anki)
        blank = _by_key(db)["sent:test-01:2:13-16"]["id"]
        card, ord_ = _card_id(db, blank), _ord(anki, _card_id(db, blank))
        before = _flds(anki, db, blank)

        mint_lesson_cards(db, LANG, _line2(_sentence_cards(), text="Hei du, hvordan går det?"))
        await _sync(db, anki)

        assert _card_id(db, blank) == card
        assert _ord(anki, card) == ord_
        flds = _flds(anki, db, blank)
        assert f"{{{{c{ord_ + 1}::går}}}}" in flds
        assert "Hei du, hvordan" in flds and flds != before
        assert _graves(anki) == []

    async def test_e7_e10_a_mined_blank_linked_then_corrected_keeps_its_card(self, no_tts, caplog):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        anki = _conn()
        await _sync(db, anki)
        mined = add_blank(db, LANG, LINE2, (17, 20), "it")
        corrected = _line2(_sentence_cards(), text="Hei du, hvordan går det?")

        with caplog.at_level(logging.INFO):  # E10: the line is partly linked, so it is held
            mint_lesson_cards(db, LANG, corrected)
        assert f"LESSON_LINE_UPDATE_HELD line={LINE2}" in caplog.text
        assert _by_key(db)[LINE2]["text"] == "Hei, hvordan går det?"

        await _sync(db, anki)  # links the mined blank
        card = _card_id(db, mined)
        mint_lesson_cards(db, LANG, corrected)  # E10: now it updates
        await _sync(db, anki)

        assert _card_id(db, mined) == card
        assert f"{{{{c{_ord(anki, card) + 1}::det}}}}" in _flds(anki, db, mined)
        assert _by_key(db)[LINE2]["text"] == "Hei du, hvordan går det?"

    async def test_e8_the_corrected_blank_word_reaches_the_note(self, no_tts):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _sentence_cards())
        anki = _conn()
        await _sync(db, anki)
        blank = _by_key(db)["sent:test-01:2:13-16"]["id"]
        card = _card_id(db, blank)

        mint_lesson_cards(
            db,
            LANG,
            _line2(_sentence_cards(), text="Hei, hvordan står det?", blanks=[{"form": "står", "gloss": "stands"}]),
        )
        await _sync(db, anki)

        assert _card_id(db, blank) == card
        flds = _flds(anki, db, blank)
        assert f"{{{{c{_ord(anki, card) + 1}::står}}}}" in flds
        assert "stands" in flds

    async def test_e11_the_note_plays_the_corrected_take_after_a_sync(self, no_tts, tmp_path, language_no):
        import app.plugins.anki_sync.sync as sync_mod

        db = SRSDatabase(":memory:")
        cache, media = tmp_path / "cache", sync_mod._MEDIA_DIR
        mint_lesson_cards(db, LANG, _sentence_cards())
        _attach(db, _sentence_cards(), _lesson(cache), cache, media, language_no.tts_voice_map)
        anki = _conn()
        await _sync(db, anki)
        whole = _by_key(db)[LINE2]["id"]
        old = db.get_sentence_audio_filename(whole)
        assert f"[sound:{old}]" in _flds(anki, db, whole)
        cards = _line2(_sentence_cards(), spoken=NEW_SPOKEN)

        mint_lesson_cards(db, LANG, cards)
        _attach(db, cards, _lesson(cache, NEW_SPOKEN), cache, media, language_no.tts_voice_map)
        await _sync(db, anki)

        new = db.get_sentence_audio_filename(whole)
        assert new != old
        assert f"[sound:{new}]" in _flds(anki, db, whole)
        assert f"[sound:{old}]" not in _flds(anki, db, whole)

    async def test_e13_a_drill_that_lost_its_last_step_rotates_onto_a_valid_step(self, no_tts):
        from tests.test_anki_drill_card_sync import GRADUATE_GOOD, REVIEW_GOOD, _answer_in_anki

        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, LANG, _drill_cards(), drills=DRILLS)
        anki = _conn()
        await _sync(db, anki)
        cid = _by_key(db)[SUB]["id"]
        _answer_in_anki(anki, _card_id(db, cid), [GRADUATE_GOOD, REVIEW_GOOD, REVIEW_GOOD])
        await _sync(db, anki)
        assert db.count_answers_into_review(cid) == 3  # step 3 of 4

        mint_lesson_cards(db, LANG, _drill_cards(), drills=_drills(steps=copy.deepcopy(DRILLS[0]["steps"][:3])))
        assert await _sync(db, anki) == 1

        assert _flds(anki, db, cid) == _expected(db, cid, step=0)  # 3 mod 3


class TestCardsList:
    async def test_e15_the_cards_list_shows_the_corrected_text(self):
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        db = SRSDatabase(":memory:")
        app.state.srs_db = db
        mint_lesson_cards(db, LANG, _sentence_cards())
        cid = _by_key(db)[LINE2]["id"]
        mint_lesson_cards(db, LANG, _line2(_sentence_cards(), text="Hei du, hvordan går det?"))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            items = (await client.get("/api/srs/items", params={"limit": 200})).json()["items"]

        row = next(i for i in items if i["id"] == cid)
        assert row["text"] == "Hei du, hvordan går det?"
        assert not any(i["text"] == "Hei, hvordan går det?" for i in items)


# ── tunatale-flqc item 2: _relocate holds the line instead of raising ──────────────────────────────────────────


def _blank_card(text: str, blank: tuple[int, int]):
    from app.srs.sentence_card import SentenceCard

    return SentenceCard(
        lesson_id="L", line_index=0, speaker="A", text=text, spoken=text, english="", glosses=(), blank=blank
    )


def test_relocate_returns_none_when_an_overlapping_match_hides_the_blank():
    """'xabababa' with blank (3, 6) = 'bab': finditer is non-overlapping, so the match at 1 swallows the one at 3
    and the blank's own occurrence is never listed. .index() raised ValueError (a 500 on the listen, the Grade
    save or the import); None holds the line with LESSON_LINE_UPDATE_HELD like any other unmatched row."""
    from app.srs.lesson_card_mint import _relocate

    assert _relocate(_blank_card("xabababa", (3, 6)), "xabababa") is None


def test_relocate_control_finds_the_same_occurrence_in_the_corrected_text():
    from app.srs.lesson_card_mint import _relocate

    # The second 'bab' (non-overlapping) is at 5 in the old text and moves to 6 in the corrected one.
    assert _relocate(_blank_card("xbabxbab", (5, 8)), "xbabxxbab") == (6, 9)
