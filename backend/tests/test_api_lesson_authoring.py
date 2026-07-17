"""Tests for lesson authoring endpoints."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.languages import get_language
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


class TestLessonAuthoringEndpoints:
    """Story-JSON export/import round-trip (docs/lesson-authoring.md)."""

    @staticmethod
    def _story() -> dict:
        return {
            "title": "Ordering Coffee",
            "key_phrases": [{"phrase": "dober dan", "translation": "good day"}],
            "scenes": [
                {
                    "label": "At the Café",
                    "lines": [
                        {"speaker": "female-1", "text": "Dober dan!", "translation": "Good day!"},
                        {"speaker": "male-1", "text": "Prosim kavo.", "translation": "A coffee please."},
                    ],
                }
            ],
            "dialogue_glosses": [{"word": "kavo", "translation": "coffee"}],
            "morphology_focus": [],
        }

    def _store_with_curriculum(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="c1",
            topic="coffee",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Day 1",
                    focus="greetings",
                    learning_objective="greet",
                    story_guidance="café",
                    collocations=["dober dan"],
                )
            ],
        )
        store.save_curriculum("c1", curriculum)
        return store

    async def test_get_source_returns_self_describing_story(self):
        from app.generation.story import build_lesson_from_story

        store = self._store_with_curriculum()
        lesson = build_lesson_from_story(self._story(), language=get_language("sl"))
        store.save_lesson("lesson-1", "c1", 1, lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/story/lesson-1/source")

        assert response.status_code == 200
        data = response.json()
        assert data["curriculum_id"] == "c1"
        assert data["day"] == 1
        assert data["story"]["title"] == "Ordering Coffee"
        assert data["story"]["scenes"][0]["lines"][0] == {
            "speaker": "female-1",
            "text": "Dober dan!",
            "translation": "Good day!",
        }

    async def test_get_source_404_when_lesson_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/story/nope/source")
        assert response.status_code == 404

    async def test_import_creates_lesson_and_mirrors_generate_response(self):
        from app.generation.pipeline import LessonPipeline
        from app.llm.activity import ActivityLog
        from app.srs.database import SRSDatabase

        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")
        app.state.srs_db = SRSDatabase(":memory:")

        pipeline = LessonPipeline(
            story_generator=None,
            renderer=None,
            audio_dir=None,
            content_stores={"sl": store},
            languages={"sl": get_language("sl")},
            srs_dbs={},
            activity_log=ActivityLog(maxlen=100),
            llm_client=None,
        )
        app.state.pipeline = pipeline

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/import",
                json={"curriculum_id": "c1", "day": 1, "story": self._story()},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["id"].startswith("ordering-coffee-")
        assert data["title"] == "Ordering Coffee"
        assert len(data["sections"]) == 7
        assert data["warnings"] == []
        assert store.get_lesson(data["id"]) is not None
        # Verify pipeline enqueued a render job
        assert pipeline._jobs[("sl", "c1", 1)]["state"] == "queued"
        assert pipeline._jobs[("sl", "c1", 1)]["kind"] == "render"
        app.state.srs_db.close()
        del app.state.srs_db

    async def test_import_404_when_curriculum_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        app.state.language = get_language("sl")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/import",
                json={"curriculum_id": "ghost", "day": 1, "story": self._story()},
            )
        assert response.status_code == 404

    async def test_import_422_on_invalid_story_with_clear_message(self):
        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")
        story = self._story()
        del story["scenes"][0]["lines"][0]["speaker"]

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/import",
                json={"curriculum_id": "c1", "day": 1, "story": story},
            )

        assert response.status_code == 422
        assert "scenes[0].lines[0]" in response.json()["detail"]
        assert "speaker" in response.json()["detail"]

    async def test_import_warns_on_unknown_speaker(self):
        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")
        story = self._story()
        story["scenes"][0]["lines"][1]["speaker"] = "robot-9"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/import",
                json={"curriculum_id": "c1", "day": 1, "story": story},
            )

        assert response.status_code == 201
        warnings = response.json()["warnings"]
        assert len(warnings) == 1
        assert "robot-9" in warnings[0]

    async def test_import_round_trips_via_source(self):
        """Export → import → export: the story survives the round trip."""
        from app.srs.database import SRSDatabase

        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")
        app.state.srs_db = SRSDatabase(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post(
                "/api/story/import",
                json={"curriculum_id": "c1", "day": 1, "story": self._story()},
            )
            exported = (await client.get(f"/api/story/{first.json()['id']}/source")).json()
            second = await client.post("/api/story/import", json=exported)
            re_exported = (await client.get(f"/api/story/{second.json()['id']}/source")).json()

        assert re_exported["story"] == exported["story"]
        assert re_exported["curriculum_id"] == "c1"
        app.state.srs_db.close()
        del app.state.srs_db
