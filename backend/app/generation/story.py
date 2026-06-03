"""Story generator: produces a Lesson with 4 Pimsleur sections from a CurriculumDay."""

from __future__ import annotations

import json
import logging
import re

from app.generation.prompts import _build_cefr_block, build_story_system_prompt, get_strategy_prompt
from app.generation.section_builder import (
    build_key_phrases_section,
    build_natural_speed_section,
    build_slow_speed_section,
    build_translated_section,
)
from app.models.curriculum import CurriculumDay
from app.models.language import Language
from app.models.lesson import KeyPhraseInfo, Lesson
from app.models.strategy import ContentStrategy
from app.srs.lemmatizer import get_lemmatizer, lemmatize_surfaces_in_context
from app.srs.tokenizer import tokenize

logger = logging.getLogger(__name__)

# Reasoning models (e.g. qwen3) emit <think>…</think> before the answer; strip it
# so it can't swallow the JSON object during brace-extraction.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_fences(raw: str) -> str:
    """Strip markdown code fences from an LLM response."""
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)
        raw = raw.strip()
    return raw


class StoryGenerationError(Exception):
    pass


def _missing_log(missing: list[str], language_code: str) -> None:
    """Log a warning when the LLM omitted words from dialogue_glosses."""
    sample = sorted(missing)[:10]
    logger.warning(
        "LLM omitted %d word(s) from dialogue_glosses (%s): %s",
        len(missing),
        language_code,
        " ".join(sample),
    )


class StoryGenerator:
    """Generates a Lesson from a CurriculumDay using the LLM client."""

    def __init__(self, llm_client) -> None:
        self._llm = llm_client

    async def generate(
        self,
        curriculum_day: CurriculumDay,
        language: Language,
        strategy: ContentStrategy,
        cefr_level: str = "A2",
    ) -> Lesson:
        """Generate a Lesson for the given curriculum day.

        Args:
            curriculum_day: Day specification including collocations and objectives.
            language: Target language configuration.
            strategy: WIDER or DEEPER content strategy.
            cefr_level: CEFR level string (e.g. "A2") to calibrate dialogue complexity.

        Returns:
            Parsed Lesson with 4 Pimsleur sections built mechanically from LLM JSON.
        """
        system_prompt = build_story_system_prompt(language)

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
            cefr_block=_build_cefr_block(cefr_level),
        )

        logger.info("Generating story for day %d (%s)", curriculum_day.day, strategy.value)
        raw = await self._llm.complete(user_prompt, system_prompt=system_prompt, temperature=0.7, max_tokens=4096)
        data = self._parse_json(raw)
        lesson = self._parse_response(data, language=language)
        return lesson

    @staticmethod
    def _parse_json(raw: str) -> dict:
        # Model-agnostic: drop <think> reasoning, code fences, and any prose the model
        # wraps around the JSON (gpt-oss prepends "**Lesson Title:** …"; others append
        # commentary). Try the cleaned string, then the first balanced {…} span.
        cleaned = _strip_fences(_THINK_RE.sub("", raw).strip())
        candidates = [cleaned]
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            candidates.append(cleaned[start : end + 1])
        last_error: json.JSONDecodeError | None = None
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError as e:
                last_error = e
        logger.error(
            "LLM returned unparseable response (len=%d): %r",
            len(cleaned),
            cleaned[:500],
        )
        raise StoryGenerationError(f"LLM returned invalid JSON: {last_error}") from last_error

    def _parse_response(self, data: dict, language: Language) -> Lesson:
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

        kp_infos = [KeyPhraseInfo(phrase=kp["phrase"], translation=kp["translation"]) for kp in key_phrases]

        glosses = data.get("dialogue_glosses", [])
        lemmatizer = get_lemmatizer()

        # Sentence-aware surface→lemma map (prevents POS-blind fallback
        # where single-word lemmatize miskeys e.g. "hotel" → as verb "hoteti"
        # instead of noun "hotel").
        surface_lemma: dict[str, str] = {}
        for scene in scenes:
            for line in scene.get("lines", []):
                text = line.get("text", "").strip()
                if not text:
                    continue
                surfaces = tokenize(text)
                lemmas = lemmatize_surfaces_in_context(surfaces, text, lemmatizer, language.code)
                for s, lem in zip(surfaces, lemmas, strict=True):
                    surface_lemma.setdefault(s.lower(), lem)

        token_glosses: dict[str, str] = {}
        glossed_surfaces: set[str] = set()
        for g in glosses:
            raw_key = g.get("word") or g.get("lemma", "")
            translation = g.get("translation", "")
            if raw_key and translation:
                glossed_surfaces.add(raw_key.lower())
                lemma = surface_lemma.get(raw_key.lower(), raw_key)
                # Surface key preserves the specific conjugated translation
                # (e.g. "boste" → "you will", "bom" → "I will").
                token_glosses[raw_key] = translation
                # Lemma key provides a fallback generic translation
                # (e.g. "biti" → "you will" from whichever surface came first).
                token_glosses.setdefault(lemma, translation)

        missing = [s for s in surface_lemma if s not in glossed_surfaces]
        if missing:
            _missing_log(missing, language.code)

        sentence_translations: dict[str, str] = {}
        for scene in scenes:
            for line in scene.get("lines", []):
                l2 = line.get("text", "").strip()
                en = line.get("translation", "").strip()
                if l2 and en:
                    sentence_translations[l2] = en

        return Lesson(
            title=title,
            language_code=language.code,
            sections=sections,
            narrator_voice=narrator_voice,
            key_phrases=kp_infos,
            generation_metadata={
                "token_glosses": token_glosses,
                "sentence_translations": sentence_translations,
                "morphology_focus": data.get("morphology_focus", []),
            },
        )
