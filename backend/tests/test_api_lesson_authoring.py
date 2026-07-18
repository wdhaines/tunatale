"""Tests for lesson authoring endpoints."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

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


class TestRawStoryImport:
    """POST /api/story/import with raw paste-back (prose + fenced JSON → parse_json_object)."""

    _STORY_JSON = json.dumps(
        {
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
        }
    )

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

    async def test_raw_import_with_prose_and_fenced_json(self):
        """raw containing prose + fenced JSON → 201, lesson created."""
        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")

        raw_text = f"Here is the story:\n\n```json\n{self._STORY_JSON}\n```"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/story/import",
                json={"curriculum_id": "c1", "day": 1, "raw": raw_text},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Ordering Coffee"
        assert len(data["sections"]) == 7
        assert store.get_lesson(data["id"]) is not None

    async def test_raw_import_no_parseable_json_422(self):
        """raw with no parseable JSON → 422."""
        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/story/import",
                json={"curriculum_id": "c1", "day": 1, "raw": "just prose, no JSON here"},
            )

        assert resp.status_code == 422

    async def test_raw_import_both_fields_422(self):
        """Both story and raw provided → 422 from model validator."""
        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/story/import",
                json={
                    "curriculum_id": "c1",
                    "day": 1,
                    "story": {"title": "X", "key_phrases": [], "scenes": []},
                    "raw": "text",
                },
            )

        assert resp.status_code == 422

    async def test_raw_import_neither_field_422(self):
        """Neither story nor raw → 422 from model validator."""
        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/story/import",
                json={"curriculum_id": "c1", "day": 1},
            )

        assert resp.status_code == 422

    async def test_raw_import_exported_source_identical_to_story_import(self):
        """A raw import yields a lesson identical to importing the equivalent
        story dict — compared via each lesson's exported Story-JSON source."""
        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")

        raw_text = f"Claude says:\n\n```json\n{self._STORY_JSON}\n```"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            raw_resp = await client.post(
                "/api/story/import",
                json={"curriculum_id": "c1", "day": 1, "raw": raw_text},
            )
            dict_resp = await client.post(
                "/api/story/import",
                json={"curriculum_id": "c1", "day": 1, "story": json.loads(self._STORY_JSON)},
            )
            assert raw_resp.status_code == 201
            assert dict_resp.status_code == 201
            # Distinct lessons — the comparison below must never collapse into
            # exporting the same row twice.
            assert raw_resp.json()["id"] != dict_resp.json()["id"]
            raw_source = await client.get(f"/api/story/{raw_resp.json()['id']}/source")
            dict_source = await client.get(f"/api/story/{dict_resp.json()['id']}/source")

        assert raw_source.status_code == 200
        assert dict_source.status_code == 200
        assert raw_source.json() == dict_source.json()


class TestStoryPromptEndpoint:
    """GET /api/story/prompt — export the exact prompts for manual Claude-chat flow."""

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

    async def test_prompt_returns_both_prompts(self):
        """GET /api/story/prompt → 200 with system_prompt and user_prompt."""
        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/story/prompt", params={"curriculum_id": "c1", "day": 1})

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data["system_prompt"], str) and len(data["system_prompt"]) > 0
        assert isinstance(data["user_prompt"], str) and len(data["user_prompt"]) > 0
        assert "dober dan" in data["user_prompt"]

    async def test_prompt_404_unknown_curriculum(self):
        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/story/prompt", params={"curriculum_id": "ghost", "day": 1})

        assert resp.status_code == 404

    async def test_prompt_404_unknown_day(self):
        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/story/prompt", params={"curriculum_id": "c1", "day": 99})

        assert resp.status_code == 404

    async def test_prompt_works_without_groq_key(self):
        """Endpoint works with no GROQ_API_KEY configured."""
        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/story/prompt", params={"curriculum_id": "c1", "day": 1})

        assert resp.status_code == 200

    async def test_prompt_junk_strategy_422(self):
        """Junk strategy → 422 (Literal validator, not KeyError → 500)."""
        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(
                "/api/story/prompt",
                params={"curriculum_id": "c1", "day": 1, "strategy": "JUNK"},
            )

        assert resp.status_code == 422

    async def test_drift_guard_user_prompt_identical_to_generate_path(self):
        """user_prompt from GET /prompt is byte-identical to what generate() passes to llm.complete."""
        from app.generation.story import StoryGenerator

        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = get_language("sl")

        story_json = json.dumps(
            {
                "title": "Ordering Coffee",
                "key_phrases": [{"phrase": "dober dan", "translation": "good day"}],
                "scenes": [
                    {
                        "label": "At the Café",
                        "lines": [
                            {"speaker": "female-1", "text": "Dober dan!", "translation": "Good day!"},
                        ],
                    }
                ],
            }
        )
        recording_llm = MagicMock()
        recording_llm.complete = AsyncMock(return_value=story_json)
        recording_llm.last_finish_reason = None
        recording_llm.last_provider = "groq"
        recording_llm.last_usage = None
        generator = StoryGenerator(llm_client=recording_llm)
        app.state.story_generator = generator

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            prompt_resp = await client.get("/api/story/prompt", params={"curriculum_id": "c1", "day": 1})
            generate_resp = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "c1", "day": 1, "strategy": "WIDER"},
            )

        assert prompt_resp.status_code == 200
        assert generate_resp.status_code == 201

        exported_user_prompt = prompt_resp.json()["user_prompt"]
        (llm_call,) = recording_llm.complete.call_args_list
        actual_user_prompt = llm_call.args[0]
        assert exported_user_prompt == actual_user_prompt
