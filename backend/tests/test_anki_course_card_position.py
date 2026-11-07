"""Course cards reach the deck in the course's order (tunatale-h0q3.43).

TT's own additions go to the very FRONT of the new queue, newest first
(``_next_front_position``, Layers 24/83): right for a word the learner just met,
wrong for a lesson's card list. Measured on the live Cebuano collection
(2026-11-06): DECK gather, every add at ``MIN(due) - slots``. Wolff 1's ~60
cards minted that way would serve in REVERSE (its last sentence first, the
sentences before the words they use), and a later lesson would overtake an
earlier lesson's remainder.

So a course card (``source == "course"``) is written with ``course=True``: the
note is tagged ``tt-course``, and its cards go FIFO. The first course card opens
a region ``COURSE_REGION_GAP`` ahead of every new card in the collection; each
later one goes right after the highest course card still new. The frequency
deck stays behind the course; a word TT adds from a listen still lands ahead of
everything, as before.
"""

from __future__ import annotations

from dataclasses import replace

from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
from app.plugins.anki_sync.sync_writer import COURSE_REGION_GAP, COURSE_TAG
from app.srs.database import SRSDatabase
from app.srs.sentence_card import SentenceCard
from tests._helpers.anki_sync_create_new import FakeReader, _make_dual_collection_conn


def _make_db() -> SRSDatabase:
    return SRSDatabase(":memory:")


def _seed_new_card(conn, nid: int, due: int, *, card_type: int = 0) -> None:
    conn.execute(
        "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data) "
        "VALUES (?, ?, 1000002, 0, 0, '', 'x', 'x', 0, 0, '')",
        (nid, f"seed{nid}"),
    )
    conn.execute(
        "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl, factor, reps, lapses, left, "
        "odue, odid, flags, data) VALUES (?, ?, 12345, 0, 0, 0, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
        (nid * 10, nid, card_type, card_type, due),
    )
    conn.commit()


def _dues(conn, nid: int) -> list[int]:
    return [r["due"] for r in conn.execute("SELECT due FROM cards WHERE nid = ? ORDER BY ord", (nid,))]


def _tags(conn, nid: int) -> str:
    return conn.execute("SELECT tags FROM notes WHERE id = ?", (nid,)).fetchone()["tags"]


def _cloze(writer: OfflineWriter, text: str, *, course: bool) -> int:
    return writer.create_cloze_note("0. Slovene", text, language_code="no", course=course)


class TestWriterCoursePosition:
    def test_the_first_course_card_opens_a_region_ahead_of_every_new_card(self):
        conn = _make_dual_collection_conn()
        _seed_new_card(conn, 1, 5)
        _seed_new_card(conn, 2, -40)
        nid = _cloze(OfflineWriter(conn), "{{c1::Hei}}!", course=True)
        assert _dues(conn, nid) == [-40 - COURSE_REGION_GAP]
        assert COURSE_REGION_GAP == 100_000

    def test_course_cards_follow_each_other_first_in_first_out(self):
        conn = _make_dual_collection_conn()
        _seed_new_card(conn, 1, 5)
        writer = OfflineWriter(conn)
        a = _cloze(writer, "{{c1::En}}", course=True)
        b = _cloze(writer, "{{c1::To}}", course=True)
        c = _cloze(writer, "{{c1::Tre}}", course=True)
        first = 5 - COURSE_REGION_GAP
        assert [_dues(conn, n) for n in (a, b, c)] == [[first], [first + 1], [first + 2]]

    def test_a_course_vocab_note_keeps_its_templates_contiguous_and_in_order(self):
        conn = _make_dual_collection_conn()
        _seed_new_card(conn, 1, 5)
        writer = OfflineWriter(conn)
        a = _cloze(writer, "{{c1::En}}", course=True)
        word = writer.create_note(
            "0. Slovene",
            "Slovene Vocabulary",
            {"Slovene": "takk", "English": "thanks"},
            ["tunatale"],
            language_code="no",
            course=True,
        )
        base = _dues(conn, a)[0]
        assert _dues(conn, word) == [base + 1, base + 2]

    def test_a_front_add_still_goes_ahead_of_the_course(self):
        conn = _make_dual_collection_conn()
        _seed_new_card(conn, 1, 5)
        writer = OfflineWriter(conn)
        course = _cloze(writer, "{{c1::En}}", course=True)
        front = _cloze(writer, "{{c1::ord}}", course=False)
        assert _dues(conn, front)[0] < _dues(conn, course)[0]

    def test_a_later_course_card_is_not_overtaken_by_a_front_add_between(self):
        """Lesson 2's cards go behind lesson 1's remainder, whatever was added in between."""
        conn = _make_dual_collection_conn()
        _seed_new_card(conn, 1, 5)
        writer = OfflineWriter(conn)
        a = _cloze(writer, "{{c1::En}}", course=True)
        _cloze(writer, "{{c1::ord}}", course=False)
        b = _cloze(writer, "{{c1::To}}", course=True)
        assert _dues(conn, b) == [_dues(conn, a)[0] + 1]

    def test_only_new_course_cards_anchor_the_next_one(self):
        """A course card already studied is not in the queue; the next course card starts a fresh region."""
        conn = _make_dual_collection_conn()
        _seed_new_card(conn, 1, 5)
        writer = OfflineWriter(conn)
        a = _cloze(writer, "{{c1::En}}", course=True)
        conn.execute("UPDATE cards SET type = 2, queue = 2, due = 30 WHERE nid = ?", (a,))
        _seed_new_card(conn, 2, -7)
        b = _cloze(writer, "{{c1::To}}", course=True)
        assert _dues(conn, b) == [-7 - COURSE_REGION_GAP]

    def test_course_notes_carry_the_course_tag_and_others_do_not(self):
        conn = _make_dual_collection_conn()
        writer = OfflineWriter(conn)
        course = _cloze(writer, "{{c1::En}}", course=True)
        other = _cloze(writer, "{{c1::ord}}", course=False)
        assert COURSE_TAG == "tt-course"
        assert f" {COURSE_TAG} " in _tags(conn, course)
        assert COURSE_TAG not in _tags(conn, other)

    def test_the_course_tag_alone_is_not_added_twice(self):
        conn = _make_dual_collection_conn()
        nid = OfflineWriter(conn).create_cloze_note(
            "0. Slovene", "{{c1::En}}", tags=["tunatale", COURSE_TAG], language_code="no", course=True
        )
        assert _tags(conn, nid).split() == ["tunatale", COURSE_TAG]

    def test_under_highest_position_gather_the_course_runs_downward(self):
        """Descending gather serves the highest position first, so FIFO means each later card one LOWER."""
        conn = _make_dual_collection_conn()
        _seed_new_card(conn, 1, 5)
        writer = OfflineWriter(conn, gather_descending=True)
        a = _cloze(writer, "{{c1::En}}", course=True)
        b = _cloze(writer, "{{c1::To}}", course=True)
        assert _dues(conn, a) == [5 + COURSE_REGION_GAP]
        assert _dues(conn, b) == [5 + COURSE_REGION_GAP - 1]

    def test_an_empty_collection_opens_the_region_from_position_one(self):
        conn = _make_dual_collection_conn()
        nid = _cloze(OfflineWriter(conn), "{{c1::En}}", course=True)
        assert _dues(conn, nid) == [1 - COURSE_REGION_GAP]


def _course_sentence(line: int, text: str) -> SentenceCard:
    return SentenceCard(
        lesson_id="test-01",
        line_index=line,
        speaker="Kari",
        text=text,
        spoken=text,
        english="-",
        glosses=(),
    )


class TestSyncCreateNewCourse:
    async def test_course_cards_are_created_in_mint_order_behind_each_other(self):
        db = _make_db()
        for line, text in [(1, "Hei!"), (2, "Hvordan går det?"), (3, "Bra, takk.")]:
            db.add_collocation(replace(_course_sentence(line, text).unit(), source="course"), language_code="no")
        conn = _make_dual_collection_conn()
        _seed_new_card(conn, 1, 5)
        await AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(conn)).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        rows = conn.execute(
            "SELECT n.flds, c.due FROM notes n JOIN cards c ON c.nid = n.id WHERE n.tags LIKE ? ORDER BY c.due",
            (f"% {COURSE_TAG} %",),
        ).fetchall()
        firsts = [r["flds"] for r in rows]
        assert ["Hei!" in firsts[0], "Hvordan" in firsts[1], "Bra, takk" in firsts[2]] == [True, True, True]
        assert [r["due"] for r in rows] == [5 - COURSE_REGION_GAP + i for i in range(3)]

    async def test_a_course_word_is_a_course_note(self):
        db = _make_db()
        from app.models.syntactic_unit import SyntacticUnit

        db.add_collocation(
            SyntacticUnit(text="takk", translation="thanks", word_count=1, difficulty=1, source="course", lemma="takk"),
            language_code="no",
        )
        conn = _make_dual_collection_conn()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(conn)).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        (note,) = conn.execute("SELECT id, tags FROM notes").fetchall()
        assert COURSE_TAG in note["tags"].split()
        assert _dues(conn, note["id"]) == [1 - COURSE_REGION_GAP, 2 - COURSE_REGION_GAP]

    async def test_a_non_course_word_is_not(self):
        db = _make_db()
        from app.models.syntactic_unit import SyntacticUnit

        db.add_collocation(
            SyntacticUnit(text="takk", translation="thanks", word_count=1, difficulty=1, source="llm", lemma="takk"),
            language_code="no",
        )
        conn = _make_dual_collection_conn()
        await AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(conn)).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )
        (note,) = conn.execute("SELECT tags FROM notes").fetchall()
        assert COURSE_TAG not in note["tags"].split()
