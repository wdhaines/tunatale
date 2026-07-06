"""Tests for pipeline status and control API endpoints."""

from __future__ import annotations

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.generation.pipeline import LessonPipeline
from app.llm.activity import ActivityLog
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language
from app.models.lesson import Lesson, Section, SectionType
from app.storage.store import ContentStore


class FakeStoryGenerator:
    def __init__(self):
        self.calls = []
        self.lesson_to_return = Lesson(
            title="Generated Lesson",
            language_code="sl",
            sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
        )
        self.fail_count = 0

    async def generate(self, curriculum_day, language, strategy, cefr_level="A2"):
        self.calls.append(
            {
                "curriculum_day": curriculum_day,
                "language": language,
                "strategy": strategy,
                "cefr_level": cefr_level,
            }
        )
        if self.fail_count > 0:
            self.fail_count -= 1
            raise ValueError("mock error")
        return self.lesson_to_return


class FakeRenderer:
    def __init__(self):
        self.calls = []

    async def render(self, lesson, full_path, section_paths=None):
        self.calls.append({"lesson": lesson, "full_path": full_path, "section_paths": section_paths})
        from app.audio.cues import Cue

        return [
            Cue(
                index=0,
                start_ms=0,
                end_ms=1000,
                section_index=None,
                section_type=None,
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="test",
            )
        ]


@pytest.fixture(autouse=True)
def _clean_app_state():
    yield
    for attr in ("content_store", "srs_db", "language", "pipeline", "story_generator"):
        resource = getattr(app.state, attr, None)
        if resource is not None and hasattr(resource, "close"):
            resource.close()
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def _day(day: int) -> CurriculumDay:
    return CurriculumDay(day=day, title=f"Day {day}", focus="f", collocations=["c"], learning_objective=f"lo{day}")


def _pipeline(tmp_path) -> LessonPipeline:
    gen = FakeStoryGenerator()
    rdr = FakeRenderer()
    store = ContentStore(":memory:")
    activity_log = ActivityLog(maxlen=100)
    pipeline = LessonPipeline(
        story_generator=gen,
        renderer=rdr,
        audio_dir=tmp_path,
        content_stores={"sl": store},
        languages={"sl": Language.slovene()},
        srs_dbs={},
        activity_log=activity_log,
        llm_client=None,
        max_attempts=2,
    )
    return pipeline, store, gen, rdr


class TestPipelineStatusEndpoint:
    async def test_no_pipeline_returns_inactive(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/cur-1/pipeline")
        assert response.status_code == 200
        assert response.json() == {"active": False, "days": []}

    async def test_unknown_curriculum_404(self, tmp_path):
        pipeline, store, _, _ = _pipeline(tmp_path)
        app.state.content_store = store
        app.state.language = Language.slovene()
        app.state.srs_db = store
        app.state.pipeline = pipeline

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/no-such/pipeline")
        assert response.status_code == 404

    async def test_reconciles_and_returns_status(self, tmp_path):
        pipeline, store, gen, _ = _pipeline(tmp_path)
        curriculum = Curriculum(id="cur-1", topic="t", language_code="sl", cefr_level="A2", days=[_day(1)])
        store.save_curriculum("cur-1", curriculum)
        app.state.content_store = store
        app.state.language = Language.slovene()
        app.state.srs_db = store
        app.state.pipeline = pipeline

        pipeline.start()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/cur-1/pipeline")

        assert response.status_code == 200
        data = response.json()
        assert data["active"] is True
        assert len(data["days"]) == 1
        assert data["days"][0]["day"] == 1
        assert data["days"][0]["state"] in ("queued", "generating", "rendering", "ready")


class TestPipelineRetryEndpoint:
    async def test_no_pipeline_returns_404(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/cur-1/pipeline/retry", json={"day": 1})
        assert response.status_code == 404

    async def test_unknown_day_404(self, tmp_path):
        pipeline, store, _, _ = _pipeline(tmp_path)
        curriculum = Curriculum(id="cur-1", topic="t", language_code="sl", cefr_level="A2", days=[_day(1)])
        store.save_curriculum("cur-1", curriculum)
        app.state.content_store = store
        app.state.language = Language.slovene()
        app.state.srs_db = store
        app.state.pipeline = pipeline

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/cur-1/pipeline/retry", json={"day": 99})
        assert response.status_code == 404

    async def test_active_job_returns_409(self, tmp_path):
        pipeline, store, gen, _ = _pipeline(tmp_path)
        curriculum = Curriculum(id="cur-1", topic="t", language_code="sl", cefr_level="A2", days=[_day(1)])
        store.save_curriculum("cur-1", curriculum)
        app.state.content_store = store
        app.state.language = Language.slovene()
        app.state.srs_db = store
        app.state.pipeline = pipeline

        pipeline.enqueue("sl", "cur-1", 1, "generate")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/cur-1/pipeline/retry", json={"day": 1})
        assert response.status_code == 409

    async def test_retry_failed_job_returns_queued(self, tmp_path):
        pipeline, store, gen, _ = _pipeline(tmp_path)
        pipeline._max_attempts = 1
        gen.fail_count = 1
        curriculum = Curriculum(id="cur-1", topic="t", language_code="sl", cefr_level="A2", days=[_day(1)])
        store.save_curriculum("cur-1", curriculum)
        app.state.content_store = store
        app.state.language = Language.slovene()
        app.state.srs_db = store
        app.state.pipeline = pipeline

        pipeline.start()
        pipeline.enqueue("sl", "cur-1", 1, "generate")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            record = pipeline._jobs.get(("sl", "cur-1", 1))
            if record and record["state"] == "failed":
                break
            await asyncio.sleep(0.05)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/cur-1/pipeline/retry", json={"day": 1})
        assert response.status_code == 200
        assert response.json() == {"status": "queued"}


class TestPipelineRegenerateEndpoint:
    async def test_no_pipeline_returns_404(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/cur-1/pipeline/regenerate", json={"day": 1})
        assert response.status_code == 404

    async def test_unknown_day_404(self, tmp_path):
        pipeline, store, _, _ = _pipeline(tmp_path)
        curriculum = Curriculum(id="cur-1", topic="t", language_code="sl", cefr_level="A2", days=[_day(1)])
        store.save_curriculum("cur-1", curriculum)
        app.state.content_store = store
        app.state.language = Language.slovene()
        app.state.srs_db = store
        app.state.pipeline = pipeline

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/cur-1/pipeline/regenerate", json={"day": 99})
        assert response.status_code == 404

    async def test_active_job_returns_409(self, tmp_path):
        pipeline, store, _, _ = _pipeline(tmp_path)
        curriculum = Curriculum(id="cur-1", topic="t", language_code="sl", cefr_level="A2", days=[_day(1)])
        store.save_curriculum("cur-1", curriculum)
        app.state.content_store = store
        app.state.language = Language.slovene()
        app.state.srs_db = store
        app.state.pipeline = pipeline

        pipeline.enqueue("sl", "cur-1", 1, "generate")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/cur-1/pipeline/regenerate", json={"day": 1})
        assert response.status_code == 409

    async def test_regenerate_returns_queued(self, tmp_path):
        pipeline, store, gen, _ = _pipeline(tmp_path)
        curriculum = Curriculum(id="cur-1", topic="t", language_code="sl", cefr_level="A2", days=[_day(1)])
        store.save_curriculum("cur-1", curriculum)
        lesson = Lesson(
            title="Old", language_code="sl", sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])]
        )
        store.save_lesson("old-id", "cur-1", 1, lesson)
        app.state.content_store = store
        app.state.language = Language.slovene()
        app.state.srs_db = store
        app.state.pipeline = pipeline

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/cur-1/pipeline/regenerate", json={"day": 1})
        assert response.status_code == 200
        assert response.json() == {"status": "queued"}
