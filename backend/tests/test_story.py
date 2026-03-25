"""Story generation tests."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.generation.story import StoryGenerator
from app.models.curriculum import CurriculumDay
from app.models.language import Language
from app.models.lesson import Lesson, SectionType
from app.models.strategy import ContentStrategy
from app.srs.database import SRSDatabase


@pytest.fixture
def language():
    return Language.slovene()


@pytest.fixture
def db():
    with SRSDatabase(":memory:") as database:
        yield database


def _make_curriculum_day() -> CurriculumDay:
    return CurriculumDay(
        day=1,
        title="Ordering Coffee",
        focus="Café vocabulary",
        collocations=["dober dan", "prosim kavo", "hvala lepa"],
        learning_objective="Order a coffee using basic Slovene",
        story_guidance="Scene at a Ljubljana café",
    )


def _mock_story_response() -> str:
    return json.dumps(
        {
            "sections": [
                {
                    "type": "key_phrases",
                    "phrases": [
                        {"text": "dober dan", "language": "sl"},
                        {"text": "prosim kavo", "language": "sl"},
                        {"text": "hvala lepa", "language": "sl"},
                    ],
                },
                {
                    "type": "natural_speed",
                    "phrases": [
                        {"text": "Dober dan! Prosim kavo.", "language": "sl"},
                        {"text": "Good day! A coffee please.", "language": "en"},
                    ],
                },
                {
                    "type": "slow_speed",
                    "phrases": [
                        {"text": "Dober dan! Prosim kavo.", "language": "sl"},
                    ],
                },
                {
                    "type": "translated",
                    "phrases": [
                        {"text": "Good day! A coffee please.", "language": "en"},
                    ],
                },
            ]
        }
    )


@pytest.fixture
def mock_llm():
    client = MagicMock()
    client.complete = AsyncMock(return_value=_mock_story_response())
    return client


@pytest.fixture
def generator(mock_llm, db):
    return StoryGenerator(llm_client=mock_llm, srs_db=db)


@pytest.mark.asyncio
async def test_generate_returns_lesson(generator, language):
    day = _make_curriculum_day()
    lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
    assert isinstance(lesson, Lesson)


@pytest.mark.asyncio
async def test_generate_lesson_has_all_four_sections(generator, language):
    day = _make_curriculum_day()
    lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
    section_types = {s.section_type for s in lesson.sections}
    assert SectionType.KEY_PHRASES in section_types
    assert SectionType.NATURAL_SPEED in section_types
    assert SectionType.SLOW_SPEED in section_types
    assert SectionType.TRANSLATED in section_types


@pytest.mark.asyncio
async def test_generate_key_phrases_section_bounded(generator, language):
    day = _make_curriculum_day()
    lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
    kp_section = next(s for s in lesson.sections if s.section_type == SectionType.KEY_PHRASES)
    assert len(kp_section.phrases) <= 8


@pytest.mark.asyncio
async def test_generate_invalid_json_raises(language, db):
    bad_client = MagicMock()
    bad_client.complete = AsyncMock(return_value="not json")
    gen = StoryGenerator(llm_client=bad_client, srs_db=db)
    day = _make_curriculum_day()
    from app.generation.story import StoryGenerationError

    with pytest.raises(StoryGenerationError):
        await gen.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
