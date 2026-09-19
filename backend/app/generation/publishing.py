"""Shared write+publish seam for lesson-shaped content (bd tunatale-w1fp).

Seven code paths write lesson-shaped content, and each used to perform the
same post-generation steps by hand, dropping different ones. The observable
bug that started this: creating a review session never rendered audio, while
creating a lesson always did. ``publish_lesson`` is the one ordering every
writer must share, and the ordering is load-bearing:

0. Lemma resolution, AWAITED and BEFORE the UPOS step, so the tags it reads
   already reflect context (``app.srs.lemma_resolver``; a no-op unless the
   language runs a table lemmatizer). *llm* is a required keyword so a new
   writer cannot silently skip it.
1. UPOS annotation, AWAITED and BEFORE the write. A detached task races the
   write: the tags land on an in-memory Lesson nobody persists again, so the
   stored lesson is untagged and every ambiguous word falls back to plain
   synthesis for the life of that content. Observed in production on
   2026-08-26 — a freshly generated lesson had 0 of 47 chunks tagged, and
   re-running the same annotation over the stored copy tagged all 47. The
   pipeline's generate path and the API handlers must not disagree.
2. ``target.write(lesson)`` — persist the lesson and return its content id.
3. ``if replace: target.invalidate_audio(content_id)`` — drop the audio of the
   content being REPLACED before the new render starts.
4. Pre-warm the analysis cache as an ANCHORED background task. The event loop
   only keeps a weak reference to a task, so an un-anchored one can be
   garbage-collected mid-flight; the module-level ``_background_tasks`` set is
   the strong reference, and tests drain exactly that set to wait for
   fire-and-forget work deterministically.
5. ``await target.schedule_render(content_id, lesson)`` — ensure audio will be
   rendered for the new content.

A review session is deliberately NOT a ``LessonPipeline`` job: that pipeline is
keyed ``(language_code, curriculum_id, day)`` throughout, and a session has
nothing to key on. ``ReviewSessionTarget`` therefore renders via
``render_lesson_audio`` directly, anchored in the same ``_background_tasks``
set so an auto-started render outlives the request that scheduled it.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Protocol

from app.audio.render_service import render_lesson_audio
from app.generation.ids import mint_id
from app.models.lesson import Lesson
from app.storage.lesson_io import sync_curriculum_day_title

_logger = logging.getLogger(__name__)

# Strong refs to fire-and-forget tasks spawned by publish_lesson: the event
# loop only keeps a weak reference, so an un-anchored task can be
# garbage-collected mid-flight. Both the prewarm tasks and the review-session
# render tasks land here; tests drain this set to wait deterministically
# instead of sleeping on timing.
_background_tasks: set[asyncio.Task] = set()


class ContentTarget(Protocol):
    """Where a written Lesson goes, and what must follow it there."""

    def write(self, lesson: Lesson) -> str:
        """Persist the lesson and any placement bookkeeping. Returns content id."""

    def invalidate_audio(self, content_id: str) -> None:
        """Drop audio rows/files for content being REPLACED. No-op on create."""

    async def schedule_render(self, content_id: str, lesson: Lesson) -> None:
        """Ensure audio will be rendered for this content."""


def _anchor(task: asyncio.Task) -> None:
    """Hold a strong reference to the task until it finishes."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def publish_lesson(
    lesson: Lesson,
    *,
    target: ContentTarget,
    srs_db,
    lemmatizer_kwargs: dict,
    replace: bool,
    llm,
) -> str:
    """Lemmas + UPOS (awaited, pre-write) -> write -> invalidate -> prewarm -> render."""
    if srs_db is not None:
        # Imported here, not at module scope: publishing is imported by the API
        # routers, and these helpers live in app.api.generation, so a module-
        # level import would be a cycle. Same lazy pattern LessonPipeline uses.
        from app.api.generation import annotate_chunk_upos_for_lesson
        from app.srs.lemma_resolver import resolve_lesson_lemmas

        await resolve_lesson_lemmas(lesson, srs_db, llm, **lemmatizer_kwargs)
        await annotate_chunk_upos_for_lesson(lesson, srs_db, **lemmatizer_kwargs)

    content_id = target.write(lesson)

    if replace:
        target.invalidate_audio(content_id)

    if srs_db is not None:
        from app.api.generation import _prewarm_lesson

        _anchor(asyncio.create_task(_prewarm_lesson(lesson, srs_db)))

    await target.schedule_render(content_id, lesson)
    return content_id


class CurriculumDayTarget:
    """A lesson written into a curriculum day; the pipeline renders it."""

    def __init__(self, store, language_code: str, curriculum_id: str, day: int, pipeline) -> None:
        self._store = store
        self._language_code = language_code
        self._curriculum_id = curriculum_id
        self._day = day
        self._pipeline = pipeline
        self._superseded_id: str | None = None

    def write(self, lesson: Lesson) -> str:
        # A regenerate mints a FRESH id (mint_id is {slug}-{uuid4hex8}), so it
        # INSERTS a second row rather than overwriting. Capture the day's current
        # lesson id here, before the write, so invalidate_audio can drop the old
        # row's audio once the new lesson is saved — the new id has no audio of
        # its own, which is exactly why the no-op could not be fixed in place.
        latest = self._store.get_latest_lesson_by_day(self._curriculum_id, self._day)
        self._superseded_id = latest[0] if latest is not None else None
        lesson_id = mint_id(lesson.title)
        self._store.save_lesson(lesson_id, self._curriculum_id, self._day, lesson)
        sync_curriculum_day_title(self._store, self._curriculum_id, self._day, lesson.title)
        return lesson_id

    def invalidate_audio(self, content_id: str) -> None:
        # Drop the SUPERSEDED lesson's audio: rows deleted from the store, files
        # unlinked from disk (the storage layer owns rows, the caller owns the
        # filesystem). The superseded lesson ROW itself is deliberately KEPT —
        # the user's decision (2026-09-17): a row is cheap and a possible undo,
        # while orphaned audio is bigger and can always be regenerated. The
        # content_id guard stops the id just written from being deleted if a
        # degenerate lookup ever captures it as the superseded id.
        superseded_id = self._superseded_id
        if superseded_id is None or superseded_id == content_id:
            return
        for file_path in self._store.delete_audio_files_for_lesson(superseded_id):
            file_path.unlink(missing_ok=True)

    async def schedule_render(self, content_id: str, lesson: Lesson) -> None:
        pipeline = self._pipeline
        if pipeline is not None:
            pipeline.enqueue(self._language_code, self._curriculum_id, self._day, "render")


class ReviewSessionTarget:
    """A session written under its own id; rendering is a direct audio call."""

    def __init__(
        self,
        store,
        language_code: str,
        session_id: str,
        session_date: str,
        renderer,
        audio_dir,
        renders_in_flight: set[str],
    ) -> None:
        self._store = store
        self._language_code = language_code
        self._session_id = session_id
        self._session_date = session_date
        self._renderer = renderer
        self._audio_dir = audio_dir
        self._renders_in_flight = renders_in_flight

    def write(self, lesson: Lesson) -> str:
        metadata = lesson.generation_metadata
        self._store.save_review_session(
            self._session_id,
            self._language_code,
            self._session_date,
            lesson,
            review_requested=metadata.get("review_requested"),
            review_used=metadata.get("review_used"),
        )
        return self._session_id

    def invalidate_audio(self, content_id: str) -> None:
        for file_path in self._store.delete_review_session_audio(content_id):
            Path(file_path).unlink(missing_ok=True)

    async def schedule_render(self, content_id: str, lesson: Lesson) -> None:
        # Anchored in the same module-level set as the prewarm tasks, so a
        # render that outlives the request cannot be garbage-collected mid-
        # flight and tests can drain it deterministically. Registered with the
        # SAME in-flight set the manual render route and render-status share,
        # so the frontend's poll-on-mount observes an auto-started render — a
        # second set would make the two surfaces disagree.
        _anchor(
            asyncio.create_task(
                _render_session_audio(
                    store=self._store,
                    renderer=self._renderer,
                    audio_dir=self._audio_dir,
                    session_id=content_id,
                    lesson=lesson,
                    renders_in_flight=self._renders_in_flight,
                )
            )
        )


async def _render_session_audio(
    *,
    store,
    renderer,
    audio_dir,
    session_id: str,
    lesson: Lesson,
    renders_in_flight: set[str],
) -> None:
    """Render one session's audio in the background, guarded by the in-flight set.

    The session id is dropped in a ``finally`` so a render that finishes OR
    fails releases the session — the same guarantee the manual render route
    makes, so one failed auto-render cannot wedge the render-status poll.
    """
    renders_in_flight.add(session_id)
    try:
        await render_lesson_audio(
            store=store,
            renderer=renderer,
            audio_dir=audio_dir,
            lesson_id=session_id,
            lesson=lesson,
        )
    except Exception:
        _logger.warning("Background render failed for review session %s", session_id, exc_info=True)
    finally:
        renders_in_flight.discard(session_id)
