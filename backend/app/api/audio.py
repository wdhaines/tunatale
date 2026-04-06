"""Audio generation and streaming endpoints."""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.generation.section_builder import SECTION_TITLES
from app.models.lesson import SectionType

router = APIRouter(prefix="/api/audio", tags=["audio"])


def _sanitize_filename(name: str) -> str:
    """Strip filesystem-illegal characters and collapse whitespace to underscores."""
    name = re.sub(r'[/\\:*?"<>|]', "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name or "audio"


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
    audio_dir.mkdir(parents=True, exist_ok=True)

    # Allocate UUIDs for full lesson and each section
    audio_id = str(uuid.uuid4())
    full_path = audio_dir / f"{audio_id}.wav"

    section_ids = [str(uuid.uuid4()) for _ in lesson.sections]
    section_paths = [audio_dir / f"{sid}.wav" for sid in section_ids]

    await renderer.render(lesson, full_path, section_paths=section_paths)

    # Persist full lesson row
    store.save_audio_file(audio_id, body.lesson_id, str(full_path))

    # Persist per-section rows
    for i, (sid, section) in enumerate(zip(section_ids, lesson.sections, strict=True)):
        store.save_audio_file(
            sid,
            body.lesson_id,
            str(section_paths[i]),
            section_index=i,
            section_type=section.section_type.value,
        )

    sections = [
        {
            "audio_id": sid,
            "section_index": i,
            "section_type": section.section_type.value,
            "title": SECTION_TITLES.get(section.section_type, section.section_type.value),
        }
        for i, (sid, section) in enumerate(zip(section_ids, lesson.sections, strict=True))
    ]

    return {"audio_id": audio_id, "lesson_id": body.lesson_id, "sections": sections}


@router.get("/lesson/{lesson_id}", status_code=200)
async def get_lesson_audio(lesson_id: str, request: Request):
    """Return the audio file list for a lesson (full + sections) without re-rendering."""
    store = request.app.state.content_store
    rows = store.list_audio_files_for_lesson(lesson_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No audio found for this lesson")

    full_row = next((r for r in rows if r["section_index"] is None), None)
    if full_row is None:
        raise HTTPException(status_code=404, detail="Full lesson audio not found")

    section_rows = [r for r in rows if r["section_index"] is not None]

    sections = []
    for r in section_rows:
        section_type_str = r["section_type"] or ""
        try:
            st = SectionType(section_type_str)
            title = SECTION_TITLES.get(st, section_type_str)
        except ValueError:
            title = section_type_str
        sections.append(
            {
                "audio_id": r["id"],
                "section_index": r["section_index"],
                "section_type": section_type_str,
                "title": title,
            }
        )

    return {
        "audio_id": full_row["id"],
        "lesson_id": lesson_id,
        "sections": sections,
    }


@router.get("/{audio_id}", status_code=200)
async def get_audio(audio_id: str, request: Request):
    store = request.app.state.content_store
    row = store.get_audio_file_row(audio_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Audio not found")

    path = Path(row["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio file missing")

    # Build a friendly download filename
    lesson = store.get_lesson(row["lesson_id"])
    lesson_title = lesson.title if lesson else "audio"
    safe_title = _sanitize_filename(lesson_title)

    if row["section_index"] is not None:
        section_type = row["section_type"] or "section"
        idx = row["section_index"]
        filename = f"{safe_title}_{idx:02d}_{section_type}.wav"
    else:
        filename = f"{safe_title}.wav"

    return FileResponse(
        str(path),
        media_type="audio/wav",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
