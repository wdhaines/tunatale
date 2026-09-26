"""Story generation endpoints."""

from __future__ import annotations

import logging
from typing import Literal

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
    ReglossLessonResponse,
)
from app.generation.glossing import ensure_dialogue_glosses
from app.generation.json_parsing import parse_json_object
from app.generation.publishing import CurriculumDayTarget, publish_lesson
from app.generation.story import (
    NoReviewVocabularyError,
    StoryGenerationError,
    build_lesson_from_story,
    build_story_prompts,
)
from app.llm.client import LLMError, LLMQuotaExceededError
from app.models.lesson import Lesson
from app.models.strategy import ContentStrategy
from app.storage.lesson_io import export_lesson, validate_story

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/story", tags=["generation"])


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
            content_store=store,
            curriculum_id=body.curriculum_id,
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

    # request.state, NOT request.app.state (bd tunatale-pf4i). main.py:181 binds
    # app.state.srs_db to the DEFAULT language once at startup; main.py:310 resolves
    # request.state.srs_db per request from X-TT-Language. `store` and `language` in
    # this same handler already read request.state — reading app.state here
    # annotated and prewarmed the WRONG language's deck on any non-default-language
    # request, silently, because both are text-keyed caches.
    srs_db = getattr(request.state, "srs_db", None)

    # UPOS (awaited, pre-write), write, prewarm and render scheduling all live in
    # publish_lesson; its module docstring explains why the ordering is load-bearing.
    lesson_id = await publish_lesson(
        lesson,
        target=CurriculumDayTarget(
            store=store,
            language_code=request.state.language_code,
            curriculum_id=body.curriculum_id,
            day=body.day,
            pipeline=getattr(request.app.state, "pipeline", None),
        ),
        srs_db=srs_db,
        lemmatizer_kwargs=_injected_lemmatizer(request),
        replace=False,
        llm=getattr(request.app.state, "llm", None),
    )

    sections = [{"type": s.section_type.value, "phrase_count": len(s.phrases)} for s in lesson.sections]
    return {
        "id": lesson_id,
        "title": lesson.title,
        "sections": sections,
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

    # The exported prompt is byte-identical to the generate-path prompt, so it no
    # longer asks for dialogue_glosses either (bd tunatale-yet7). Without this a
    # pasted story would build a lesson with no hover translations at all. A story
    # pasted WITH glosses — an older prompt, or hand-added — skips the call.
    await ensure_dialogue_glosses(story, getattr(request.app.state, "llm", None), language)

    try:
        # Validate and rebuild BEFORE writing — the same derivation import_lesson
        # used, minus its write. publish_lesson runs UPOS before its single
        # write, so the old write-then-rewrite (import_lesson wrote, then a
        # second write persisted the tags) is gone.
        validate_story(story, language=language)
        curriculum = store.get_curriculum(body.curriculum_id)
        review_words = curriculum.review_request(body.day) if curriculum is not None else ()
        lesson = build_lesson_from_story(story, language=language, review_words=review_words)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # request.state, NOT request.app.state (bd tunatale-pf4i): main.py:310
    # resolves request.state.srs_db per request from X-TT-Language; reading
    # app.state here annotated and prewarmed the WRONG language's deck on any
    # non-default-language request.
    srs_db = getattr(request.state, "srs_db", None)

    lesson_id = await publish_lesson(
        lesson,
        target=CurriculumDayTarget(
            store=store,
            language_code=request.state.language_code,
            curriculum_id=body.curriculum_id,
            day=body.day,
            pipeline=getattr(request.app.state, "pipeline", None),
        ),
        srs_db=srs_db,
        lemmatizer_kwargs=_injected_lemmatizer(request),
        replace=False,
        llm=getattr(request.app.state, "llm", None),
    )

    sections = [{"type": s.section_type.value, "phrase_count": len(s.phrases)} for s in lesson.sections]
    return {
        "id": lesson_id,
        "title": lesson.title,
        "sections": sections,
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
            content_store=store,
            curriculum_id=curriculum_id,
        )
    except NoReviewVocabularyError as e:
        # 409, not 422 or 502: the request is well-formed and nothing upstream
        # failed — the collection simply has nothing due to review right now.
        raise HTTPException(status_code=409, detail=str(e)) from e
    # A write on a GET, deliberately: recording what an EXPORT handed out is
    # part of exporting it, and this is the only moment the requested set exists
    # on the manual path. Re-exporting simply overwrites (bd tunatale-g4c9).
    curriculum.record_review_request(day, prompts.review_words)
    store.save_curriculum(curriculum_id, curriculum)
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


@router.post("/{lesson_id}/regloss", status_code=200, response_model=ReglossLessonResponse)
async def regloss_lesson_story(lesson_id: str, request: Request):
    """Re-run the gloss pass on a stored lesson's story.

    Lessons can be stored with zero hover translations when
    ``ensure_dialogue_glosses`` degrades. This repairs them in place — only
    ``dialogue_glosses`` inside the story blob is replaced, then the lesson is
    rebuilt through the same derivation the import route uses.

    ⚠️ Uses ``update_lesson_data``, NEVER ``save_lesson``: the latter is
    ``INSERT OR REPLACE``, which assigns a NEW rowid and resets ``created_at``.
    ``get_lesson_days`` surfaces only ``MAX(rowid)``, so a regloss written that
    way would leave the previous row invisible-but-present — manufacturing the
    duplicate-row condition tracked as bd tunatale-326c.
    """
    store = request.state.content_store
    row = store.get_lesson_row(lesson_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    lesson = Lesson.from_json(row["data_json"])
    story = lesson.generation_metadata.get("story")
    if story is None:
        raise HTTPException(
            status_code=409,
            detail="This lesson has no stored story to re-gloss",
        )

    story.pop("dialogue_glosses", None)
    language = request.state.language
    await ensure_dialogue_glosses(story, getattr(request.app.state, "llm", None), language)

    try:
        validate_story(story, language=language)
        curriculum = store.get_curriculum(row["curriculum_id"])
        review_words = curriculum.review_request(row["day"]) if curriculum is not None else ()
        lesson = build_lesson_from_story(story, language=language, review_words=review_words)
    except (StoryGenerationError, ValueError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    store.update_lesson_data(lesson_id, lesson)

    metadata = lesson.generation_metadata
    warnings: list[str] = []
    if metadata.get("gloss_entry_count") == 0:
        warnings.append("This lesson has no hover translations")
    return {
        "id": lesson_id,
        "gloss_entry_count": metadata.get("gloss_entry_count", 0),
        "warnings": warnings,
    }
