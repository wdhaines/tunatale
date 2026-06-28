"""Story generation tests."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

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
            {"word": "dober", "translation": "good"},
            {"word": "dan", "translation": "day"},
            {"word": "prosim", "translation": "please"},
            {"word": "kavo", "translation": "coffee"},
        ]
    return json.dumps(data)


@pytest.fixture
def mock_llm():
    client = MagicMock()
    client.complete = AsyncMock(return_value=_mock_story_response())
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

    async def test_dialogue_glosses_present(self, language):
        # Glosses are stored in generation_metadata
        client = MagicMock()
        client.complete = AsyncMock(return_value=_mock_story_response(include_glosses=True))
        gen = StoryGenerator(llm_client=client)
        day = _make_curriculum_day()
        lesson = await gen.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        glosses = lesson.generation_metadata.get("token_glosses", {})
        assert glosses.get("dober") == "good"
        assert glosses.get("kavo") == "coffee"

    async def test_dialogue_glosses_skips_empty_entry(self, language):
        # Entries with empty word or translation are silently skipped
        data = json.loads(_mock_story_response(include_glosses=True))
        data["dialogue_glosses"].append({"word": "", "translation": "nothing"})
        data["dialogue_glosses"].append({"word": "extra", "translation": ""})
        client = MagicMock()
        client.complete = AsyncMock(return_value=json.dumps(data))
        gen = StoryGenerator(llm_client=client)
        day = _make_curriculum_day()
        lesson = await gen.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        glosses = lesson.generation_metadata.get("token_glosses", {})
        assert glosses.get("dober") == "good"  # real entries survive
        assert "extra" not in glosses  # empty translation skipped

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

    async def test_parse_json_handles_prose_preamble(self, language):
        """gpt-oss-style: prose text before a ```json fence should still parse."""
        raw = f"**Lesson Title:** Here's the lesson.\n\n```json\n{_mock_story_response()}\n```"
        data = StoryGenerator._parse_json(raw)
        assert data["title"] == "Ordering Coffee"

    async def test_parse_json_strips_think_block(self, language):
        """qwen3-style: <think> reasoning (which may itself contain braces) before the JSON."""
        raw = f"<think>I will emit JSON with a {{title}} key.</think>\n{_mock_story_response()}"
        data = StoryGenerator._parse_json(raw)
        assert data["title"] == "Ordering Coffee"

    async def test_parse_json_tolerates_trailing_prose(self, language):
        """Trailing commentary after the JSON object is tolerated."""
        raw = f"{_mock_story_response()}\n\nHope this helps!"
        data = StoryGenerator._parse_json(raw)
        assert data["title"] == "Ordering Coffee"

    async def test_parse_response_validates_key_phrases_and_scenes(self, language):
        from app.generation.story import StoryGenerationError

        generator = StoryGenerator(llm_client=MagicMock())
        data = {"title": "Empty", "key_phrases": [], "scenes": []}
        with pytest.raises(StoryGenerationError, match="missing"):
            generator._parse_response(data, language=language)

    async def test_generate_sentence_translations_in_metadata(self, language):
        """sentence_translations dict maps L2 sentences to their English translations."""
        from app.generation.story import StoryGenerator

        generator = StoryGenerator(llm_client=MagicMock())
        data = {
            "title": "Test",
            "key_phrases": [],
            "scenes": [
                {
                    "label": "Scene 1",
                    "lines": [
                        {"speaker": "f1", "text": "Dober dan!", "translation": "Good day!"},
                        {"speaker": "f1", "text": "Kje je banka?", "translation": "Where is the bank?"},
                    ],
                }
            ],
        }
        lesson = generator._parse_response(data, language=language)
        st = lesson.generation_metadata.get("sentence_translations", {})
        assert st["Dober dan!"] == "Good day!"
        assert st["Kje je banka?"] == "Where is the bank?"

    async def test_generate_sentence_translations_skips_missing_translation(self, language):
        """Lines without translation are omitted from sentence_translations."""
        from app.generation.story import StoryGenerator

        generator = StoryGenerator(llm_client=MagicMock())
        data = {
            "title": "Test",
            "key_phrases": [],
            "scenes": [
                {
                    "label": "Scene 1",
                    "lines": [
                        {"speaker": "f1", "text": "Dober dan!", "translation": "Good day!"},
                        {"speaker": "f1", "text": "Brez prevoda", "translation": ""},
                    ],
                }
            ],
        }
        lesson = generator._parse_response(data, language=language)
        st = lesson.generation_metadata.get("sentence_translations", {})
        assert st["Dober dan!"] == "Good day!"
        assert "Brez prevoda" not in st

    async def test_parse_response_skips_blank_dialogue_line_for_surface_lemma_map(self, language):
        """A scene line with empty/whitespace text is skipped when building the
        sentence-aware surface→lemma map (covers the `if not text: continue` guard)."""
        from app.generation.story import StoryGenerator

        generator = StoryGenerator(llm_client=MagicMock())
        data = {
            "title": "Test",
            "key_phrases": [],
            "scenes": [
                {
                    "label": "Scene 1",
                    "lines": [
                        {"speaker": "f1", "text": "   ", "translation": "Good day!"},  # blank → skipped
                        {"speaker": "f1", "text": "Dober dan!", "translation": "Good day!"},
                    ],
                }
            ],
            "dialogue_glosses": [{"word": "dober", "translation": "good"}],
        }
        lesson = generator._parse_response(data, language=language)
        glosses = lesson.generation_metadata["token_glosses"]
        assert glosses["dober"] == "good"

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

    def test_missing_log_emits_warning_on_missing_glosses(self):
        from app.generation.story import _missing_log

        with patch("app.generation.story.logger.warning") as mock_warn:
            _missing_log(["boste", "bom"], "sl")
        (args, _kwargs) = mock_warn.call_args
        assert "omitted" in args[0]
        assert "boste" in args[3]  # sample string includes the word

    async def test_parse_response_logs_when_word_missing_from_glosses(self, language):
        from app.generation.story import StoryGenerator

        generator = StoryGenerator(llm_client=MagicMock())
        data = {
            "title": "Test",
            "key_phrases": [],
            "scenes": [
                {
                    "label": "S1",
                    "lines": [
                        {"speaker": "f1", "text": "Dober dan", "translation": "Good day"},
                    ],
                }
            ],
            "dialogue_glosses": [
                {"word": "dober", "translation": "good"},
            ],
        }
        with patch("app.generation.story.logger.warning") as mock_warn:
            lesson = generator._parse_response(data, language=language)
        glosses = lesson.generation_metadata["token_glosses"]
        assert glosses.get("dober") == "good"
        mock_warn.assert_called_once()

    async def test_generate_system_prompt_contains_slovene_style_notes(self, generator, language, mock_llm):
        day = _make_curriculum_day()
        await generator.generate(curriculum_day=day, language=language, strategy=ContentStrategy.WIDER)
        call_kwargs = mock_llm.complete.call_args_list[0]
        system_prompt = call_kwargs.kwargs.get("system_prompt", "")
        # Style notes for Slovene must include the izvinite/oprostite guardrail
        assert "oprostite" in system_prompt.lower()


class TestNorwegianStoryGeneration:
    """Norwegian listening-lesson generation: voices, syllabifier routing, prompt.

    Uses a mocked LLM (Norwegian content). Recording real Norwegian cassettes
    is a separate user-gated `--llm-mode=record` step (needs GROQ_API_KEY).
    """

    @staticmethod
    def _norwegian_response() -> str:
        return json.dumps(
            {
                "title": "God morgen",
                "key_phrases": [
                    {"phrase": "god morgen", "translation": "good morning"},
                    {"phrase": "tusen takk", "translation": "a thousand thanks"},
                ],
                "scenes": [
                    {
                        "label": "At the Bakery",
                        "lines": [
                            {"speaker": "female-1", "text": "God morgen!", "translation": "Good morning!"},
                            {
                                "speaker": "male-1",
                                "text": "Jeg vil gjerne ha kaffe.",
                                "translation": "I'd like coffee.",
                            },
                        ],
                    }
                ],
                "dialogue_glosses": [
                    {"word": "god", "translation": "good"},
                    {"word": "morgen", "translation": "morning"},
                ],
            }
        )

    @pytest.fixture
    def norwegian(self):
        from app.models.language import Language

        return Language.norwegian()

    @pytest.fixture
    def norwegian_generator(self):
        client = MagicMock()
        client.complete = AsyncMock(return_value=self._norwegian_response())
        return StoryGenerator(llm_client=client)

    async def test_generates_lesson_in_norwegian(self, norwegian_generator, norwegian):
        day = _make_curriculum_day()
        lesson = await norwegian_generator.generate(
            curriculum_day=day, language=norwegian, strategy=ContentStrategy.WIDER
        )
        assert isinstance(lesson, Lesson)
        assert lesson.language_code == "no"

    async def test_key_phrases_use_norwegian_voice(self, norwegian_generator, norwegian):
        day = _make_curriculum_day()
        lesson = await norwegian_generator.generate(
            curriculum_day=day, language=norwegian, strategy=ContentStrategy.WIDER
        )
        kp = next(s for s in lesson.sections if s.section_type == SectionType.KEY_PHRASES)
        l2_phrases = [p for p in kp.phrases if p.language_code == "no"]
        assert l2_phrases  # there are L2 phrases
        assert all(p.voice_id == "nb-NO-PernilleNeural" for p in l2_phrases)

    async def test_breakdown_uses_norwegian_syllabifier(self, norwegian_generator, norwegian):
        """The KEY_PHRASES breakdown must split 'morgen' with Norwegian rules
        (mor-gen), proving section_builder routes through the active language."""
        day = _make_curriculum_day()
        lesson = await norwegian_generator.generate(
            curriculum_day=day, language=norwegian, strategy=ContentStrategy.WIDER
        )
        kp = next(s for s in lesson.sections if s.section_type == SectionType.KEY_PHRASES)
        texts = [p.text for p in kp.phrases]
        assert "mor" in texts
        assert "gen" in texts

    async def test_norwegian_system_prompt_has_bokmal_no_slavic_morphology(self, norwegian):
        client = MagicMock()
        client.complete = AsyncMock(return_value=self._norwegian_response())
        gen = StoryGenerator(llm_client=client)
        day = _make_curriculum_day()
        await gen.generate(curriculum_day=day, language=norwegian, strategy=ContentStrategy.WIDER)
        system_prompt = client.complete.call_args_list[0].kwargs.get("system_prompt", "")
        assert "Bokmål" in system_prompt
        assert "Allowed cases for A1" not in system_prompt
