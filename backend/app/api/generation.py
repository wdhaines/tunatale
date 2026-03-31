"""Story generation endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.models.strategy import ContentStrategy

router = APIRouter(prefix="/api/story", tags=["generation"])


class GenerateStoryRequest(BaseModel):
    curriculum_id: str
    day: int = 1
    strategy: str = "WIDER"


@router.post("/generate", status_code=201)
async def generate_story(body: GenerateStoryRequest, request: Request):
    curricula = getattr(request.app.state, "curricula", {})
    if body.curriculum_id not in curricula:
        raise HTTPException(status_code=404, detail="Curriculum not found")

    curriculum = curricula[body.curriculum_id]
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

    lesson_id = str(uuid.uuid4())
    if not hasattr(request.app.state, "lessons"):
        request.app.state.lessons = {}
    request.app.state.lessons[lesson_id] = lesson

    sections = [{"type": s.section_type.value, "phrase_count": len(s.phrases)} for s in lesson.sections]
    return {"id": lesson_id, "title": lesson.title, "sections": sections}


@router.get("/{lesson_id}", status_code=200)
async def get_lesson(lesson_id: str, request: Request):
    lessons = getattr(request.app.state, "lessons", {})
    if lesson_id not in lessons:
        raise HTTPException(status_code=404, detail="Lesson not found")
    lesson = lessons[lesson_id]
    return {
        "id": lesson_id,
        "title": lesson.title,
        "language_code": lesson.language_code,
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
