"""Audio generation and streaming endpoints."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/audio", tags=["audio"])


class RenderAudioRequest(BaseModel):
    lesson_id: str


@router.post("/render", status_code=202)
async def render_audio(body: RenderAudioRequest, request: Request):
    store = request.app.state.content_store
    lesson = store.get_lesson(body.lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    renderer = request.app.state.renderer
    audio_dir: Path = request.app.state.audio_dir

    audio_id = str(uuid.uuid4())
    output_path = audio_dir / f"{audio_id}.wav"
    audio_dir.mkdir(parents=True, exist_ok=True)

    await renderer.render(lesson, output_path)

    store.save_audio_file(audio_id, body.lesson_id, str(output_path))

    return {"audio_id": audio_id, "lesson_id": body.lesson_id}


@router.get("/{audio_id}", status_code=200)
async def get_audio(audio_id: str, request: Request):
    store = request.app.state.content_store
    file_path = store.get_audio_file(audio_id)
    if file_path is None:
        raise HTTPException(status_code=404, detail="Audio not found")

    path = Path(file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio file missing")

    return FileResponse(str(path), media_type="audio/wav")
