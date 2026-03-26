"""Story generator: produces a Lesson with 4 Pimsleur sections from a CurriculumDay."""

from __future__ import annotations

import json
import logging

from app.generation.prompts import SYSTEM_PROMPT, get_strategy_prompt
from app.generation.section_builder import (
    build_key_phrases_section,
    build_natural_speed_section,
    build_slow_speed_section,
    build_translated_section,
)
from app.models.curriculum import CurriculumDay
from app.models.language import Language
from app.models.lesson import Lesson
from app.models.strategy import ContentStrategy
from app.srs.database import SRSDatabase

logger = logging.getLogger(__name__)


class StoryGenerationError(Exception):
    pass


class StoryGenerator:
    """Generates a Lesson from a CurriculumDay using the LLM client."""

    def __init__(self, llm_client, srs_db: SRSDatabase) -> None:
        self._llm = llm_client
        self._db = srs_db

    async def generate(
        self,
        curriculum_day: CurriculumDay,
        language: Language,
        strategy: ContentStrategy,
    ) -> Lesson:
        """Generate a Lesson for the given curriculum day.

        Args:
            curriculum_day: Day specification including collocations and objectives.
            language: Target language configuration.
            strategy: WIDER or DEEPER content strategy.

        Returns:
            Parsed Lesson with 4 Pimsleur sections built mechanically from LLM JSON.
        """
        system_prompt = SYSTEM_PROMPT.format(
            language_name=language.name,
            language_code=language.code,
        )

        new_collocations = "\n".join(f"- {c}" for c in curriculum_day.collocations)
        user_prompt_template = get_strategy_prompt(strategy)
        user_prompt = user_prompt_template.format(
            language_name=language.name,
            language_code=language.code,
            learning_objective=curriculum_day.learning_objective,
            focus=curriculum_day.focus,
            story_guidance=curriculum_day.story_guidance,
            new_collocations=new_collocations,
            review_collocations="(none yet)",
            source_day_transcript="(not available)",
        )

        logger.info("Generating story for day %d (%s)", curriculum_day.day, strategy.value)
        raw = await self._llm.complete(user_prompt, system_prompt=system_prompt, temperature=0.7, max_tokens=8192)
        return self._parse_response(raw, language=language)

    def _parse_response(self, raw: str, language: Language) -> Lesson:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise StoryGenerationError(f"LLM returned invalid JSON: {e}") from e

        key_phrases = data.get("key_phrases", [])
        scenes = data.get("scenes", [])
        title = data.get("title", "Lesson")

        if not key_phrases and not scenes:
            raise StoryGenerationError("LLM response missing 'key_phrases' and 'scenes'")

        narrator_voice = language.tts_voice_map.get("narrator", "en-US-GuyNeural")

        sections = [
            build_key_phrases_section(key_phrases, language.tts_voice_map, narrator_voice, language.code),
            build_natural_speed_section(scenes, language.tts_voice_map, narrator_voice, language.code),
            build_slow_speed_section(scenes, language.tts_voice_map, narrator_voice, language.code),
            build_translated_section(scenes, language.tts_voice_map, narrator_voice, language.code),
        ]

        return Lesson(title=title, language_code=language.code, sections=sections)
