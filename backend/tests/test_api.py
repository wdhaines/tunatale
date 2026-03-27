"""API endpoint tests."""

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language
from app.models.lesson import Lesson, Phrase, Section, SectionType


@pytest.mark.asyncio
async def test_health_returns_ok():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ── Curriculum endpoints ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_curriculum_returns_201(monkeypatch):
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

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/curriculum/generate",
            json={"topic": "ordering coffee", "cefr_level": "A2", "num_days": 1},
        )

    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["topic"] == "ordering coffee"


@pytest.mark.asyncio
async def test_get_curriculum_returns_404_when_missing():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/curriculum/nonexistent-id")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_curricula_returns_200():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/curriculum")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


# ── Story generation endpoints ────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_story_returns_201(monkeypatch):
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

    # Store a curriculum for lookup
    if not hasattr(app.state, "curricula"):
        app.state.curricula = {}
    curriculum_id = "test-curriculum-id"
    app.state.curricula[curriculum_id] = mock_curriculum

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/story/generate",
            json={"curriculum_id": curriculum_id, "day": 1, "strategy": "WIDER"},
        )

    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert "sections" in data


# ── SRS endpoints ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_srs_due_returns_200():
    from app.srs.database import SRSDatabase

    db = SRSDatabase(":memory:")
    app.state.srs_db = db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/srs/due")

    assert response.status_code == 200
    data = response.json()
    assert "due" in data
    assert isinstance(data["due"], list)
    db.close()


@pytest.mark.asyncio
async def test_srs_stats_returns_200():
    from app.srs.database import SRSDatabase

    db = SRSDatabase(":memory:")
    app.state.srs_db = db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/srs/stats")

    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    db.close()


@pytest.mark.asyncio
async def test_srs_feedback_returns_ok():
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
    db.close()


# ── Audio endpoints ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audio_render_returns_202(tmp_path, monkeypatch):
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

    app.state.renderer = mock_renderer
    app.state.audio_dir = tmp_path

    if not hasattr(app.state, "lessons"):
        app.state.lessons = {}
    lesson_id = "test-lesson-id"
    app.state.lessons[lesson_id] = mock_lesson

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/audio/render", json={"lesson_id": lesson_id})

    assert response.status_code == 202
    data = response.json()
    assert "audio_id" in data


@pytest.mark.asyncio
async def test_audio_get_returns_404_when_missing():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/audio/nonexistent-id")
    assert response.status_code == 404
