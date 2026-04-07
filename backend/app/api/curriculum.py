"""Curriculum generation and retrieval endpoints."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/curriculum", tags=["curriculum"])


def _slug(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:50]


class GenerateCurriculumRequest(BaseModel):
    topic: str
    cefr_level: str = "A2"
    num_days: int = 7


@router.post("/generate", status_code=201)
async def generate_curriculum(body: GenerateCurriculumRequest, request: Request):
    generator = request.app.state.curriculum_generator
    language = request.app.state.language
    store = request.app.state.content_store

    curriculum = await generator.generate(
        topic=body.topic,
        language=language,
        cefr_level=body.cefr_level,
        num_days=body.num_days,
    )

    curriculum_id = f"{_slug(body.topic)}-{uuid.uuid4().hex[:8]}"
    store.save_curriculum(curriculum_id, curriculum)

    return {
        "id": curriculum_id,
        "topic": curriculum.topic,
        "language_code": curriculum.language_code,
        "days": len(curriculum.days),
    }


@router.get("", status_code=200)
async def list_curricula(request: Request):
    store = request.app.state.content_store
    return store.list_curricula()


@router.get("/{curriculum_id}", status_code=200)
async def get_curriculum(curriculum_id: str, request: Request):
    store = request.app.state.content_store
    curriculum = store.get_curriculum(curriculum_id)
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")
    return {
        "id": curriculum_id,
        "topic": curriculum.topic,
        "language_code": curriculum.language_code,
        "days": len(curriculum.days),
    }


@router.get("/{curriculum_id}/days/{day}/lesson", status_code=200)
async def get_lesson_by_day(curriculum_id: str, day: int, request: Request):
    store = request.app.state.content_store
    result = store.get_latest_lesson_by_day(curriculum_id, day)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No lesson found for day {day}")
    lesson_id, lesson = result
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
