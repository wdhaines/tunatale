"""Audio generation and streaming endpoints."""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from app.api.models import RenderAudioRequest
from app.audio.render_service import render_lesson_audio
from app.audio.transcode import EXT_MEDIA_TYPE
from app.generation.section_builder import SECTION_TITLES
from app.models.lesson import SectionType

router = APIRouter(prefix="/api/audio", tags=["audio"])


def _sanitize_filename(name: str) -> str:
    """Strip filesystem-illegal characters and collapse whitespace to underscores."""
    name = re.sub(r'[/\\:*?"<>|]', "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name or "audio"


def _section_title(section_type: str) -> str:
    """Resolve a section type string to a human-readable title, falling back raw."""
    try:
        st = SectionType(section_type)
        return SECTION_TITLES.get(st, section_type)
    except ValueError:
        return section_type


def _resolve_topic_day(store, lesson_id: str) -> tuple[str, int]:
    """Resolve (topic, day) for a lesson, falling back to ('audio', 1)."""
    topic = "audio"
    day = 1
    lesson_row = store.get_lesson_row(lesson_id)
    if lesson_row is not None:
        day = lesson_row["day"]
        curriculum = store.get_curriculum(lesson_row["curriculum_id"])
        if curriculum is not None:
            topic = curriculum.topic
        else:
            lesson = store.get_lesson(lesson_id)
            topic = lesson.title
    return topic, day


def _build_section_filename(topic: str, day: int, section_index: int, section_type: str, ext: str = ".wav") -> str:
    """Build a context-rich section filename: {Topic}_Day{DD}_{NN}_{Title}{ext}."""
    safe_topic = _sanitize_filename(topic)
    title = _section_title(section_type)
    safe_title = _sanitize_filename(title)
    return f"{safe_topic}_Day{day:02d}_{section_index + 1:02d}_{safe_title}{ext}"


@router.post("/render", status_code=202)
async def render_audio(body: RenderAudioRequest, request: Request):
    store = request.state.content_store
    lesson = store.get_lesson(body.lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    return await render_lesson_audio(
        store=store,
        renderer=request.app.state.renderer,
        audio_dir=request.app.state.audio_dir,
        lesson_id=body.lesson_id,
        lesson=lesson,
    )


@router.get("/lesson/{lesson_id}", status_code=200)
async def get_lesson_audio(lesson_id: str, request: Request):
    """Return the audio file list for a lesson (full + sections) without re-rendering."""
    store = request.state.content_store
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
        title = _section_title(section_type_str)
        section_cues = json.loads(r["cues_json"]) if r["cues_json"] else None
        sections.append(
            {
                "audio_id": r["id"],
                "section_index": r["section_index"],
                "section_type": section_type_str,
                "title": title,
                "cues": section_cues,
            }
        )

    cues: list | None = None
    raw = full_row.get("cues_json")
    if raw is not None:
        cues = json.loads(raw)

    return {
        "audio_id": full_row["id"],
        "lesson_id": lesson_id,
        "sections": sections,
        "cues": cues,
    }


@router.get("/lesson/{lesson_id}/zip", status_code=200)
async def download_lesson_zip(lesson_id: str, request: Request):
    """Return a ZIP of all section WAVs for a lesson with context-rich filenames."""
    store = request.state.content_store
    rows = store.list_audio_files_for_lesson(lesson_id)
    full_row = next((r for r in rows if r["section_index"] is None), None)
    section_rows = [r for r in rows if r["section_index"] is not None]

    if not section_rows:
        raise HTTPException(status_code=404, detail="No section audio files found for this lesson")

    # Validate all files exist before building the ZIP
    all_rows = ([full_row] if full_row else []) + section_rows
    for r in all_rows:
        if not Path(r["file_path"]).exists():
            raise HTTPException(status_code=404, detail=f"Audio file missing: {r['file_path']}")

    topic, day = _resolve_topic_day(store, lesson_id)
    safe_topic = _sanitize_filename(topic)

    # Build ZIP in memory: full lesson file first (sorts as _00_), then sections
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
        if full_row:
            full_ext = Path(full_row["file_path"]).suffix or ".wav"
            full_filename = f"{safe_topic}_Day{day:02d}_00_Full{full_ext}"
            zf.write(full_row["file_path"], arcname=full_filename)
        for r in sorted(section_rows, key=lambda x: x["section_index"]):
            ext = Path(r["file_path"]).suffix or ".wav"
            filename = _build_section_filename(topic, day, r["section_index"], r["section_type"] or "", ext)
            zf.write(r["file_path"], arcname=filename)

    zip_name = f"{_sanitize_filename(topic)}_Day{day:02d}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


@router.get("/{audio_id}", status_code=200)
async def get_audio(audio_id: str, request: Request):
    store = request.state.content_store
    row = store.get_audio_file_row(audio_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Audio not found")

    path = Path(row["file_path"])
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio file missing")

    # Build a friendly download filename with curriculum context
    lesson_id = row["lesson_id"]
    topic, day = _resolve_topic_day(store, lesson_id)

    # Derive extension + media type from the actual stored file, so pre-existing
    # WAV files and newly-rendered compressed files both serve correctly.
    ext = path.suffix or ".wav"
    media_type = EXT_MEDIA_TYPE.get(ext, "application/octet-stream")

    if row["section_index"] is not None:
        filename = _build_section_filename(topic, day, row["section_index"], row["section_type"] or "", ext)
    else:
        filename = f"{_sanitize_filename(topic)}_Day{day:02d}_full{ext}"

    return FileResponse(
        str(path),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
