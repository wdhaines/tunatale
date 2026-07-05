"""Story generation endpoints."""

from __future__ import annotations

import asyncio
import logging

import anyio
from fastapi import APIRouter, HTTPException, Request

from app.api._serializers import serialize_lesson
from app.api.models import GenerateStoryRequest, ImportLessonRequest
from app.generation.ids import mint_id
from app.generation.story import StoryGenerationError
from app.models.lesson import Lesson, SectionType
from app.models.strategy import ContentStrategy
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import analyze_sentence_cached, get_lemmatizer, model_version_for
from app.storage.lesson_io import export_lesson, import_lesson, speaker_warnings

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/story", tags=["generation"])

# Strong refs to fire-and-forget pre-warm tasks: the event loop only keeps a
# weak reference, so an un-anchored task can be garbage-collected mid-flight.
_background_tasks: set[asyncio.Task] = set()


async def _prewarm_lesson(lesson: Lesson, srs_db: SRSDatabase) -> None:
    """Background pre-warm: cache a freshly generated lesson's sentences.

    Runs the new lesson's natural-speed L2 sentences through
    ``analyze_sentence_cached`` so the transcript view never triggers a
    classla load for this content.
    """
    try:
        lemmatizer = get_lemmatizer()
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


@router.post("/generate", status_code=201)
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
        )
    except StoryGenerationError as e:
        # Malformed LLM output — nothing persisted; the user retries.
        raise HTTPException(status_code=502, detail=str(e)) from e

    lesson_id = mint_id(lesson.title)
    store.save_lesson(lesson_id, body.curriculum_id, body.day, lesson)

    # Pre-warm the analysis cache off the request path
    srs_db = getattr(request.app.state, "srs_db", None)
    if srs_db is not None:
        task = asyncio.create_task(_prewarm_lesson(lesson, srs_db))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    sections = [{"type": s.section_type.value, "phrase_count": len(s.phrases)} for s in lesson.sections]
    return {"id": lesson_id, "title": lesson.title, "sections": sections}


@router.post("/import", status_code=201)
async def import_story(body: ImportLessonRequest, request: Request):
    """Rebuild a Lesson from an edited Story-JSON file (docs/lesson-authoring.md).

    Same shape as generate_story's response, plus `warnings` (e.g. a speaker
    missing from the voice map, which would silently fall back to the narrator).
    """
    store = request.state.content_store
    if store.get_curriculum(body.curriculum_id) is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    language = request.state.language
    try:
        lesson_id, lesson = import_lesson(
            store,
            {"curriculum_id": body.curriculum_id, "day": body.day, "story": body.story},
            language,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    # Same background pre-warm as generation, so the transcript view is warm.
    srs_db = getattr(request.app.state, "srs_db", None)
    if srs_db is not None:
        asyncio.create_task(_prewarm_lesson(lesson, srs_db))

    sections = [{"type": s.section_type.value, "phrase_count": len(s.phrases)} for s in lesson.sections]
    return {
        "id": lesson_id,
        "title": lesson.title,
        "sections": sections,
        "warnings": speaker_warnings(body.story, language),
    }


@router.get("/{lesson_id}/source", status_code=200)
async def get_lesson_source(lesson_id: str, request: Request):
    """Export a lesson as its editable, self-describing Story-JSON file."""
    store = request.state.content_store
    try:
        return export_lesson(store, lesson_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Lesson not found") from None


@router.get("/{lesson_id}", status_code=200)
async def get_lesson(lesson_id: str, request: Request):
    store = request.state.content_store
    row = store.get_lesson_row(lesson_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    lesson = Lesson.from_json(row["data_json"])
    return serialize_lesson(lesson_id, lesson, day=row["day"])
