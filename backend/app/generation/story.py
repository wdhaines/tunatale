"""Story generator: produces a Lesson with 4 Pimsleur sections from a CurriculumDay."""

from __future__ import annotations

import copy
import logging
from collections.abc import Sequence
from typing import NamedTuple

from app.generation.glossing import dialogue_surface_lemmas, ensure_dialogue_glosses, uncovered_surfaces
from app.generation.json_parsing import parse_json_object
from app.generation.prompts import (
    _build_cefr_block,
    build_review_block,
    build_story_system_prompt,
    get_strategy_prompt,
)
from app.generation.review_coverage import review_word_usage
from app.generation.section_builder import (
    build_en_translated_section,
    build_key_phrases_section,
    build_natural_speed_section,
    build_slow_en_translated_section,
    build_slow_speed_section,
    build_slow_translated_section,
    build_translated_section,
)
from app.languages import get_story_text_normalizer
from app.llm.call_sites import CallSite
from app.models.curriculum import CurriculumDay
from app.models.language import NARRATOR_VOICE, Language
from app.models.lesson import KeyPhraseInfo, Lesson, SectionType
from app.models.strategy import ContentStrategy, ReviewPressure
from app.srs.database import SRSDatabase
from app.srs.review_selector import select_review_collocations
from app.storage.store import ContentStore

logger = logging.getLogger(__name__)

# Groq's free-tier gpt-oss budget: prompt_tokens + max_completion_tokens are
# reserved against 8000 tokens per request (over → hard 413, not a retryable 429).
_GROQ_FREE_TIER_REQUEST_BUDGET = 8000
# Headroom kept when re-deriving max_tokens from measured prompt_tokens.
_TRUNCATION_RETRY_MARGIN = 128
_STORY_MAX_TOKENS = 4096
# What a story PROMPT may cost, estimated at chars/4: the request budget minus
# the completion reserve (3904). tunatale-4p44 measured the worst stored
# Norwegian lesson's DEEPER prompt at ~2586, so this binds only as the corpus
# grows, and a DEEPER source transcript is what gets trimmed when it does.
PROMPT_TOKEN_BUDGET = _GROQ_FREE_TIER_REQUEST_BUDGET - _STORY_MAX_TOKENS
_TRANSCRIPT_TRIM_NOTE = "[transcript trimmed to fit the request budget]"


class NoReviewVocabularyError(Exception):
    """A REVIEW story was asked for with nothing due to review.

    Its own type, and NOT a StoryGenerationError: nothing upstream failed and
    nothing is malformed. The request is valid and the current state simply
    cannot satisfy it, so the API answers 409 rather than the 502 that
    StoryGenerationError maps to.
    """


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


class StoryPrompts(NamedTuple):
    """What build_story_prompts produces.

    `review_words` is the set the prompt ACTUALLY asked for, carried out so
    the caller can later check whether the generated lesson used any of them
    — 'we asked the model' is not 'it happened'.
    """

    system_prompt: str
    user_prompt: str
    review_words: tuple[str, ...]


def _review_usage_log(used: list[str], unused: list[str], language_code: str) -> None:
    """Report how much of the requested review vocabulary the story actually used.

    ⚠️ INFO, NOT WARNING, and that is a decision rather than an oversight. The
    prompt explicitly licenses the model to skip a word that does not fit the
    scene — at the default NATURAL pressure, skipping IS a correct answer — so a
    warning would fire on a perfectly good generation and train the reader to
    ignore the line. The RATIO is the product here, not an alarm.
    """
    logger.info(
        "Review words used %d/%d (%s); unused: %s",
        len(used),
        len(used) + len(unused),
        language_code,
        ", ".join(unused) or "-",
    )


def prior_day_transcript(store: ContentStore, curriculum_id: str, day: int) -> str | None:
    """The dialogue of the lesson before *day*, as a DEEPER prompt's source.

    "Before" is the previous STORED day, not ``day - 1``: days are stable keys
    with gaps (a deleted day 5 leaves 4 then 6), and a regenerated day's latest
    lesson wins. The stored lesson is a snapshot, so this reads its text as
    stored rather than assuming today's generator produced it.

    The transcript is the NATURAL_SPEED section, the dialogue as written:
    scene labels in brackets, then ``speaker: line``. ``None`` when there is no
    earlier lesson or it has no dialogue — the first day of a curriculum always
    — and the caller then leaves the source block out entirely.
    """
    earlier = [d["day"] for d in store.get_lesson_days(curriculum_id) if d["day"] < day]
    found = store.get_latest_lesson_by_day(curriculum_id, max(earlier)) if earlier else None
    if found is None:
        return None
    lines: list[str] = []
    for section in found[1].sections:
        if section.section_type is not SectionType.NATURAL_SPEED:
            continue
        # phrases[0] is the section's own spoken title, not part of the story.
        for phrase in section.phrases[1:]:
            lines.append(f"[{phrase.text}]" if phrase.role == "narrator" else f"{phrase.role}: {phrase.text}")
    return "\n".join(lines) or None


def _source_block(transcript: str | None) -> str:
    """The DEEPER prompt's fenced source section, or nothing at all.

    Never a placeholder: a fenced block around "(not available)" asked the model
    to enhance nothing, on every DEEPER prompt ever built (tunatale-g8mu).
    """
    if transcript is None:
        return ""
    return f"**SOURCE TRANSCRIPT TO ENHANCE:**\n```\n{transcript}\n```\n\n"


def build_story_prompts(
    curriculum_day: CurriculumDay,
    language: Language,
    strategy: ContentStrategy,
    cefr_level: str,
    *,
    srs_db: SRSDatabase | None = None,
    review_pressure: ReviewPressure = ReviewPressure.NATURAL,
    content_store: ContentStore | None = None,
    curriculum_id: str | None = None,
) -> StoryPrompts:
    """Build the (system_prompt, user_prompt) pair for story generation.

    Shared by ``StoryGenerator.generate`` and the ``GET /api/story/prompt``
    export endpoint so the manual-paste path can never drift from the Groq path.

    SELECTION HAPPENS HERE, NOT AT THE CALL SITES. Both modes funnel through
    this one function — that is what its first paragraph is promising — so
    ``srs_db`` goes in and words come out in a single place. If each caller
    selected its own words, the caller that forgot would send "(none yet)"
    forever with no error: a worse lesson and no way to notice.

    ``srs_db=None`` renders the prompt EXACTLY as it was before this feature
    existed, at every pressure setting. Every story cassette was recorded that
    way and the cassette key is sha256(system + user), so this is not a courtesy
    default — it is what keeps the recorded corpus valid.

    The DEEPER source transcript is selected here for the same reason:
    *content_store* and *curriculum_id* go in, the previous day's dialogue comes
    out (:func:`prior_day_transcript`). Without them, or with no earlier lesson,
    the source block is absent. WIDER and REVIEW never read either.
    """
    review_words = select_review_collocations(srs_db) if srs_db is not None else ()
    if strategy is ContentStrategy.REVIEW:
        # Delegated so there is exactly ONE REVIEW prompt builder. Safe because
        # a REVIEW prompt provably uses nothing from *curriculum_day*: the
        # template's format fields are only cefr_block / language_code /
        # language_name / review_collocations, and two unrelated days render
        # byte-identically through this function (measured on bd tunatale-9p9d).
        # Passing the day here and ignoring it there would be the same fact
        # written twice, and the copy nobody edits is the one that goes stale.
        return _build_review_prompts(language, cefr_level, review_words)

    system_prompt = build_story_system_prompt(language)

    new_collocations = "\n".join(f"- {c}" for c in curriculum_day.collocations)
    user_prompt_template = get_strategy_prompt(strategy)

    def render(transcript: str | None) -> str:
        return user_prompt_template.format(
            language_name=language.name,
            language_code=language.code,
            learning_objective=curriculum_day.learning_objective,
            focus=curriculum_day.focus,
            story_guidance=curriculum_day.story_guidance,
            new_collocations=new_collocations,
            review_collocations=build_review_block(review_words, review_pressure),
            source_block=_source_block(transcript),
            cefr_block=_build_cefr_block(cefr_level),
        )

    transcript = None
    if strategy is ContentStrategy.DEEPER and content_store is not None and curriculum_id is not None:
        transcript = prior_day_transcript(content_store, curriculum_id, curriculum_day.day)
    user_prompt = render(None) if transcript is None else _fit_source_transcript(system_prompt, transcript, render)
    return StoryPrompts(system_prompt, user_prompt, tuple(review_words))


def _estimate_tokens(system_prompt: str, user_prompt: str) -> int:
    """chars/4: the same estimate tunatale-4p44 sized the budget with."""
    return (len(system_prompt) + len(user_prompt)) // 4


def _fit_source_transcript(system_prompt: str, transcript: str, render) -> str:
    """Render the DEEPER prompt, dropping transcript lines from the END to fit.

    Asserted per prompt because the corpus grows (tunatale-4p44 measured 34%
    headroom on the lessons that existed). The opening of a dialogue is what
    sets its scene, so the tail goes first, and the prompt SAYS it was cut —
    a silently shortened source would read as the whole story.
    """
    lines = transcript.splitlines()
    kept = len(lines)
    user_prompt = render(transcript)
    while kept > 0 and _estimate_tokens(system_prompt, user_prompt) > PROMPT_TOKEN_BUDGET:
        kept -= 1
        user_prompt = render("\n".join([*lines[:kept], _TRANSCRIPT_TRIM_NOTE]))
    if kept < len(lines):
        logger.warning(
            "DEEPER source transcript trimmed from %d to %d lines to fit the %d-token prompt budget",
            len(lines),
            kept,
            PROMPT_TOKEN_BUDGET,
        )
    return user_prompt


def _build_review_prompts(language: Language, cefr_level: str, review_words: Sequence[str]) -> StoryPrompts:
    """Render the themeless REVIEW prompt. The one place it is built.

    ⚠️ An empty set is NOT "nothing to add" here — it is a prompt with no
    content at all. This is the one strategy that cannot reuse the
    empty-set-is-byte-identical rule the others hold to, because there is no
    prior behaviour to match, so it refuses instead. The refusal happens before
    any LLM call by construction: this runs while the prompt is still being
    assembled.

    Pressure is forced rather than passed. NATURAL's wording ("candidates, not
    requirements", "including none of them is a correct answer") is
    self-contradictory in a story whose only content is those words, so the
    strategy sets both axes and a caller's dial has no say.
    """
    if not review_words:
        raise NoReviewVocabularyError("REVIEW needs due review vocabulary and none is due for this language today")
    user_prompt = get_strategy_prompt(ContentStrategy.REVIEW).format(
        language_name=language.name,
        language_code=language.code,
        review_collocations=build_review_block(review_words, ReviewPressure.INSISTENT),
        cefr_block=_build_cefr_block(cefr_level),
    )
    return StoryPrompts(build_story_system_prompt(language), user_prompt, tuple(review_words))


def build_review_session_prompts(
    language: Language,
    cefr_level: str,
    *,
    srs_db: SRSDatabase | None = None,
    review_words: Sequence[str] | None = None,
) -> StoryPrompts:
    """The REVIEW prompt for a session that belongs to no curriculum.

    A review session has no theme, no position in a sequence, and its content is
    drawn from the whole language deck rather than one plan — so there is no
    ``CurriculumDay`` to hand in, and this signature is the honest expression of
    that (bd tunatale-9p9d). It is not a convenience wrapper: manufacturing a
    synthetic day to satisfy the older signature would put a fake curriculum in
    the data model to satisfy a function that then ignores it.

    Selection still happens in exactly one place per path, and both paths render
    through :func:`_build_review_prompts`, so this cannot drift from
    ``build_story_prompts(..., ContentStrategy.REVIEW, ...)``.

    *review_words* PINS the set instead of selecting one, for the manual-paste
    route (tunatale-w1i3). The learner exports this prompt, writes a dialogue
    elsewhere, and pastes it back; coverage is then measured against the session's
    ORIGINAL request, so the prompt has to ask for that same set or the meter
    reads low for a reason nothing on screen explains. Same rule ``import_lesson``
    already follows (tunatale-fgeq.1) — import paths pin, generation paths select.
    ``None`` means "select"; an explicit empty sequence still refuses below, which
    is why this is not defaulted to ``()``.
    """
    if review_words is None:
        review_words = select_review_collocations(srs_db) if srs_db is not None else ()
    return _build_review_prompts(language, cefr_level, review_words)


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
        *,
        srs_db: SRSDatabase | None = None,
        review_pressure: ReviewPressure = ReviewPressure.NATURAL,
        content_store: ContentStore | None = None,
        curriculum_id: str | None = None,
    ) -> Lesson:
        """Generate a Lesson for the given curriculum day.

        Args:
            curriculum_day: Day specification including collocations and objectives.
            language: Target language configuration.
            strategy: WIDER or DEEPER content strategy.
            cefr_level: CEFR level string (e.g. "A2") to calibrate dialogue complexity.
            srs_db: Per-language SRS database for review collocation selection.
            review_pressure: How hard the prompt should push to use review words.
            content_store: The language's content store, for DEEPER's source
                transcript (the previous stored day's dialogue).
            curriculum_id: The curriculum *curriculum_day* belongs to.

        Returns:
            Parsed Lesson with 4 Pimsleur sections built mechanically from LLM JSON.
        """
        prompts = build_story_prompts(
            curriculum_day,
            language,
            strategy,
            cefr_level,
            srs_db=srs_db,
            review_pressure=review_pressure,
            content_store=content_store,
            curriculum_id=curriculum_id,
        )
        return await self._complete(prompts, language, f"day {curriculum_day.day} ({strategy.value})")

    async def generate_review_session(
        self,
        language: Language,
        cefr_level: str = "A2",
        *,
        srs_db: SRSDatabase | None = None,
    ) -> Lesson:
        """Generate a Lesson for a review session — no curriculum, no day.

        Deliberately NOT ``generate(..., strategy=REVIEW)`` with a stand-in day:
        a review session belongs to no plan, and a signature that demanded one
        would force a fake curriculum into the data model to satisfy a parameter
        the REVIEW prompt then ignores (bd tunatale-9p9d).

        Raises ``NoReviewVocabularyError`` — before any LLM call, while the
        prompt is still being assembled — when nothing is due.
        """
        prompts = build_review_session_prompts(language, cefr_level, srs_db=srs_db)
        return await self._complete(prompts, language, "a review session")

    async def _complete(self, prompts: StoryPrompts, language: Language, label: str) -> Lesson:
        """Call the model and parse it, with the truncation retry.

        Shared by both entry points so the token budget below, and the retry
        that re-derives it, exist exactly once. *label* is for the log line
        only — it is the one thing the two paths genuinely differ on.
        """
        system_prompt = prompts.system_prompt
        user_prompt = prompts.user_prompt

        logger.info("Generating story for %s", label)
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
                user_prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=max_tokens,
                call_site=CallSite.STORY,
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
            # Splicing the glosses into `data` BEFORE the build is what keeps this
            # change small: build_lesson_from_story, the stored `story` blob and the
            # paste round-trip all see the shape they always did.
            await ensure_dialogue_glosses(data, self._llm, language)
            return self._parse_response(data, language=language, review_words=prompts.review_words)
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

    def _parse_response(self, data: dict, language: Language, *, review_words: Sequence[str] = ()) -> Lesson:
        return build_lesson_from_story(data, language=language, review_words=review_words)


def build_lesson_from_story(data: dict, language: Language, *, review_words: Sequence[str] = ()) -> Lesson:
    """Build a Lesson from Story JSON — the ONE Story-JSON → Lesson build step.

    Used by generation (via ``StoryGenerator._parse_response``) and by lesson
    authoring import (``app.storage.lesson_io``), so authored and generated
    lessons are identical in shape. See docs/lesson-authoring.md.

    *review_words* is what the PROMPT asked the model to work in. It is measured
    here rather than at the call site because the surface→lemma map this function
    already builds from the real dialogue is exactly the matcher the check needs,
    and rebuilding it outside would mean a second lemmatiser pass. The authoring
    import passes nothing — recording what a hand-pasted prompt requested is
    tunatale-g4c9's job.
    """
    title = data.get("title", "Lesson")

    # Normalize the story's TARGET-LANGUAGE text once, before anything reads
    # `data` (tunatale-w4m7.11): Tagalog's affix-hyphen cleanup turns the LLM's
    # `mag‑kape` (often U+2011) into standard `magkape`. Applied on a DEEP COPY
    # so the caller's dict is never mutated. Only scenes[*].lines[*].text,
    # key_phrases[*].phrase and dialogue_glosses[*].word/.lemma are touched —
    # never translation/title/base or any English.
    normalizer = get_story_text_normalizer(language.code)
    if normalizer is not None:
        data = copy.deepcopy(data)
        # Same shape assumptions as the reads below: scenes, lines and glosses
        # are dicts; a key phrase may not be (it is skipped with a warning).
        for scene in data.get("scenes", []):
            for line in scene.get("lines", []):
                if "text" in line:
                    line["text"] = normalizer(line["text"])
        for kp in data.get("key_phrases", []):
            if isinstance(kp, dict) and "phrase" in kp:
                kp["phrase"] = normalizer(kp["phrase"])
        for gloss in data.get("dialogue_glosses", []):
            for field in ("word", "lemma"):
                if field in gloss:
                    gloss[field] = normalizer(gloss[field])

    key_phrases = data.get("key_phrases", [])
    scenes = data.get("scenes", [])

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

    # Sentence-aware surface→lemma map (prevents POS-blind fallback
    # where single-word lemmatize miskeys e.g. "hotel" → as verb "hoteti"
    # instead of noun "hotel"). The loop lives in glossing.py on purpose: the
    # top-up path in ensure_dialogue_glosses must count coverage with THE SAME
    # map, so there is one copy, not two.
    # dialogue_lines is collected separately — a multi-word collocation has no
    # entry in a token map, so the review meter falls back to a phrase search
    # over this list.
    dialogue_lines: list[str] = []
    for scene in scenes:
        for line in scene.get("lines", []):
            text = line.get("text", "").strip()
            if not text:
                continue
            dialogue_lines.append(text)
    surface_lemma = dialogue_surface_lemmas(dialogue_lines, language.code)

    token_glosses: dict[str, str] = {}
    for g in glosses:
        raw_key = g.get("word") or g.get("lemma", "")
        translation = g.get("translation", "")
        if raw_key and translation:
            # Keys are lowercase — every consumer looks up surface.lower()
            # or a lowercase lemma (transcript.py, api/srs.py).
            key = raw_key.lower()
            lemma = surface_lemma.get(key, key)
            # Surface key preserves the specific conjugated translation
            # (e.g. "boste" → "you will", "bom" → "I will").
            token_glosses[key] = translation
            # Lemma key provides a fallback generic translation
            # (e.g. "biti" → "you will" from whichever surface came first).
            token_glosses.setdefault(lemma, translation)

    # A separate, VERB-only map of base-form glosses (the LLM's optional "base"
    # key, e.g. `lyver` → "lie"), kept beside token_glosses on purpose: the
    # transcript's map needs the *in-context* conjugated gloss while the card,
    # whose front is the infinitive, needs the bare dictionary form. Same
    # surface-key + lemma-fallback shape as token_glosses so the resolution in
    # api/srs.py can look up either.
    verb_base_glosses: dict[str, str] = {}
    for g in glosses:
        raw_key = g.get("word") or g.get("lemma", "")
        base = g.get("base", "")
        if raw_key and base:
            key = raw_key.lower()
            lemma = surface_lemma.get(key, key)
            verb_base_glosses[key] = base
            verb_base_glosses.setdefault(lemma, base)

    missing = uncovered_surfaces(surface_lemma, glosses)
    if missing:
        _missing_log(missing, language.code)

    review_used, review_unused = review_word_usage(review_words, surface_lemma, "\n".join(dialogue_lines))
    if review_words:
        _review_usage_log(review_used, review_unused, language.code)

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
            "verb_base_glosses": verb_base_glosses,
            "sentence_translations": sentence_translations,
            "morphology_focus": data.get("morphology_focus", []),
            # How many gloss entries the model returned — the raw count, not
            # the expanded token_glosses map (which carries extra lemma keys).
            "gloss_entry_count": len(glosses),
            # What the prompt ASKED for and what the story actually used. Kept
            # on the lesson so the answer survives the log buffer.
            "review_requested": list(review_words),
            "review_used": review_used,
            # Exact Story-JSON source (docs/lesson-authoring.md decision #4):
            # export returns this verbatim; reconstruction is only the fallback
            # for lessons stored before it existed. Deep copy so later caller
            # mutations can't corrupt the persisted source.
            "story": copy.deepcopy(data),
        },
    )
