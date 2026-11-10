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
from httpx import ASGITransport, AsyncClient

from app.common.guid import compute_guid
from app.main import app
from app.models.srs_item import Direction, SRSState
from app.plugins.anki_sync.sync_writer import COURSE_TAG
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


class TestRemoveBlank:
    def test_a_new_blank_goes_without_a_confirm(self):
        db = SRSDatabase(":memory:")
        _line(db)
        row_id = mining.add_blank(db, LANG, LINE, BA, "q")

        assert mining.remove_blank(db, LINE, BA, confirm=False) == row_id
        assert _row(db, BA) is None
        assert len(db.list_sibling_rows(LINE)) == 2

    def test_a_reviewed_blank_needs_the_confirm(self):
        db = SRSDatabase(":memory:")
        _line(db)
        row_id = _row(db, NAA)
        _reviewed(db, row_id)

        with pytest.raises(mining.BlankHasHistory):
            mining.remove_blank(db, LINE, NAA, confirm=False)
        assert _row(db, NAA) == row_id

        assert mining.remove_blank(db, LINE, NAA, confirm=True) == row_id
        assert _row(db, NAA) is None

    def test_a_blank_the_line_does_not_have_is_not_found(self):
        db = SRSDatabase(":memory:")
        _line(db)
        with pytest.raises(mining.BlankNotFound):
            mining.remove_blank(db, LINE, BA, confirm=True)

    def test_an_unknown_line_is_not_found(self):
        db = SRSDatabase(":memory:")
        with pytest.raises(mining.LineNotFound):
            mining.remove_blank(db, LINE, BA, confirm=True)

    def test_the_lines_last_card_stays(self):
        """Deleting a line's only row would leave its note in Anki (nothing flags it); that is
        deleting the card, not un-marking a blank."""
        db = SRSDatabase(":memory:")
        db.add_collocation(_course(_BASE.with_blank(NAA, "there")), LANG)
        with pytest.raises(mining.LastCardOfLine):
            mining.remove_blank(db, LINE, NAA, confirm=True)
        assert _row(db, NAA) is not None


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
        db = SRSDatabase(":memory:")
        anki_conn = _conn()
        _line(db)
        row_id = mining.add_blank(db, LANG, LINE, BA, "q")
        await _mint(db, anki_conn)
        assert set(_cards(anki_conn)) == {0, 1, 2}
        gone = db.get_collocation_by_id(row_id)[1].directions[Direction.PRODUCTION].anki_card_id

        mining.remove_blank(db, LINE, BA, confirm=False)
        from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
        from tests._helpers.anki_sync_create_new import FakeReader

        AnkiSync(db=db, _reader=FakeReader(), _writer=OfflineWriter(anki_conn), language_code=LANG).sync_push()

        assert set(_cards(anki_conn)) == {0, 1}
        assert anki_conn.execute("SELECT type FROM graves WHERE oid = ?", (gone,)).fetchone()[0] == 0
        [(_nid, _mid, _guid, flds, _sfld, _usn)] = _notes(anki_conn)
        assert "{{c3::" not in flds and "ba" in flds


@pytest.fixture
def no_tts(monkeypatch):
    import app.audio.cloze_tts as cloze_tts_mod

    async def _boom(text, voice=None):
        raise AssertionError(f"sentence card mint synthesized {text!r}")

    monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _boom)


# --- the routes -------------------------------------------------------------------------


@pytest.fixture
def ceb_db(api_app_state):
    """The API's db, as the Cebuano deck (the routes take the request's language)."""
    from app.languages import get_language

    app.state.language = get_language(LANG)
    return api_app_state


def _client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"x-tt-language": LANG})


def _llm(reply: str):
    from unittest.mock import AsyncMock

    llm = AsyncMock()
    llm.complete.return_value = reply
    app.state.llm = llm
    return llm


class TestAddRoute:
    async def test_adds_the_blank_with_the_lines_own_gloss(self, ceb_db):
        _line(ceb_db)
        llm = _llm("unused")
        async with _client() as client:
            resp = await client.post("/api/srs/sentence-blanks", json={"line_key": LINE, "start": BA[0], "end": BA[1]})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body == {
            "collocation_id": _row(ceb_db, BA),
            "key": _BASE.with_blank(BA, "").key,
            "gloss": "question particle",
            "created": True,
        }
        llm.complete.assert_not_called()

    async def test_a_word_the_line_does_not_gloss_is_glossed_by_the_model(self, ceb_db):
        _line(ceb_db)
        llm = _llm("name particle")
        async with _client() as client:
            resp = await client.post("/api/srs/sentence-blanks", json={"line_key": LINE, "start": SI[0], "end": SI[1]})
        assert resp.status_code == 201
        assert resp.json()["gloss"] == "name particle"
        prompt = llm.complete.call_args
        assert "si" in str(prompt) and _BASE.text in str(prompt)

    async def test_a_gloss_the_caller_gives_wins(self, ceb_db):
        _line(ceb_db)
        llm = _llm("unused")
        async with _client() as client:
            resp = await client.post(
                "/api/srs/sentence-blanks", json={"line_key": LINE, "start": BA[0], "end": BA[1], "gloss": "yes/no?"}
            )
        assert resp.json()["gloss"] == "yes/no?"
        llm.complete.assert_not_called()

    async def test_no_gloss_from_anywhere_is_refused_and_writes_nothing(self, ceb_db):
        _line(ceb_db)
        _llm("")
        async with _client() as client:
            resp = await client.post("/api/srs/sentence-blanks", json={"line_key": LINE, "start": SI[0], "end": SI[1]})
        assert resp.status_code == 503
        assert _row(ceb_db, SI) is None

    async def test_no_model_and_no_gloss_is_refused(self, ceb_db):
        _line(ceb_db)
        async with _client() as client:
            resp = await client.post("/api/srs/sentence-blanks", json={"line_key": LINE, "start": SI[0], "end": SI[1]})
        assert resp.status_code == 503
        assert _row(ceb_db, SI) is None

    async def test_a_second_post_is_the_same_row_and_no_model_call(self, ceb_db):
        _line(ceb_db)
        llm = _llm("unused")
        async with _client() as client:
            first = await client.post("/api/srs/sentence-blanks", json={"line_key": LINE, "start": BA[0], "end": BA[1]})
            again = await client.post("/api/srs/sentence-blanks", json={"line_key": LINE, "start": BA[0], "end": BA[1]})
        assert again.status_code == 200
        assert again.json()["collocation_id"] == first.json()["collocation_id"]
        assert again.json()["created"] is False
        assert again.json()["gloss"] == "question particle"
        llm.complete.assert_not_called()

    @pytest.mark.parametrize(
        ("line_key", "span", "status"),
        [
            ("sent:nowhere:1", BA, 404),
            (LINE, (1, 3), 422),
            (LINE, (NAA[0], BA[1]), 409),
        ],
    )
    async def test_refusals(self, ceb_db, line_key, span, status):
        _line(ceb_db)
        _llm("x")
        async with _client() as client:
            resp = await client.post(
                "/api/srs/sentence-blanks", json={"line_key": line_key, "start": span[0], "end": span[1]}
            )
        assert resp.status_code == status, resp.text
        assert len(ceb_db.list_sibling_rows(LINE)) == 2

    async def test_the_new_card_is_served_today_without_a_sync(self, ceb_db):
        """The card-adding contract: a NEW latecomer is tail-appended to the frozen queue.

        The line's other cards are reviewed and due later: a NEW sibling would bury
        the mined card for the day, as Anki does (decision d: bury only)."""
        _line(ceb_db)
        for span in (NAA, None):
            _reviewed(ceb_db, _row(ceb_db, span), due_at="2099-01-01T04:00:00+00:00")
        async with _client() as client:
            frozen = await client.get("/api/srs/review-queue?session_start=1")
            assert frozen.json()["queue"] == []
            resp = await client.post(
                "/api/srs/sentence-blanks", json={"line_key": LINE, "start": BA[0], "end": BA[1], "gloss": "q"}
            )
            queue = await client.get("/api/srs/review-queue")
        assert [i["id"] for i in queue.json()["queue"]] == [resp.json()["collocation_id"]]


class TestRemoveRoute:
    async def test_removes_a_new_blank(self, ceb_db):
        _line(ceb_db)
        async with _client() as client:
            resp = await client.delete(
                "/api/srs/sentence-blanks", params={"line_key": LINE, "start": NAA[0], "end": NAA[1]}
            )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "deleted"}
        assert _row(ceb_db, NAA) is None

    async def test_a_reviewed_blank_is_refused_without_confirm_then_removed_with_it(self, ceb_db):
        _line(ceb_db)
        _reviewed(ceb_db, _row(ceb_db, NAA))
        params = {"line_key": LINE, "start": NAA[0], "end": NAA[1]}
        async with _client() as client:
            refused = await client.delete("/api/srs/sentence-blanks", params=params)
            assert refused.status_code == 409
            assert "history" in refused.json()["detail"]
            assert _row(ceb_db, NAA) is not None
            done = await client.delete("/api/srs/sentence-blanks", params={**params, "confirm": "true"})
        assert done.status_code == 200
        assert _row(ceb_db, NAA) is None

    @pytest.mark.parametrize(("line_key", "span", "status"), [("sent:nowhere:1", NAA, 404), (LINE, BA, 404)])
    async def test_not_found(self, ceb_db, line_key, span, status):
        _line(ceb_db)
        async with _client() as client:
            resp = await client.delete(
                "/api/srs/sentence-blanks", params={"line_key": line_key, "start": span[0], "end": span[1]}
            )
        assert resp.status_code == status
