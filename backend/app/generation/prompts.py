"""Prompt builder for curriculum and story generation.

Language-aware: instructions adjust based on the target language.
All prompts request JSON responses for deterministic parsing.
"""

from __future__ import annotations

from app.models.language import Language
from app.models.strategy import ContentStrategy

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


# ── Story system prompt (always applied) ─────────────────────────────────

SYSTEM_PROMPT = """\
You are an expert {language_name} language instructor creating Pimsleur-style audio lessons.

**VOICE ASSIGNMENT PROTOCOL**
- Use ONLY these 4 L2 voices: female-1, female-2, male-1, male-2
- KEY_PHRASES section: always use female-1 only
- Maintain character-to-voice consistency within each lesson
- Narrator (English descriptions and translations): narrator voice only

**JSON OUTPUT SCHEMA**
Respond with ONLY a JSON object matching this schema (no markdown fences, no preamble):
{{
  "title": "Descriptive lesson title",
  "key_phrases": [
    {{"phrase": "{language_code} phrase", "translation": "English translation"}}
  ],
  "scenes": [
    {{
      "label": "Scene description in English",
      "lines": [
        {{"speaker": "female-1", "text": "{language_code} dialogue line", "translation": "English translation"}}
      ]
    }}
  ]
}}

**CONTENT QUALITY STANDARDS**
- Total word count: 400-500 words
- Dialogue: 80%+ of content
- Scenes: 4-6 distinct scenes with English labels
- Key phrases: 3-8 practical collocations (female-1 only in KEY_PHRASES)
- NEVER generate syllable breakdowns — those are added by post-processing
- NEVER use voice numbers higher than 2 (no female-3, male-3)

**SCENE HEADER FORMAT**
- All scene labels must be in English, describing location/time/situation
- Example: "At the Riverside Café", "Morning at the Train Station"
- NEVER use standalone L2 scene headers

**TRANSLATION GUIDELINES**
- Provide direct translations only — no cultural commentary
- Keep translations concise and literal
"""

# ── Strategy-specific user prompt templates ───────────────────────────────

STORY_PROMPT_TEMPLATE = """\
**Language Learning Content Generation Request**

**Language:** {language_name} ({language_code})
**Learning Objective:** {learning_objective}
**Theme/Focus:** {focus}
**Story Guidance:** {story_guidance}

**New Collocations to Teach:**
{new_collocations}

**Review Collocations to Include:**
{review_collocations}

**CONTENT GENERATION INSTRUCTIONS**
- Use 80%+ dialogue between characters
- Include ALL provided collocations naturally in the dialogue
- Generate 4-6 scenes with English scene labels
- Use appropriate voice assignments (female-1, female-2, male-1, male-2)
- Keep language at a practical, conversational level
"""

STORY_PROMPT_WIDER_TEMPLATE = """\
**Scenario Expansion Language Learning Content Generation Request**

**Language:** {language_name} ({language_code})
**Learning Objective:** {learning_objective}
**Theme/Focus:** {focus}
**Strategy:** WIDER (New Scenarios, Same Difficulty)
**Story Guidance:** {story_guidance}

**New Collocations to Teach:**
{new_collocations}

**Review Collocations to Include:**
{review_collocations}

**WIDER STRATEGY RULES**
- Create NEW scenario contexts using familiar vocabulary
- Maintain the SAME difficulty level as prior material
- Introduce maximum 5 new words per scenario to maintain difficulty
- Expand learner's practical application range without increasing complexity
- Reinforce learned patterns in diverse, realistic situations
- Use 80%+ dialogue between characters
"""

STORY_PROMPT_DEEPER_TEMPLATE = """\
**DEEPER Strategy Content Generation Request**

**Language:** {language_name} ({language_code})
**Learning Objective:** {learning_objective}
**Theme/Focus:** {focus}
**Strategy:** DEEPER (Enhanced Language Complexity)
**Story Guidance:** {story_guidance}

**SOURCE TRANSCRIPT TO ENHANCE:**
```
{source_day_transcript}
```

**New Collocations to Teach:**
{new_collocations}

**Review Collocations to Include:**
{review_collocations}

**DEEPER STRATEGY RULES**
- Enhance language complexity while keeping the same scenarios
- 90%+ L2 dialogue — minimize English usage
- Focus on sophisticated, authentic language patterns
- Each collocation should demonstrate enhanced language complexity
"""


def get_strategy_prompt(strategy: ContentStrategy) -> str:
    """Return the user prompt template for the given content strategy."""
    if strategy == ContentStrategy.WIDER:
        return STORY_PROMPT_WIDER_TEMPLATE
    if strategy == ContentStrategy.DEEPER:
        return STORY_PROMPT_DEEPER_TEMPLATE
    raise ValueError(f"Unknown strategy: {strategy}")


# ── Curriculum prompts ────────────────────────────────────────────────────


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
