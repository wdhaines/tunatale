"""Story generation endpoints."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid

import anyio
from fastapi import APIRouter, HTTPException, Request

from app.api.models import GenerateStoryRequest
from app.models.lesson import Lesson, SectionType
from app.models.strategy import ContentStrategy
from app.srs.database import SRSDatabase
from app.srs.lemmatizer import analyze_sentence_cached, get_lemmatizer, model_version_for

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/story", tags=["generation"])


def _slug(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:50]


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
    store = request.app.state.content_store
    curriculum = store.get_curriculum(body.curriculum_id)
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    days = [d for d in curriculum.days if d.day == body.day]
    if not days:
        raise HTTPException(status_code=404, detail=f"Day {body.day} not found in curriculum")

    curriculum_day = days[0]
    strategy = ContentStrategy[body.strategy]
    language = request.app.state.language
    generator = request.app.state.story_generator

    lesson = await generator.generate(
        curriculum_day=curriculum_day,
        language=language,
        strategy=strategy,
        cefr_level=curriculum.cefr_level,
    )

    lesson_id = f"{_slug(lesson.title)}-{uuid.uuid4().hex[:8]}"
    store.save_lesson(lesson_id, body.curriculum_id, body.day, lesson)

    # Pre-warm the analysis cache off the request path
    srs_db = getattr(request.app.state, "srs_db", None)
    if srs_db is not None:
        asyncio.create_task(_prewarm_lesson(lesson, srs_db))

    sections = [{"type": s.section_type.value, "phrase_count": len(s.phrases)} for s in lesson.sections]
    return {"id": lesson_id, "title": lesson.title, "sections": sections}


@router.get("/{lesson_id}", status_code=200)
async def get_lesson(lesson_id: str, request: Request):
    store = request.app.state.content_store
    row = store.get_lesson_row(lesson_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    lesson = Lesson.from_json(row["data_json"])
    return {
        "id": lesson_id,
        "day": row["day"],
        "title": lesson.title,
        "language_code": lesson.language_code,
        "key_phrases": [{"phrase": kp.phrase, "translation": kp.translation} for kp in lesson.key_phrases],
        "sections": [
            {
                "type": s.section_type.value,
                "phrases": [
                    {"text": p.text, "role": p.role, "language_code": p.language_code, "voice_id": p.voice_id}
                    for p in s.phrases
                ],
            }
            for s in lesson.sections
        ],
    }
