"""The tools-menu re-render, shared by lessons and review sessions (bd tunatale-9paa).

Both pages offer the same two actions on their own ids — price the re-render,
then run it — and the audio rows key on the id either way, so the bodies live
here once and the two routers only resolve the id to a Lesson.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import HTTPException, Request

from app.api import app_state
from app.audio.render_cost import estimate_render
from app.audio.render_service import SectionSelectionError, rerender_lesson_audio, resolve_section_selection
from app.config import settings


def _selection(lesson, raw):
    try:
        return resolve_section_selection(lesson, raw)
    except SectionSelectionError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


def estimate(lesson, raw: list[str] | None) -> dict:
    """The cost of re-rendering *raw*'s sections, against the live TTS cache."""
    return asdict(estimate_render(lesson, _selection(lesson, raw), cache_dir=settings.tts_cache_dir))


async def rerender(
    request: Request, content_id: str, lesson, raw: list[str] | None, renders: set[str], busy: str
) -> dict:
    """Re-render *raw*'s sections of *lesson*, one at a time per id.

    The selection is checked BEFORE the in-flight marker is taken, so a bad
    request neither waits behind nor blocks a real one. The marker is dropped in
    a ``finally``, so a failed render does not wedge the button (the review
    session render route's reasoning).
    """
    section_types = _selection(lesson, raw)
    if content_id in renders:
        raise HTTPException(status_code=409, detail=busy)
    renders.add(content_id)
    try:
        return await rerender_lesson_audio(
            store=request.state.content_store,
            renderer=request.app.state.renderer,
            tts=app_state.tts(request),
            audio_dir=request.app.state.audio_dir,
            lesson_id=content_id,
            lesson=lesson,
            section_types=section_types,
        )
    except ValueError as e:
        # The reassemble's preconditions: no audio to keep yet, or a section
        # count that no longer matches. A whole-lesson re-render is the fix.
        raise HTTPException(status_code=409, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    finally:
        renders.discard(content_id)
