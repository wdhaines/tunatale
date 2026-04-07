"""Story generation endpoints."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.models.strategy import ContentStrategy

router = APIRouter(prefix="/api/story", tags=["generation"])


def _slug(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:50]


class GenerateStoryRequest(BaseModel):
    curriculum_id: str
    day: int = 1
    strategy: str = "WIDER"


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
    )

    lesson_id = f"{_slug(lesson.title)}-{uuid.uuid4().hex[:8]}"
    store.save_lesson(lesson_id, body.curriculum_id, body.day, lesson)

    sections = [{"type": s.section_type.value, "phrase_count": len(s.phrases)} for s in lesson.sections]
    return {"id": lesson_id, "title": lesson.title, "sections": sections}


@router.get("/{lesson_id}", status_code=200)
async def get_lesson(lesson_id: str, request: Request):
    store = request.app.state.content_store
    lesson = store.get_lesson(lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    return {
        "id": lesson_id,
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
