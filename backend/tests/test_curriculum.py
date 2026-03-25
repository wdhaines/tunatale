"""Curriculum generation tests."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.generation.curriculum import CurriculumGenerator
from app.generation.prompts import PromptBuilder
from app.models.curriculum import Curriculum
from app.models.language import Language


@pytest.fixture
def language():
    return Language.slovene()


@pytest.fixture
def prompt_builder(language):
    return PromptBuilder()


# ── PromptBuilder ──────────────────────────────────────────────────────────


def test_prompt_includes_topic(prompt_builder, language):
    prompt = prompt_builder.build_curriculum_prompt(
        topic="ordering coffee in Ljubljana",
        language=language,
        cefr_level="A2",
        num_days=3,
    )
    assert "ordering coffee in Ljubljana" in prompt


def test_prompt_includes_language_name(prompt_builder, language):
    prompt = prompt_builder.build_curriculum_prompt(
        topic="coffee",
        language=language,
        cefr_level="A2",
        num_days=3,
    )
    assert "Slovene" in prompt or "slovenščina" in prompt or "sl" in prompt


def test_prompt_includes_cefr_level(prompt_builder, language):
    prompt = prompt_builder.build_curriculum_prompt(
        topic="coffee",
        language=language,
        cefr_level="B1",
        num_days=3,
    )
    assert "B1" in prompt


def test_prompt_includes_json_instruction(prompt_builder, language):
    prompt = prompt_builder.build_curriculum_prompt(
        topic="coffee",
        language=language,
        cefr_level="A2",
        num_days=3,
    )
    assert "JSON" in prompt or "json" in prompt


def test_system_prompt_mentions_slovene(prompt_builder, language):
    system = prompt_builder.build_system_prompt(language=language)
    assert "Slovene" in system or "slovenščina" in system or "sl" in system


# ── CurriculumGenerator ────────────────────────────────────────────────────


def _mock_llm_response(num_days: int = 3) -> str:
    days = [
        {
            "day": i + 1,
            "title": f"Day {i + 1}: Coffee Talk",
            "focus": "Café vocabulary",
            "collocations": ["dober dan", "prosim kavo", "hvala lepa"],
            "learning_objective": "Order coffee in Slovene",
            "story_guidance": "Scene at a Ljubljana café",
        }
        for i in range(num_days)
    ]
    return json.dumps({"days": days})


@pytest.fixture
def mock_llm():
    client = MagicMock()
    client.complete = AsyncMock(return_value=_mock_llm_response(3))
    return client


@pytest.fixture
def generator(mock_llm):
    return CurriculumGenerator(llm_client=mock_llm)


@pytest.mark.asyncio
async def test_generate_returns_curriculum(generator, language):
    curriculum = await generator.generate(
        topic="ordering coffee in Ljubljana",
        language=language,
        cefr_level="A2",
        num_days=3,
    )
    assert isinstance(curriculum, Curriculum)


@pytest.mark.asyncio
async def test_generate_curriculum_has_correct_num_days(generator, language):
    curriculum = await generator.generate(
        topic="ordering coffee in Ljubljana",
        language=language,
        cefr_level="A2",
        num_days=3,
    )
    assert len(curriculum.days) == 3


@pytest.mark.asyncio
async def test_generate_curriculum_days_have_collocations(generator, language):
    curriculum = await generator.generate(
        topic="coffee",
        language=language,
        cefr_level="A2",
        num_days=3,
    )
    for day in curriculum.days:
        assert len(day.collocations) > 0


@pytest.mark.asyncio
async def test_generate_curriculum_has_learning_objectives(generator, language):
    curriculum = await generator.generate(
        topic="coffee",
        language=language,
        cefr_level="A2",
        num_days=3,
    )
    for day in curriculum.days:
        assert day.learning_objective != ""


@pytest.mark.asyncio
async def test_generate_handles_invalid_json_gracefully(language):
    bad_client = MagicMock()
    bad_client.complete = AsyncMock(return_value="not valid json {{{}}")
    gen = CurriculumGenerator(llm_client=bad_client)
    from app.generation.curriculum import CurriculumGenerationError

    with pytest.raises(CurriculumGenerationError):
        await gen.generate(topic="coffee", language=language, cefr_level="A2", num_days=3)


@pytest.mark.asyncio
async def test_generate_curriculum_language_code_set(generator, language):
    curriculum = await generator.generate(
        topic="coffee",
        language=language,
        cefr_level="A2",
        num_days=3,
    )
    assert curriculum.language_code == "sl"


@pytest.mark.asyncio
async def test_generate_curriculum_topic_stored(generator, language):
    curriculum = await generator.generate(
        topic="ordering coffee",
        language=language,
        cefr_level="A2",
        num_days=3,
    )
    assert "coffee" in curriculum.topic.lower()
