"""Shared audio render service — extracted from POST /api/audio/render."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import asdict, replace
from pathlib import Path

from app.audio.cues import Cue
from app.audio.transcode import CODEC_EXT
from app.config import settings
from app.generation.section_builder import SECTION_TITLES
from app.models.lesson import SectionType
from app.storage.store import ContentStore

# Map from slow section type → the structural-twin section type whose L2 line
# cues provide the natural-text scrub source.  slow_speed numbers its L2 lines
# identically to natural_speed (both include every line), and slow_translated
# numbers identically to translated (both skip lines without translations).
_SLOW_TEXT_SOURCE: dict[SectionType, SectionType] = {
    SectionType.SLOW_SPEED: SectionType.NATURAL_SPEED,
    SectionType.SLOW_TRANSLATED: SectionType.TRANSLATED,
}


def derive_section_cues(cues: list[Cue], lesson) -> dict[int, list[Cue]]:
    """Group the full manifest by section_index, rebase, and scrub ellipsis text.

    Returns ``{section_index: [Cue, ...]}`` — one entry per section.  The lesson
    title cue (``section_index=None``) is excluded.

    For SLOW_SPEED / SLOW_TRANSLATED sections, each L2 line cue's ``text`` is
    overwritten with the natural (non-slowed) text from the structural-twin
    section so that the player subtitle never shows ellipsis-broken text.
    """
    l2_code = lesson.language_code

    # Group by section_index, preserving cue order.
    groups: dict[int, list[Cue]] = defaultdict(list)
    for cue in cues:
        if cue.section_index is not None:
            groups[cue.section_index].append(cue)

    # Build text scrub maps for slow sections from their structural twin.
    scrub_maps: dict[int, dict[int, str]] = {}
    for sec_idx, section in enumerate(lesson.sections):
        source_type = _SLOW_TEXT_SOURCE.get(section.section_type)
        if source_type is None:
            continue
        # Find the structural-twin group index.
        twin_idx: int | None = None
        for other_idx, other_sec in enumerate(lesson.sections):
            if other_sec.section_type == source_type:
                twin_idx = other_idx
                break
        if twin_idx is None or twin_idx not in groups:
            continue
        text_map: dict[int, str] = {}
        for c in groups[twin_idx]:
            if c.language_code == l2_code and c.ref and c.ref.get("kind") == "line":
                text_map.setdefault(c.ref["target_index"], c.text)
        if text_map:
            scrub_maps[sec_idx] = text_map

    result: dict[int, list[Cue]] = {}
    for sec_idx, group in sorted(groups.items()):
        first_start = group[0].start_ms
        rebased = []
        for c in group:
            new_c = replace(
                c,
                start_ms=c.start_ms - first_start,
                end_ms=c.end_ms - first_start,
            )
            # Scrub ellipsis text for slow sections.
            if (
                sec_idx in scrub_maps
                and new_c.ref
                and new_c.ref.get("kind") == "line"
                and new_c.language_code == l2_code
            ):
                target = new_c.ref["target_index"]
                if target in scrub_maps[sec_idx]:
                    new_c = replace(new_c, text=scrub_maps[sec_idx][target])
            rebased.append(new_c)
        result[sec_idx] = rebased
    return result


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

    section_cues = derive_section_cues(cues, lesson)

    store.delete_audio_files_for_lesson(lesson_id)
    store.save_audio_file(audio_id, lesson_id, str(full_path), cues_json=cues_json)
    for i, (sid, section) in enumerate(zip(section_ids, lesson.sections, strict=True)):
        sec_cues = section_cues.get(i, [])
        sec_cues_json = json.dumps([asdict(c) for c in sec_cues], ensure_ascii=False) if sec_cues else None
        store.save_audio_file(
            sid,
            lesson_id,
            str(section_paths[i]),
            section_index=i,
            section_type=section.section_type.value,
            cues_json=sec_cues_json,
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
