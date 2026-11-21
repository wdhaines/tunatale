"""A re-imported lesson keeps the keys of the lines the learner mined (PR #449 review F1, F2; tunatale-h0q3.68).

Source: ``.beads-tasks/briefs/findings-review-2026-11-21.md`` (F1, F2, N1) and its probes. Owner
decision 2026-11-21: carry the key.

A lesson's lines are keyed by its *line set*: its card list's ``set``, else the ``line_set`` an
import carried into it, else its own content id. Every import mints a new content id, so without
the carry a mined line of a lesson with no list lands on a key the new lesson never reads: Grade
shows it off, its due counts miss it, and a list arriving later makes a second Anki note.

* An import with no card list carries the day's current line set (``generation_metadata.line_set``).
* An import WITH a list whose ``set`` differs from the day's line set is refused (422, naming the
  set to use) when that line set holds mined rows. With no mined rows, any set is accepted.
* An import updates the mined lines no list names to their corrected scene text, all of a line's
  rows or none (``lesson_card_mint._update_line``), as the study pass does for the list's lines.
"""

from __future__ import annotations

import copy

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson
from app.srs import sentence_mining as mining
from app.srs.database import SRSDatabase
from app.srs.sentence_card import sentence_card
from app.srs.sentence_lines import line_set, list_lines, sentence_lines
from app.storage.store import ContentStore
from tests.test_lesson_card_list import _story

OG = (11, 13)  # "og" in "Bra, takk, og du?" (scene line 3)
GLOSSES = [{"word": w, "translation": t} for w, t in {"og": "and", "du": "you", "morgen": "tomorrow"}.items()]


def _setup() -> tuple[SRSDatabase, ContentStore]:
    from app.generation.pipeline import LessonPipeline
    from app.languages import get_language
    from app.llm.activity import ActivityLog
    from app.models.curriculum import Curriculum, CurriculumDay

    store = ContentStore(":memory:")
    day = CurriculumDay(day=2, title="d", focus="f", learning_objective="o", collocations=[])
    store.save_curriculum("c1", Curriculum(id="c1", topic="t", language_code="no", cefr_level="A1", days=[day]))
    db = SRSDatabase(":memory:")
    app.state.content_store = store
    app.state.language = get_language("no")
    app.state.srs_db = db
    app.state.pipeline = LessonPipeline(
        story_generator=None,
        renderer=None,
        audio_dir=None,
        content_stores={"no": store},
        languages={"no": get_language("no")},
        srs_dbs={},
        activity_log=ActivityLog(maxlen=100),
        llm_client=None,
    )
    return db, store


def _no_list(text3: str = "Bra, takk, og du?") -> dict:
    story = _story()
    del story["cards"]
    story["scenes"][0]["lines"][2]["text"] = text3
    story["dialogue_glosses"] = copy.deepcopy(GLOSSES)
    return story


def _with_list(card_set: str = "test-01") -> dict:
    story = _story()
    story["cards"]["set"] = card_set
    story["dialogue_glosses"] = copy.deepcopy(GLOSSES)
    return story


async def _import(story: dict, replaces: str | None = None):
    """POST the import. *replaces*: the lesson an edit of its source corrects (LessonSourcePanel);
    None for a new story (the curriculum page's manual story)."""
    body = {"curriculum_id": "c1", "day": 2, "story": story}
    if replaces is not None:
        body["replaces"] = replaces
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/story/import", json=body)


async def _imported(store: ContentStore, story: dict, replaces: str | None = None) -> tuple[str, Lesson]:
    resp = await _import(story, replaces)
    assert resp.status_code == 201, resp.text
    lesson_id = resp.json()["id"]
    return lesson_id, store.get_lesson(lesson_id)


def _mine(db, lesson, content_id, position, span, gloss):
    key = f"sent:{line_set(lesson, content_id)}:{position}"
    plan = mining.plan_line_edits(db, lesson, content_id, [{"line_key": key, "on": [list(span)], "off": []}])
    mining.write_marks(db, plan)
    return mining.apply_rows(db, "no", lesson, plan, {(key, span): gloss}, studied=True)


def _line(db, lesson, content_id, position):
    return next(x for x in sentence_lines(db, lesson, content_id) if x.position == position)


def _token(line, span):
    return next(t for t in line.tokens if (t.start, t.end) == span)


# --- the line set --------------------------------------------------------------------------------


class TestLineSet:
    def test_a_list_lessons_line_set_is_its_lists_set(self):
        lesson = Lesson(title="t", language_code="no", generation_metadata={"story": _story(), "line_set": "x"})
        assert line_set(lesson, "cid-1") == "test-01"

    def test_a_carried_line_set_wins_over_the_content_id(self):
        lesson = Lesson(title="t", language_code="no", generation_metadata={"story": _no_list(), "line_set": "old-1"})
        assert line_set(lesson, "cid-2") == "old-1"
        assert {ln.card.line_key for ln in list_lines(lesson, "cid-2").values()} == {
            f"sent:old-1:{n}" for n in (1, 2, 3)
        }

    def test_control_with_neither_it_is_the_content_id(self):
        assert line_set(Lesson(title="t", language_code="no", generation_metadata={"story": _no_list()}), "c") == "c"


# --- F1: a re-import keeps the mined line's key -----------------------------------------------------


class TestCarryTheKey:
    async def test_a_first_import_carries_nothing(self):
        _db, store = _setup()
        _a, lesson = await _imported(store, _no_list())
        assert "line_set" not in lesson.generation_metadata

    async def test_a_list_less_re_import_carries_the_days_line_set(self):
        db, store = _setup()
        a, lesson_a = await _imported(store, _no_list())
        (row,) = _mine(db, lesson_a, a, 3, OG, "and").made
        b, lesson_b = await _imported(store, _no_list(), replaces=a)
        assert lesson_b.generation_metadata["line_set"] == a
        og = _token(_line(db, lesson_b, b, 3), OG)
        assert (og.state, og.origin, og.in_deck) == ("on", "mine", True)
        assert _mine(db, lesson_b, b, 3, OG, "and").made == []  # tapping it again adds nothing
        c, lesson_c = await _imported(store, _no_list(), replaces=b)  # and the carry chains
        assert lesson_c.generation_metadata["line_set"] == a
        assert db.list_sibling_rows(f"sent:{a}:3")[0][2] == row

    async def test_f1_a_list_with_another_set_is_refused_when_the_day_has_mined_lines(self):
        db, store = _setup()
        a, lesson_a = await _imported(store, _no_list())
        _mine(db, lesson_a, a, 3, OG, "and")
        resp = await _import(_with_list("test-01"), replaces=a)
        assert resp.status_code == 422, resp.text
        assert f"cards.set must be {a!r}" in resp.json()["detail"]
        assert store.get_latest_lesson_by_day("c1", 2)[0] == a  # nothing was written

    async def test_f1_a_list_whose_set_is_the_days_line_set_adopts_the_mined_line(self):
        db, store = _setup()
        a, lesson_a = await _imported(store, _no_list())
        _mine(db, lesson_a, a, 3, OG, "and")
        db.record_listen(a)  # the day is studied: the list mints at import
        b, lesson_b = await _imported(store, _with_list(a), replaces=a)
        keys = sorted(i.syntactic_unit.disambig_key for _, i, _ in db.list_sibling_rows(f"sent:{a}:3"))
        assert keys == sorted(f"sent:{a}:3:{s}-{e}" for s, e in [(5, 9), OG, (14, 16)])  # one line: one note
        assert _token(_line(db, lesson_b, b, 3), OG).state == "on"

    async def test_control_a_day_with_no_mined_lines_takes_any_set(self):
        _db, store = _setup()
        a, _ = await _imported(store, _no_list())
        _b, lesson_b = await _imported(store, _with_list("test-01"), replaces=a)
        assert line_set(lesson_b, _b) == "test-01"

    async def test_control_a_course_row_under_the_days_set_does_not_refuse_a_new_set(self):
        """Only mined rows are a reason to refuse: a renamed list's course cards are the author's call."""
        db, store = _setup()
        a, _ = await _imported(store, _with_list("test-01"))
        from app.srs.lesson_card_mint import mint_lesson_cards

        mint_lesson_cards(db, "no", _with_list("test-01")["cards"])
        assert (await _import(_with_list("test-02"), replaces=a)).status_code == 201


# --- F2: an import updates the mined lines no list names ----------------------------------------------

OLD4, NEW4 = "Vi ses i morgen, Per!", "Vi sees i morgen, Per!"
MORGEN_OLD, MORGEN_NEW = (9, 15), (10, 16)


def _with_line4(text: str) -> dict:
    story = _with_list("test-01")
    story["scenes"][0]["lines"].append({"speaker": "male-1", "text": text, "translation": "See you tomorrow!"})
    return story


class TestCorrectionReImport:
    async def test_f2_a_corrected_unlisted_line_re_anchors_its_mined_blank(self):
        db, store = _setup()
        a, lesson_a = await _imported(store, _with_line4(OLD4))
        _mine(db, lesson_a, a, 4, MORGEN_OLD, "tomorrow")
        b, lesson_b = await _imported(store, _with_line4(NEW4), replaces=a)
        ((_g, item, _r),) = db.list_sibling_rows("sent:test-01:4")
        card = sentence_card(item.syntactic_unit)
        assert (card.text, card.blank, card.blank_gloss) == (NEW4, MORGEN_NEW, "tomorrow")
        morgen = _token(_line(db, lesson_b, b, 4), MORGEN_NEW)
        assert (morgen.state, morgen.in_deck) == ("on", True)
        assert _mine(db, lesson_b, b, 4, MORGEN_NEW, "tomorrow").made == []  # no 422, nothing new

    async def test_f2_a_list_less_correction_re_anchors_too(self):
        db, store = _setup()
        a, lesson_a = await _imported(store, _no_list())
        _mine(db, lesson_a, a, 3, OG, "and")
        await _imported(store, _no_list("Bra takk, og du?"), replaces=a)
        ((_g, item, _r),) = db.list_sibling_rows(f"sent:{a}:3")
        assert sentence_card(item.syntactic_unit).blank == (10, 12)

    async def test_control_an_unchanged_re_import_writes_nothing(self, caplog):
        db, store = _setup()
        a, lesson_a = await _imported(store, _no_list())
        _mine(db, lesson_a, a, 3, OG, "and")
        with caplog.at_level("INFO"):
            await _imported(store, _no_list(), replaces=a)
        assert "LESSON_CARD_UPDATED" not in caplog.text

    async def test_a_line_whose_blank_has_no_place_is_held_and_logged(self, caplog):
        db, store = _setup()
        a, lesson_a = await _imported(store, _no_list())
        _mine(db, lesson_a, a, 3, OG, "and")
        with caplog.at_level("WARNING"):
            await _imported(store, _no_list("Bra, takk, du?"), replaces=a)  # "og" is gone
        ((_g, item, _r),) = db.list_sibling_rows(f"sent:{a}:3")
        assert sentence_card(item.syntactic_unit).text == "Bra, takk, og du?"
        assert "LESSON_LINE_UPDATE_HELD" in caplog.text


# --- round 2 G1: only an edit of a named lesson carries ----------------------------------------------


def _other_story() -> dict:
    """A different story for the same day (the manual-story flow): no line is a line of _story's."""
    story = _no_list()
    story["scenes"][0]["lines"] = [
        {"speaker": "female-1", "text": "God kveld!", "translation": "Good evening!"},
        {"speaker": "male-1", "text": "Den står ute.", "translation": "It is outside."},
        {"speaker": "female-1", "text": "Takk, og god natt!", "translation": "Thanks, and good night!"},
    ]
    return story


class TestNewStoryCarriesNothing:
    async def test_g1_a_new_story_neither_carries_nor_rewrites_the_days_mined_cards(self, caplog):
        db, store = _setup()
        a, lesson_a = await _imported(store, _no_list())
        _mine(db, lesson_a, a, 3, OG, "and")
        plan = mining.plan_line_edits(db, lesson_a, a, [{"line_key": f"sent:{a}:2", "on": ["whole"], "off": []}])
        mining.apply_rows(db, "no", lesson_a, plan, {}, studied=True)
        with caplog.at_level("INFO"):
            b, lesson_b = await _imported(store, _other_story())
        assert "line_set" not in lesson_b.generation_metadata
        assert line_set(lesson_b, b) == b
        texts = {i.syntactic_unit.text for g in (f"sent:{a}:2", f"sent:{a}:3") for _, i, _ in db.list_sibling_rows(g)}
        assert texts == {"Hei, hvordan gar det?", "Bra, takk, og du?"}
        assert "LESSON_CARD_UPDATED" not in caplog.text
        assert {t.state for t in _line(db, lesson_b, b, 3).tokens} == {"off"}

    async def test_g1_a_new_story_with_any_list_is_accepted(self):
        db, store = _setup()
        a, lesson_a = await _imported(store, _no_list())
        _mine(db, lesson_a, a, 3, OG, "and")
        assert (await _import(_with_list("test-01"))).status_code == 201

    async def test_replaces_must_name_a_lesson_of_this_day(self):
        _db, store = _setup()
        await _imported(store, _no_list())
        resp = await _import(_no_list(), replaces="nowhere-1")
        assert resp.status_code == 422, resp.text
        assert "replaces" in resp.json()["detail"]

    async def test_an_edit_carries_from_the_lesson_it_names_not_the_days_latest(self):
        db, store = _setup()
        a, _ = await _imported(store, _no_list())
        await _imported(store, _with_list("test-09"))  # a later, different story on the same day
        _c, lesson_c = await _imported(store, _no_list(), replaces=a)  # the learner edits the older one
        assert lesson_c.generation_metadata["line_set"] == a


# --- round 2 G2: course rows of a line the list stops naming stay the list's ----------------------------


class TestCourseRowsAreNotRewrittenFromTheScene:
    async def test_g2_a_line_the_list_stops_naming_keeps_its_course_card_and_is_held(self, caplog):
        from app.srs.lesson_card_mint import mint_lesson_cards

        db, store = _setup()
        a, _ = await _imported(store, _with_list("test-01"))
        mint_lesson_cards(db, "no", _with_list("test-01")["cards"])
        dropped = _with_list("test-01")
        dropped["cards"]["items"] = [i for i in dropped["cards"]["items"] if i.get("line") != 1]
        with caplog.at_level("WARNING"):
            await _imported(store, dropped, replaces=a)
        ((_g, item, _r),) = db.list_sibling_rows("sent:test-01:1")
        card = sentence_card(item.syntactic_unit)
        assert (card.speaker, card.picture) == ("Kari", "door.svg")
        assert "LESSON_LINE_UPDATE_HELD line=sent:test-01:1" in caplog.text

    async def test_g2_control_a_line_the_list_still_names_is_left_to_the_list(self, caplog):
        from app.srs.lesson_card_mint import mint_lesson_cards

        db, store = _setup()
        a, _ = await _imported(store, _with_list("test-01"))
        mint_lesson_cards(db, "no", _with_list("test-01")["cards"])
        with caplog.at_level("WARNING"):
            await _imported(store, _with_list("test-01"), replaces=a)
        assert "LESSON_LINE_UPDATE_HELD" not in caplog.text


# --- round 2 G3: the screen's text is the one a blank is made on -------------------------------------


def test_g3_a_tap_on_a_line_whose_row_holds_other_text_is_refused():
    """A review session replaced in place, or any path that changes a line under its key."""
    db = SRSDatabase(":memory:")
    old = Lesson(title="t", language_code="no", generation_metadata={"story": _no_list()})
    _mine(db, old, "rs-1", 3, OG, "and")
    new = Lesson(title="t", language_code="no", generation_metadata={"story": _no_list("Takk, og god natt!")})
    with pytest.raises(mining.LineChanged) as exc:
        mining.plan_line_edits(db, new, "rs-1", [{"line_key": "sent:rs-1:3", "on": [[0, 4]], "off": []}])
    assert exc.value.status == 409
    assert "Bra, takk, og du?" in str(exc.value) and "Takk, og god natt!" in str(exc.value)


def test_g3_a_review_session_gets_a_fresh_line_set_every_write():
    from app.generation.publishing import ReviewSessionTarget

    store = ContentStore(":memory:")
    target = ReviewSessionTarget(
        store=store,
        language_code="no",
        session_id="review-2026-11-21-ab12cd34",
        session_date="2026-11-21",
        renderer=None,
        audio_dir=None,
        renders_in_flight=set(),
        render_failures={},
        owner=True,
    )
    sets = []
    for _ in range(2):
        lesson = Lesson(title="t", language_code="no", generation_metadata={"story": _no_list()})
        target.write(lesson)
        sets.append(line_set(store.get_review_session("review-2026-11-21-ab12cd34"), "review-2026-11-21-ab12cd34"))
    assert sets[0] != sets[1]
    assert all(s.startswith("review-2026-11-21-ab12cd34-") for s in sets)


# --- round 2 G4: a minted id is a valid set -----------------------------------------------------------


def test_g4_a_minted_id_never_holds_a_double_hyphen():
    from app.generation.ids import mint_id
    from app.srs.drill_card import SET_SLUG

    title = "a" * 49 + " b"  # the slug's 50th character is the hyphen
    minted = mint_id(title)
    assert "--" not in minted and SET_SLUG.fullmatch(minted)


# --- N1: a speaker's case does not change its voice -------------------------------------------------


@pytest.mark.parametrize("speaker", ["Female-1", "FEMALE-1"])
def test_n1_a_scene_lines_role_is_the_voice_maps_lowercase_key(speaker):
    story = _no_list()
    story["scenes"][0]["lines"][2]["speaker"] = speaker
    story["scenes"][0]["lines"][1]["speaker"] = speaker
    listed = list_lines(Lesson(title="t", language_code="no", generation_metadata={"story": story}), "c")[3]
    assert (listed.role, listed.cue_role) == ("female-1", "female-1")
