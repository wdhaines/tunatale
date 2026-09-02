"""The manual path records what it asked for (bd tunatale-g4c9, the epic's M3).

In AUTO mode the prompt is built and consumed in seconds, so the requested set is
still in hand when the lesson comes back. MANUAL mode exports a prompt, the user
pastes it into an external LLM hours or days later, and imports the story later
still — and export and import are joined ONLY by curriculum_id + day. Nothing
recorded what that prompt actually asked for, so "did the lesson include it?" was
unanswerable on the path that most needs asking.

⚠️ RECOMPUTING AT IMPORT DOES NOT FIX THIS, and that is not a preference — it was
settled in tunatale-fgeq.1. A recompute gives a DIFFERENT answer precisely when
time has passed, which is the only case the check exists for. The set is stored
at export and read back at import.
`test_import_measures_the_stored_set_not_a_recompute` is that discriminator: it
empties the deck between the two halves, so a recomputing implementation finds
nothing and reports a false 0/0.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.common.guid import compute_guid
from app.languages import get_language
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

WORD = "prosim"
CID = "c-manual"

STORY = {
    "title": "Kava",
    "key_phrases": [{"phrase": "dober dan", "translation": "good day"}],
    "scenes": [
        {
            "label": "Kavarna",
            "lines": [
                {"speaker": "female-1", "text": "Dober dan, prosim kavo.", "translation": "Good day, a coffee please."},
                {"speaker": "male-1", "text": "Takoj.", "translation": "Right away."},
            ],
        }
    ],
}


def _days() -> list[CurriculumDay]:
    return [
        CurriculumDay(day=n, title=f"D{n}", focus="f", collocations=["dober dan"], learning_objective="lo")
        for n in (1, 2)
    ]


@pytest.fixture
def store():
    s = ContentStore(":memory:")
    s.save_curriculum(CID, Curriculum(id=CID, topic="t", language_code="sl", cefr_level="A2", days=_days()))
    app.state.content_store = s
    app.state.language = get_language("sl")
    return s


@pytest.fixture
def db():
    with SRSDatabase(":memory:") as database:
        unit = SyntacticUnit(text=WORD, translation="please", word_count=1, difficulty=1, source="test")
        database.add_collocation(unit, language_code="sl")
        database.update_direction(
            compute_guid(WORD, "sl", ""),
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_at=datetime.now(UTC) - timedelta(days=20),
                stability=2.0,
                last_review=datetime.now(UTC) - timedelta(days=20, hours=3),
                reps=4,
            ),
        )
        app.state.srs_db = database
        yield database


async def _export(day: int = 1) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/story/prompt?curriculum_id={CID}&day={day}")
    assert resp.status_code == 200


async def _import(day: int = 1) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/story/import", json={"curriculum_id": CID, "day": day, "story": STORY})
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestExportRecords:
    async def test_exporting_a_prompt_stores_what_it_asked_for(self, store, db):
        await _export()
        assert store.get_curriculum(CID).review_request(1) == (WORD,)

    async def test_the_key_survives_a_store_round_trip(self, store, db):
        """Metadata goes through JSON, where an int dict key comes back as a
        string. Keying by `day` without normalising is a lookup that silently
        finds nothing after exactly one reload."""
        await _export()
        reloaded = store.get_curriculum(CID)
        assert reloaded.review_request(1) == (WORD,)
        assert reloaded.review_request(2) == ()

    async def test_a_second_export_replaces_the_first(self, store, db):
        """Last export wins: the user pastes the most recent prompt, so an older
        request is not evidence about the story they bring back."""
        await _export()
        db.update_direction(
            compute_guid(WORD, "sl", ""),
            Direction.RECOGNITION,
            DirectionState(
                direction=Direction.RECOGNITION,
                state=SRSState.REVIEW,
                due_at=datetime.now(UTC) + timedelta(days=30),
                stability=2.0,
                last_review=datetime.now(UTC),
                reps=5,
            ),
        )
        await _export()
        assert store.get_curriculum(CID).review_request(1) == ()


class TestImportMeasures:
    async def test_import_reads_the_stored_set(self, store, db):
        await _export()
        lesson_id = (await _import())["id"]
        meta = store.get_lesson(lesson_id).generation_metadata
        assert meta["review_requested"] == [WORD]
        assert meta["review_used"] == [WORD]

    async def test_import_measures_the_stored_set_not_a_recompute(self, store, db):
        """THE DISCRIMINATOR for the fgeq.1 decision. The deck is emptied between
        export and import — exactly what a week of reviews would do. An
        implementation that recomputed at import would find nothing due and
        report a confident, false 0/0."""
        await _export()
        with SRSDatabase(":memory:") as empty:
            app.state.srs_db = empty
            lesson_id = (await _import())["id"]
        meta = store.get_lesson(lesson_id).generation_metadata
        assert meta["review_requested"] == [WORD], "the requested set must come from the export, not from now"
        assert meta["review_used"] == [WORD]

    async def test_no_stored_set_reads_as_unmeasurable_not_as_failure(self, store, db):
        """A curriculum exported before this shipped, or a story pasted without
        exporting. Empty is 'we do not know', and must not error."""
        lesson_id = (await _import())["id"]
        meta = store.get_lesson(lesson_id).generation_metadata
        assert meta["review_requested"] == []
        assert meta["review_used"] == []

    async def test_one_days_request_does_not_measure_another_days_import(self, store, db):
        await _export(day=1)
        lesson_id = (await _import(day=2))["id"]
        assert store.get_lesson(lesson_id).generation_metadata["review_requested"] == []
