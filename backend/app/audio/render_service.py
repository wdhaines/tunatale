"""Shared audio render service — extracted from POST /api/audio/render."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from pathlib import Path

from app.audio.transcode import CODEC_EXT
from app.config import settings
from app.generation.section_builder import SECTION_TITLES
from app.storage.store import ContentStore


async def render_lesson_audio(
    store: ContentStore,
    renderer,
    audio_dir: Path,
    lesson_id: str,
    lesson,
) -> dict:
    """Render audio for a lesson and persist the results.

    Lifted verbatim from the POST /api/audio/render endpoint. Returns the same
    payload shape so both the endpoint and the pipeline caller get identical
    results.
    """
    old_rows = store.list_audio_files_for_lesson(lesson_id)
    old_file_paths = [r["file_path"] for r in old_rows]

    audio_dir.mkdir(parents=True, exist_ok=True)

    ext = CODEC_EXT.get(settings.audio_delivery_codec, "wav")
    audio_id = str(uuid.uuid4())
    full_path = audio_dir / f"{audio_id}.{ext}"

    section_ids = [str(uuid.uuid4()) for _ in lesson.sections]
    section_paths = [audio_dir / f"{sid}.{ext}" for sid in section_ids]

    cues = await renderer.render(lesson, full_path, section_paths=section_paths)
    cues_json = json.dumps([asdict(c) for c in cues], ensure_ascii=False)

    store.delete_audio_files_for_lesson(lesson_id)
    store.save_audio_file(audio_id, lesson_id, str(full_path), cues_json=cues_json)
    for i, (sid, section) in enumerate(zip(section_ids, lesson.sections, strict=True)):
        store.save_audio_file(
            sid,
            lesson_id,
            str(section_paths[i]),
            section_index=i,
            section_type=section.section_type.value,
        )

    for fp in old_file_paths:
        Path(fp).unlink(missing_ok=True)

    sections = [
        {
            "audio_id": sid,
            "section_index": i,
            "section_type": section.section_type.value,
            "title": SECTION_TITLES.get(section.section_type, section.section_type.value),
        }
        for i, (sid, section) in enumerate(zip(section_ids, lesson.sections, strict=True))
    ]

    return {
        "audio_id": audio_id,
        "lesson_id": lesson_id,
        "sections": sections,
        "cues": json.loads(cues_json),
    }
