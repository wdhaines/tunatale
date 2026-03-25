"""Curriculum generator: LLM + PromptBuilder → Curriculum model."""

from __future__ import annotations

import json
import logging
import uuid

from app.generation.prompts import PromptBuilder
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language

logger = logging.getLogger(__name__)


class CurriculumGenerationError(Exception):
    pass


class CurriculumGenerator:
    """Generates a Curriculum from a topic using the LLM client."""

    def __init__(self, llm_client) -> None:
        self._llm = llm_client
        self._prompt_builder = PromptBuilder()

    async def generate(
        self,
        topic: str,
        language: Language,
        cefr_level: str,
        num_days: int = 5,
    ) -> Curriculum:
        """Generate a curriculum for the given topic.

        Args:
            topic: Learning topic (e.g., "ordering coffee in Ljubljana")
            language: Target language configuration
            cefr_level: CEFR level string (e.g., "A2", "B1")
            num_days: Number of curriculum days to generate

        Returns:
            Parsed Curriculum model
        """
        system_prompt = self._prompt_builder.build_system_prompt(language)
        user_prompt = self._prompt_builder.build_curriculum_prompt(
            topic=topic,
            language=language,
            cefr_level=cefr_level,
            num_days=num_days,
        )

        logger.info("Generating %d-day curriculum for topic %r (%s)", num_days, topic, language.code)
        raw = await self._llm.complete(user_prompt, system_prompt=system_prompt, temperature=0.7, max_tokens=4096)

        return self._parse_response(raw, topic=topic, language=language, cefr_level=cefr_level)

    def _parse_response(self, raw: str, *, topic: str, language: Language, cefr_level: str) -> Curriculum:
        """Parse the LLM JSON response into a Curriculum."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise CurriculumGenerationError(f"LLM returned invalid JSON: {e}\nRaw: {raw[:200]}") from e

        days_data = data.get("days", [])
        if not days_data:
            raise CurriculumGenerationError(f"LLM response missing 'days' key: {raw[:200]}")

        days = []
        for d in days_data:
            days.append(
                CurriculumDay(
                    day=d["day"],
                    title=d.get("title", f"Day {d['day']}"),
                    focus=d.get("focus", ""),
                    collocations=d.get("collocations", []),
                    learning_objective=d.get("learning_objective", ""),
                    story_guidance=d.get("story_guidance", ""),
                )
            )

        return Curriculum(
            id=str(uuid.uuid4()),
            topic=topic,
            language_code=language.code,
            cefr_level=cefr_level,
            days=days,
        )
