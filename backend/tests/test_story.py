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
            "title": "Ordering Coffee",
            "key_phrases": [
                {"phrase": "dober dan", "translation": "good day"},
                {"phrase": "prosim kavo", "translation": "a coffee please"},
            ],
            "scenes": [
                {
                    "label": "At the Riverside Café",
                    "lines": [
                        {"speaker": "female-1", "text": "Dober dan!", "translation": "Good day!"},
                        {"speaker": "male-1", "text": "Prosim kavo.", "translation": "A coffee please."},
                    ],
                }
            ],
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
    # Each key_phrase produces multiple phrases via breakdown; at least 2 input phrases
    assert len(kp_section.phrases) >= 2


@pytest.mark.asyncio
async def test_generate_invalid_json_raises(language, db):
    bad_client = MagicMock()
    bad_client.complete = AsyncMock(return_value="not json")
    gen = StoryGenerator(llm_client=bad_client, srs_db=db)
    day = _make_curriculum_day()
    from app.generation.story import StoryGenerationError

    with pytest.raises(StoryGenerationError):
        await gen.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)


@pytest.mark.asyncio
async def test_generate_key_phrases_have_narrator_translations(generator, language):
    day = _make_curriculum_day()
    lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
    kp_section = next(s for s in lesson.sections if s.section_type == SectionType.KEY_PHRASES)
    narrator_phrases = [p for p in kp_section.phrases if p.role == "narrator"]
    assert len(narrator_phrases) >= 1


@pytest.mark.asyncio
async def test_generate_natural_speed_has_scene_labels(generator, language):
    day = _make_curriculum_day()
    lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
    nat_section = next(s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED)
    narrator_phrases = [p for p in nat_section.phrases if p.role == "narrator"]
    assert any("Riverside" in p.text or "Café" in p.text for p in narrator_phrases)


@pytest.mark.asyncio
async def test_generate_slow_speed_has_ellipsis(generator, language):
    day = _make_curriculum_day()
    lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
    slow_section = next(s for s in lesson.sections if s.section_type == SectionType.SLOW_SPEED)
    dialogue = [p for p in slow_section.phrases if p.role != "narrator"]
    assert any(" ... " in p.text for p in dialogue)


@pytest.mark.asyncio
async def test_generate_translated_interleaves(generator, language):
    day = _make_curriculum_day()
    lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
    trans_section = next(s for s in lesson.sections if s.section_type == SectionType.TRANSLATED)
    narrator_translations = [p for p in trans_section.phrases if p.role == "narrator" and p.language_code == "en"]
    assert len(narrator_translations) >= 2  # at least the translations (not just scene labels)


@pytest.mark.asyncio
async def test_generate_uses_system_prompt(generator, language, mock_llm):
    day = _make_curriculum_day()
    await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
    call_kwargs = mock_llm.complete.call_args
    assert call_kwargs.kwargs.get("system_prompt") is not None or (
        len(call_kwargs.args) > 1 and call_kwargs.args[1] is not None
    )
