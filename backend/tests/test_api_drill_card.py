"""Drill cards through the routes (tunatale-5i1p.4): mint, review payload (A1), Grade plan, mining.

The review payload carries the step view of a drill card, built per request
from the card's answers that left it in review (tunatale-5i1p.10): a learning
answer keeps the step, the graduating one moves it on, and Undo takes it back
(oracle A1 / S9, through the real routes).
A plain sentence card is the control: its payload does not move.

Brief: ``.beads-tasks/briefs/design-rotating-drill-card-2026-11.md`` § Build 1, § Oracle.
"""

from __future__ import annotations

import copy

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Section, SectionType
from app.srs.database import SRSDatabase
from app.srs.lesson_card_mint import mint_lesson_cards
from app.storage.store import ContentStore
from tests import test_lesson_card_mint as mint_tests
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests.test_lesson_card_list import _cards as _sentence_cards
from tests.test_lesson_drill_cards import EXPECTED_KEYS, _keys
from tests.test_lesson_drill_cards import _story as _drill_story


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _install(story: dict) -> SRSDatabase:
    lesson = Lesson(
        title="Drills",
        language_code="no",
        sections=[Section(SectionType.NATURAL_SPEED, [])],
        key_phrases=[],
        generation_metadata={"story": story},
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


def _mixed_story() -> dict:
    """A rotating drill card and one plain sentence card (the control) in one card list."""
    story = _drill_story()
    sentence = copy.deepcopy(_sentence_cards()["items"][1])  # line 1, whole, no cue
    story["cards"]["items"] = [{"kind": "drill", "drill": 1, "role": "male-1"}, sentence]
    return story


def _mint_mixed(db: SRSDatabase) -> tuple[int, int]:
    story = _mixed_story()
    minted = mint_lesson_cards(db, "no", story["cards"], story["drills"])
    by_key = {card.unit.disambig_key: cid for cid, card in minted}
    return by_key["sent:test-01:drill-1"], by_key["sent:test-01:1"]


async def _queue_item(c: AsyncClient, cid: int) -> dict:
    queue = (await c.get("/api/srs/review-queue")).json()["queue"]
    return next(q for q in queue if q["id"] == cid)


async def _listed_drill(c: AsyncClient, cid: int) -> dict:
    r = await c.get("/api/srs/items", params={"limit": 200})
    assert r.status_code == 200
    return next(i for i in r.json()["items"] if i["id"] == cid)["drill"]


# --------------------------------------------------------------------------
# A1: the payload is built per request from the card's answers into review
# --------------------------------------------------------------------------


class TestReviewPayload:
    async def test_a1_a_learning_answer_keeps_the_step_graduating_moves_it_and_undo_takes_it_back(self):
        """S9 (5i1p.10), through the real routes: the step moves when the card lands in review."""
        db = _install(_mixed_story())
        drill_id, sentence_id = _mint_mixed(db)

        async def grade(cid: int, rating: str) -> None:
            r = await c.post(f"/api/srs/items/{cid}/direction/production/feedback", json={"rating": rating})
            assert r.status_code == 200

        async with _client() as c:
            first = await _queue_item(c, drill_id)
            control_first = await _queue_item(c, sentence_id)
            await grade(sentence_id, "again")
            await grade(drill_id, "again")  # learning: the same step comes back
            second = await _queue_item(c, drill_id)
            control_second = await _queue_item(c, sentence_id)
            await grade(drill_id, "easy")  # graduates: the next step
            assert db.count_answers_into_review(drill_id) == 1
            graduated = await _listed_drill(c, drill_id)  # not due now: read from the item list
            r = await c.post(f"/api/srs/items/{drill_id}/direction/production/undo")
            assert r.status_code == 200
            undone = await _listed_drill(c, drill_id)

        assert (first["drill"]["step"], first["drill"]["cue"], first["drill"]["before"]) == (1, "hun", "Han er her.")
        assert second["drill"] == first["drill"]
        assert (graduated["step"], graduated["cue"], graduated["before"]) == (2, "de", "Hun er her.")
        assert undone == first["drill"]
        # control: a plain sentence card graded the same way shows the same card
        assert control_first["drill"] is None
        assert control_second["sentence"] == control_first["sentence"]
        assert control_second["sentence"] is not None

    async def test_the_payload_carries_step_of_and_kind(self):
        db = _install(_mixed_story())
        drill_id, _ = _mint_mixed(db)
        async with _client() as c:
            item = await _queue_item(c, drill_id)
        assert item["sentence"] is None  # a drill row is not a sentence card
        assert item["drill"] == {
            "kind": "substitution",
            "step": 1,
            "of": 4,
            "cue": "hun",
            "answer": "Hun er her.",
            "english": "",
            "before": "Han er her.",
            "before_cue": None,
            "cue_audio_url": None,
            "answer_audio_url": None,
            "before_audio_url": None,
            "before_cue_audio_url": None,
        }

    async def test_each_part_plays_its_own_clip(self):
        db = _install(_mixed_story())
        drill_id, _ = _mint_mixed(db)
        for kind in ("drill_model", "drill_cue_0", "drill_answer_0"):
            db.add_media(
                collocation_id=drill_id,
                kind=kind,
                filename=f"{kind}.mp3",
                path=f"media/{kind}.mp3",
                anki_filename=f"{kind}.mp3",
                sha256="0" * 64,
                size_bytes=1,
            )
        async with _client() as c:
            drill = (await _queue_item(c, drill_id))["drill"]
        assert drill["before_audio_url"] == "/api/srs/media/drill_model.mp3"
        assert drill["cue_audio_url"] == "/api/srs/media/drill_cue_0.mp3"
        assert drill["answer_audio_url"] == "/api/srs/media/drill_answer_0.mp3"
        assert drill["before_cue_audio_url"] is None

    async def test_a_transformation_payload_carries_its_worked_pair(self):
        story = _drill_story()
        story["cards"]["items"] = [{"kind": "drill", "drill": 2, "role": "female-1"}]
        db = _install(story)
        (cid, _), *_ = mint_lesson_cards(db, "no", story["cards"], story["drills"])
        async with _client() as c:
            drill = (await _queue_item(c, cid))["drill"]
        assert (drill["before_cue"], drill["before"], drill["cue"]) == ("Vi er ute.", "Hvor er vi?", "Han er der.")


# --------------------------------------------------------------------------
# The mint routes: the listen and the import
# --------------------------------------------------------------------------


class TestMintRoutes:
    async def test_the_listen_mints_the_drill_cards(self):
        db = _install(_drill_story())
        async with _client() as c:
            resp = await c.post("/api/srs/listen", json={"content_id": "lesson-1"})
        assert resp.status_code == 200
        assert _keys(db) == EXPECTED_KEYS
        assert resp.json()["created"] == len(EXPECTED_KEYS)

    async def test_a_minted_drill_card_surfaces_today_without_a_sync(self):
        db = _install(_drill_story())
        async with _client() as c:
            await c.post("/api/srs/listen", json={"content_id": "lesson-1"})
            queue = (await c.get("/api/srs/review-queue")).json()["queue"]
        rows = {cid for _, _, cid in db.list_sibling_rows("sent:test-01:drill-1")}
        assert any(q["id"] in rows and q["state"] == "new" and q["drill"] is not None for q in queue)


class TestImportMintsDrills(mint_tests.TestImportMints):
    """The import route's mint (a studied day) passes the story's drills too."""

    @staticmethod
    def _file() -> dict:
        story = _drill_story()
        story["key_phrases"] = [{"phrase": "Hei.", "translation": "Hi."}]
        return {"curriculum_id": "c1", "day": 2, "story": story}

    async def test_a_studied_day_mints_its_new_card_list_at_import(self):
        db = self._setup()
        db.record_listen("old-1")
        async with _client() as c:
            resp = await c.post("/api/story/import", json=self._file())
        assert resp.status_code == 201, resp.text
        assert _keys(db) == EXPECTED_KEYS

    async def test_a_re_import_mints_nothing_twice(self):
        db = self._setup()
        db.record_listen("old-1")
        async with _client() as c:
            await c.post("/api/story/import", json=self._file())
            await c.post("/api/story/import", json=self._file())
        assert _keys(db) == EXPECTED_KEYS


# --------------------------------------------------------------------------
# U8: no blank is mined on a drill row
# --------------------------------------------------------------------------


class TestMining:
    async def test_u8_a_blank_on_a_drill_row_is_refused_and_writes_nothing(self):
        db = _install(_mixed_story())
        _mint_mixed(db)
        before = _keys(db)
        async with _client() as c:
            r = await c.post(
                "/api/srs/sentence-blanks",
                json={"line_key": "sent:test-01:drill-1", "start": 0, "end": 3, "gloss": "he"},
            )
            # control: the same request on the plain sentence line is accepted
            ok = await c.post(
                "/api/srs/sentence-blanks", json={"line_key": "sent:test-01:1", "start": 0, "end": 3, "gloss": "hi"}
            )
        assert r.status_code == 404
        assert ok.status_code == 201
        assert _keys(db) == [*before, "sent:test-01:1:0-3"]


# --------------------------------------------------------------------------
# The Cards page's "Cloze sentence" rewrite is refused on a lesson card's row
# --------------------------------------------------------------------------


class _CountingLLM:
    """Counts model calls; the refusal must come before any."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, *_a, **_k):
        self.calls += 1
        return ""


class TestClozeSentenceRewrite:
    """A drill row and a sentence card are `card_type = "cloze"`, but their sentence lives in card_data.

    Before the refusal (2026-11-15), saving the stored sentence back on a drill
    row returned 200: the row was flagged for sync, its translation blanked,
    and two clips synthesized for a row whose audio is the lesson's own takes.
    The plain cloze this route exists for is the control, in
    ``test_api_cloze_sentence.py``.
    """

    async def test_both_halves_refuse_a_drill_row_and_a_sentence_card_and_write_nothing(self):
        db = _install(_mixed_story())
        llm = _CountingLLM()
        app.state.llm = llm
        rows = _mint_mixed(db)
        stored = {cid: db.get_collocation_by_id(cid)[1].syntactic_unit for cid in rows}

        async with _client() as c:
            statuses = [
                (
                    await c.put(f"/api/srs/items/{cid}/cloze/sentence", json={"sentence": unit.source_sentence})
                ).status_code
                for cid, unit in stored.items()
            ] + [(await c.post(f"/api/srs/items/{cid}/cloze/propose")).status_code for cid in rows]

        assert statuses == [409, 409, 409, 409]
        assert {cid: db.get_collocation_by_id(cid)[1].syntactic_unit for cid in rows} == stored
        assert (db.list_dirty_field_edits(), llm.calls) == ([], 0)
