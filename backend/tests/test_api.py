"""API endpoint tests."""

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language
from app.models.lesson import Lesson, Phrase, Section, SectionType


@pytest.fixture(autouse=True)
def _clean_app_state():
    yield
    for attr in (
        "content_store",
        "language",
        "curriculum_generator",
        "story_generator",
        "renderer",
        "audio_dir",
        "srs_db",
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

    async def test_generate_curriculum_returns_201(self, monkeypatch):
        from app.storage.store import ContentStore

        mock_curriculum = Curriculum(
            id="test-id",
            topic="ordering coffee",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="First day",
                    focus="greetings",
                    learning_objective="say hello",
                    story_guidance="use dober dan",
                    collocations=["dober dan"],
                )
            ],
        )

        mock_generator = AsyncMock()
        mock_generator.generate = AsyncMock(return_value=mock_curriculum)

        app.state.curriculum_generator = mock_generator
        app.state.language = Language.slovene()
        app.state.content_store = ContentStore(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/curriculum/generate",
                json={"topic": "ordering coffee", "cefr_level": "A2", "num_days": 1},
            )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["topic"] == "ordering coffee"
        # Verify persisted
        restored = app.state.content_store.get_curriculum(data["id"])
        assert restored is not None
        assert restored.topic == "ordering coffee"

    async def test_get_curriculum_returns_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/nonexistent-id")
        assert response.status_code == 404

    async def test_list_curricula_returns_200(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


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

    async def test_get_lesson_returns_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/story/nonexistent-lesson-id")
        assert response.status_code == 404

    async def test_generate_story_returns_201(self, monkeypatch):
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

        store = ContentStore(":memory:")
        curriculum_id = "test-curriculum-id"
        store.save_curriculum(curriculum_id, mock_curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": curriculum_id, "day": 1, "strategy": "WIDER"},
            )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert "sections" in data
        # Verify lesson was persisted
        assert store.get_lesson(data["id"]) is not None


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

    async def test_srs_feedback_returns_ok(self):
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(text="dober dan", translation="good day", word_count=2, difficulty=1, source="llm")
        db.add_collocation(unit)
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/feedback",
                json={"collocation_text": "dober dan", "signal": "no_help"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

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


class TestAudioEndpoints:
    """Tests for audio render and retrieval endpoints."""

    async def test_audio_render_returns_202(self, tmp_path, monkeypatch):
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=lambda lesson, path: path.write_bytes(b"audio"))

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
        # Verify audio file mapping was persisted
        assert store.get_audio_file(data["audio_id"]) is not None

    async def test_audio_get_returns_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/nonexistent-id")
        assert response.status_code == 404
