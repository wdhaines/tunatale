"""Story generator: produces a Lesson with 4 Pimsleur sections from a CurriculumDay."""

from __future__ import annotations

import json
import logging

from app.models.curriculum import CurriculumDay
from app.models.language import Language
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.strategy import ContentStrategy
from app.srs.database import SRSDatabase

logger = logging.getLogger(__name__)

_SECTION_TYPE_MAP = {
    "key_phrases": SectionType.KEY_PHRASES,
    "natural_speed": SectionType.NATURAL_SPEED,
    "slow_speed": SectionType.SLOW_SPEED,
    "translated": SectionType.TRANSLATED,
}

_STORY_PROMPT_TEMPLATE = """\
Generate a Pimsleur-style language lesson for the following curriculum day.

Language: {language_name} ({language_code})
Day: {day} — {title}
Focus: {focus}
Learning objective: {learning_objective}
Story guidance: {story_guidance}
Strategy: {strategy}

Key collocations to include:
{collocations}

Respond with a JSON object matching this schema:
{{
  "sections": [
    {{
      "type": "key_phrases",
      "phrases": [{{"text": "...", "language": "{language_code}"}}]
    }},
    {{
      "type": "natural_speed",
      "phrases": [{{"text": "...", "language": "{language_code}"}}]
    }},
    {{
      "type": "slow_speed",
      "phrases": [{{"text": "...", "language": "{language_code}"}}]
    }},
    {{
      "type": "translated",
      "phrases": [{{"text": "...", "language": "en"}}]
    }}
  ]
}}

Requirements:
- Respond with ONLY the JSON object, no markdown fences
- All 4 section types must be present
- key_phrases section: include 3–8 target collocations
- natural_speed: full story dialogue at natural pace
- slow_speed: repeat key phrases at reduced pace for practice
- translated: English translation of the natural_speed dialogue
"""


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
            Parsed Lesson with 4 Pimsleur sections.
        """
        collocation_list = "\n".join(f"- {c}" for c in curriculum_day.collocations)
        prompt = _STORY_PROMPT_TEMPLATE.format(
            language_name=language.name,
            language_code=language.code,
            day=curriculum_day.day,
            title=curriculum_day.title,
            focus=curriculum_day.focus,
            learning_objective=curriculum_day.learning_objective,
            story_guidance=curriculum_day.story_guidance,
            strategy=strategy.value,
            collocations=collocation_list,
        )

        logger.info("Generating story for day %d (%s)", curriculum_day.day, strategy.value)
        raw = await self._llm.complete(prompt, temperature=0.7, max_tokens=4096)
        return self._parse_response(raw, language=language)

    def _parse_response(self, raw: str, language: Language) -> Lesson:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise StoryGenerationError(f"LLM returned invalid JSON: {e}") from e

        sections_data = data.get("sections", [])
        if not sections_data:
            raise StoryGenerationError("LLM response missing 'sections' key")

        sections = []
        for s in sections_data:
            section_type = _SECTION_TYPE_MAP.get(s.get("type", ""))
            if section_type is None:
                logger.warning("Unknown section type %r — skipping", s.get("type"))
                continue
            phrases = [
                Phrase(
                    text=p["text"],
                    voice_id=language.tts_voice_map.get("female", ""),
                    language_code=p.get("language", language.code),
                )
                for p in s.get("phrases", [])
            ]
            sections.append(Section(section_type=section_type, phrases=phrases))

        return Lesson(
            title=f"Day {sections[0].phrases[0].text[:20] if sections and sections[0].phrases else 'lesson'}",
            language_code=language.code,
            sections=sections,
        )
