"""Story generator: produces a Lesson with 4 Pimsleur sections from a CurriculumDay."""

from __future__ import annotations

import copy
import logging

from app.generation.json_parsing import parse_json_object
from app.generation.prompts import _build_cefr_block, build_story_system_prompt, get_strategy_prompt
from app.generation.section_builder import (
    build_en_translated_section,
    build_key_phrases_section,
    build_natural_speed_section,
    build_slow_en_translated_section,
    build_slow_speed_section,
    build_slow_translated_section,
    build_translated_section,
)
from app.models.curriculum import CurriculumDay
from app.models.language import NARRATOR_VOICE, Language
from app.models.lesson import KeyPhraseInfo, Lesson
from app.models.strategy import ContentStrategy
from app.srs.lemmatizer import get_lemmatizer, lemmatize_surfaces_in_context
from app.srs.tokenizer import tokenize

logger = logging.getLogger(__name__)

# Groq's free-tier gpt-oss budget: prompt_tokens + max_completion_tokens are
# reserved against 8000 tokens per request (over → hard 413, not a retryable 429).
_GROQ_FREE_TIER_REQUEST_BUDGET = 8000
# Headroom kept when re-deriving max_tokens from measured prompt_tokens.
_TRUNCATION_RETRY_MARGIN = 128
_STORY_MAX_TOKENS = 4096


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


def build_story_prompts(
    curriculum_day: CurriculumDay,
    language: Language,
    strategy: ContentStrategy,
    cefr_level: str,
) -> tuple[str, str]:
    """Build the (system_prompt, user_prompt) pair for story generation.

    Shared by ``StoryGenerator.generate`` and the ``GET /api/story/prompt``
    export endpoint so the manual-paste path can never drift from the Groq path.
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
    return system_prompt, user_prompt


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
        system_prompt, user_prompt = build_story_prompts(curriculum_day, language, strategy, cefr_level)

        logger.info("Generating story for day %d (%s)", curriculum_day.day, strategy.value)
        # 4096, NOT 5500. gpt-oss-120b's free-tier budget is 8000 tokens/request and
        # Groq reserves prompt_tokens + max_completion_tokens against it up front, so a
        # request over 8000 is a hard 413 (not a retryable 429). The story system prompt
        # is ~2800 tokens (the Slovene morphology-tagging block), so 5500 → ~8300 → 413,
        # which then falls through to the Ollama junk-JSON fallback. Measured on the real
        # prompt at reasoning_effort=low: reasoning is negligible and the JSON payload is
        # ~1900 completion tokens, finishing cleanly well inside 4096 — the earlier
        # "reasoning ~1400 + JSON ~3200" estimate that justified 5500 was wrong. 4096
        # keeps prompt+budget ~6900 under the cap with headroom for prompt growth.
        # When a response IS truncated (finish_reason=length — reasoning spike, or a
        # smaller-prompt language like Norwegian writing a longer story), the retry
        # below re-derives the cap from the measured prompt_tokens.
        max_tokens = _STORY_MAX_TOKENS
        failure: StoryGenerationError | None = None
        for attempt in range(2):
            raw = await self._llm.complete(
                user_prompt, system_prompt=system_prompt, temperature=0.7, max_tokens=max_tokens
            )
            try:
                data = self._parse_json(raw)
            except StoryGenerationError as e:
                truncated = getattr(self._llm, "last_finish_reason", None) == "length"
                failure = self._enrich_parse_failure(e, truncated=truncated, max_tokens=max_tokens)
                if truncated:
                    max_tokens = self._bump_max_tokens_after_truncation(max_tokens)
                logger.warning("Story JSON parse failed on attempt %d/2: %s", attempt + 1, failure)
                continue
            return self._parse_response(data, language=language)
        raise failure

    def _enrich_parse_failure(
        self, error: StoryGenerationError, *, truncated: bool, max_tokens: int
    ) -> StoryGenerationError:
        """Attach the diagnosis a bare json.JSONDecodeError message can't carry."""
        if truncated:
            return StoryGenerationError(
                f"{error} — response truncated at max_tokens={max_tokens} (finish_reason=length)"
            )
        if getattr(self._llm, "last_provider", None) == "ollama":
            return StoryGenerationError(
                f"{error} — from the offline Ollama fallback; Groq was unavailable (likely rate-limited), retry shortly"
            )
        return error

    def _bump_max_tokens_after_truncation(self, current: int) -> int:
        """Re-derive the completion cap from the measured prompt size, never shrinking."""
        usage = getattr(self._llm, "last_usage", None)
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        if isinstance(prompt_tokens, int) and prompt_tokens > 0:
            return max(current, _GROQ_FREE_TIER_REQUEST_BUDGET - prompt_tokens - _TRUNCATION_RETRY_MARGIN)
        return current

    @staticmethod
    def _parse_json(raw: str) -> dict:
        try:
            return parse_json_object(raw)
        except ValueError as e:
            raise StoryGenerationError(str(e)) from e

    def _parse_response(self, data: dict, language: Language) -> Lesson:
        return build_lesson_from_story(data, language=language)


def build_lesson_from_story(data: dict, language: Language) -> Lesson:
    """Build a Lesson from Story JSON — the ONE Story-JSON → Lesson build step.

    Used by generation (via ``StoryGenerator._parse_response``) and by lesson
    authoring import (``app.storage.lesson_io``), so authored and generated
    lessons are identical in shape. See docs/lesson-authoring.md.
    """
    key_phrases = data.get("key_phrases", [])
    scenes = data.get("scenes", [])
    title = data.get("title", "Lesson")

    if not key_phrases and not scenes:
        raise StoryGenerationError("LLM response missing 'key_phrases' and 'scenes'")

    narrator_voice = language.tts_voice_map.get("narrator", NARRATOR_VOICE)

    sections = [
        build_key_phrases_section(key_phrases, language.tts_voice_map, narrator_voice, language.code),
        build_natural_speed_section(scenes, language.tts_voice_map, narrator_voice, language.code),
        build_slow_speed_section(scenes, language.tts_voice_map, narrator_voice, language.code),
        build_translated_section(scenes, language.tts_voice_map, narrator_voice, language.code),
        build_slow_translated_section(scenes, language.tts_voice_map, narrator_voice, language.code),
        build_en_translated_section(scenes, language.tts_voice_map, narrator_voice, language.code),
        build_slow_en_translated_section(scenes, language.tts_voice_map, narrator_voice, language.code),
    ]

    kp_infos = []
    for kp in key_phrases:
        if not isinstance(kp, dict):
            logger.warning("Skipping non-dict key phrase: %r", kp)
            continue
        phrase = kp.get("phrase", "")
        translation = kp.get("translation", "")
        if not phrase or not translation:
            logger.warning("Skipping key phrase with missing phrase or translation: %r", kp)
            continue
        kp_infos.append(KeyPhraseInfo(phrase=phrase, translation=translation))

    glosses = data.get("dialogue_glosses", [])
    lemmatizer = get_lemmatizer(language.code)

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
            # Keys are lowercase — every consumer looks up surface.lower()
            # or a lowercase lemma (transcript.py, api/srs.py).
            key = raw_key.lower()
            glossed_surfaces.add(key)
            lemma = surface_lemma.get(key, key)
            # Surface key preserves the specific conjugated translation
            # (e.g. "boste" → "you will", "bom" → "I will").
            token_glosses[key] = translation
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
            # Exact Story-JSON source (docs/lesson-authoring.md decision #4):
            # export returns this verbatim; reconstruction is only the fallback
            # for lessons stored before it existed. Deep copy so later caller
            # mutations can't corrupt the persisted source.
            "story": copy.deepcopy(data),
        },
    )
