"""Prompt builder for curriculum and story generation.

Language-aware: instructions adjust based on the target language.
All prompts request JSON responses for deterministic parsing.
"""

from __future__ import annotations

from app.models.language import Language

_CURRICULUM_PROMPT_TEMPLATE = """\
You are generating a {num_days}-day language learning curriculum.

Topic: {topic}
Target language: {language_name} ({language_code})
CEFR level: {cefr_level}

Respond with a JSON object matching this schema exactly:
{{
  "days": [
    {{
      "day": 1,
      "title": "Short lesson title",
      "focus": "Main focus area for this day",
      "collocations": ["phrase one", "phrase two", "phrase three"],
      "learning_objective": "Specific skill the learner will practice",
      "story_guidance": "Brief setting/scenario hint for audio story generation"
    }}
  ]
}}

Requirements:
- Respond with ONLY the JSON object, no markdown fences, no preamble
- All collocations must be in {language_name} ({language_code}) using {script} script
- 3–8 collocations per day (natural 1–5 word phrases)
- Days should progress from simpler to more complex vocabulary
- Make collocations practical for real-world use of the topic
"""

_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert language curriculum designer specializing in {language_name}.
You create structured, practical curricula for learners studying {language_name} ({language_code}).

Language details:
- ISO code: {language_code}
- Script: {script}
- Native name: {native_name}

When generating collocations, use authentic {language_name} as a native speaker would.
Focus on practical, conversational phrases appropriate for the learner's CEFR level.
"""


class PromptBuilder:
    """Builds prompts for LLM-powered curriculum and story generation."""

    def build_system_prompt(self, language: Language) -> str:
        """Build the system prompt for a given target language."""
        return _SYSTEM_PROMPT_TEMPLATE.format(
            language_name=language.name,
            language_code=language.code,
            script=language.script,
            native_name=language.native_name,
        )

    def build_curriculum_prompt(
        self,
        topic: str,
        language: Language,
        cefr_level: str,
        num_days: int,
    ) -> str:
        """Build the user prompt for curriculum generation."""
        return _CURRICULUM_PROMPT_TEMPLATE.format(
            topic=topic,
            language_name=language.name,
            language_code=language.code,
            script=language.script,
            cefr_level=cefr_level,
            num_days=num_days,
        )
