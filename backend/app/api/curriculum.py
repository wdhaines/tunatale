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

    curriculum = await generator.generate(
        topic=body.topic,
        language=language,
        cefr_level=body.cefr_level,
        num_days=body.num_days,
    )

    curriculum_id = str(uuid.uuid4())
    if not hasattr(request.app.state, "curricula"):
        request.app.state.curricula = {}
    request.app.state.curricula[curriculum_id] = curriculum

    return {
        "id": curriculum_id,
        "topic": curriculum.topic,
        "language_code": curriculum.language_code,
        "days": len(curriculum.days),
    }


@router.get("", status_code=200)
async def list_curricula(request: Request):
    curricula = getattr(request.app.state, "curricula", {})
    return [{"id": cid, "topic": c.topic} for cid, c in curricula.items()]


@router.get("/{curriculum_id}", status_code=200)
async def get_curriculum(curriculum_id: str, request: Request):
    curricula = getattr(request.app.state, "curricula", {})
    if curriculum_id not in curricula:
        raise HTTPException(status_code=404, detail="Curriculum not found")
    c = curricula[curriculum_id]
    return {"id": curriculum_id, "topic": c.topic, "language_code": c.language_code, "days": len(c.days)}
