"""A line mined in TT that no card list names reaches Anki as one note (tunatale-h0q3.68).

Design: ``.beads-tasks/briefs/design-sentence-mining-2026-11.md`` § 7, oracle row M4.
The round-trip tests before this covered a mined row joining a minted line; a note
made from mined rows alone had none. Per the card-adding contract
(``.claude/rules/anki-sync.md``): a round trip through ``sync_create_new``,
idempotency, and (in ``test_api_grade_sentences.py``) the same-day queue.

A collection without the "TT Sentence" notetype (Norwegian, Slovene until the owner
runs ``add_sentence_notetype``) holds the line. That hold was a log line only; it is
now in the sync's report, so the screen can say it.
"""

from __future__ import annotations

from app.models.lesson import Lesson
from app.plugins.anki_sync.add_sentence_notetype import add_sentence_notetype
from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
from app.plugins.anki_sync.sync_writer import COURSE_TAG
from app.srs import sentence_mining as mining
from app.srs.database import SRSDatabase
from tests._helpers.anki_sync_create_new import FakeReader, _make_dual_collection_conn
from tests.test_anki_course_card_position import _seed_new_card

CID = "dag-1-ab12cd34"
KEY = f"sent:{CID}:2"  # "Hei, hvordan gar det?"


def _lesson() -> Lesson:
    story = {
        "title": "Hei",
        "scenes": [
            {
                "label": "On the street",
                "lines": [
                    {"speaker": "female-1", "text": "Hei!", "translation": "Hi!"},
                    {"speaker": "male-1", "text": "Hei, hvordan gar det?", "translation": "Hi, how are you?"},
                ],
            }
        ],
    }
    return Lesson(title="Dag 1", language_code="no", generation_metadata={"story": story})


def _mine(db) -> list[int]:
    """The whole line and the blank "hvordan", mined in one Save."""
    lesson = _lesson()
    plan = mining.plan_line_edits(db, lesson, CID, [{"line_key": KEY, "on": ["whole", [5, 12]], "off": []}])
    return mining.apply_rows(db, "no", lesson, plan, {(KEY, (5, 12)): "how"}, studied=True).made


async def _create(db, conn):
    return await AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(conn)).sync_create_new(
        deck_name="0. Slovene", model_name="Slovene Vocabulary"
    )


def _notes(conn) -> list:
    return conn.execute("SELECT id, tags, flds FROM notes WHERE flds LIKE '%hvordan%'").fetchall()


class TestRoundTrip:
    async def test_m4_the_mined_rows_alone_make_one_note_at_the_front(self):
        db, conn = SRSDatabase(":memory:"), _make_dual_collection_conn()
        add_sentence_notetype(conn)
        _seed_new_card(conn, 1, 5)
        rows = _mine(db)
        report = await _create(db, conn)
        assert report.sentence_lines_held == []
        (note,) = _notes(conn)
        assert COURSE_TAG not in note["tags"].split()
        dues = [r["due"] for r in conn.execute("SELECT due FROM cards WHERE nid = ?", (note["id"],))]
        assert len(dues) == 2 and max(dues) < 5  # ahead of the deck's new cards: a TT addition
        assert {db.get_collocation_by_id(r)[1].anki_note_id for r in rows} == {note["id"]}

    async def test_m4_a_second_sync_adds_nothing(self):
        db, conn = SRSDatabase(":memory:"), _make_dual_collection_conn()
        add_sentence_notetype(conn)
        _mine(db)
        await _create(db, conn)
        before = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        again = await _create(db, conn)
        assert (again.created, conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]) == (0, before)
        assert len(_notes(conn)) == 1


class TestNoNotetype:
    async def test_a_held_line_is_in_the_report(self):
        db, conn = SRSDatabase(":memory:"), _make_dual_collection_conn()
        rows = _mine(db)
        report = await _create(db, conn)
        assert report.sentence_lines_held == [KEY]
        assert report.skipped == len(rows)
        assert _notes(conn) == []

    async def test_control_with_the_notetype_nothing_is_held(self):
        db, conn = SRSDatabase(":memory:"), _make_dual_collection_conn()
        add_sentence_notetype(conn)
        _mine(db)
        assert (await _create(db, conn)).sentence_lines_held == []
