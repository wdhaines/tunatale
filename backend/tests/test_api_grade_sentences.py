"""The Grade page's Sentences: the plan lists a lesson's lines, a Save carries their edits (tunatale-h0q3.66).

Design: ``.beads-tasks/briefs/design-sentence-mining-2026-11.md`` § 4-5; oracle rows
G1, G2, M2 and N3 are named below. The single-blank routes these replace
(``/api/srs/sentence-blanks``) had no caller; their cases moved here, onto the one
write path.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.main import app
from app.models.lesson import Lesson
from app.models.srs_item import Direction
from app.srs.sentence_card import sentence_card
from tests.test_api_grade_page import (
    GRADE_URL,
    PROD,
    _grade,
    _install,
    _lesson,
    _plan,
    _post,
    _review_by_id,
    _rows,
)
from tests.test_api_grade_page import _stub_lemmatizer as _stub_lemmatizer  # noqa: F401
from tests.test_lesson_card_list import _story
from tests.test_sentence_mining import _reviewed

L3 = "sent:test-01:3"  # "Bra, takk, og du?": list blanks takk (5, 9) and du (14, 16)
OG, BRA = [11, 13], [0, 3]


def _llm(reply: str) -> AsyncMock:
    llm = AsyncMock()
    llm.complete.return_value = reply
    app.state.llm = llm
    return llm


def _blank_rows(db, line_key: str = L3) -> dict[tuple[int, int] | None, int]:
    return {sentence_card(i.syntactic_unit).blank: r for _, i, r in db.list_sibling_rows(line_key)}


def _edit(on=(), off=(), **extra) -> dict:
    return {"sentence_edits": [{"line_key": L3, "on": list(on), "off": list(off), **extra}]}


class TestPlan:
    async def test_the_plan_lists_the_lessons_lines_with_their_tokens(self, srs_db):
        _install(srs_db, _lesson("Hei!", story=_story()))
        sentences = (await _plan())["suggestions"]["sentences"]
        assert [s["line_key"] for s in sentences] == ["sent:test-01:1", "sent:test-01:2", L3]
        line = sentences[2]
        assert (line["text"], line["linked"]) == ("Bra, takk, og du?", False)
        assert [(t["start"], t["end"], t["state"], t["origin"]) for t in line["tokens"]] == [
            (0, 3, "off", "mine"),
            (5, 9, "on", "lesson"),
            (11, 13, "off", "mine"),
            (14, 16, "on", "lesson"),
        ]
        assert line["tokens"][0]["gloss"] == "fine" and line["tokens"][0]["part"] is None
        assert [t["in_deck"] for t in line["tokens"]] == [False, False, False, False]  # not studied yet
        assert line["whole"]["state"] == "off"

    async def test_a_lesson_with_no_list_has_no_lines(self, srs_db):
        _install(srs_db)
        assert (await _plan())["suggestions"]["sentences"] == []


class TestSave:
    async def test_a_blank_takes_the_lines_own_gloss_and_no_model_call(self, srs_db):
        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})  # the study pass (its word suggestions call the model too)
        llm = _llm("unused")
        result = await _grade(_edit(on=[BRA]))
        row = _blank_rows(db)[(0, 3)]
        assert sentence_card(db.get_collocation_by_id(row)[1].syntactic_unit).blank_gloss == "fine"
        assert result["not_made"] == []
        llm.complete.assert_not_called()

    async def test_a_word_the_line_does_not_gloss_is_glossed_by_the_model(self, srs_db):
        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})
        llm = _llm("and")
        await _grade(_edit(on=[OG]))
        row = _blank_rows(db)[(11, 13)]
        assert sentence_card(db.get_collocation_by_id(row)[1].syntactic_unit).blank_gloss == "and"
        assert "og" in str(llm.complete.call_args) and "Bra, takk, og du?" in str(llm.complete.call_args)

    async def test_g1_a_blank_with_no_gloss_is_named_and_everything_else_applies(self, srs_db):
        db = _install(srs_db, _lesson("Hei!", story=_story()))
        _llm("")
        result = await _grade(_edit(on=[OG, BRA]))
        assert result["status"] == "ok" and result["recorded_study"] is True
        assert result["not_made"] == [{"line_key": L3, "start": 11, "end": 13, "word": "og"}]
        assert set(_blank_rows(db)) == {(0, 3), (5, 9), (14, 16)}

    async def test_g1_no_model_and_no_gloss_is_not_made(self, srs_db):
        db = _install(srs_db, _lesson("Hei!", story=_story()))
        result = await _grade(_edit(on=[OG]))
        assert [n["word"] for n in result["not_made"]] == ["og"]
        assert (11, 13) not in _blank_rows(db)

    async def test_g1_control_a_save_with_no_sentence_edits_answers_as_before(self, srs_db):
        _install(srs_db, _lesson("Hei!", story=_story()))
        result = await _grade({})
        assert result["not_made"] == []

    async def test_the_mined_card_counts_as_created(self, srs_db):
        _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})
        assert (await _grade(_edit(on=[BRA])))["created"] == 1

    async def test_a_list_blank_turned_off_on_the_first_save_is_never_minted(self, srs_db):
        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade(_edit(off=[[5, 9]]))
        await _grade({"studied": True})
        assert set(_blank_rows(db)) == {(14, 16)}
        line = (await _plan())["suggestions"]["sentences"][2]
        assert line["tokens"][1]["state"] == "declined"

    async def test_n3_a_reviewed_card_turned_off_without_confirm_is_409_and_nothing_applies(self, srs_db):
        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})
        takk = _blank_rows(db)[(5, 9)]
        _reviewed(db, takk)
        resp = await _post(GRADE_URL, {**_edit(on=[BRA], off=[[5, 9]]), "studied": True})
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["needs_confirm"] == ["sent:test-01:3:5-9"]
        assert set(_blank_rows(db)) == {(5, 9), (14, 16)}
        assert db.count_listens("lesson-1") == 1  # the study mark did not apply either

    async def test_n3_with_confirm_it_goes(self, srs_db):
        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})
        _reviewed(db, _blank_rows(db)[(5, 9)])
        await _grade(_edit(off=[[5, 9]], confirm=True))
        assert set(_blank_rows(db)) == {(14, 16)}

    async def test_refusals_name_the_reason(self, srs_db):
        _install(srs_db, _lesson("Hei!", story=_story()))
        for body, status in [
            ({"sentence_edits": [{"line_key": "sent:nowhere:1", "on": [[0, 3]]}]}, 404),
            (_edit(on=[[1, 3]]), 422),
        ]:
            resp = await _post(GRADE_URL, body)
            assert resp.status_code == status, resp.text

    async def test_f3_a_word_inside_a_two_word_list_blank_is_refused_and_nothing_applies(self, srs_db):
        """Review F3: it was a 500 out of ``apply_rows`` after the removals, marks and listen were written."""
        from tests.test_sentence_lines import _two_word_story

        db = _install(srs_db, _lesson("Hei!", story=_two_word_story()))
        await _grade({})
        _llm("and")
        resp = await _post(GRADE_URL, {"studied": True, **_edit(on=[[11, 13]], off=[[5, 9]])})
        assert resp.status_code == 422, resp.text
        assert set(_blank_rows(db)) == {(5, 9), (11, 16)}
        assert db.sentence_card_marks() == set()
        assert db.count_listens("lesson-1") == 1
        control = await _post(GRADE_URL, {"studied": True, **_edit(on=[BRA], off=[[5, 9]])})
        assert control.status_code == 200, control.text

    async def test_the_new_card_is_served_today_without_a_sync(self, srs_db):
        """The card-adding contract: a NEW latecomer is tail-appended to the frozen queue.

        The line's other cards are reviewed and due later: a NEW sibling would bury the
        mined card for the day, as Anki does (decision d: bury only)."""
        from httpx import ASGITransport, AsyncClient

        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})
        for row in _blank_rows(db).values():
            _reviewed(db, row, due_at="2099-01-01T04:00:00+00:00")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            frozen = await client.get("/api/srs/review-queue?session_start=1")
            assert [i["id"] for i in frozen.json()["queue"] if i["id"] in _blank_rows(db).values()] == []
            await client.post(GRADE_URL, json=_edit(on=[BRA]))
            queue = await client.get("/api/srs/review-queue")
        assert _blank_rows(db)[(0, 3)] in [i["id"] for i in queue.json()["queue"]]

    async def test_a_studied_lessons_mined_row_gets_the_lines_takes(self, srs_db):
        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})
        takk = _blank_rows(db)[(5, 9)]
        db.add_media(takk, "audio_tts_sentence", "line.mp3", "/m/line.mp3", "line.mp3", "0" * 64, 1)
        await _grade(_edit(on=[BRA]))
        assert db.has_media_row(_blank_rows(db)[(0, 3)], "audio_tts_sentence")


class TestG2LessonWithNoList:
    async def test_g2_a_mined_line_of_a_lesson_with_no_list_is_graded(self, srs_db):
        """Its line key is ``sent:<content id>:<pos>``: the plan reads every line under that prefix."""
        from app.srs.sentence_card import SentenceCard

        story = _story()
        del story["cards"]
        db = _install(srs_db, _lesson("Hei!", story=story))
        card = SentenceCard("lesson-1", 2, "Per", "Hei, hvordan går det?", "Hei, hvordan gar det?", "Hi", ())
        db.add_collocation(card.with_blank((13, 16), "goes").unit(), "no")
        row = next(r for _, _, r in db.list_sibling_rows(card.line_key))
        _review_by_id(db, row, PROD, due_in=0)

        plan = await _plan()
        assert [c["item_id"] for c in plan["cards"]] == [row]
        from tests.test_api_grade_page import _get

        counts = (await _get("/api/srs/due-counts", {"content_id": "lesson-1", "range_days": 0})).json()
        assert counts["counts"]["lesson-1"] == 1

    async def test_g2_control_a_list_lessons_cards_as_before(self, srs_db):
        db = _install(srs_db, _lesson("Hei!", story=_story()))
        await _grade({})
        row = _blank_rows(db)[(5, 9)]
        _review_by_id(db, row, PROD, due_in=0)
        assert row in [c["item_id"] for c in (await _plan())["cards"]]

    async def test_a_drill_group_under_the_prefix_is_read_once_as_a_drill(self, srs_db):
        from tests.test_lesson_drill_cards import _story as drill_story

        db = _install(srs_db, Lesson(title="D", language_code="no", generation_metadata={"story": drill_story()}))
        await _grade({})
        rows = _rows(db, "collocations", "WHERE disambig_key LIKE 'sent:%drill-%'")
        assert rows
        for row in rows:
            _review_by_id(db, row["id"], Direction.PRODUCTION, due_in=0)
        kinds = {c["kind"] for c in (await _plan())["cards"]}
        assert kinds == {"drill"}
