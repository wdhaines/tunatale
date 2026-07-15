"""API endpoint tests."""

from datetime import UTC, datetime, time
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType


@pytest.fixture(autouse=True)
def _clean_app_state():
    yield
    for attr in (
        "content_store",
        "language",
        "story_generator",
        "renderer",
        "audio_dir",
        "srs_db",
        "pipeline",
    ):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


class TestHealth:
    """Tests for the /api/health endpoint."""

    async def test_health_returns_ok(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestCurriculumEndpoints:
    """Tests for curriculum CRUD endpoints."""

    async def test_get_curriculum_returns_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/nonexistent-id")
        assert response.status_code == 404

    async def test_get_curriculum_returns_200_with_data(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="test-c",
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
                    collocations=["zdravo"],
                )
            ],
        )
        store.save_curriculum("coffee-abc", curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/coffee-abc")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "coffee-abc"
        assert data["topic"] == "coffee"
        assert data["cefr_level"] == "A2"
        assert data["proposed"] is None
        assert data["days"] == [
            {
                "day": 1,
                "title": "Day 1",
                "focus": "greetings",
                "collocations": ["zdravo"],
                "learning_objective": "greet",
                "story_guidance": "café",
            }
        ]

    async def test_get_curriculum_days_sorted_and_proposed_exposed(self):
        """days come back sorted by day number; a pending proposal is included."""
        from app.storage.store import ContentStore

        proposed = {
            "start_day": 3,
            "days": [
                {
                    "day": 3,
                    "title": "Day 3",
                    "focus": "food",
                    "collocations": ["kava"],
                    "learning_objective": "order",
                    "story_guidance": "",
                }
            ],
        }
        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="test-c",
            topic="coffee",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=2, title="Day 2", focus="f2", learning_objective="o2", collocations=["b"]),
                CurriculumDay(day=1, title="Day 1", focus="f1", learning_objective="o1", collocations=["a"]),
            ],
            metadata={"planner": {"chat": [], "proposed": proposed, "feedback": []}},
        )
        store.save_curriculum("coffee-abc", curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/coffee-abc")

        data = response.json()
        assert [d["day"] for d in data["days"]] == [1, 2]
        assert data["proposed"] == proposed

    async def test_list_curricula_returns_200(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_curriculum("c1", Curriculum(id="c1", topic="coffee", language_code="sl", cefr_level="A2"))
        app.state.content_store = store
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "c1"
        assert data[0]["topic"] == "coffee"
        assert "created_at" in data[0]

    async def test_get_lesson_by_day_returns_full_lesson(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        lesson = Lesson(
            title="Day 1: Coffee",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="kavo prosim", role="female-1", voice_id="sl-SI-PetraNeural", language_code="sl"),
                    ],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="kavo prosim", translation="a coffee please")],
        )
        store.save_lesson("lesson-day1", "curriculum-abc", 1, lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/curriculum-abc/days/1/lesson")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "lesson-day1"
        assert data["title"] == "Day 1: Coffee"
        assert data["language_code"] == "sl"
        assert len(data["sections"]) == 1
        assert data["sections"][0]["phrases"][0]["text"] == "kavo prosim"
        assert data["key_phrases"][0]["translation"] == "a coffee please"

    async def test_get_lesson_by_day_returns_most_recent(self):
        """When two lessons exist for the same day, the newer one is returned."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        old = Lesson(title="Old", language_code="sl", sections=[], key_phrases=[])
        new = Lesson(title="New", language_code="sl", sections=[], key_phrases=[])
        store.save_lesson("lesson-old", "curriculum-abc", 1, old)
        store.save_lesson("lesson-new", "curriculum-abc", 1, new)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/curriculum-abc/days/1/lesson")

        assert response.status_code == 200
        assert response.json()["id"] == "lesson-new"

    async def test_get_lesson_by_day_returns_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/no-such-curriculum/days/1/lesson")
        assert response.status_code == 404

    async def test_get_curriculum_progress_returns_lesson_days(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="c1",
            topic="coffee",
            language_code="sl",
            cefr_level="A2",
        )
        store.save_curriculum("c1", curriculum)
        lesson = Lesson(title="Day 1", language_code="sl", sections=[], key_phrases=[])
        store.save_lesson("l1", "c1", 1, lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/c1/progress")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["day"] == 1
        assert data[0]["lesson_id"] == "l1"

    async def test_get_curriculum_progress_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/nonexistent/progress")
        assert response.status_code == 404


class TestCurriculumPlanIOEndpoints:
    """Tests for curriculum plan source/import endpoints."""

    async def test_source_returns_200(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="test-c",
            topic="coffee",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Day 1",
                    focus="greetings",
                    learning_objective="greet",
                    collocations=["zdravo"],
                )
            ],
        )
        store.save_curriculum("test-c", curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/test-c/source")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "test-c"
        assert data["topic"] == "coffee"
        assert len(data["days"]) == 1
        assert "metadata" not in data

    async def test_source_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/no-such/source")

        assert response.status_code == 404
        assert response.json()["detail"] == "Curriculum not found"

    async def test_import_returns_201(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        app.state.content_store = store

        body = {
            "topic": "ordering coffee",
            "language_code": "sl",
            "cefr_level": "A2",
            "days": [
                {
                    "day": 1,
                    "title": "Day 1",
                    "focus": "greetings",
                    "collocations": ["dober dan"],
                    "learning_objective": "say hello",
                    "story_guidance": "",
                },
            ],
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/import", json=body)

        assert response.status_code == 201
        data = response.json()
        assert data["id"].startswith("ordering-coffee-")
        assert data["topic"] == "ordering coffee"
        assert data["language_code"] == "sl"
        assert data["days"] == 1

        restored = store.get_curriculum(data["id"])
        assert restored is not None

    async def test_import_with_id_updates_existing(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        existing = Curriculum(
            id="existing-id",
            topic="old topic",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Old Day",
                    focus="old",
                    learning_objective="old",
                    collocations=["old"],
                )
            ],
            metadata={"planner": {"chat": [], "proposed": None, "feedback": []}},
        )
        store.save_curriculum("existing-id", existing)
        app.state.content_store = store

        body = {
            "id": "existing-id",
            "topic": "new topic",
            "language_code": "sl",
            "cefr_level": "B1",
            "days": [
                {
                    "day": 1,
                    "title": "New Day",
                    "focus": "new",
                    "collocations": ["new phrase"],
                    "learning_objective": "new objective",
                    "story_guidance": "",
                },
            ],
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/import", json=body)

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "existing-id"
        assert data["topic"] == "new topic"
        assert data["days"] == 1

        restored = store.get_curriculum("existing-id")
        assert restored is not None
        assert restored.metadata == {"planner": {"chat": [], "proposed": None, "feedback": []}}

    async def test_import_422_on_bad_days(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")

        body = {
            "topic": "test",
            "language_code": "sl",
            "cefr_level": "A2",
            "days": [
                {
                    "day": 0,
                    "title": "Bad",
                    "focus": "bad",
                    "collocations": ["x"],
                    "learning_objective": "bad",
                    "story_guidance": "",
                },
            ],
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/import", json=body)

        assert response.status_code == 422
        assert "day" in response.json()["detail"]

    async def test_import_404_when_id_not_found(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")

        body = {
            "id": "no-such-id",
            "topic": "test",
            "language_code": "sl",
            "cefr_level": "A2",
            "days": [
                {
                    "day": 1,
                    "title": "Day 1",
                    "focus": "test",
                    "collocations": ["x"],
                    "learning_objective": "test",
                    "story_guidance": "",
                },
            ],
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/import", json=body)

        assert response.status_code == 404
        assert "no-such-id" in response.json()["detail"]


class TestStoryEndpoints:
    """Tests for story/lesson generation endpoints."""

    async def test_get_lesson_returns_full_script(self):
        from app.storage.store import ContentStore

        mock_lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="dober dan", role="female-1", voice_id="sl-SI-PetraNeural", language_code="sl"),
                    ],
                ),
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="kako ste", role="male-1", voice_id="sl-SI-RokNeural", language_code="sl"),
                    ],
                ),
            ],
        )

        store = ContentStore(":memory:")
        store.save_lesson("lesson-abc", "some-curriculum-id", 1, mock_lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/story/lesson-abc")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "lesson-abc"
        assert data["title"] == "Day 1"
        assert len(data["sections"]) == 2
        phrase = data["sections"][0]["phrases"][0]
        assert phrase["text"] == "dober dan"
        assert phrase["role"] == "female-1"
        assert phrase["language_code"] == "sl"
        assert phrase["voice_id"] == "sl-SI-PetraNeural"

    async def test_get_lesson_includes_day(self):
        """GET /api/story/{id} exposes the curriculum day so the UI can regenerate it."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        mock_lesson = Lesson(title="Day 4", language_code="sl", sections=[])
        store.save_lesson("lesson-day4", "curriculum-1", 4, mock_lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/story/lesson-day4")

        assert response.status_code == 200
        assert response.json()["day"] == 4

    async def test_get_lesson_includes_key_phrases(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        mock_lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="prosim kavo", translation="a coffee please"),
            ],
        )
        store.save_lesson("lesson-kp", "curriculum-1", 1, mock_lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/story/lesson-kp")

        assert response.status_code == 200
        data = response.json()
        assert len(data["key_phrases"]) == 2
        assert data["key_phrases"][0] == {"phrase": "dober dan", "translation": "good day"}
        assert data["key_phrases"][1] == {"phrase": "prosim kavo", "translation": "a coffee please"}

    async def test_get_lesson_returns_empty_key_phrases_for_old_lesson(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        mock_lesson = Lesson(title="Day 1", language_code="sl", sections=[])
        store.save_lesson("lesson-old", "curriculum-1", 1, mock_lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/story/lesson-old")

        assert response.status_code == 200
        assert response.json()["key_phrases"] == []

    async def test_get_lesson_returns_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/story/nonexistent-lesson-id")
        assert response.status_code == 404

    async def test_generate_story_returns_404_curriculum_not_found(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "nonexistent", "day": 1, "strategy": "WIDER"},
            )
        assert response.status_code == 404

    async def test_generate_story_returns_404_day_not_found(self):
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
                    collocations=["zdravo"],
                )
            ],
        )
        store.save_curriculum("c1", curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "c1", "day": 99, "strategy": "WIDER"},
            )
        assert response.status_code == 404

    async def test_generate_story_invalid_strategy_422(self):
        """An unknown strategy must be a validation error, not a KeyError 500."""
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "c1", "day": 1, "strategy": "SIDEWAYS"},
            )
        assert response.status_code == 422

    async def test_generate_story_llm_failure_502(self):
        """Malformed LLM output (StoryGenerationError) maps to 502, mirroring
        how plan_turn maps PlannerError — never a raw 500 traceback."""
        from app.generation.story import StoryGenerationError
        from app.storage.store import ContentStore

        mock_generator = AsyncMock()
        mock_generator.generate = AsyncMock(side_effect=StoryGenerationError("LLM returned invalid JSON"))

        store = ContentStore(":memory:")
        store.save_curriculum(
            "c1",
            Curriculum(
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
                        collocations=["zdravo"],
                    )
                ],
            ),
        )
        app.state.content_store = store
        app.state.story_generator = mock_generator
        app.state.language = Language.slovene()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "c1", "day": 1, "strategy": "WIDER"},
            )
        assert response.status_code == 502
        assert "invalid JSON" in response.json()["detail"]

    async def test_generate_story_llm_error_502(self):
        """A raw LLMError (opt-in fallback: complete() no longer rescues a 429 via
        Ollama) must map to 502 with the error detail, not escape as a 500 traceback."""
        from app.llm.client import LLMError
        from app.storage.store import ContentStore

        mock_generator = AsyncMock()
        mock_generator.generate = AsyncMock(
            side_effect=LLMError("Groq returned 429 Too Many Requests (retry after 37s)")
        )

        store = ContentStore(":memory:")
        store.save_curriculum(
            "c1",
            Curriculum(
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
                        collocations=["zdravo"],
                    )
                ],
            ),
        )
        app.state.content_store = store
        app.state.story_generator = mock_generator
        app.state.language = Language.slovene()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "c1", "day": 1, "strategy": "WIDER"},
            )
        assert response.status_code == 502
        assert "429" in response.json()["detail"]

    async def test_generate_story_returns_201(self, monkeypatch):
        from app.generation.pipeline import LessonPipeline
        from app.llm.activity import ActivityLog
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        mock_lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
                )
            ],
        )

        mock_generator = AsyncMock()
        mock_generator.generate = AsyncMock(return_value=mock_lesson)

        mock_curriculum = Curriculum(
            id="test-id",
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Day 1",
                    focus="greetings",
                    learning_objective="greet",
                    story_guidance="greet each other",
                    collocations=["dober dan"],
                )
            ],
        )
        app.state.story_generator = mock_generator
        app.state.language = Language.slovene()
        app.state.srs_db = SRSDatabase(":memory:")

        store = ContentStore(":memory:")
        curriculum_id = "test-curriculum-id"
        store.save_curriculum(curriculum_id, mock_curriculum)
        app.state.content_store = store

        pipeline = LessonPipeline(
            story_generator=None,
            renderer=None,
            audio_dir=None,
            content_stores={"sl": store},
            languages={"sl": Language.slovene()},
            srs_dbs={},
            activity_log=ActivityLog(maxlen=100),
            llm_client=None,
        )
        app.state.pipeline = pipeline

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": curriculum_id, "day": 1, "strategy": "WIDER"},
            )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["id"].startswith("day-1"), f"Expected slug prefix, got: {data['id']}"
        assert "sections" in data
        # Verify lesson was persisted
        assert store.get_lesson(data["id"]) is not None
        # Verify CurriculumDay.title was synced
        curriculum = store.get_curriculum(curriculum_id)
        assert curriculum is not None
        assert curriculum.days[0].title == "Day 1"
        # Verify pipeline enqueued a render job
        assert pipeline._jobs[("sl", "test-curriculum-id", 1)]["state"] == "queued"
        assert pipeline._jobs[("sl", "test-curriculum-id", 1)]["kind"] == "render"
        app.state.srs_db.close()

    async def test_generate_story_no_srs_db_still_succeeds(self, monkeypatch):
        """generate_story works when app.state has no srs_db (pre-warm is skipped)."""
        from app.storage.store import ContentStore

        mock_lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Dober dan", voice_id="v1", language_code="sl", role="A")],
                )
            ],
        )

        mock_generator = AsyncMock()
        mock_generator.generate = AsyncMock(return_value=mock_lesson)

        mock_curriculum = Curriculum(
            id="test-id",
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Day 1",
                    focus="greetings",
                    learning_objective="greet",
                    story_guidance="greet each other",
                    collocations=["dober dan"],
                )
            ],
        )
        app.state.story_generator = mock_generator
        app.state.language = Language.slovene()

        store = ContentStore(":memory:")
        store.save_curriculum("cid", mock_curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "cid", "day": 1, "strategy": "WIDER"},
            )

        assert response.status_code == 201
        assert "id" in response.json()

    async def test_prewarm_lesson_populates_cache(self, monkeypatch):
        """_prewarm_lesson fills the lemma_analysis_cache for a lesson's L2 phrases."""
        from app.api.generation import _prewarm_lesson
        from app.srs.database import SRSDatabase
        from app.srs.lemmatizer import LowercaseLemmatizer

        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Dober dan", voice_id="v1", language_code="sl", role="A"),
                        Phrase(text="Kako si", voice_id="v1", language_code="sl", role="B"),
                    ],
                ),
            ],
        )

        class _CachingLemmatizer(LowercaseLemmatizer):
            _cache_version = "test-v1"

        monkeypatch.setattr("app.api.generation.get_lemmatizer", lambda code: _CachingLemmatizer())

        srs_db = SRSDatabase(":memory:")
        try:
            await _prewarm_lesson(lesson, srs_db)

            for text in ("Dober dan", "Kako si"):
                cached = srs_db.get_sentence_analysis(text, "sl", "test-v1")
                assert cached is not None, f"Expected cache entry for {text}"
        finally:
            srs_db.close()

    async def test_prewarm_skips_cheap_lemmatizer(self, monkeypatch):
        """_prewarm_lesson is a no-op for LowercaseLemmatizer (no _cache_version)."""
        from app.api.generation import _prewarm_lesson
        from app.srs.database import SRSDatabase
        from app.srs.lemmatizer import LowercaseLemmatizer

        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Dober dan", voice_id="v1", language_code="sl", role="A")],
                ),
            ],
        )

        call_count = 0

        class _CountingLemmatizer(LowercaseLemmatizer):
            def analyze_sentence(self, sentence, language_code):
                nonlocal call_count
                call_count += 1
                return super().analyze_sentence(sentence, language_code)

        monkeypatch.setattr("app.api.generation.get_lemmatizer", lambda code: _CountingLemmatizer())

        srs_db = SRSDatabase(":memory:")
        try:
            await _prewarm_lesson(lesson, srs_db)
            # Cheap lemmatizer → no caching → no analyze_sentence calls (early return)
            assert call_count == 0
        finally:
            srs_db.close()

    async def test_prewarm_skips_no_natural_speed(self, monkeypatch):
        """_prewarm_lesson returns early when lesson has no NATURAL_SPEED section (line 47)."""
        from app.api.generation import _prewarm_lesson
        from app.srs.database import SRSDatabase
        from app.srs.lemmatizer import LowercaseLemmatizer

        lesson = Lesson(
            title="No NS",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="dober dan", voice_id="v1", language_code="sl")],
                ),
            ],
        )

        class _CachingLemmatizer(LowercaseLemmatizer):
            _cache_version = "test-v1"

        monkeypatch.setattr("app.api.generation.get_lemmatizer", lambda code: _CachingLemmatizer())

        srs_db = SRSDatabase(":memory:")
        try:
            await _prewarm_lesson(lesson, srs_db)  # should not raise
        finally:
            srs_db.close()

    async def test_prewarm_swallows_exception(self, monkeypatch):
        """_prewarm_lesson logs and swallows exceptions from get_lemmatizer (lines 52-53)."""
        from app.api.generation import _prewarm_lesson
        from app.srs.database import SRSDatabase

        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Dober dan", voice_id="v1", language_code="sl", role="A")],
                ),
            ],
        )

        def _raise(code):
            raise RuntimeError("boom")

        monkeypatch.setattr("app.api.generation.get_lemmatizer", _raise)

        srs_db = SRSDatabase(":memory:")
        try:
            await _prewarm_lesson(lesson, srs_db)  # should not raise
        finally:
            srs_db.close()


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
        lesson = build_lesson_from_story(self._story(), language=Language.slovene())
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
        app.state.language = Language.slovene()
        app.state.srs_db = SRSDatabase(":memory:")

        pipeline = LessonPipeline(
            story_generator=None,
            renderer=None,
            audio_dir=None,
            content_stores={"sl": store},
            languages={"sl": Language.slovene()},
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
        app.state.language = Language.slovene()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/import",
                json={"curriculum_id": "ghost", "day": 1, "story": self._story()},
            )
        assert response.status_code == 404

    async def test_import_422_on_invalid_story_with_clear_message(self):
        store = self._store_with_curriculum()
        app.state.content_store = store
        app.state.language = Language.slovene()
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
        app.state.language = Language.slovene()
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
        app.state.language = Language.slovene()
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


class TestSRSEndpoints:
    """Tests for SRS due/stats/feedback/new endpoints."""

    async def test_srs_due_returns_200(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/due")

        assert response.status_code == 200
        data = response.json()
        assert "due" in data
        assert isinstance(data["due"], list)

    async def test_srs_stats_returns_200(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/stats")

        assert response.status_code == 200
        data = response.json()
        assert "total" in data

    async def test_srs_new_returns_200(self):
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        db.add_collocation(
            SyntacticUnit(text="dober dan", translation="good day", word_count=2, difficulty=1, source="llm")
        )
        db.add_collocation(
            SyntacticUnit(text="prosim kavo", translation="a coffee please", word_count=2, difficulty=1, source="llm")
        )
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/new")

        assert response.status_code == 200
        data = response.json()
        assert "new" in data
        assert len(data["new"]) == 2
        assert all("text" in item and "translation" in item for item in data["new"])

    async def test_srs_new_returns_empty_when_no_new_cards(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/new")

        assert response.json()["new"] == []

    async def test_srs_new_respects_limit(self):
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        for i in range(15):
            db.add_collocation(
                SyntacticUnit(text=f"phrase {i}", translation=f"trans {i}", word_count=2, difficulty=1, source="llm")
            )
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/new")

        assert len(response.json()["new"]) == 10

    async def test_listen_skips_phrases_with_wrong_language_code(self):
        """NATURAL_SPEED phrases whose language_code != lesson language_code are skipped."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        # Lesson is Slovene but one NATURAL_SPEED phrase is English (narrator)
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="At the café", voice_id="narrator", language_code="en", role="narrator"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-foreign", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-foreign"})

        assert response.status_code == 200
        # The English phrase should contribute 0 lemmas (registered = 0 lemmas + 0 key phrases)
        assert db.count_collocations() == 0

    async def test_listen_same_lemma_in_multiple_phrases_dedup_sentence(self):
        """When the same lemma appears in multiple NATURAL_SPEED phrases, only
        the first sentence is stored as source_sentence."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1"),
                        Phrase(text="Kje je center?", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        # kje and je appear in both phrases, but only first sentence stored
        kje = db.get_collocation_by_lemma("kje")
        assert kje is not None
        assert kje.syntactic_unit.source_sentence == "{{c1::Kje}} je banka?"

    async def test_listen_registers_collocations(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
                )
            ],
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="prosim kavo", translation="a coffee please"),
            ],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["registered"] == 2
        assert db.count_collocations() == 2
        assert db.get_collocation("dober dan") is not None
        assert db.get_collocation("prosim kavo") is not None

    async def test_listen_creates_collocations_with_source_llm_and_no_anki_link(self):
        """Layer 34 spec: /listen creates TT collocations with source='llm' and
        anki_note_id=NULL — guaranteeing the next sync_create_new picks them up
        and pushes them as proper Slovene Vocabulary notes (or links via guid if
        Anki already has them)."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )
        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # Inspect the persisted collocation
        with db._get_conn() as conn:
            row = conn.execute("SELECT text, source, anki_note_id FROM collocations WHERE text='dober dan'").fetchone()
        assert row is not None
        assert row["source"] == "llm"
        assert row["anki_note_id"] is None

    async def test_listen_is_idempotent(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        assert db.count_collocations() == 1

    async def test_listen_returns_404_for_missing_lesson(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        app.state.srs_db = SRSDatabase(":memory:")
        app.state.content_store = ContentStore(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "nonexistent"})

        assert response.status_code == 404

    async def test_listen_registers_all_l2_words(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        # 2 cloze (kje, je) + 1 vocab (banka)
        assert data["registered"] == 3
        assert db.get_collocation_by_lemma("kje") is not None
        assert db.get_collocation_by_lemma("je") is not None
        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        assert banka.syntactic_unit.card_type == "vocab"

    async def test_listen_surface_keyed_card_graded_not_duplicated(self, monkeypatch):
        """A token whose lemma has no card but whose surface matches an existing
        card (greeting 'dobrodošli' → lemma 'dobrodošel') grades the existing card
        instead of spawning a 'dobrodošel' duplicate."""
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase
        from app.srs.lemmatizer import TokenAnalysis
        from app.storage.store import ContentStore
        from tests._helpers.lemmatizer import StubLemmatizer

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Dobrodošli", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )
        db = SRSDatabase(":memory:")
        db.add_collocation(
            SyntacticUnit(
                text="dobrodošli",
                translation="Welcome.",
                word_count=1,
                difficulty=1,
                source="llm",
                lemma="dobrodošli",
            ),
            language_code="sl",
        )
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        import app.api.srs as srs_mod

        stub = StubLemmatizer()
        stub.set_sentence("Dobrodošli", [TokenAnalysis(surface="Dobrodošli", lemma="dobrodošel")])
        # Inject the stub as the per-language engine (Slovene lesson → get_lemmatizer("sl")).
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        assert db.get_collocation_by_lemma("dobrodošel") is None
        assert db.get_collocation_by_lemma("dobrodošli") is not None

    async def test_listen_key_phrases_translation_preserved(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        assert item.syntactic_unit.translation == "good day"

    async def test_listen_lemma_auto_filled_on_single_word_collocation(self):
        """Single-word collocations now auto-fill lemma = casefolded text,
        so get_collocation_by_lemma finds the pre-existing row and no duplicate is created."""
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        # Pre-insert with text="banka" — lemma is auto-filled by add_collocation
        db.add_collocation(SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus"))
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        assert db.count_collocations() == 1
        # Lemma was auto-filled, so the pre-existing row is found
        assert db.get_collocation_by_lemma("banka") is not None

    async def test_listen_auto_added_cards_have_null_anki_note_id(self):
        """Auto-added key_phrase collocations have anki_note_id IS NULL and appear in list_items_without_anki_note."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="prosim kavo", translation="a coffee please"),
            ],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        items_without_anki = db.list_items_without_anki_note()
        assert len(items_without_anki) == 2
        for _guid, item, _coll_id in items_without_anki:
            assert item.anki_note_id is None

    async def test_listen_key_phrases_state_is_new_on_first_listen(self):
        """Key phrases get state=NEW on first listen — no FSRS grade (soft exposure only)."""
        from app.models.srs_item import SRSState
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        assert item.state == SRSState.NEW
        assert item.reps == 0

    async def test_listen_key_phrases_do_not_duplicate_on_relisten(self):
        """Second listen with same key_phrases does not create duplicate collocations."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="prosim kavo", translation="a coffee please"),
            ],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
            second = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert first.status_code == 200
        assert second.status_code == 200
        assert db.count_collocations() == 2

    async def test_listen_empty_word_ratings_does_not_crash(self):
        """Explicitly verify that word_ratings={} (as sent by the frontend) works."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/listen",
                json={"lesson_id": "lesson-1", "word_ratings": {}},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["registered"] == 1


class TestResolveGlossTranslation:
    """Gloss lookup for /listen card creation: lemma → surface → warn (no silent '')."""

    def test_lemma_hit(self):
        from app.api.srs import _resolve_gloss_translation

        got = _resolve_gloss_translation("banka", {"banka": "bank"}, {"banka"}, "banka", language_code="sl")
        assert got == "bank"

    def test_surface_fallback_when_lemma_missing(self):
        """Stanza lemmatizes 'snøen' → 'snø' but the LLM glossed the surface 'snøen';
        the card is keyed by lemma, so fall back to the surface form."""
        from app.api.srs import _resolve_gloss_translation

        got = _resolve_gloss_translation("snø", {"snøen": "the snow"}, {"snøen"}, "snøen", language_code="no")
        assert got == "the snow"

    def test_first_surface_preferred_over_other_surfaces(self):
        from app.api.srs import _resolve_gloss_translation

        got = _resolve_gloss_translation(
            "x",
            {"first": "A", "second": "B"},
            {"first", "second"},
            "first",
            language_code="no",
        )
        assert got == "A"

    def test_miss_logs_warning_and_returns_empty(self, caplog):
        """The 'går' bug: lemma 'går' and its only surface 'går' are absent from the
        gloss map (LLM keyed 'gå' + 'i går'). No silent '' — emit a warning."""
        from app.api.srs import _resolve_gloss_translation

        with caplog.at_level("WARNING"):
            got = _resolve_gloss_translation(
                "går", {"gå": "go / walk", "i går": "yesterday"}, {"går"}, "går", language_code="no"
            )
        assert got == ""
        assert any("går" in r.message and "empty translation" in r.message for r in caplog.records)


class TestListenClozeIntegration:
    """Phase F: /listen as recognition-exposure event (Layer 1 redesign)."""

    async def _setup_lesson(
        self,
        phrase_text: str = "Kje je banka?",
        language_code: str = "sl",
    ):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code=language_code,
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(
                            text=phrase_text,
                            voice_id="female-1",
                            language_code=language_code,
                            role="female-1",
                        ),
                    ],
                )
            ],
            key_phrases=[],
        )
        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store
        return db

    async def test_listen_creates_cloze_card_when_enabled(self):
        """Created cloze rows have state='new', reps=0, introduced_at IS NULL."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.card_type == "cloze"
        prod = item_kje.directions[Direction.PRODUCTION]
        assert prod.state == SRSState.NEW
        assert prod.reps == 0
        assert prod.introduced_at is None

        item_je = db.get_collocation_by_lemma("je")
        assert item_je is not None
        assert item_je.syntactic_unit.card_type == "cloze"
        assert item_je.syntactic_unit.source_sentence == "Kje {{c1::je}} banka?"

    async def test_listen_creates_no_cloze_for_biti_surface(self, monkeypatch):
        """A biti surface (lemma "biti") is clozes-only — no base cloze created by /listen.
        biti's per-person conjugation clozes are created by click, not auto.
        Regression: Phase 2b surface-blanking test now reflects the special-case."""
        import app.api.srs as srs_mod

        def fake_lemmatize(surfaces, text, lemmatizer, language_code, db=None, model_version=""):
            return ["biti" if s.lower() == "sem" else s.lower() for s in surfaces]

        monkeypatch.setattr(srs_mod, "lemmatize_surfaces_in_context", fake_lemmatize)

        db = await self._setup_lesson(phrase_text="Sem doma.")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # No base cloze should be created for biti
        item = db.get_collocation_by_lemma("biti")
        assert item is None

        # Other function words (e.g. "kje" if present) still get base clozes fine.
        # In this lesson ("Sem doma."), there are no other function words — just verify
        # no biti row was created regardless of surface.
        with db._get_conn() as conn:
            rows = conn.execute("SELECT * FROM collocations WHERE lemma = 'biti'").fetchall()
        assert len(rows) == 0

    async def test_listen_skips_biti_via_pos_aux(self, monkeypatch):
        """A biti surface (ste → classla AUX) is a clozes-only verb — no base cloze
        created by /listen. Without an analyzer 'ste' would fall through to
        standalone vocab, but even with POS detection biti is skipped."""
        import app.api.srs as srs_mod
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_analysis("ste", "biti", upos="AUX")
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)
        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", AsyncMock())

        db = await self._setup_lesson(phrase_text="Zdravo kje ste")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # No base cloze for biti
        item = db.get_collocation_by_lemma("biti")
        assert item is None

        # Other function words (kje) still get base clozes
        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.card_type == "cloze"

    async def test_listen_creates_no_base_cloze_for_biti(self, monkeypatch):
        """A lesson with biti surfaces creates NO base cloze — only click-triggered
        conjugation clozes. Regression for the biti clozes-only special case."""
        import app.api.srs as srs_mod
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_lemma("ste", "biti")
        stub.set_lemma("sem", "biti")
        stub.set_analysis("ste", "biti", upos="AUX")
        stub.set_analysis("sem", "biti", upos="AUX")
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)
        audio_mock = AsyncMock()
        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", audio_mock)

        db = await self._setup_lesson(phrase_text="Sem doma, kje ste")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # biti should have NO base cloze (card_type='cloze', lemma='biti', empty disambig)
        with db._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM collocations WHERE lemma = 'biti' AND card_type = 'cloze' AND (disambig_key IS NULL OR disambig_key = '')"
            ).fetchall()
        assert len(rows) == 0, f"Expected no base cloze for biti, got {len(rows)}"

        # Other function words in the lesson (kje) should still get their base clozes
        with db._get_conn() as conn:
            kje_row = conn.execute("SELECT * FROM collocations WHERE lemma = 'kje'").fetchone()
        assert kje_row is not None

    async def test_listen_cloze_audio_uses_surface_not_lemma(self, monkeypatch):
        """The answer-word audio for a plain cloze is synthesized from the surface
        that was blanked, not the dictionary lemma. Uses a non-biti function word
        (biti is clozes-only and no longer creates a base cloze)."""
        import app.api.srs as srs_mod

        # Use a mock lemmatizer that diverges lemma from surface for a non-biti
        # function word: "kje" maps to lemma "kje" (same), so we fake a different
        # scenario: surface "Kje" (capitalized) maps to "kje"
        audio_mock = AsyncMock()
        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", audio_mock)

        await self._setup_lesson(phrase_text="Kje je banka?")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert resp.status_code == 200

        # Cloze for kje — audio uses the surface that appears in the sentence
        words = [call.args[3] for call in audio_mock.await_args_list]
        assert "Kje" in words

    async def test_listen_existing_cloze_surface_keyed(self, monkeypatch):
        """A cloze card keyed by surface (not lemma) is found via the surface
        fallback and does not crash on re-query — the id is carried from the
        initial lookup rather than re-queried by lemma."""
        import app.api.srs as srs_mod
        from app.models.syntactic_unit import SyntacticUnit

        def fake_lemmatize(surfaces, text, lemmatizer, language_code, db=None, model_version=""):
            return ["pozdrav" if s.lower() == "zdravo" else s.lower() for s in surfaces]

        monkeypatch.setattr(srs_mod, "lemmatize_surfaces_in_context", fake_lemmatize)
        audio_mock = AsyncMock()
        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", audio_mock)

        db = await self._setup_lesson(phrase_text="Zdravo svet")

        # Pre-create a cloze card keyed by the surface form, not the lemma
        pre_unit = SyntacticUnit(
            text="zdravo",
            translation="hello",
            word_count=1,
            difficulty=1,
            source="test",
            lemma="zdravo",
            card_type="cloze",
            source_sentence="Zdravo svet",
        )
        db.add_collocation(pre_unit, language_code="sl")
        pre_id, _ = db.get_collocation_by_lemma_with_id("zdravo")
        assert pre_id is not None

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # No new card was created for the dictionary lemma (surface-keyed one was reused)
        assert db.get_collocation_by_lemma("pozdrav") is None

        # Audio was synthesized for the pre-existing card, not a new one
        assert audio_mock.await_args is not None
        call_ids = [call.args[1] for call in audio_mock.await_args_list]
        assert pre_id in call_ids

    async def test_listen_creates_vocab_for_unknown_content_word(self):
        """Unknown content-word lemma → vocab row, state='new', both directions present."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation_by_lemma("banka")
        assert item is not None
        assert item.syntactic_unit.card_type == "vocab"
        assert Direction.RECOGNITION in item.directions
        assert Direction.PRODUCTION in item.directions
        assert item.directions[Direction.RECOGNITION].state == SRSState.NEW
        assert item.directions[Direction.RECOGNITION].reps == 0

    async def test_listen_grades_recognition_when_learning(self):
        """Pre-existing vocab with recognition state=LEARNING → grade recognition, production unchanged."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        # Force banka's recognition to LEARNING
        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.LEARNING
        rec.reps = 1
        db.update_collocation(banka)

        prod_before = banka.directions.get(Direction.PRODUCTION)
        prod_reps_before = prod_before.reps if prod_before else 0

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        rec = banka.directions[Direction.RECOGNITION]
        assert rec.reps == 2  # graded
        assert rec.state != SRSState.NEW

        prod = banka.directions.get(Direction.PRODUCTION)
        if prod:
            assert prod.reps == prod_reps_before

    async def test_listen_grades_recognition_when_review_first_time_today(self):
        """Pre-existing vocab with rec state=REVIEW, last_review 2 days ago → graded."""
        from datetime import timedelta

        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(days=2)
        rec.reps = 5
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 6

    async def test_listen_skips_recognition_when_review_already_today(self):
        """Pre-existing vocab with rec state=REVIEW, last_review today → no grade."""

        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC)
        rec.reps = 5
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 5  # unchanged

    async def test_listen_creates_no_morphology_clozes_after_phase_4b(self):
        """Phase 4b: /listen on a lesson with inflected A1 surfaces creates zero morphology clozes."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(
                            text="Grem v Ljubljano. Sem v hotelu.",
                            voice_id="female-1",
                            language_code="sl",
                            role="female-1",
                        ),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {},
                "sentence_translations": {
                    "Grem v Ljubljano. Sem v hotelu.": "I'm going to Ljubljana. I'm at the hotel."
                },
                "morphology_focus": [
                    {"lemma": "ljubljana", "surface": "Ljubljano", "feature": "noun:acc:sg", "gloss": "Ljubljana"},
                ],
            },
        )
        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("phase-4b-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "phase-4b-1"})
        assert response.status_code == 200

        with db._get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS cnt FROM collocations WHERE card_type = 'cloze' AND disambig_key LIKE 'morph:%'",
            ).fetchall()
        assert rows[0]["cnt"] == 0, "Phase 4b: /listen should create zero morphology clozes"

    async def test_listen_never_grades_production(self):
        """Pre-existing vocab with production state=LEARNING → /listen does not touch production."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        if Direction.PRODUCTION in banka.directions:
            prod = banka.directions[Direction.PRODUCTION]
            prod.state = SRSState.LEARNING
            prod.reps = 3
            db.update_collocation(banka)  # saves RECOGNITION, not production

            # Directly update production in DB
            db.update_direction(banka.guid, Direction.PRODUCTION, prod)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        prod = banka.directions.get(Direction.PRODUCTION)
        if prod:
            assert prod.reps == 3  # unchanged
            assert prod.state == SRSState.LEARNING  # unchanged

    async def test_listen_never_grades_cloze(self):
        """Pre-existing cloze row → /listen never grades it."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        kje = db.get_collocation_by_lemma("kje")
        assert kje is not None
        assert kje.syntactic_unit.card_type == "cloze"
        prod = kje.directions[Direction.PRODUCTION]
        prod.state = SRSState.LEARNING
        prod.reps = 1
        db.update_direction(kje.guid, Direction.PRODUCTION, prod)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        kje = db.get_collocation_by_lemma("kje")
        prod = kje.directions[Direction.PRODUCTION]
        assert prod.reps == 1  # unchanged
        assert prod.state == SRSState.LEARNING  # unchanged

    async def test_listen_skips_existing_new_state(self):
        """Pre-existing vocab, recognition state=NEW → no grade."""
        from app.models.srs_item import Direction

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        # Already state=NEW after creation
        rec_reps_before = banka.directions[Direction.RECOGNITION].reps

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == rec_reps_before

    async def test_listen_skips_cloze_and_non_slovene_content_still_created(self):
        """Non-Slovene lesson: content words created as vocab (no en function-word list)."""

        db = await self._setup_lesson(
            phrase_text="Where is the bank?",
            language_code="en",
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # English words are all "content" (no function-word list for en) → created as vocab
        for lemma in ("where", "is", "the", "bank"):
            item = db.get_collocation_by_lemma(lemma)
            assert item is not None, f"{lemma} should be created as vocab"
            assert item.syntactic_unit.card_type == "vocab"
        # Cloze card type should not appear
        assert not any(
            db.get_collocation_by_lemma(lemma).syntactic_unit.card_type == "cloze"
            for lemma in ("where", "is", "the", "bank")
        )

    async def test_listen_cloze_returns_card_type_and_source_sentence_via_api(self):
        """Cloze items expose card_type and source_sentence via the items API."""
        await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

            response = await client.get("/api/srs/items", params={"limit": 50})
        assert response.status_code == 200
        items = {i["text"]: i for i in response.json()["items"]}

        kje = items.get("kje")
        assert kje is not None
        assert kje["card_type"] == "cloze"
        assert kje["source_sentence"] == "{{c1::Kje}} je banka?"

        banka = items.get("banka")
        assert banka is not None
        assert banka["card_type"] == "vocab"

    async def test_listen_cloze_response_state_reflects_production_direction(self):
        """`_item_to_dict` for cloze items reads state from PRODUCTION."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

            item = db.get_collocation_by_lemma("kje")
            assert item is not None
            item.directions[Direction.PRODUCTION].state = SRSState.LEARNING
            item.directions[Direction.PRODUCTION].reps = 2
            db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

            response = await client.get("/api/srs/items", params={"limit": 50})
        assert response.status_code == 200
        items = {i["text"]: i for i in response.json()["items"]}
        kje = items["kje"]
        assert kje["state"] == "learning"
        assert kje["reps"] == 2

    async def test_listen_with_flag_on_still_registers_key_phrases(self):
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        assert lesson is not None
        lesson.key_phrases = [
            KeyPhraseInfo(phrase="dober dan", translation="good day"),
            KeyPhraseInfo(phrase="hvala lepa", translation="thank you very much"),
        ]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        kp1 = db.get_collocation("dober dan")
        assert kp1 is not None
        kp2 = db.get_collocation("hvala lepa")
        assert kp2 is not None

        assert db.get_collocation_by_lemma("kje") is not None
        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        assert banka.syntactic_unit.card_type == "vocab"

    # ── Key-phrase auto-grade tests ──────────────────────────────────────

    async def test_listen_grades_key_phrase_when_recognition_learning(self):
        """Pre-existing KP with rec state=LEARNING → reps incremented, production untouched."""
        from app.models.srs_item import Direction, SRSState
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.LEARNING
        rec.reps = 1
        db.update_collocation(item)

        prod_before = item.directions.get(Direction.PRODUCTION)
        prod_reps_before = prod_before.reps if prod_before else 0

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        rec = item.directions[Direction.RECOGNITION]
        assert rec.reps == 2  # graded
        assert rec.state != SRSState.NEW

        prod = item.directions.get(Direction.PRODUCTION)
        if prod:
            assert prod.reps == prod_reps_before

    async def test_listen_grades_key_phrase_when_review_first_time_today(self):
        """Pre-existing KP with rec state=REVIEW, last_review 2 days ago → graded (reps+1)."""
        from datetime import timedelta

        from app.models.srs_item import Direction, SRSState
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(days=2)
        rec.reps = 5
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.directions[Direction.RECOGNITION].reps == 6

    async def test_listen_skips_key_phrase_when_review_already_today(self):
        """Pre-existing KP with rec state=REVIEW, last_review today → no grade."""

        from app.models.srs_item import Direction, SRSState
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC)
        rec.reps = 5
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.directions[Direction.RECOGNITION].reps == 5  # unchanged

    async def test_listen_skips_key_phrase_when_recognition_new(self):
        """Pre-existing KP with rec state=NEW → no grade."""
        from app.models.srs_item import Direction
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec_reps_before = item.directions[Direction.RECOGNITION].reps

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.directions[Direction.RECOGNITION].reps == rec_reps_before  # unchanged

    async def test_listen_skips_key_phrase_when_recognition_known(self):
        """Pre-existing KP with rec state=KNOWN → no grade."""
        from app.models.srs_item import Direction, SRSState
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.KNOWN
        rec.reps = 3
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.directions[Direction.RECOGNITION].reps == 3  # unchanged

    async def test_listen_creates_cloze_with_sentence_translation_from_metadata(self):
        """New cloze uses sentence_translations from lesson.generation_metadata."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.generation_metadata = {"sentence_translations": {"Kje je banka?": "Where is the bank?"}}
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.source_sentence_translation == "Where is the bank?"

    async def test_listen_creates_cloze_with_sentence_translation_from_translated_section(self):
        """When metadata is missing, /listen falls back to deriving from TRANSLATED section."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        # No sentence_translations in metadata; emulate pre-Layer-N stored lesson
        lesson.generation_metadata = {}
        lesson.sections.append(
            Section(
                section_type=SectionType.TRANSLATED,
                phrases=[
                    Phrase(text="Kje je banka?", voice_id="v", language_code="sl"),
                    Phrase(text="Where is the bank?", voice_id="v", language_code="en"),
                ],
            )
        )
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.source_sentence_translation == "Where is the bank?"

    async def test_listen_backfills_existing_cloze_sentence_translation(self):
        """Existing cloze with empty sentence_translation gets populated and marked dirty."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.source_sentence_translation == ""

        # Simulate "card already in Anki" so sync_push will see it: stamp anki_note_id.
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET anki_note_id = 9999 WHERE guid = ?", (item_kje.guid,))
            conn.commit()

        # Re-load lesson with TRANSLATED section so /listen has source data
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.generation_metadata = {"sentence_translations": {"Kje je banka?": "Where is the bank?"}}
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje.syntactic_unit.source_sentence_translation == "Where is the bank?"

        dirty = db.get_dirty_fields(item_kje.guid)
        assert "sentence_translation" in dirty.split(",")

    async def test_listen_skips_key_phrase_when_cloze(self):
        """Key phrase existing as cloze (defensive) → skip, no crash."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        # Manually change card_type to cloze
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET card_type = 'cloze' WHERE text = 'dober dan'")
            conn.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.syntactic_unit.card_type == "cloze"

    async def test_listen_skips_key_phrase_without_recognition_direction(self):
        """Key phrase existing without RECOGNITION direction (defensive) → skip, no crash."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        # Delete recognition direction
        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE collocation_id = (SELECT id FROM collocations WHERE text = 'dober dan') AND direction = 'recognition'"
            )
            conn.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

    async def test_listen_skips_existing_cloze_with_populated_sentence_translation(self):
        """Existing cloze already has sentence_translation → no re-backfill, dirty_fields unchanged."""
        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        # Manually set sentence_translation to simulate already-backfilled state
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET sentence_translation = 'already set' WHERE text = 'kje'")
            conn.commit()
        # Clear dirty fields to verify they aren't re-marked
        item = db.get_collocation_by_lemma("kje")
        db.set_dirty_fields(item.guid, "")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation_by_lemma("kje")
        assert item.syntactic_unit.source_sentence_translation == "already set"
        dirty = db.get_dirty_fields(item.guid)
        assert dirty == ""  # not re-marked

    # ── Edge cases for _listen_grade_eligible (lines 228, 233, 236, 238) ──

    async def test_listen_skips_known_state(self):
        """Pre-existing vocab, recognition state=KNOWN → skip (no grade)."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.KNOWN
        rec.reps = 3
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 3

    async def test_listen_grades_review_with_no_last_review(self):
        """REVIEW state with last_review=None → eligible for grade (line 233)."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = None
        rec.reps = 5
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 6

    async def test_listen_grades_review_with_suspended_state(self):
        """SUSPENDED state → skip (not eligible for grade), covers fallthrough at line 238."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.SUSPENDED
        rec.reps = 3
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 3

    async def test_listen_skips_vocab_without_recognition_direction(self):
        """Line 314 via /listen: vocab card without RECOGNITION direction → skip (no crash)."""

        db = await self._setup_lesson(phrase_text="testword")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE collocation_id = (SELECT id FROM collocations WHERE lemma = 'testword') AND direction = 'recognition'",
            )
            conn.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

    async def test_listen_existing_cloze_audio_uses_raw_sentence(self, monkeypatch):
        """Existing cloze audio backfill reads the raw sentence, not the pre-clozed
        source_sentence (which contains {{c1::…}} markup under Phase-2b)."""
        from datetime import date

        import app.api.srs as srs_mod
        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        # Seed a cloze row with pre-clozed source_sentence (simulating Phase-2b
        # storage) and no sentence audio.
        cloze_unit = SyntacticUnit(
            text="je",
            translation="is",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="je",
            card_type="cloze",
            source_sentence="Kje {{c1::je}} banka?",
        )
        cloze_dir = {
            Direction.PRODUCTION: DirectionState(
                Direction.PRODUCTION,
                date.today(),
                state=SRSState.NEW,
            )
        }
        db.upsert_by_guid(cloze_unit, "sl", cloze_dir)

        audio_mock = AsyncMock()
        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", audio_mock)

        store = ContentStore(":memory:")
        app.state.content_store = store
        app.state.language = None
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )
        store.save_lesson("lesson-a", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-a"})
        assert response.status_code == 200

        assert len(audio_mock.await_args_list) > 0
        for call in audio_mock.await_args_list:
            sent = call.args[2]
            assert isinstance(sent, str), f"sentence arg is not str: {sent!r}"
            assert "{{c1" not in sent, f"sentence arg contains cloze markup: {sent!r}"
            assert sent == "Kje je banka?"


class TestListenGradeEligible:
    """Direct unit tests for _listen_grade_eligible edge cases."""

    def test_rec_is_none_returns_false(self):

        from app.api.srs import _listen_grade_eligible

        assert _listen_grade_eligible(None, datetime.now(UTC), datetime.now(UTC)) is False

    def test_legacy_date_last_review_returns_true(self):
        from datetime import date, timedelta

        from app.api.srs import _listen_grade_eligible
        from app.models.srs_item import Direction, DirectionState, SRSState

        rec = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            state=SRSState.REVIEW,
            reps=5,
            last_review=date(2026, 5, 1),  # legacy date, not datetime
        )
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        assert _listen_grade_eligible(rec, today_start, today_end) is True

    def test_non_cloze_without_recognition_skipped(self):
        """Line 314: existing item is vocab but has no RECOGNITION direction → skip (no crash)."""

        from app.models.srs_item import Direction
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(
            text="testword",
            translation="",
            word_count=1,
            difficulty=1,
            source="test",
            lemma="testword",
            card_type="vocab",
        )
        db.add_collocation(unit, language_code="sl")
        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE collocation_id = (SELECT id FROM collocations WHERE lemma = 'testword') AND direction = 'recognition'"
            )
            conn.commit()

        item = db.get_collocation_by_lemma("testword")
        assert item is not None
        assert Direction.RECOGNITION not in item.directions
        assert item.syntactic_unit.card_type == "vocab"

        from datetime import timedelta

        from app.api.srs import _listen_grade_eligible

        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        rec = item.directions.get(Direction.RECOGNITION)
        assert _listen_grade_eligible(rec, today_start, today_end) is False


class TestQueueStatsEndpoint:
    """Tests for GET /api/srs/queue-stats."""

    async def test_queue_stats_returns_200_with_shape(self):
        from unittest.mock import patch

        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        with patch("app.api.srs.resolve_daily_new_cap", return_value=(20, "default")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        assert response.status_code == 200
        data = response.json()
        assert "new" in data
        assert "learning" in data
        assert "review" in data
        assert "due" not in data
        assert "daily_new_cap" in data
        assert "cap_source" in data

    async def test_queue_stats_new_is_clamped_at_cap(self):
        from unittest.mock import patch

        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        for i in range(5):
            db.add_collocation(
                SyntacticUnit(text=f"word{i}", translation="t", word_count=1, difficulty=1, source="corpus"),
                language_code="sl",
            )
        app.state.srs_db = db

        with patch("app.api.srs.resolve_daily_new_cap", return_value=(3, "default")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        data = response.json()
        assert data["new"] == 3
        assert data["daily_new_cap"] == 3
        assert data["cap_source"] == "default"

    async def test_queue_stats_cap_source_from_anki(self):
        from unittest.mock import patch

        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        with patch("app.api.srs.resolve_daily_new_cap", return_value=(30, "anki")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        data = response.json()
        assert data["cap_source"] == "anki"
        assert data["daily_new_cap"] == 30

    async def test_queue_stats_review_uses_tt_distinct_collocation_count(self):
        """Review badge is driven by TT's distinct-collocation count
        (sibling-buried equivalent of Anki's COUNT(DISTINCT nid))."""
        from unittest.mock import patch

        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        with (
            patch("app.api.srs.resolve_daily_new_cap", return_value=(20, "default")),
            patch.object(db, "count_review_due_collocations", return_value=42),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        data = response.json()
        assert data["review"] == 42

    async def test_queue_stats_review_budget_excludes_new_introduced_today(self):
        """Regression (Layer 76): new cards introduced today consume the review
        daily limit, so the review badge subtracts count_new_introduced_today.

        Anki charges today's new-card intros against reviews_per_day
        (rslib/decks/limits.rs:104-108). Before the fix TT ignored this term and
        over-counted the review badge by introduced_today — the exact symptom of
        "create a new card in TT, study it, sync, review counts don't match."

        review_cap=50, reviews_today=0, introduced_today=3, review_due_raw=60 →
        review = min(60, 50 - 0 - 3) = 47 (was 50 before the fix).
        """
        from unittest.mock import patch

        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        db.set_anki_state_cache("daily_review_cap", "50")
        db.set_anki_state_cache("daily_new_cap", "20")
        app.state.srs_db = db

        with (
            patch.object(db, "count_review_due_collocations", return_value=60),
            patch.object(db, "count_reviews_completed_today", return_value=0),
            patch.object(db, "count_new_introduced_today", return_value=3),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        data = response.json()
        assert data["review"] == 47, f"expected 50 - 3 introduced = 47, got {data['review']}"


class TestTranscriptEndpoint:
    """Tests for GET /api/srs/lesson/{lesson_id}/transcript."""

    async def test_returns_200_with_correct_shape(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Zdravo.", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="Zdravo", translation="Hello")],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/lesson-1/transcript")

        assert response.status_code == 200
        data = response.json()
        assert data["lesson_id"] == "lesson-1"
        assert isinstance(data["key_phrases"], list)
        assert isinstance(data["dialogue_lines"], list)

    async def test_returns_404_for_missing_lesson(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        app.state.srs_db = SRSDatabase(":memory:")
        app.state.content_store = ContentStore(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/nonexistent/transcript")

        assert response.status_code == 404

    async def test_threads_lesson_language_to_lemmatizer(self, monkeypatch):
        """Guardrail (item #25): the transcript endpoint resolves the lemmatizer for
        the LESSON's language, not a process-wide default — so a Norwegian lesson is
        analyzed with the Norwegian engine even when both languages share one process.
        """
        import app.api.srs as srs_mod
        from app.srs.database import SRSDatabase
        from app.srs.lemmatizer import LowercaseLemmatizer
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Dag 1",
            language_code="no",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Hei.", voice_id="female-1", language_code="no", role="female-1")],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-no", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        captured: list[str] = []

        def _spy(code: str):
            captured.append(code)
            return LowercaseLemmatizer()

        monkeypatch.setattr(srs_mod, "get_lemmatizer", _spy)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/lesson-no/transcript")

        assert response.status_code == 200
        assert captured == ["no"], f"expected the lemmatizer resolved for 'no', got {captured}"

    async def test_l2_filter_excludes_english_narrator(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Scene: At the market", voice_id="narrator", language_code="en", role="narrator"),
                        Phrase(text="Zdravo.", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/lesson-1/transcript")

        data = response.json()
        assert len(data["dialogue_lines"]) == 1
        assert data["dialogue_lines"][0]["role"] == "female-1"

    async def test_known_word_has_correct_srs_state(self):
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        db.add_collocation(unit, language_code="sl")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/lesson-1/transcript")

        data = response.json()
        word = data["dialogue_lines"][0]["words"][0]
        assert word["srs_state"] == "new"
        assert word["lemma"] == "banka"
        assert word["surface"] == "banka"


def _make_mock_lesson_with_sections() -> Lesson:
    return Lesson(
        title="Day 1: Ordering Coffee",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.KEY_PHRASES,
                phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
            ),
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl")],
            ),
        ],
    )


def _fake_render(lesson, full_path, section_paths=None):
    """Fake renderer.render: writes minimal audio bytes and returns mock cues."""
    full_path.write_bytes(b"audio")
    if section_paths:
        for sp in section_paths:
            sp.write_bytes(b"section audio")
    from app.audio.cues import Cue

    cues = [
        Cue(
            index=0,
            start_ms=0,
            end_ms=1000,
            section_index=None,
            section_type=None,
            phrase_index=0,
            role="narrator",
            language_code="en",
            text=lesson.title,
            ref={"kind": "narration"},
        )
    ]
    idx = 1
    for si, section in enumerate(lesson.sections):
        for pi, phrase in enumerate(section.phrases):
            cues.append(
                Cue(
                    index=idx,
                    start_ms=idx * 1000,
                    end_ms=(idx + 1) * 1000,
                    section_index=si,
                    section_type=section.section_type.value,
                    phrase_index=pi,
                    role=phrase.role,
                    language_code=phrase.language_code,
                    text=phrase.text,
                    ref={"kind": "line", "target_index": 0},
                )
            )
            idx += 1
    return cues


class TestAudioEndpoints:
    """Tests for audio render and retrieval endpoints."""

    async def test_render_audio_returns_404_for_missing_lesson(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/audio/render", json={"lesson_id": "nonexistent"})
        assert response.status_code == 404

    async def test_audio_render_returns_202(self, tmp_path):
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "test-lesson-id"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/audio/render", json={"lesson_id": lesson_id})

        assert response.status_code == 202
        data = response.json()
        assert "audio_id" in data
        assert store.get_audio_file_row(data["audio_id"]) is not None

    async def test_render_returns_sections_in_response(self, tmp_path):
        """POST /api/audio/render response includes a sections array."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-sections-test"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/audio/render", json={"lesson_id": lesson_id})

        assert response.status_code == 202
        data = response.json()
        assert "sections" in data
        assert len(data["sections"]) == len(mock_lesson.sections)

        sec = data["sections"][0]
        assert "audio_id" in sec
        assert sec["section_index"] == 0
        assert sec["section_type"] == "key_phrases"
        assert sec["title"] == "Key Phrases"

    async def test_render_replaces_existing_rows(self, tmp_path):
        """Re-rendering a lesson replaces stale rows so count is exactly len(sections)+1."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-replace-test"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        expected_count = len(mock_lesson.sections) + 1  # sections + full row

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # First render
            resp1 = await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            assert resp1.status_code == 202
            after_first = store.list_audio_files_for_lesson(lesson_id)
            assert len(after_first) == expected_count, (
                f"Expected {expected_count} rows after first render, got {len(after_first)}"
            )

            # Second render — should replace, not append
            resp2 = await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            assert resp2.status_code == 202
            after_second = store.list_audio_files_for_lesson(lesson_id)
            assert len(after_second) == expected_count, (
                f"Expected {expected_count} rows after re-render, got {len(after_second)}"
            )

            # Audio IDs should be different (new cohort)
            assert resp1.json()["audio_id"] != resp2.json()["audio_id"]

    async def test_failed_rerender_preserves_existing_rows(self, tmp_path):
        """A render that raises must not leave the lesson without audio rows.

        Guards backlog 14: the old code deleted rows *before* rendering, so a
        render failure 404'd the lesson even though the old files were still on
        disk. Now rows are deleted only after a successful render.
        """
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-failed-rerender"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        expected_count = len(mock_lesson.sections) + 1

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp1 = await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            assert resp1.status_code == 202
            assert len(store.list_audio_files_for_lesson(lesson_id)) == expected_count

            # Second render fails mid-flight.
            mock_renderer.render = AsyncMock(side_effect=RuntimeError("edge-tts blew up"))
            with pytest.raises(RuntimeError):
                await client.post("/api/audio/render", json={"lesson_id": lesson_id})

            # The old rows must survive — the lesson still has its audio.
            after_fail = store.list_audio_files_for_lesson(lesson_id)
            assert len(after_fail) == expected_count, "failed render wiped the existing audio rows"

    async def test_successful_rerender_unlinks_old_files(self, tmp_path):
        """A successful re-render removes the previous cohort's files from disk.

        Guards backlog 14 part 2: every render mints new UUID paths, so without
        an explicit unlink the old files leaked forever.
        """
        from pathlib import Path

        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-unlink-old"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            old_paths = [r["file_path"] for r in store.list_audio_files_for_lesson(lesson_id)]
            assert old_paths and all(Path(p).exists() for p in old_paths)

            await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            new_paths = [r["file_path"] for r in store.list_audio_files_for_lesson(lesson_id)]

            assert all(not Path(p).exists() for p in old_paths), "old cohort files were not unlinked"
            assert all(Path(p).exists() for p in new_paths), "new cohort files should be on disk"
            assert set(old_paths).isdisjoint(new_paths)

    async def test_render_returns_cues_in_post_response(self, tmp_path):
        """POST /api/audio/render includes cues in the response body."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-cues-post"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/audio/render", json={"lesson_id": lesson_id})

        assert response.status_code == 202
        data = response.json()
        assert "cues" in data
        assert len(data["cues"]) > 0
        first = data["cues"][0]
        assert "start_ms" in first
        assert "end_ms" in first
        assert "index" in first
        assert "text" in first

    async def test_render_persists_cues_in_store(self, tmp_path):
        """After render, cues are persisted on the full-lesson audio row."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-cues-persist"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/audio/render", json={"lesson_id": lesson_id})

        assert response.status_code == 202
        data = response.json()
        full_row = store.get_audio_file_row(data["audio_id"])
        assert full_row is not None
        assert full_row["cues_json"] is not None

    async def test_get_lesson_audio_endpoint(self, tmp_path):
        """GET /api/audio/lesson/{lesson_id} returns existing audio files list."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-lookup-test"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        # First render
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            response = await client.get(f"/api/audio/lesson/{lesson_id}")

        assert response.status_code == 200
        data = response.json()
        assert "audio_id" in data
        assert "sections" in data
        assert len(data["sections"]) == len(mock_lesson.sections)

    async def test_get_lesson_audio_includes_cues(self, tmp_path):
        """GET /api/audio/lesson/{id} includes cues in the response."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-cues-get"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            response = await client.get(f"/api/audio/lesson/{lesson_id}")

        assert response.status_code == 200
        data = response.json()
        assert "cues" in data
        assert len(data["cues"]) > 0

        # A7: each section carries its own rebased cue list (not just the full
        # track). Without this the frontend's per-variant subtitle sync is dead.
        for s in data["sections"]:
            assert "cues" in s
        natural = next(s for s in data["sections"] if s["section_type"] == "natural_speed")
        assert natural["cues"] is not None
        assert natural["cues"][0]["start_ms"] == 0  # rebased to its own zero

    async def test_get_lesson_audio_scrubs_slow_section_cue_text(self, tmp_path):
        """A7 + A6: a slow section's cues expose natural text through the API,
        never the ellipsis-broken text that drives TTS."""
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="T",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="hvala", voice_id="v", language_code="sl")],
                ),
                Section(
                    section_type=SectionType.SLOW_SPEED,
                    phrases=[Phrase(text="hva ... la", voice_id="v", language_code="sl")],
                ),
            ],
        )
        store = ContentStore(":memory:")
        lesson_id = "lesson-slow-scrub"
        store.save_lesson(lesson_id, "cur", 1, lesson)

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)
        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            response = await client.get(f"/api/audio/lesson/{lesson_id}")

        data = response.json()
        slow = next(s for s in data["sections"] if s["section_type"] == "slow_speed")
        line_texts = [
            c["text"] for c in slow["cues"] if c["language_code"] == "sl" and (c["ref"] or {}).get("kind") == "line"
        ]
        assert line_texts == ["hvala"]
        assert all(" ... " not in t for t in line_texts)

    async def test_get_lesson_audio_returns_null_cues_for_old_lesson(self, tmp_path):
        """GET /api/audio/lesson/{id} returns cues:null for lessons without manifest."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        lesson_id = "old-no-cues"

        # Insert a full-lesson row with cues_json=NULL (simulating pre-manifest lesson)
        store.save_audio_file("old-full-id", lesson_id, "/tmp/old.wav")
        store.save_audio_file(
            "old-sec-id", lesson_id, "/tmp/old-sec.wav", section_index=0, section_type="natural_speed"
        )

        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/audio/lesson/{lesson_id}")

        assert response.status_code == 200
        data = response.json()
        assert "cues" in data
        assert data["cues"] is None

    async def test_get_lesson_audio_returns_404_when_not_rendered(self):
        """GET /api/audio/lesson/{lesson_id} returns 404 when no audio exists."""
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/never-rendered-id")
        assert response.status_code == 404

    async def test_get_audio_sets_content_disposition(self, tmp_path):
        """GET /api/audio/{audio_id} sets Content-Disposition with sanitized filename."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c-dl", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c-dl", curriculum)
        lesson_id = "lesson-download-test"
        store.save_lesson(lesson_id, "c-dl", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            render_resp = await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            data = render_resp.json()

            # Check full lesson download filename
            full_audio_id = data["audio_id"]
            response = await client.get(f"/api/audio/{full_audio_id}")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "attachment" in cd
        # Default delivery codec is opus, so the rendered file is served as .opus.
        assert ".opus" in cd
        assert "ordering_coffee" in cd.lower()

    async def test_get_audio_serves_ogg_media_type_for_opus_file(self, tmp_path):
        """A stored .opus file is served as audio/ogg (media type inferred from suffix)."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        opus = tmp_path / "audio.opus"
        opus.write_bytes(b"OggS-fake-opus")
        store.save_audio_file("opus-audio", "ghost-lesson", str(opus))
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/opus-audio")

        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/ogg"
        assert ".opus" in response.headers.get("content-disposition", "")

    async def test_get_audio_serves_wav_media_type_for_wav_file(self, tmp_path):
        """A pre-existing .wav file still serves as audio/wav (back-compat)."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"RIFF-fake-wav")
        store.save_audio_file("wav-audio", "ghost-lesson", str(wav))
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/wav-audio")

        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"

    async def test_get_audio_section_content_disposition(self, tmp_path):
        """GET /api/audio/{section_audio_id} includes topic, day, and section type in filename."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c-sec-dl", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c-sec-dl", curriculum)
        lesson_id = "lesson-sec-dl-test"
        store.save_lesson(lesson_id, "c-sec-dl", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            render_resp = await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            sec_audio_id = render_resp.json()["sections"][0]["audio_id"]
            response = await client.get(f"/api/audio/{sec_audio_id}")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert "Key_Phrases" in cd
        assert "ordering_coffee" in cd.lower()
        assert "Day01" in cd

    async def test_audio_get_returns_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/nonexistent-id")
        assert response.status_code == 404

    async def test_get_lesson_audio_returns_404_when_no_full_row(self):
        """GET /audio/lesson/{id} returns 404 when only section rows exist (no full-lesson row)."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        # Save a section-only row (section_index=0, no full row)
        store.save_audio_file("sec-1", "lesson-x", "/tmp/sec.wav", section_index=0, section_type="key_phrases")
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-x")

        assert response.status_code == 404
        assert "Full lesson audio" in response.json()["detail"]

    async def test_get_lesson_audio_falls_back_for_unknown_section_type(self):
        """GET /audio/lesson/{id} gracefully uses raw string when section_type is unrecognized."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        # Full row
        store.save_audio_file("full-1", "lesson-y", "/tmp/full.wav")
        # Section row with unknown section_type
        store.save_audio_file("sec-2", "lesson-y", "/tmp/sec.wav", section_index=0, section_type="unknown_custom")
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-y")

        assert response.status_code == 200
        data = response.json()
        # The title should fall back to the raw section_type string
        assert data["sections"][0]["title"] == "unknown_custom"

    async def test_get_audio_returns_404_when_file_missing_on_disk(self, tmp_path):
        """GET /api/audio/{audio_id} returns 404 when DB row exists but file is absent."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        nonexistent_path = str(tmp_path / "does_not_exist.wav")
        store.save_audio_file("audio-gone", "lesson-z", nonexistent_path)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/audio-gone")

        assert response.status_code == 404
        assert "missing" in response.json()["detail"]

    # ── ZIP download endpoint tests ───────────────────────────────────────

    async def test_zip_download_returns_zip_with_sections(self, tmp_path):
        """GET /api/audio/lesson/{id}/zip returns a ZIP containing all section WAVs."""
        import io
        import zipfile

        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c1", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c1", curriculum)
        store.save_lesson("lesson-zip-1", "c1", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": "lesson-zip-1"})
            response = await client.get("/api/audio/lesson/lesson-zip-1/zip")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(response.content))
        names = z.namelist()
        # full lesson + one per section
        assert len(names) == len(mock_lesson.sections) + 1

    async def test_zip_download_filenames_include_topic_and_day(self, tmp_path):
        """ZIP filenames include sanitized curriculum topic and zero-padded day."""
        import io
        import zipfile

        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c2", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c2", curriculum)
        store.save_lesson("lesson-zip-2", "c2", 3, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": "lesson-zip-2"})
            response = await client.get("/api/audio/lesson/lesson-zip-2/zip")

        assert response.status_code == 200
        z = zipfile.ZipFile(io.BytesIO(response.content))
        names = z.namelist()
        for name in names:
            assert "ordering_coffee" in name.lower()
            assert "Day03" in name
        # full file sorts first (00), then sections (01, 02…); opus is the default codec
        assert names[0].endswith("_00_Full.opus")
        assert names[1].endswith("_01_Key_Phrases.opus")
        assert names[2].endswith("_02_Natural_Speed.opus")

    async def test_zip_download_content_disposition_header(self, tmp_path):
        """ZIP Content-Disposition includes topic and day in the filename."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c3", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c3", curriculum)
        store.save_lesson("lesson-zip-3", "c3", 2, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": "lesson-zip-3"})
            response = await client.get("/api/audio/lesson/lesson-zip-3/zip")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert ".zip" in cd
        assert "ordering_coffee" in cd.lower()
        assert "Day02" in cd

    async def test_zip_download_returns_404_when_no_audio(self):
        """GET /api/audio/lesson/{id}/zip returns 404 when no audio exists."""
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/no-audio/zip")
        assert response.status_code == 404

    async def test_zip_download_returns_404_when_no_sections(self):
        """GET /api/audio/lesson/{id}/zip returns 404 when only a full-lesson row exists."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_audio_file("full-only", "lesson-no-sec", "/tmp/full.wav")
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-no-sec/zip")
        assert response.status_code == 404
        assert "section" in response.json()["detail"].lower()

    async def test_zip_download_falls_back_when_curriculum_missing(self, tmp_path):
        """ZIP endpoint uses lesson title as fallback when curriculum is not found."""
        import io
        import zipfile

        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        # Save lesson with a curriculum_id that has no corresponding curriculum row
        store.save_lesson("lesson-zip-fallback", "missing-c", 5, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": "lesson-zip-fallback"})
            response = await client.get("/api/audio/lesson/lesson-zip-fallback/zip")

        assert response.status_code == 200
        z = zipfile.ZipFile(io.BytesIO(response.content))
        names = z.namelist()
        # full lesson + sections
        assert len(names) == len(mock_lesson.sections) + 1

    async def test_zip_download_returns_404_when_section_file_missing_on_disk(self, tmp_path):
        """ZIP endpoint returns 404 when a section file is absent from disk."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_audio_file("full-x", "lesson-missing-file", str(tmp_path / "full.wav"))
        store.save_audio_file(
            "sec-x", "lesson-missing-file", str(tmp_path / "missing.wav"), section_index=0, section_type="key_phrases"
        )
        # Write the full file but NOT the section file
        (tmp_path / "full.wav").write_bytes(b"audio")
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-missing-file/zip")
        assert response.status_code == 404
        assert "missing" in response.json()["detail"].lower()

    async def test_zip_download_falls_back_to_defaults_when_lesson_row_missing(self, tmp_path):
        """ZIP uses fallback topic/day when no lesson row exists in the DB."""
        import io
        import zipfile

        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        # Save only audio rows — no lesson row in lessons table
        sec_path = tmp_path / "sec.wav"
        sec_path.write_bytes(b"audio")
        store.save_audio_file(
            "sec-no-lesson", "lesson-ghost", str(sec_path), section_index=0, section_type="key_phrases"
        )
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-ghost/zip")

        assert response.status_code == 200
        z = zipfile.ZipFile(io.BytesIO(response.content))
        assert len(z.namelist()) == 1

    async def test_build_section_filename_falls_back_for_unknown_section_type(self, tmp_path):
        """_build_section_filename uses the raw string for unrecognized section types."""
        from app.api.audio import _build_section_filename

        name = _build_section_filename("topic", 1, 0, "custom_unknown")
        assert "custom_unknown" in name
        assert name.endswith(".wav")

    async def test_get_audio_falls_back_when_no_lesson_row(self, tmp_path):
        """GET /api/audio/{id} uses fallback name when lesson row is absent."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"data")
        # Save audio row but no lesson row
        store.save_audio_file("audio-no-lesson", "ghost-lesson", str(wav))
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/audio-no-lesson")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "audio" in cd
        assert ".wav" in cd

    async def test_get_audio_uses_lesson_title_when_curriculum_missing(self, tmp_path):
        """GET /api/audio/{id} falls back to lesson title when curriculum row is absent."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        # Save lesson with no matching curriculum
        store.save_lesson("lesson-no-c", "nonexistent-curriculum", 2, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            render_resp = await client.post("/api/audio/render", json={"lesson_id": "lesson-no-c"})
            sec_id = render_resp.json()["sections"][0]["audio_id"]
            response = await client.get(f"/api/audio/{sec_id}")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert ".opus" in cd
        # Lesson title is "Day 1: Ordering Coffee" → sanitized
        assert "Day_1" in cd or "Ordering_Coffee" in cd


class TestCreatePhraseIntegration:
    """Integration tests for multi-word phrase creation via POST /api/srs/items."""

    async def test_create_multiword_item_returns_201(self):
        """POST /api/srs/items with word_count=2 creates a SyntacticUnit with lemma=None."""
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/items",
                json={"text": "centru mesta", "language_code": "sl", "word_count": 2, "translation": ""},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["text"] == "centru mesta"
        # Multi-word items have no lemma
        assert data.get("translation") == ""

    async def test_create_multiword_item_duplicate_returns_409(self):
        """Second POST with same text returns 409 Conflict."""
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/srs/items",
                json={"text": "centru mesta", "language_code": "sl", "word_count": 2, "translation": ""},
            )
            response = await client.post(
                "/api/srs/items",
                json={"text": "centru mesta", "language_code": "sl", "word_count": 2, "translation": ""},
            )

        assert response.status_code == 409

    async def test_create_phrase_then_transcript_shows_collocation_span(self):
        """After creating 'centru mesta', transcript tokens for that phrase
        share a collocation_span_id and collocation_lemma='centru mesta'."""
        from app.models.lesson import Lesson, Phrase, Section, SectionType
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(
                            text="v centru mesta",
                            voice_id="female-1",
                            language_code="sl",
                            role="female-1",
                        )
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-phrase", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Create the phrase
            create_resp = await client.post(
                "/api/srs/items",
                json={"text": "centru mesta", "language_code": "sl", "word_count": 2, "translation": "city centre"},
            )
            assert create_resp.status_code == 201

            # Fetch transcript — match_spans should pick up the new collocation
            transcript_resp = await client.get("/api/srs/lesson/lesson-phrase/transcript")
            assert transcript_resp.status_code == 200

        data = transcript_resp.json()
        words = data["dialogue_lines"][0]["words"]

        # Find centru and mesta tokens
        centru = next((w for w in words if w["surface"] == "centru"), None)
        mesta = next((w for w in words if w["surface"] == "mesta"), None)

        assert centru is not None, "centru token not found"
        assert mesta is not None, "mesta token not found"
        assert centru["collocation_span_id"] is not None
        assert mesta["collocation_span_id"] is not None
        assert centru["collocation_span_id"] == mesta["collocation_span_id"]
        assert centru["collocation_lemma"] == "centru mesta"


class TestClozeTTSIntegration:
    """Tests for cloze TTS audio generation via /listen and /review-queue."""

    async def test_listen_creates_media_for_new_cloze(self, monkeypatch):
        """New cloze from /listen gets both audio_tts_sentence and audio_tts media rows."""
        import app.audio.cloze_tts as cloze_tts_mod

        async def _fake_tts(text, voice="sl-SI-PetraNeural"):
            return b"fake-mp3"

        monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

        from app.models.lesson import Lesson, Phrase, Section, SectionType
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-ct", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ct"})
        assert response.status_code == 200

        for lemma in ("kje", "je"):
            coll = db.get_collocation_by_lemma_with_id(lemma)
            assert coll is not None, f"{lemma} should exist"
            coll_id, _ = coll
            sent_fn = db.get_sentence_audio_filename(coll_id)
            word_fn = db.get_audio_filename(coll_id)
            assert sent_fn is not None, f"{lemma} missing sentence audio filename"
            assert word_fn is not None, f"{lemma} missing word audio filename"
            assert sent_fn.startswith("tts_sentence_")
            assert word_fn.startswith("tts_")

    async def test_review_queue_returns_word_audio_url_for_cloze(self):
        """Cloze cards in the review queue have word_audio_url set; vocab cards do not."""
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        # Cloze collocation
        cloze_unit = SyntacticUnit(
            text="je",
            translation="is",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="je",
            card_type="cloze",
            source_sentence="Kje je banka?",
        )
        cloze_dir = {
            Direction.PRODUCTION: DirectionState(
                Direction.PRODUCTION,
                date.today(),
                state=SRSState.NEW,
            )
        }
        cloze_id = db.upsert_by_guid(cloze_unit, "sl", cloze_dir)

        db.add_media(cloze_id, "audio_tts_sentence", "tts_sentence_abc.mp3", "/tmp/s.mp3", "", "s1", 100)
        db.add_media(cloze_id, "audio_tts", "tts_je.mp3", "/tmp/w.mp3", "", "w1", 100)

        # Vocab collocation
        vocab_unit = SyntacticUnit(
            text="banka",
            translation="bank",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="banka",
        )
        dirs = {
            Direction.RECOGNITION: DirectionState(Direction.RECOGNITION, date.today(), state=SRSState.NEW),
            Direction.PRODUCTION: DirectionState(Direction.PRODUCTION, date.today(), state=SRSState.NEW),
        }
        vocab_id = db.upsert_by_guid(vocab_unit, "sl", dirs)
        db.add_media(vocab_id, "audio_tts", "tts_banka.mp3", "/tmp/w.mp3", "", "w2", 100)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/review-queue", params={"session_start": "1"})
        assert response.status_code == 200

        items = response.json()["queue"]
        cloze_items = [i for i in items if i.get("card_type") == "cloze"]
        vocab_items = [i for i in items if i.get("card_type") == "vocab"]

        assert len(cloze_items) >= 1
        assert len(vocab_items) >= 1

        for ci in cloze_items:
            assert ci.get("word_audio_url") is not None, f"cloze {ci['text']} missing word_audio_url"
            assert ci.get("audio_url") is not None, f"cloze {ci['text']} missing audio_url"

        for vi in vocab_items:
            assert vi.get("word_audio_url") is None, f"vocab {vi['text']} should not have word_audio_url"
            assert vi.get("audio_url") is not None, f"vocab {vi['text']} missing audio_url"

    async def test_listen_tolerates_synthesizer_error_new_cloze(self, monkeypatch):
        """New function-word cloze card is created even if TTS fails."""
        import app.api.srs as srs_mod

        async def _broken_synth(db, collocation_id, sentence, word, *, voice=None):
            raise RuntimeError("TTS failed")

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", _broken_synth)

        from app.models.lesson import Lesson, Phrase, Section, SectionType
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(
                            text="Kje je banka v Ljubljani?", voice_id="female-1", language_code="sl", role="female-1"
                        ),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {
                    "kje": "where",
                    "je": "is",
                    "banka": "bank",
                    "v": "in",
                    "ljubljana": "Ljubljana",
                },
                "sentence_translations": {
                    "Kje je banka v Ljubljani?": "Where is the bank in Ljubljana?",
                },
            },
        )

        db = SRSDatabase(":memory:")

        store = ContentStore(":memory:")
        store.save_lesson("lesson-ct2", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ct2"})
        assert response.status_code == 200

        # Function-word cloze card should still exist
        coll = db.get_collocation_by_lemma("kje")
        assert coll is not None

    async def test_listen_threads_language_voice_into_cloze_synth(self, monkeypatch):
        """Backlog #28: cloze audio is synthesized in the lesson language's voice,
        not the hardcoded Slovene default (guards the srs.py call-site wiring)."""
        import app.api.srs as srs_mod

        captured: list[str | None] = []

        async def _capture(db, collocation_id, sentence, word, *, voice=None):
            captured.append(voice)

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", _capture)

        from app.models.lesson import Lesson, Phrase, Section, SectionType
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )
        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-ctv", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ctv"})
        assert response.status_code == 200
        # Every synth call for a Slovene lesson uses the Slovene voice.
        assert captured and all(v == "sl-SI-PetraNeural" for v in captured)

    async def test_listen_tolerates_synthesizer_error_existing_cloze(self, monkeypatch):
        """Existing cloze card audio backfill failure doesn't crash the endpoint."""
        import app.api.srs as srs_mod

        calls = [0]

        async def _succeed_once_then_fail(db, collocation_id, sentence, word, *, voice=None):
            calls[0] += 1
            if calls[0] > 1:
                raise RuntimeError("TTS failed on second call")
            return None

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", _succeed_once_then_fail)

        from app.models.lesson import Lesson, Phrase, Section, SectionType
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-ct3", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        # First listen creates cloze cards and succeeds at TTS
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ct3"})
        assert response.status_code == 200
        assert calls[0] >= 1  # at least one TTS call succeeded

        # Second listen should hit the existing cloze backfill path with failure
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ct3"})
        assert response.status_code == 200

        # Cloze card should still exist
        coll = db.get_collocation_by_lemma("kje")
        assert coll is not None
