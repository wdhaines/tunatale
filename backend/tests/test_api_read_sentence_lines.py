"""Read's Cloze…: the lesson's lines for the line editor, and one line's edit saved (tunatale-h0q3.69).

Design: ``.beads-tasks/briefs/design-sentence-mining-2026-11.md`` § 1 (``dialogue_index``), § 4
(routes), § 6. Oracle R1 (the same edit from Read and from a Grade Save leaves an identical
database) is :class:`TestR1SameEditSameDatabase`.

``dialogue_index`` is the index of the line in the transcript's ``dialogue_lines``: the lesson's
Natural Speed phrases in its own language, in order (``drill_lines.py`` walks the same list). A
line is matched by its spoken text, case, punctuation and accents aside, in story order.
"""

from __future__ import annotations

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore
from tests.test_api_grade_page import _get, _grade, _install, _lesson, _post
from tests.test_api_grade_page import _stub_lemmatizer as _stub_lemmatizer  # noqa: F401
from tests.test_api_grade_sentences import BRA, L3, OG, _blank_rows, _llm, _scene_lesson
from tests.test_lesson_card_list import _story
from tests.test_sentence_mining import _reviewed

LINES_URL = "/api/srs/content/lesson-1/sentence-lines"
SPOKEN = ["Hei!", "Hei, hvordan gar det?", "Bra, takk, og du?"]  # what the voice says, in order


def _spoken_lesson(phrases: list[tuple[str, str]] | None = None, story: dict | None = None) -> Lesson:
    """A list lesson whose Natural Speed section speaks *phrases* ((text, language), in order)."""
    phrases = phrases if phrases is not None else [("Natural Speed", "en")] + [(s, "no") for s in SPOKEN]
    return Lesson(
        title="Dag 1",
        language_code="no",
        sections=[
            Section(
                SectionType.NATURAL_SPEED,
                [Phrase(text, "voice", lang, role="female-1") for text, lang in phrases],
            )
        ],
        generation_metadata={"story": story if story is not None else _story()},
    )


async def _lines(url: str = LINES_URL) -> list[dict]:
    resp = await _get(url)
    assert resp.status_code == 200, resp.text
    return resp.json()["lines"]


async def _save(edit: dict, url: str = LINES_URL):
    return await _post(url, {"edits": [edit]})


def _on(line_key: str = L3, on=(), off=(), **extra) -> dict:
    return {"line_key": line_key, "on": list(on), "off": list(off), **extra}


class TestGetLines:
    async def test_each_line_names_its_transcript_line(self, srs_db):
        _install(srs_db, _spoken_lesson())
        lines = await _lines()
        assert [ln["line_key"] for ln in lines] == ["sent:test-01:1", "sent:test-01:2", L3]
        assert [ln["dialogue_index"] for ln in lines] == [0, 1, 2]  # the English title is not a transcript line

    async def test_an_accented_card_line_maps_by_what_the_voice_says(self, srs_db):
        """Line 2's card text is "går", the voice (and the transcript) says "gar"."""
        _install(srs_db, _spoken_lesson())
        line = (await _lines())[1]
        assert line["text"] == "Hei, hvordan går det?"
        assert line["dialogue_index"] == 1
        assert line["spoken_map"] == [0, 1, 2, 3]

    async def test_an_unaccented_transcript_of_an_accented_spoken_field_still_maps(self, srs_db):
        """Accents aside: a list whose ``spoken`` keeps "går" while the take says "gar"."""
        story = _story()
        story["cards"]["items"][2]["spoken"] = "Hei, hvordan går det?"
        _install(srs_db, _spoken_lesson(story=story))
        assert (await _lines())[1]["dialogue_index"] == 1

    async def test_a_line_the_voice_does_not_say_has_no_transcript_line(self, srs_db):
        """Control: only the first line is spoken; the others are not matched to it."""
        _install(srs_db, _lesson("Hei!", story=_story()))
        assert [ln["dialogue_index"] for ln in await _lines()] == [0, None, None]

    async def test_an_extra_spoken_line_shifts_the_lines_after_it(self, srs_db):
        _install(srs_db, _spoken_lesson([(SPOKEN[0], "no"), ("Ja.", "no"), (SPOKEN[1], "no"), (SPOKEN[2], "no")]))
        assert [ln["dialogue_index"] for ln in await _lines()] == [0, 2, 3]

    async def test_a_line_said_otherwise_does_not_take_a_later_lines_saying(self, srs_db):
        """Review N1 (2026-11-22): line 2 "Ja." is said "Ja, takk.". A greedy walk gave it line 4's
        saying, and lines 3 and 4 then matched nothing ([0, 3, None, None])."""
        story = _story()
        del story["cards"]
        template = story["scenes"][0]["lines"][0]
        story["scenes"][0]["lines"] = [dict(template, text=t) for t in ["Hei!", "Ja.", "Bra.", "Ja."]]
        said = ["Hei!", "Ja, takk.", "Bra.", "Ja."]
        _install(srs_db, _spoken_lesson([(s, "no") for s in said], story=story))
        assert [ln["dialogue_index"] for ln in await _lines()] == [0, None, 2, 3]

    async def test_a_repeated_line_maps_to_each_saying_in_order(self, srs_db):
        story = _story()
        del story["cards"]
        story["scenes"][0]["lines"].append(dict(story["scenes"][0]["lines"][0]))  # "Hei!" again, line 4
        _install(srs_db, _spoken_lesson([(s, "no") for s in [*SPOKEN, "Hei!"]], story=story))
        assert [ln["dialogue_index"] for ln in await _lines()] == [0, 1, 2, 3]

    async def test_the_lines_carry_their_tokens_as_grade_reads_them(self, srs_db):
        _install(srs_db, _spoken_lesson())
        line = (await _lines())[2]
        assert [(t["start"], t["end"], t["state"]) for t in line["tokens"]] == [
            (0, 3, "off"),
            (5, 9, "on"),
            (11, 13, "off"),
            (14, 16, "on"),
        ]

    async def test_a_lesson_with_no_story_has_no_lines(self, srs_db):
        _install(srs_db)
        assert await _lines() == []

    async def test_an_unknown_lesson_is_404(self, srs_db):
        _install(srs_db)
        assert (await _get("/api/srs/content/nope/sentence-lines")).status_code == 404


class TestSave:
    async def test_a_blank_saved_from_read_is_a_card(self, srs_db):
        db = _install(srs_db, _spoken_lesson())
        await _grade({})  # studied
        resp = await _save(_on(on=[BRA]))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "ok", "created": 1, "not_made": []}
        assert (0, 3) in _blank_rows(db)

    async def test_read_never_marks_the_lesson_studied(self, srs_db):
        db = _install(srs_db, _spoken_lesson())
        resp = await _save(_on(on=[BRA]))
        assert resp.status_code == 200, resp.text
        assert db.count_listens("lesson-1") == 0
        assert set(_blank_rows(db)) == {(0, 3)}  # the list's blanks wait for the study pass

    async def test_a_list_card_turned_off_before_study_is_never_minted(self, srs_db):
        db = _install(srs_db, _spoken_lesson())
        assert (await _save(_on(off=[[5, 9]]))).status_code == 200
        await _grade({})
        assert set(_blank_rows(db)) == {(14, 16)}

    async def test_a_blank_with_no_gloss_is_named(self, srs_db):
        db = _install(srs_db, _spoken_lesson())
        await _grade({})
        _llm("")
        body = (await _save(_on(on=[OG]))).json()
        assert body["not_made"] == [{"line_key": L3, "start": 11, "end": 13, "word": "og"}]
        assert (11, 13) not in _blank_rows(db)

    async def test_a_reviewed_card_turned_off_without_confirm_is_409_and_names_it(self, srs_db):
        db = _install(srs_db, _spoken_lesson())
        await _grade({})
        _reviewed(db, _blank_rows(db)[(5, 9)])
        resp = await _save(_on(on=[BRA], off=[[5, 9]]))
        assert resp.status_code == 409, resp.text
        assert resp.json()["detail"]["needs_confirm"] == ["sent:test-01:3:5-9"]
        assert set(_blank_rows(db)) == {(5, 9), (14, 16)}
        resp = await _save(_on(off=[[5, 9]], confirm=True))
        assert resp.status_code == 200, resp.text
        assert set(_blank_rows(db)) == {(14, 16)}

    async def test_half_a_word_is_422(self, srs_db):
        _install(srs_db, _spoken_lesson())
        resp = await _save(_on(on=[[0, 2]]))
        assert resp.status_code == 422, resp.text

    async def test_a_line_whose_cards_hold_another_text_is_409(self, srs_db):
        """The lesson was edited after its cards were made: a blank would land on the wrong words."""
        db = _install(srs_db, _scene_lesson())
        assert (await _save(_on("sent:lesson-1:3", on=[BRA]))).status_code == 200
        edited = _scene_lesson()
        edited.generation_metadata["story"]["scenes"][0]["lines"][2]["text"] = "Bra, takk, og deg?"
        _install(db, edited)
        resp = await _save(_on("sent:lesson-1:3", on=[OG]))
        assert resp.status_code == 409, resp.text
        assert "changed" in resp.json()["detail"]

    async def test_an_unknown_line_is_404(self, srs_db):
        _install(srs_db, _spoken_lesson())
        resp = await _save(_on("sent:test-01:99", on=[BRA]))
        assert resp.status_code == 404, resp.text

    async def test_an_unknown_lesson_is_404(self, srs_db):
        _install(srs_db)
        resp = await _save(_on(on=[BRA]), url="/api/srs/content/nope/sentence-lines")
        assert resp.status_code == 404

    async def test_a_studied_lessons_mined_row_gets_the_lines_takes(self, srs_db):
        db = _install(srs_db, _spoken_lesson())
        await _grade({})
        db.add_media(_blank_rows(db)[(5, 9)], "audio_tts_sentence", "line.mp3", "/m/line.mp3", "line.mp3", "0" * 64, 1)
        assert (await _save(_on(on=[BRA]))).status_code == 200
        assert db.has_media_row(_blank_rows(db)[(0, 3)], "audio_tts_sentence")

    async def test_a_line_no_list_names_mines_under_the_content_id(self, srs_db):
        db = _install(srs_db, _scene_lesson())
        resp = await _save(_on("sent:lesson-1:3", on=[BRA]))
        assert resp.status_code == 200, resp.text
        assert (0, 3) in _blank_rows(db, "sent:lesson-1:3")


class TestReviewSession:
    async def test_a_review_session_mines_under_its_own_line_set(self, srs_db):
        lesson = _scene_lesson()
        lesson.generation_metadata["line_set"] = "review-7f3a"
        store = ContentStore(":memory:")
        store.save_review_session("session-1", "no", "2026-11-22", lesson)
        app.state.srs_db = srs_db
        app.state.content_store = store
        url = "/api/srs/content/session-1/sentence-lines"
        lines = await _lines(url)
        assert [ln["line_key"] for ln in lines] == ["sent:review-7f3a:1", "sent:review-7f3a:2", "sent:review-7f3a:3"]
        assert [ln["dialogue_index"] for ln in lines] == [0, 1, 2]
        resp = await _save(_on("sent:review-7f3a:3", on=[BRA]), url=url)
        assert resp.status_code == 200, resp.text
        assert (0, 3) in _blank_rows(srs_db, "sent:review-7f3a:3")


# Columns that differ between two runs of the same writes: row ids, guids, clocks, media file
# names; and ``lemma_key``, which Grade's plan read (its word analysis) backfills on the rows the
# first Save made. That is Grade reading the lesson, not the edit (measured: the only other diff).
_VOLATILE = {"id", "guid", "collocation_id", "due_date", "path", "filename", "anki_filename", "sha256", "lemma_key"}


def _table(conn, sql: str) -> list[tuple]:
    out = []
    for row in conn.execute(sql):
        fields = dict(row)  # a sqlite Row iterates its values, not its names
        out.append(tuple(sorted((k, v) for k, v in fields.items() if k not in _VOLATILE and not k.endswith("_at"))))
    return sorted(out, key=repr)


def _snapshot(db) -> dict:
    """Everything a sentence edit can write, keyed by card key rather than row id."""
    with db._get_conn() as conn:
        return {
            "rows": _table(conn, "SELECT * FROM collocations"),
            "dirs": _table(
                conn,
                "SELECT d.*, c.disambig_key AS card FROM collocation_directions d "
                "JOIN collocations c ON c.id = d.collocation_id",
            ),
            "media": _table(
                conn, "SELECT m.*, c.disambig_key AS card FROM media m JOIN collocations c ON c.id = m.collocation_id"
            ),
            "marks": sorted(db.sentence_card_marks()),
            "listens": db.count_listens("lesson-1"),
        }


def _fresh_db():
    from app.srs.database import SRSDatabase

    return SRSDatabase(":memory:")


class TestR1SameEditSameDatabase:
    """Oracle R1: Read's POST and a Grade Save carrying the same edit leave the same database."""

    EDIT = _on(on=[BRA, "whole"], off=[[14, 16]])

    async def _after(self, make_db, via_read: bool) -> dict:
        db = _install(make_db(), _spoken_lesson())
        await _grade({})  # studied: Grade's later Save records no study pass of its own
        db.add_media(_blank_rows(db)[(5, 9)], "audio_tts_sentence", "line.mp3", "/m/line.mp3", "line.mp3", "0" * 64, 1)
        if via_read:
            assert (await _save(self.EDIT)).status_code == 200
        else:
            await _grade({"sentence_edits": [self.EDIT]})
        return _snapshot(db)

    async def test_r1_read_and_grade_leave_identical_databases(self):
        read, grade = await self._after(_fresh_db, True), await self._after(_fresh_db, False)
        assert read == grade
        keys = {dict(row)["disambig_key"] for row in read["rows"]}
        assert "sent:test-01:3:0-3" in keys and "sent:test-01:3" in keys  # the edit did apply
        assert "sent:test-01:3:14-16" not in keys
        assert any(dict(m)["card"] == "sent:test-01:3:0-3" for m in read["media"])  # with the line's take

    async def test_r1_control_an_edit_left_out_differs(self):
        read = await self._after(_fresh_db, True)
        db = _install(_fresh_db(), _spoken_lesson())
        await _grade({})
        assert _snapshot(db) != read
