"""Greedy background pipeline — story generation + audio render."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from app.audio.render_service import render_lesson_audio
from app.generation.ids import mint_id
from app.generation.story import StoryGenerationError
from app.llm.activity import ActivityLog
from app.storage.store import ContentStore

logger = logging.getLogger(__name__)


class LessonPipeline:
    """Single-worker background queue that generates stories and renders audio.

    Idempotent enqueue (no-ops if a job for the same key is already active).
    Failure-stickiness: reconcile() skips previously-failed jobs.
    """

    def __init__(
        self,
        story_generator,
        renderer,
        audio_dir,
        content_stores: dict[str, ContentStore],
        languages: dict[str, object],
        srs_dbs: dict[str, object],
        activity_log: ActivityLog,
        llm_client,
        *,
        sleep: Callable[[float], object] | None = None,
        max_attempts: int = 4,
        max_wait_s: float = 90.0,
    ) -> None:
        self._story_generator = story_generator
        self._renderer = renderer
        self._audio_dir = audio_dir
        self._content_stores = content_stores
        self._languages = languages
        self._srs_dbs = srs_dbs
        self._activity_log = activity_log
        self._llm_client = llm_client
        self._sleep = sleep or asyncio.sleep
        self._max_attempts = max_attempts
        self._max_wait_s = max_wait_s

        self._queue: asyncio.Queue[tuple[str, str, int]] = asyncio.Queue()
        self._jobs: dict[tuple[str, str, int], dict] = {}
        self._worker_task: asyncio.Task | None = None

    # ── Public API ─────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._worker_task is not None:
            return
        self._worker_task = asyncio.create_task(self._worker_loop())

    async def shutdown(self) -> None:
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._worker_task
        self._worker_task = None

    def enqueue(
        self,
        language_code: str,
        curriculum_id: str,
        day: int,
        kind: str,
        force: bool = False,
        strategy: str = "WIDER",
    ) -> None:
        key = (language_code, curriculum_id, day)
        existing = self._jobs.get(key)
        if existing and existing["state"] not in ("failed", "ready"):
            return
        if existing and existing["state"] == "ready" and not force:
            return
        record = {
            "curriculum_id": curriculum_id,
            "day": day,
            "language_code": language_code,
            "kind": kind,
            "force": force,
            "strategy": strategy,
            "state": "queued",
            "detail": None,
            "error": None,
            "retryable": None,
            "attempts": 0,
            "lesson_id": None,
            "updated_at": time.time(),
        }
        self._jobs[key] = record
        self._activity_log.record_pipeline(curriculum_id, day, "queued", f"{kind} queued for day {day}")
        self._queue.put_nowait(key)

    def reconcile(self, language_code: str, curriculum_id: str) -> None:
        store = self._content_stores[language_code]
        curriculum = store.get_curriculum(curriculum_id)
        if curriculum is None:
            return
        for curriculum_day in sorted(curriculum.days, key=lambda d: d.day):
            day = curriculum_day.day
            key = (language_code, curriculum_id, day)
            existing = self._jobs.get(key)
            # Failure stickiness: do NOT re-enqueue failed jobs.
            if existing and existing["state"] == "failed":
                continue
            lesson_result = store.get_latest_lesson_by_day(curriculum_id, day)
            if lesson_result is None:
                self.enqueue(language_code, curriculum_id, day, "generate")
            else:
                lesson_id, lesson = lesson_result
                audio_rows = store.list_audio_files_for_lesson(lesson_id)
                if not audio_rows:
                    self.enqueue(language_code, curriculum_id, day, "render")

    def status_for(self, language_code: str, curriculum_id: str) -> dict:
        store = self._content_stores.get(language_code)
        curriculum = store.get_curriculum(curriculum_id) if store else None
        days_list: list[dict] = []
        active = False
        if curriculum:
            for curriculum_day in sorted(curriculum.days, key=lambda d: d.day):
                day = curriculum_day.day
                key = (language_code, curriculum_id, day)
                record = self._jobs.get(key)
                if record:
                    if record["state"] in ("queued", "generating", "rendering"):
                        active = True
                    lesson_id = record.get("lesson_id")
                    has_audio = False
                    if lesson_id is not None:
                        audio_rows = store.list_audio_files_for_lesson(lesson_id)
                        has_audio = bool(audio_rows)
                    days_list.append(
                        {
                            "day": day,
                            "state": record["state"],
                            "lesson_id": lesson_id,
                            "has_audio": has_audio,
                            "error": record.get("error"),
                            "retryable": record.get("retryable"),
                            "detail": record.get("detail"),
                        }
                    )
                else:
                    lesson_result = store.get_latest_lesson_by_day(curriculum_id, day)
                    if lesson_result:
                        lesson_id, lesson = lesson_result
                        audio_rows = store.list_audio_files_for_lesson(lesson_id)
                        days_list.append(
                            {
                                "day": day,
                                "state": "ready",
                                "lesson_id": lesson_id,
                                "has_audio": bool(audio_rows),
                                "error": None,
                                "retryable": None,
                                "detail": None,
                            }
                        )
        return {"active": active, "days": days_list}

    def retry(self, language_code: str, curriculum_id: str, day: int) -> str:
        store = self._content_stores[language_code]
        curriculum = store.get_curriculum(curriculum_id)
        if curriculum is None or day not in {d.day for d in curriculum.days}:
            raise KeyError(f"Day {day} not found in curriculum {curriculum_id}")
        key = (language_code, curriculum_id, day)
        record = self._jobs.get(key)
        if record and record["state"] in ("queued", "generating", "rendering"):
            raise RuntimeError(f"Day {day} is currently active ({record['state']})")
        lesson_result = store.get_latest_lesson_by_day(curriculum_id, day)
        if lesson_result:
            lesson_id, lesson = lesson_result
            audio_rows = store.list_audio_files_for_lesson(lesson_id)
            if audio_rows:
                if key in self._jobs:
                    del self._jobs[key]
                return "ready"
            # Lesson exists, only audio is missing — a render is enough.
            # Re-generating here would burn LLM quota for no reason.
            self.enqueue(language_code, curriculum_id, day, "render")
            return "queued"
        self.enqueue(language_code, curriculum_id, day, "generate")
        return "queued"

    def regenerate(self, language_code: str, curriculum_id: str, day: int, strategy: str = "WIDER") -> str:
        store = self._content_stores[language_code]
        curriculum = store.get_curriculum(curriculum_id)
        if curriculum is None or day not in {d.day for d in curriculum.days}:
            raise KeyError(f"Day {day} not found in curriculum {curriculum_id}")
        key = (language_code, curriculum_id, day)
        record = self._jobs.get(key)
        if record and record["state"] in ("queued", "generating", "rendering"):
            raise RuntimeError(f"Day {day} is currently active ({record['state']})")
        self.enqueue(language_code, curriculum_id, day, "generate", force=True, strategy=strategy)
        return "queued"

    # ── Internal worker ────────────────────────────────────────────────────

    async def _worker_loop(self) -> None:
        """Consume jobs one at a time."""
        while True:
            try:
                key = await self._queue.get()
            except asyncio.CancelledError:
                break
            try:
                await self._process_job(key)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Pipeline worker: unexpected error processing %s", key)
                record = self._jobs.get(key)
                if record:
                    record["state"] = "failed"
                    record["error"] = "Unexpected pipeline error"
                    record["retryable"] = True
                    record["updated_at"] = time.time()
                    self._activity_log.record_pipeline(
                        record["curriculum_id"], record["day"], "failed", record["error"]
                    )
            finally:
                self._queue.task_done()

    async def _process_job(self, key: tuple[str, str, int]) -> None:
        language_code, curriculum_id, day = key
        record = self._jobs.get(key)
        if record is None:
            return

        kind = record["kind"]
        store = self._content_stores[language_code]

        if kind == "generate":
            await self._generate(record, store, language_code, curriculum_id, day)
        elif kind == "render":
            await self._render(record, store, language_code, curriculum_id, day)

    async def _generate(
        self,
        record: dict,
        store: ContentStore,
        language_code: str,
        curriculum_id: str,
        day: int,
    ) -> None:
        curriculum = store.get_curriculum(curriculum_id)
        if curriculum is None:
            record["state"] = "failed"
            record["error"] = "Curriculum not found"
            record["retryable"] = False
            record["updated_at"] = time.time()
            self._activity_log.record_pipeline(curriculum_id, day, "failed", "Curriculum not found")
            return

        curriculum_days = [d for d in curriculum.days if d.day == day]
        if not curriculum_days:
            record["state"] = "failed"
            record["error"] = f"Day {day} not found in curriculum"
            record["retryable"] = False
            record["updated_at"] = time.time()
            self._activity_log.record_pipeline(curriculum_id, day, "failed", f"Day {day} not found")
            return

        curriculum_day = curriculum_days[0]
        language = self._languages[language_code]

        record["state"] = "generating"
        record["updated_at"] = time.time()
        self._activity_log.record_pipeline(curriculum_id, day, "generating", "Generating story")

        from app.models.strategy import ContentStrategy

        attempt = 0
        while attempt < self._max_attempts:  # pragma: no cover — loop body always returns
            attempt += 1
            record["attempts"] = attempt
            t0 = time.time()
            try:
                lesson = await self._story_generator.generate(
                    curriculum_day=curriculum_day,
                    language=language,
                    strategy=ContentStrategy[record["strategy"]],
                    cefr_level=curriculum.cefr_level,
                )
            except StoryGenerationError as e:
                msg = str(e)
                is_rate_limit = (
                    (self._llm_client.last_429 is not None and self._llm_client.last_429.get("at", 0) >= t0)
                    or "rate-limited" in msg
                    or "Ollama" in msg
                )
                if is_rate_limit and attempt < self._max_attempts:
                    retry_after_s = (
                        self._llm_client.last_429.get("retry_after_s", 0) if self._llm_client.last_429 else 0
                    )
                    tokens_reset_remaining = 0.0
                    if self._llm_client.last_rate_limits:
                        captured = self._llm_client.last_rate_limits.get("captured_at")
                        reset_s = self._llm_client.last_rate_limits.get("tokens_reset_s")
                        if captured is not None and reset_s is not None:
                            tokens_reset_remaining = max(0.0, captured + reset_s - time.time())
                    wait = min(max(retry_after_s, tokens_reset_remaining, 15.0), self._max_wait_s)
                    record["detail"] = (
                        f"waiting {wait:.0f}s for rate-limit window (attempt {attempt}/{self._max_attempts})"
                    )
                    record["updated_at"] = time.time()
                    self._activity_log.record_pipeline(curriculum_id, day, "generating", record["detail"])
                    await self._sleep(wait)
                    continue
                record["state"] = "failed"
                record["error"] = msg
                record["retryable"] = True
                record["updated_at"] = time.time()
                self._activity_log.record_pipeline(curriculum_id, day, "failed", msg)
                return

            lesson_id = mint_id(lesson.title)
            store.save_lesson(lesson_id, curriculum_id, day, lesson)
            record["lesson_id"] = lesson_id

            # Pre-warm the analysis cache in the background
            srs_db = self._srs_dbs.get(language_code)
            if srs_db is not None:
                from app.api.generation import _prewarm_lesson

                asyncio.create_task(_prewarm_lesson(lesson, srs_db))

            # Transition to render step
            await self._render(record, store, language_code, curriculum_id, day)
            return

    async def _render(
        self,
        record: dict,
        store: ContentStore,
        language_code: str,
        curriculum_id: str,
        day: int,
    ) -> None:
        lesson_id = record.get("lesson_id")
        if lesson_id is None:
            lesson_result = store.get_latest_lesson_by_day(curriculum_id, day)
            if lesson_result is None:
                record["state"] = "failed"
                record["error"] = "No lesson found for this day"
                record["retryable"] = True
                record["updated_at"] = time.time()
                self._activity_log.record_pipeline(curriculum_id, day, "failed", "No lesson found")
                return
            lesson_id, lesson = lesson_result
            record["lesson_id"] = lesson_id
        else:
            lesson = store.get_lesson(lesson_id)
            if lesson is None:
                record["state"] = "failed"
                record["error"] = f"Lesson {lesson_id} not found in store"
                record["retryable"] = True
                record["updated_at"] = time.time()
                self._activity_log.record_pipeline(curriculum_id, day, "failed", "Lesson not found")
                return

        record["state"] = "rendering"
        record["updated_at"] = time.time()
        self._activity_log.record_pipeline(curriculum_id, day, "rendering", "Rendering audio")

        try:
            result = await render_lesson_audio(
                store=store,
                renderer=self._renderer,
                audio_dir=self._audio_dir,
                lesson_id=lesson_id,
                lesson=lesson,
            )
        except Exception as e:
            record["state"] = "failed"
            record["error"] = str(e)
            record["retryable"] = True
            record["updated_at"] = time.time()
            self._activity_log.record_pipeline(curriculum_id, day, "failed", str(e))
            return

        record["state"] = "ready"
        record["detail"] = None
        record["error"] = None
        record["updated_at"] = time.time()
        self._activity_log.record_pipeline(
            curriculum_id, day, "ready", f"Audio rendered ({len(result.get('sections', []))} sections)"
        )
