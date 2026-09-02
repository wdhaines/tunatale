"""Story generation endpoints."""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Literal

import anyio
from fastapi import APIRouter, HTTPException, Request

from app.api._serializers import serialize_lesson
from app.api.models import (
    GenerateStoryRequest,
    GenerateStoryResponse,
    GetStoryPromptResponse,
    ImportLessonRequest,
    ImportStoryResponse,
    LessonResponse,
    LessonSourceResponse,
)
from app.generation.ids import mint_id
from app.generation.json_parsing import parse_json_object
from app.generation.story import NoReviewVocabularyError, StoryGenerationError, build_story_prompts
from app.llm.client import LLMError, LLMQuotaExceededError
from app.models.language import Language
from app.models.lesson import Lesson, SectionType
from app.models.strategy import ContentStrategy
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import analyze_sentence_cached, get_lemmatizer, model_version_for
from app.storage.lesson_io import export_lesson, import_lesson, speaker_warnings, sync_curriculum_day_title

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/story", tags=["generation"])

# Strong refs to fire-and-forget pre-warm tasks: the event loop only keeps a
# weak reference, so an un-anchored task can be garbage-collected mid-flight.
_background_tasks: set[asyncio.Task] = set()


def _logged_speaker_warnings(story: dict | None, language: Language) -> list[str]:
    """Speaker warnings, mirrored to the server log.

    Returning them in a 201 body is not delivery — nothing reads the body of a
    successful generate in normal use, so an unmapped speaker was effectively
    invisible on the path that produces most lessons.

    ``story`` is ``None`` for lessons stored before the exact Story-JSON source
    was persisted in ``generation_metadata``; those carry no speakers to check.
    """
    warnings = speaker_warnings(story, language) if story else []
    for warning in warnings:
        _logger.warning("%s", warning)
    return warnings


async def _prewarm_lesson(lesson: Lesson, srs_db: SRSDatabase) -> None:
    """Background pre-warm: cache a freshly generated lesson's sentences.

    Runs the new lesson's natural-speed L2 sentences through
    ``analyze_sentence_cached`` so the transcript view never triggers a
    classla load for this content.
    """
    try:
        lemmatizer = get_lemmatizer(lesson.language_code)
        model_version = model_version_for(lemmatizer)
        if not model_version:
            return
        natural_speed = next(
            (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
            None,
        )
        if natural_speed is None:
            return
        phrases = [(p.text, p.language_code) for p in natural_speed.phrases if p.language_code == lesson.language_code]
        await anyio.to_thread.run_sync(
            _prewarm_phrases, phrases, srs_db, lemmatizer, model_version, lesson.language_code
        )
    except Exception:
        _logger.warning("Pre-warm failed for new lesson", exc_info=True)


def _prewarm_phrases(
    phrases: list[tuple[str, str]],
    srs_db: SRSDatabase,
    lemmatizer: object,
    model_version: str,
    language_code: str,
) -> None:
    for text, _ in phrases:
        analyze_sentence_cached(srs_db, lemmatizer, text, language_code, model_version)


async def annotate_chunk_upos_for_lesson(
    lesson: Lesson,
    srs_db: SRSDatabase,
    *,
    lemmatizer: object | None = None,
    model_version: str | None = None,
) -> int:
    """Resolve the lemmatizer and tag *lesson*'s chunk phrases. Never raises.

    The caller must AWAIT this BEFORE saving the lesson. Firing it as a detached
    task races the save: the lesson lands untagged and the tags are computed into
    an in-memory object nobody persists again — silently inert, with every test
    still green because they exercise ``annotate_chunk_upos`` directly.

    *lemmatizer* and *model_version* are injectable for the same reason
    ``annotate_chunk_upos`` takes them: resolving them for real loads stanza,
    which CI deliberately does not install (``--no-group lemmatizers``), so the
    only way to exercise the success path in the gate is to supply a double.
    """
    try:
        if lemmatizer is None:
            lemmatizer = get_lemmatizer(lesson.language_code)
        if model_version is None:
            model_version = model_version_for(lemmatizer)
        if not model_version:
            return 0
        return await anyio.to_thread.run_sync(
            partial(annotate_chunk_upos, lesson, srs_db, lemmatizer=lemmatizer, model_version=model_version)
        )
    except Exception:
        _logger.warning("UPOS annotation failed for lesson", exc_info=True)
        return 0


def annotate_chunk_upos(lesson: Lesson, srs_db: SRSDatabase, *, lemmatizer: object, model_version: str) -> int:
    """Post-generation pass: tag chunk phrases with their key phrase's UPOS.

    Mirrors ``_prewarm_lesson``'s shape: ``get_lemmatizer`` + ``model_version_for``
    externally, ``anyio.to_thread.run_sync`` for the NLP work, and failures
    swallowed with a warning — tagging must never break generation.

    Returns the number of phrases tagged. Walks the KEY_PHRASES section using the
    same arithmetic as ``_build_key_phrases_refs``: one title phrase, then
    ``2 + len(breakdown)`` per key phrase. This attaches each chunk to *its own*
    key phrase — never a lesson-wide surface→upos map, because a word can be a
    noun in one key phrase and a verb in another.
    """
    if not model_version:
        return 0

    from app.generation.section_builder import build_word_breakdown_spans
    from app.srs.lemmatizer import TokenAnalysis, analyze_sentence_cached

    kp_section = next(
        (s for s in lesson.sections if s.section_type == SectionType.KEY_PHRASES),
        None,
    )
    if kp_section is None:
        return 0

    l2_code = lesson.language_code
    phrases = kp_section.phrases
    count = 0
    phrase_idx = 0

    # Skip title phrase (first phrase in the section)
    if phrases and phrases[0].language_code != l2_code:
        phrase_idx = 1

    for kp in lesson.key_phrases:
        breakdown = build_word_breakdown_spans(kp.phrase, l2_code)
        expected = 2 + len(breakdown)

        remaining = len(phrases) - phrase_idx
        if remaining < expected:
            import warnings

            warnings.warn(
                f"annotate_chunk_upos: key phrase arithmetic mismatch for {kp.phrase!r}: "
                f"expected {expected} phrases, {remaining} remaining — skipping",
                UserWarning,
                stacklevel=2,
            )
            return 0

        # Analyze this key phrase's sentence
        try:
            analyses: list[TokenAnalysis] = analyze_sentence_cached(
                srs_db, lemmatizer, kp.phrase, l2_code, model_version
            )
            surface_to_upos: dict[str, str] = {ta.surface.lower(): ta.upos for ta in analyses if ta.upos}
        except Exception:
            import warnings

            warnings.warn(
                f"annotate_chunk_upos: analysis failed for {kp.phrase!r}",
                UserWarning,
                stacklevel=2,
            )
            phrase_idx += expected
            continue

        # Walk the expected phrases: L2 text (idx 0), EN translation (idx 1),
        # then breakdown chunks (idx 2..expected-1)
        for i in range(2, expected):
            chunk_phrase = phrases[phrase_idx + i]
            if chunk_phrase.source_word is not None:
                upos = surface_to_upos.get(chunk_phrase.source_word.lower(), "")
                if upos:
                    chunk_phrase.upos = upos
                    count += 1

        phrase_idx += expected

    return count


def _injected_lemmatizer(request: Request) -> dict[str, object]:
    """Lemmatizer overrides from app state, or ``{}`` to resolve for real.

    The same seam ``annotate_chunk_upos_for_lesson`` already exposes as keyword
    arguments, reached the way every other dependency in this app is reached.
    Resolving for real loads Stanza, which the default gate deliberately does
    not run (``--run-stanza``), so without an injection point the ordering these
    endpoints depend on can only be checked by mocking into ``app.`` — which the
    mock-boundary rule forbids, and rightly: the fix is a seam, not a patch.
    """
    lemmatizer = getattr(request.app.state, "lemmatizer", None)
    if lemmatizer is None:
        return {}
    return {"lemmatizer": lemmatizer, "model_version": getattr(request.app.state, "model_version", None)}


@router.post("/generate", status_code=201, response_model=GenerateStoryResponse)
async def generate_story(body: GenerateStoryRequest, request: Request):
    store = request.state.content_store
    curriculum = store.get_curriculum(body.curriculum_id)
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    days = [d for d in curriculum.days if d.day == body.day]
    if not days:
        raise HTTPException(status_code=404, detail=f"Day {body.day} not found in curriculum")

    curriculum_day = days[0]
    strategy = ContentStrategy[body.strategy]
    language = request.state.language
    generator = request.app.state.story_generator

    try:
        lesson = await generator.generate(
            curriculum_day=curriculum_day,
            language=language,
            strategy=strategy,
            cefr_level=curriculum.cefr_level,
            srs_db=request.state.srs_db,
            review_pressure=curriculum.review_pressure(body.review_pressure),
        )
    except NoReviewVocabularyError as e:
        # 409, not the neighbouring 502: nothing upstream failed and nothing is
        # malformed — a REVIEW story was asked for with nothing due to review.
        raise HTTPException(status_code=409, detail=str(e)) from e
    except StoryGenerationError as e:
        # Malformed LLM output — nothing persisted; the user retries.
        raise HTTPException(status_code=502, detail=str(e)) from e
    except LLMQuotaExceededError as e:
        # 429, not the neighbouring 502: nothing upstream failed — TT declined
        # to call because the day budget is exhausted. A 502 would read as "the
        # provider failed" and trigger retries that cannot succeed.
        raise HTTPException(status_code=429, detail=str(e)) from e
    except LLMError as e:
        # Opt-in fallback: complete() now raises a bare 429/HTTP error instead of
        # degrading to Ollama. Map to 502 (mirror plan_turn's PlannerError handling)
        # so the client gets the retry detail, never a raw 500/ASGI traceback. The
        # lesson-page Regenerate button routes through the pipeline (429 backoff +
        # sticky-failed) instead — this hardens the sync endpoint's other callers.
        raise HTTPException(status_code=502, detail=str(e)) from e

    lesson_id = mint_id(lesson.title)

    # Tag BEFORE saving. A detached task races the write: the tags land on an
    # in-memory Lesson nobody persists again, so the stored lesson is untagged
    # and every ambiguous word falls back to plain synthesis for the life of
    # that lesson. Observed in production on 2026-08-26 — a freshly generated
    # lesson had 0 of 47 chunks tagged, and re-running the same annotation over
    # the stored copy tagged all 47. This is the pipeline's ordering
    # (LessonPipeline._generate); the two paths must not disagree.
    #
    # Cost is bounded and paid on a request that already waits on an LLM story:
    # 1.85s cold, 34ms once the sentence analyses are cached.
    # request.state, NOT request.app.state (bd tunatale-pf4i). main.py:181 binds
    # app.state.srs_db to the DEFAULT language once at startup; main.py:310 resolves
    # request.state.srs_db per request from X-TT-Language. `store` and `language` in
    # this same handler already read request.state — reading app.state here
    # annotated and prewarmed the WRONG language's deck on any non-default-language
    # request, silently, because both are text-keyed caches.
    srs_db = getattr(request.state, "srs_db", None)
    if srs_db is not None:
        await annotate_chunk_upos_for_lesson(lesson, srs_db, **_injected_lemmatizer(request))

    store.save_lesson(lesson_id, body.curriculum_id, body.day, lesson)
    sync_curriculum_day_title(store, body.curriculum_id, body.day, lesson.title)

    # Pre-warming may stay detached: it only fills a cache, and nothing on the
    # lesson depends on it having finished.
    if srs_db is not None:
        task = asyncio.create_task(_prewarm_lesson(lesson, srs_db))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    # Enqueue a render job for this day
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline is not None:
        pipeline.enqueue(request.state.language_code, body.curriculum_id, body.day, "render")

    sections = [{"type": s.section_type.value, "phrase_count": len(s.phrases)} for s in lesson.sections]
    return {
        "id": lesson_id,
        "title": lesson.title,
        "sections": sections,
        "warnings": _logged_speaker_warnings(lesson.generation_metadata.get("story"), language),
    }


@router.post("/import", status_code=201, response_model=ImportStoryResponse)
async def import_story(body: ImportLessonRequest, request: Request):
    """Rebuild a Lesson from an edited Story-JSON file (docs/lesson-authoring.md).

    Same shape as generate_story's response, plus `warnings` (e.g. a speaker
    missing from the voice map, which would silently fall back to the narrator).
    """
    store = request.state.content_store
    if store.get_curriculum(body.curriculum_id) is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    language = request.state.language
    if body.raw is not None:
        try:
            story = parse_json_object(body.raw)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
    else:
        story = body.story  # guaranteed non-None by model validator

    try:
        lesson_id, lesson = import_lesson(
            store,
            {"curriculum_id": body.curriculum_id, "day": body.day, "story": story},
            language,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # import_lesson already wrote the lesson, so the tags need a second write
    # rather than an earlier one. Awaited and persisted for the same reason as
    # /generate above: a detached task tags a copy that is never stored.
    # request.state, NOT request.app.state (bd tunatale-pf4i). main.py:181 binds
    # app.state.srs_db to the DEFAULT language once at startup; main.py:310 resolves
    # request.state.srs_db per request from X-TT-Language. `store` and `language` in
    # this same handler already read request.state — reading app.state here
    # annotated and prewarmed the WRONG language's deck on any non-default-language
    # request, silently, because both are text-keyed caches.
    srs_db = getattr(request.state, "srs_db", None)
    if srs_db is not None:
        if await annotate_chunk_upos_for_lesson(lesson, srs_db, **_injected_lemmatizer(request)):
            store.update_lesson_data(lesson_id, lesson)
        asyncio.create_task(_prewarm_lesson(lesson, srs_db))

    # Enqueue a render job for this day
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline is not None:
        pipeline.enqueue(request.state.language_code, body.curriculum_id, body.day, "render")

    sections = [{"type": s.section_type.value, "phrase_count": len(s.phrases)} for s in lesson.sections]
    return {
        "id": lesson_id,
        "title": lesson.title,
        "sections": sections,
        "warnings": _logged_speaker_warnings(story, language),
    }


@router.get("/prompt", status_code=200, response_model=GetStoryPromptResponse)
async def get_story_prompt(
    request: Request,
    curriculum_id: str,
    day: int,
    strategy: Literal["WIDER", "DEEPER", "REVIEW"] = "WIDER",
    review_pressure: Literal["NATURAL", "BALANCED", "INSISTENT"] | None = None,
):
    """Export the exact prompts that the generate path would send to the LLM."""
    store = request.state.content_store
    curriculum = store.get_curriculum(curriculum_id)
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    days = [d for d in curriculum.days if d.day == day]
    if not days:
        raise HTTPException(status_code=404, detail=f"Day {day} not found in curriculum")

    language = request.state.language
    try:
        prompts = build_story_prompts(
            days[0],
            language,
            ContentStrategy[strategy],
            curriculum.cefr_level,
            srs_db=request.state.srs_db,
            review_pressure=curriculum.review_pressure(review_pressure),
        )
    except NoReviewVocabularyError as e:
        # 409, not 422 or 502: the request is well-formed and nothing upstream
        # failed — the collection simply has nothing due to review right now.
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"system_prompt": prompts.system_prompt, "user_prompt": prompts.user_prompt}


@router.get("/{lesson_id}/source", status_code=200, response_model=LessonSourceResponse)
async def get_lesson_source(lesson_id: str, request: Request):
    """Export a lesson as its editable, self-describing Story-JSON file."""
    store = request.state.content_store
    try:
        return export_lesson(store, lesson_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Lesson not found") from None


@router.get(
    "/{lesson_id}",
    status_code=200,
    response_model=LessonResponse,
    # day is only present when the serializer resolved one (get_lesson passes
    # it; get_lesson_by_day does not) — a plain response_model would rewrite the
    # payload by re-adding "day": null on the by-day route.
    response_model_exclude_unset=True,
)
async def get_lesson(lesson_id: str, request: Request):
    store = request.state.content_store
    row = store.get_lesson_row(lesson_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    lesson = Lesson.from_json(row["data_json"])
    return serialize_lesson(lesson_id, lesson, day=row["day"])
