"""Curriculum generation and retrieval endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api/curriculum", tags=["curriculum"])


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

    curriculum_id = str(uuid.uuid4())
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
