"""Story generation tests."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.generation.story import StoryGenerator
from app.models.curriculum import CurriculumDay
from app.models.lesson import Lesson, SectionType
from app.models.strategy import ContentStrategy


def _make_curriculum_day() -> CurriculumDay:
    return CurriculumDay(
        day=1,
        title="Ordering Coffee",
        focus="Café vocabulary",
        collocations=["dober dan", "prosim kavo", "hvala lepa"],
        learning_objective="Order a coffee using basic Slovene",
        story_guidance="Scene at a Ljubljana café",
    )


def _mock_fill_response() -> str:
    """Response for the auto-fill LLM call: maps missing lemmas to translations."""
    return json.dumps({"dober": "good", "dan": "day", "prosim": "please", "kavo": "coffee"})


def _mock_story_response(include_glosses: bool = False) -> str:
    data = {
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
    if include_glosses:
        data["dialogue_glosses"] = [
            {"lemma": "dober", "translation": "good"},
            {"lemma": "dan", "translation": "day"},
            {"lemma": "prosim", "translation": "please"},
            {"lemma": "kavo", "translation": "coffee"},
        ]
    return json.dumps(data)


@pytest.fixture
def mock_llm():
    client = MagicMock()
    client.complete = AsyncMock(side_effect=[_mock_story_response(), _mock_fill_response()])
    return client


@pytest.fixture
def generator(mock_llm):
    return StoryGenerator(llm_client=mock_llm)


class TestStoryGeneration:
    """Tests for StoryGenerator: lesson structure, SRS persistence, error handling."""

    async def test_generate_returns_lesson(self, generator, language):
        day = _make_curriculum_day()
        lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        assert isinstance(lesson, Lesson)

    async def test_generate_lesson_has_all_four_sections(self, generator, language):
        day = _make_curriculum_day()
        lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        section_types = {s.section_type for s in lesson.sections}
        assert SectionType.KEY_PHRASES in section_types
        assert SectionType.NATURAL_SPEED in section_types
        assert SectionType.SLOW_SPEED in section_types
        assert SectionType.TRANSLATED in section_types

    async def test_generate_key_phrases_section_bounded(self, generator, language):
        day = _make_curriculum_day()
        lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        kp_section = next(s for s in lesson.sections if s.section_type == SectionType.KEY_PHRASES)
        # Each key_phrase produces multiple phrases via breakdown; at least 2 input phrases
        assert len(kp_section.phrases) >= 2

    async def test_generate_invalid_json_raises(self, language):
        bad_client = MagicMock()
        bad_client.complete = AsyncMock(return_value="not json")
        gen = StoryGenerator(llm_client=bad_client)
        day = _make_curriculum_day()
        from app.generation.story import StoryGenerationError

        with pytest.raises(StoryGenerationError):
            await gen.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)

    async def test_generate_key_phrases_have_narrator_translations(self, generator, language):
        day = _make_curriculum_day()
        lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        kp_section = next(s for s in lesson.sections if s.section_type == SectionType.KEY_PHRASES)
        narrator_phrases = [p for p in kp_section.phrases if p.role == "narrator"]
        assert len(narrator_phrases) >= 1

    async def test_generate_natural_speed_has_scene_labels(self, generator, language):
        day = _make_curriculum_day()
        lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        nat_section = next(s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED)
        narrator_phrases = [p for p in nat_section.phrases if p.role == "narrator"]
        assert any("Riverside" in p.text or "Café" in p.text for p in narrator_phrases)

    async def test_generate_slow_speed_has_ellipsis(self, generator, language):
        day = _make_curriculum_day()
        lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        slow_section = next(s for s in lesson.sections if s.section_type == SectionType.SLOW_SPEED)
        dialogue = [p for p in slow_section.phrases if p.role != "narrator"]
        assert any(" ... " in p.text for p in dialogue)

    async def test_generate_translated_interleaves(self, generator, language):
        day = _make_curriculum_day()
        lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        trans_section = next(s for s in lesson.sections if s.section_type == SectionType.TRANSLATED)
        narrator_translations = [p for p in trans_section.phrases if p.role == "narrator" and p.language_code == "en"]
        assert len(narrator_translations) >= 2  # at least the translations (not just scene labels)

    async def test_generate_uses_system_prompt(self, generator, language, mock_llm):
        day = _make_curriculum_day()
        await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        call_kwargs = mock_llm.complete.call_args_list[0]
        assert call_kwargs.kwargs.get("system_prompt") is not None or (
            len(call_kwargs.args) > 1 and call_kwargs.args[1] is not None
        )

    async def test_generate_populates_key_phrases(self, generator, language):
        day = _make_curriculum_day()
        lesson = await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        assert len(lesson.key_phrases) == 2
        phrases = {kp.phrase: kp.translation for kp in lesson.key_phrases}
        assert phrases["dober dan"] == "good day"
        assert phrases["prosim kavo"] == "a coffee please"

    async def test_dialogue_glosses_stored_in_generation_metadata(self, language):
        client = MagicMock()
        client.complete = AsyncMock(return_value=_mock_story_response(include_glosses=True))
        gen = StoryGenerator(llm_client=client)
        day = _make_curriculum_day()
        lesson = await gen.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        glosses = lesson.generation_metadata.get("token_glosses", {})
        assert isinstance(glosses, dict)
        assert glosses.get("dober") == "good"
        assert glosses.get("dan") == "day"

    async def test_dialogue_glosses_absent_auto_fills(self, language):
        # Response without dialogue_glosses should auto-fill via a follow-up LLM call
        client = MagicMock()
        client.complete = AsyncMock(side_effect=[
            _mock_story_response(include_glosses=False),
            _mock_fill_response(),
        ])
        gen = StoryGenerator(llm_client=client)
        day = _make_curriculum_day()
        lesson = await gen.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        glosses = lesson.generation_metadata.get("token_glosses", {})
        assert glosses != {}
        assert glosses.get("dober") == "good"
        assert glosses.get("kavo") == "coffee"
        # Should have been called twice: once for story, once for fill
        assert client.complete.call_count == 2

    async def test_dialogue_glosses_fill_stray_keys_filtered(self, language):
        # LLM fill response containing extra keys should be filtered to only missing lemmas
        client = MagicMock()
        client.complete = AsyncMock(side_effect=[
            _mock_story_response(include_glosses=False),
            json.dumps({"dober": "good", "dan": "day", "EXTRA": "nope", "": "blank"}),
        ])
        gen = StoryGenerator(llm_client=client)
        day = _make_curriculum_day()
        lesson = await gen.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        glosses = lesson.generation_metadata.get("token_glosses", {})
        assert glosses.get("dober") == "good"
        assert glosses.get("dan") == "day"
        assert "EXTRA" not in glosses
        assert "" not in glosses

    async def test_dialogue_glosses_auto_fill_error_does_not_crash(self, language):
        # If the auto-fill LLM call fails (bad JSON, etc.), the lesson is still returned
        client = MagicMock()
        client.complete = AsyncMock(side_effect=[
            _mock_story_response(include_glosses=False),
            "not valid json",
        ])
        gen = StoryGenerator(llm_client=client)
        day = _make_curriculum_day()
        lesson = await gen.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        assert isinstance(lesson, Lesson)
        # Glosses should remain empty (the fill failed)
        glosses = lesson.generation_metadata.get("token_glosses", {})
        assert glosses == {}

    async def test_dialogue_glosses_partial_coverage_auto_fills_only_missing(self, language):
        # When only some lemmas have glosses, auto-fill fetches only the missing ones
        client = MagicMock()
        # Story response: glosses for "dober" and "dan" but NOT "prosim" or "kavo"
        story = json.loads(_mock_story_response(include_glosses=True))
        story["dialogue_glosses"] = [
            {"lemma": "dober", "translation": "good"},
            {"lemma": "dan", "translation": "day"},
        ]
        client.complete = AsyncMock(side_effect=[
            json.dumps(story),
            json.dumps({"prosim": "please", "kavo": "coffee"}),
        ])
        gen = StoryGenerator(llm_client=client)
        day = _make_curriculum_day()
        lesson = await gen.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        glosses = lesson.generation_metadata.get("token_glosses", {})
        assert glosses.get("dober") == "good"
        assert glosses.get("dan") == "day"
        assert glosses.get("prosim") == "please"
        assert glosses.get("kavo") == "coffee"
        assert client.complete.call_count == 2

    async def test_dialogue_glosses_present_skips_fill(self, language):
        # When glosses already cover all lemmas, no auto-fill call is made
        client = MagicMock()
        client.complete = AsyncMock(return_value=_mock_story_response(include_glosses=True))
        gen = StoryGenerator(llm_client=client)
        day = _make_curriculum_day()
        lesson = await gen.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        glosses = lesson.generation_metadata.get("token_glosses", {})
        assert glosses.get("dober") == "good"
        assert glosses.get("kavo") == "coffee"
        assert client.complete.call_count == 1  # only one LLM call

    async def test_parse_json_strips_markdown_fences(self, language):
        """Model sometimes wraps JSON in ```json...``` fences — parser should handle it."""
        fenced = f"```json\n{_mock_story_response()}\n```"
        data = StoryGenerator._parse_json(fenced)
        assert data["title"] == "Ordering Coffee"

    async def test_parse_json_strips_bare_fences(self, language):
        """Model sometimes uses ``` without a language tag."""
        fenced = f"```\n{_mock_story_response()}\n```"
        data = StoryGenerator._parse_json(fenced)
        assert data["title"] == "Ordering Coffee"

    async def test_parse_response_validates_key_phrases_and_scenes(self, language):
        import json

        from app.generation.story import StoryGenerationError

        generator = StoryGenerator(llm_client=MagicMock())
        data = {"title": "Empty", "key_phrases": [], "scenes": []}
        with pytest.raises(StoryGenerationError, match="missing"):
            generator._parse_response(data, language=language)

    async def test_generate_passes_cefr_level_in_user_prompt(self, generator, language, mock_llm):
        day = _make_curriculum_day()
        await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER, cefr_level="B1")
        call_kwargs = mock_llm.complete.call_args_list[0]
        user_prompt = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("prompt", "")
        assert "B1" in user_prompt

    async def test_generate_default_cefr_level_is_a2(self, generator, language, mock_llm):
        day = _make_curriculum_day()
        await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        call_kwargs = mock_llm.complete.call_args_list[0]
        user_prompt = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("prompt", "")
        assert "A2" in user_prompt

    async def test_generate_system_prompt_contains_slovene_style_notes(self, generator, language, mock_llm):
        day = _make_curriculum_day()
        await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        call_kwargs = mock_llm.complete.call_args_list[0]
        system_prompt = call_kwargs.kwargs.get("system_prompt", "")
        # Style notes for Slovene must include the izvinite/oprostite guardrail
        assert "oprostite" in system_prompt.lower()
