"""Sentence mining, step 1: add or remove a blank on a line that has cards (tunatale-h0q3.52).

A tapped word becomes one more blank of its line: a new row in the line's
sibling group, which the next sync turns into one more card of the line's "TT
Sentence" note. Removing one deletes the row, and the next push drops its cloze
and graves its card. Both halves of the sync already work (pinned in
``test_anki_sentence_note_sync.py``); what is new here is the rules at the edge
and where a mined card lands.

Owner decisions, 2026-11-10, on the bead:

a. A mined blank goes to the FRONT of the new queue, where TT's other additions
   go, not behind the still-new course cards of its line's course.
b. Removing a blank is free while its card is new; once it has been reviewed,
   only with a confirm, because its card and history go.
d. Spacing is sibling bury only (nothing here holds a mined card back).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.common.guid import compute_guid
from app.models.srs_item import Direction, SRSState
from app.plugins.anki_sync.sync_writer import COURSE_REGION_GAP, COURSE_TAG
from app.srs import sentence_mining as mining
from app.srs.database import SRSDatabase
from app.srs.lesson_cards import COURSE_SOURCE
from app.srs.sentence_card import Cue, Gloss, SentenceCard, blank_span, sentence_card
from tests.test_anki_sentence_note_sync import _cards, _conn, _mint, _notes

LANG = "ceb"
_BASE = SentenceCard(
    lesson_id="wolff-1",
    line_index=4,
    speaker="Ínting",
    text="Nâa ba si Místir Abáya?",
    spoken="Nâa ba si Místir Abáya?",
    english="Is Mr. Abaya there?",
    glosses=(Gloss("ba", "question particle"), Gloss("Místir", "Mister")),
    cue=Cue(speaker="Inday", text="Únsa may átò?", spoken="Únsa may átò?", english="What can I do for you?"),
)
LINE = _BASE.line_key
NAA = blank_span(_BASE.text, "nâa")  # (0, 3)
BA = blank_span(_BASE.text, "ba")
SI = blank_span(_BASE.text, "si")
MISTIR = blank_span(_BASE.text, "místir")


def _course(card: SentenceCard):
    return replace(card.unit(), source=COURSE_SOURCE)


def _line(db: SRSDatabase, *, blanks=((NAA, "there"),), course: bool = True) -> None:
    """The line as a lesson's card list mints it: its blanks, then the whole line."""
    for span, gloss in blanks:
        unit = _BASE.with_blank(span, gloss).unit()
        db.add_collocation(replace(unit, source=COURSE_SOURCE) if course else unit, LANG)
    db.add_collocation(_course(_BASE) if course else _BASE.unit(), LANG)


def _row(db: SRSDatabase, span) -> int | None:
    key = _BASE.with_blank(span, "").key
    return db.get_collocation_id_by_guid(compute_guid(_BASE.text, LANG, key))


class TestPlanBlank:
    def test_a_line_with_no_row_is_not_found(self):
        db = SRSDatabase(":memory:")
        with pytest.raises(mining.LineNotFound):
            mining.plan_blank(db, LINE, BA)

    def test_the_card_is_the_whole_line(self):
        db = SRSDatabase(":memory:")
        _line(db)
        plan = mining.plan_blank(db, LINE, BA)
        assert plan.card == _BASE and plan.existing is None

    def test_the_line_is_found_from_a_blank_row_alone(self):
        """A line whose author listed only blanks (no whole-line card) still has its text."""
        db = SRSDatabase(":memory:")
        db.add_collocation(_course(_BASE.with_blank(NAA, "there")), LANG)
        assert mining.plan_blank(db, LINE, BA).card == _BASE

    @pytest.mark.parametrize(
        "span",
        [
            (1, 3),  # inside a word
            (0, 2),  # a word's start only
            (3, 6),  # leading space
            (4, 7),  # trailing space
            (22, 23),  # the question mark alone
            (5, 5),  # empty
            (6, 4),  # reversed
            (-1, 3),
            (19, 99),
        ],
    )
    def test_a_span_that_is_not_whole_words_is_refused(self, span):
        db = SRSDatabase(":memory:")
        _line(db)
        with pytest.raises(mining.NotWholeWords):
            mining.plan_blank(db, LINE, span)

    def test_two_words_are_whole_words(self):
        db = SRSDatabase(":memory:")
        _line(db)
        assert mining.plan_blank(db, LINE, (BA[0], SI[1])).existing is None

    def test_a_span_overlapping_another_blank_is_refused(self):
        db = SRSDatabase(":memory:")
        _line(db)
        with pytest.raises(mining.BlankOverlaps):
            mining.plan_blank(db, LINE, (NAA[0], BA[1]))

    def test_the_whole_line_card_overlaps_nothing(self):
        db = SRSDatabase(":memory:")
        _line(db, blanks=())
        assert mining.plan_blank(db, LINE, BA).existing is None

    def test_the_same_blank_again_is_the_row_it_already_has(self):
        db = SRSDatabase(":memory:")
        _line(db)
        assert mining.plan_blank(db, LINE, NAA).existing == _row(db, NAA)


class TestOwnGloss:
    def test_the_lines_word_gloss_when_it_has_one(self):
        assert mining.own_gloss(_BASE, BA) == "question particle"

    def test_case_is_ignored(self):
        card = replace(_BASE, glosses=(Gloss("místir", "Mister"),))
        assert mining.own_gloss(card, MISTIR) == "Mister"

    def test_none_for_a_word_the_line_does_not_gloss(self):
        assert mining.own_gloss(_BASE, SI) == ""


class TestAddBlank:
    def test_adds_a_new_unlinked_row_to_the_lines_group(self):
        db = SRSDatabase(":memory:")
        _line(db)

        row_id = mining.add_blank(db, LANG, LINE, BA, "yes/no question particle")

        assert row_id == _row(db, BA)
        guid = compute_guid(_BASE.text, LANG, _BASE.with_blank(BA, "").key)
        item = db.get_collocation_by_guid(guid)
        unit = item.syntactic_unit
        assert unit.card_type == "cloze" and unit.source == "user"
        assert item.anki_note_id is None
        assert set(item.directions) == {Direction.PRODUCTION}
        prod = item.directions[Direction.PRODUCTION]
        assert prod.state == SRSState.NEW and prod.anki_card_id is None and prod.last_review is None
        assert db.sibling_groups()[row_id] == LINE
        card = sentence_card(unit)
        assert card == _BASE.with_blank(BA, "yes/no question particle")

    def test_is_idempotent(self):
        db = SRSDatabase(":memory:")
        _line(db)
        first = mining.add_blank(db, LANG, LINE, BA, "q")
        again = mining.add_blank(db, LANG, LINE, BA, "q")
        assert first == again
        assert len(db.list_sibling_rows(LINE)) == 3

    def test_refuses_what_the_plan_refuses(self):
        db = SRSDatabase(":memory:")
        _line(db)
        with pytest.raises(mining.BlankOverlaps):
            mining.add_blank(db, LANG, LINE, (NAA[0], BA[1]), "q")
        assert len(db.list_sibling_rows(LINE)) == 2

    def test_takes_the_lines_audio_from_a_sibling(self):
        """The takes are the line's own (lesson audio), the same for every card of it."""
        db = SRSDatabase(":memory:")
        _line(db)
        whole = _row(db, None)
        for kind, name in (("audio_tts_sentence", "take_a.mp3"), ("audio_cue", "take_c.mp3")):
            db.add_media(
                collocation_id=whole,
                kind=kind,
                filename=name,
                path=f"media/{name}",
                anki_filename=name,
                sha256="0" * 64,
                size_bytes=3,
            )

        row_id = mining.add_blank(db, LANG, LINE, BA, "q")

        assert db.get_sentence_audio_filename(row_id) == "take_a.mp3"
        assert db.get_media_filename(row_id, "audio_cue") == "take_c.mp3"

    def test_a_line_with_no_takes_adds_no_media(self):
        db = SRSDatabase(":memory:")
        _line(db)
        row_id = mining.add_blank(db, LANG, LINE, BA, "q")
        assert not db.has_media_row(row_id, "audio_tts_sentence")
        assert not db.has_media_row(row_id, "audio_cue")


def _reviewed(db: SRSDatabase, row_id: int, *, due_at: str = "2026-11-02T04:00:00+00:00") -> None:
    with db._get_conn() as conn:
        conn.execute(
            "UPDATE collocation_directions SET state = 'review', reps = 1, "
            "last_review = '2026-11-01T10:00:00+00:00', due_at = ? WHERE collocation_id = ?",
            (due_at, row_id),
        )
        conn.commit()


def _due(anki_conn, db, row_id) -> int:
    item = db.get_collocation_by_id(row_id)[1]
    cid = item.directions[Direction.PRODUCTION].anki_card_id
    return anki_conn.execute("SELECT due FROM cards WHERE id = ?", (cid,)).fetchone()["due"]


def _seed_course_backlog(anki_conn) -> None:
    """A still-new course card from an earlier lesson, so the course region has a tail to queue behind."""
    from app.plugins.anki_sync.sync_writer import OfflineWriter

    OfflineWriter(anki_conn).create_cloze_note("0. Slovene", "{{c1::Kanang}} ...", language_code=LANG, course=True)


class TestMinedCardPlacement:
    """Decision (a): a mined blank is a TT addition, so it goes to the front of the new queue."""

    async def test_a_blank_mined_on_a_minted_course_line_goes_to_the_front(self, no_tts):
        db = SRSDatabase(":memory:")
        anki_conn = _conn()
        _seed_course_backlog(anki_conn)
        _line(db)
        await _mint(db, anki_conn)
        lowest_before = anki_conn.execute("SELECT MIN(due) FROM cards WHERE type = 0").fetchone()[0]

        row_id = mining.add_blank(db, LANG, LINE, BA, "q")
        await _mint(db, anki_conn)

        assert len(_notes(anki_conn)) == 2  # the backlog note and the line's one note
        assert _due(anki_conn, db, row_id) < lowest_before, "DECK gather: the front is the lowest position"

    async def test_course_blanks_minted_with_it_keep_their_course_positions(self, no_tts):
        """Mined at the moment the line itself mints (the listen step): the author's blanks
        still go behind the course backlog, only the mined blank goes to the front."""
        db = SRSDatabase(":memory:")
        anki_conn = _conn()
        _seed_course_backlog(anki_conn)
        backlog_due = anki_conn.execute("SELECT due FROM cards").fetchone()["due"]
        _line(db)
        row_id = mining.add_blank(db, LANG, LINE, BA, "q")

        await _mint(db, anki_conn)

        assert _due(anki_conn, db, _row(db, NAA)) > backlog_due
        assert _due(anki_conn, db, _row(db, None)) > backlog_due
        assert _due(anki_conn, db, row_id) < backlog_due
        tags = anki_conn.execute("SELECT tags FROM notes WHERE id = ?", (_notes(anki_conn)[1][0],)).fetchone()[0]
        assert COURSE_TAG in tags.split()

    async def test_a_removed_blank_leaves_the_note_and_its_card_is_graved(self, no_tts):
        """Mined onto a line already minted (the rewrite path): tagged at the mint, untagged when graved."""
        db = SRSDatabase(":memory:")
        anki_conn = _conn()
        _line(db)
        await _mint(db, anki_conn)
        row_id = mining.add_blank(db, LANG, LINE, BA, "q")
        await _mint(db, anki_conn)
        assert set(_cards(anki_conn)) == {0, 1, 2}
        assert "tt-mined-c3" in _note_tags(anki_conn)
        gone = db.get_collocation_by_id(row_id)[1].directions[Direction.PRODUCTION].anki_card_id

        db.delete_collocation(row_id)
        _push(db, anki_conn)

        assert set(_cards(anki_conn)) == {0, 1}
        assert anki_conn.execute("SELECT type FROM graves WHERE oid = ?", (gone,)).fetchone()[0] == 0
        [(_nid, _mid, _guid, flds, _sfld, _usn)] = _notes(anki_conn)
        assert "{{c3::" not in flds and "ba" in flds
        assert _note_tags(anki_conn) == ["tunatale", "sentence", COURSE_TAG]

    async def test_the_next_course_line_does_not_follow_a_mined_card(self, no_tts):
        """The learner has caught up with the course and not yet studied the mined card: the
        next lesson's cards still open a course region, not ordinary positions behind it."""
        db = SRSDatabase(":memory:")
        anki_conn = _conn()
        _line(db)
        await _mint(db, anki_conn)
        anki_conn.execute("UPDATE cards SET type = 2, queue = 2")
        row_id = mining.add_blank(db, LANG, LINE, BA, "q")
        await _mint(db, anki_conn)
        mined_due = _due(anki_conn, db, row_id)

        nxt = replace(_BASE, line_index=5)
        db.add_collocation(_course(nxt), LANG)
        db.add_collocation(_course(nxt.with_blank(SI, "name particle")), LANG)
        await _mint(db, anki_conn)

        new_line = [
            r["due"]
            for r in anki_conn.execute(
                "SELECT c.due FROM cards c JOIN notes n ON n.id = c.nid WHERE n.guid = ?",
                (compute_guid(nxt.line_key, LANG, "TT Sentence"),),
            )
        ]
        assert len(new_line) == 2
        assert all(d <= mined_due - COURSE_REGION_GAP // 2 for d in new_line), (new_line, mined_due)


def _note_tags(anki_conn) -> list[str]:
    [(tags,)] = anki_conn.execute("SELECT tags FROM notes").fetchall()
    return tags.split()


def _push(db, anki_conn) -> None:
    from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
    from tests._helpers.anki_sync_create_new import FakeReader

    AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(anki_conn), language_code=LANG).sync_push()


@pytest.fixture
def no_tts(monkeypatch):
    import app.audio.cloze_tts as cloze_tts_mod

    async def _boom(text, voice=None):
        raise AssertionError(f"sentence card mint synthesized {text!r}")

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _boom)
