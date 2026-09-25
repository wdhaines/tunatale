"""Greedy background pipeline — story generation + audio render."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from app.audio.render_service import render_lesson_audio
from app.generation.publishing import CurriculumDayTarget, publish_lesson
from app.generation.render_progress import RenderProgress
from app.generation.story import StoryGenerationError
from app.llm.activity import ActivityLog
from app.llm.client import LLMError
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
        lemmatizer: object | None = None,
        model_version: str | None = None,
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
        self._lemmatizer = lemmatizer
        self._model_version = model_version

        self._queue: asyncio.Queue[tuple[str, str, int]] = asyncio.Queue()
        self._jobs: dict[tuple[str, str, int], dict] = {}
        # job key -> the rate projection for a render in flight. Separate from
        # the record because it is state, not status: status_for reads it to
        # answer "how long left", and the entry goes when the day leaves
        # `rendering` (tunatale-hbnd).
        self._render_progress: dict[tuple[str, str, int], RenderProgress] = {}
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
        manual = curriculum.metadata.get("generation_mode", "auto") == "manual"
        for curriculum_day in sorted(curriculum.days, key=lambda d: d.day):
            day = curriculum_day.day
            key = (language_code, curriculum_id, day)
            existing = self._jobs.get(key)
            # Failure stickiness: do NOT re-enqueue failed jobs.
            if existing and existing["state"] == "failed":
                continue
            lesson_result = store.get_latest_lesson_by_day(curriculum_id, day)
            if lesson_result is None:
                if not manual:
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
            # Rows are skipped for days with no work, so position can't be the row
            # index — it has to come from the curriculum's own day ordering.
            positions = curriculum.day_positions()
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
                            "position": positions[day],
                            "state": record["state"],
                            "lesson_id": lesson_id,
                            "has_audio": has_audio,
                            "error": record.get("error"),
                            "retryable": record.get("retryable"),
                            "detail": record.get("detail"),
                            **self._render_counters(key, record),
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
                                "position": positions[day],
                                "state": "ready",
                                "lesson_id": lesson_id,
                                "has_audio": bool(audio_rows),
                                "error": None,
                                "retryable": None,
                                "detail": None,
                                # Nothing is being rendered, so there is no
                                # percentage to report — not a zero one.
                                "clips_done": None,
                                "clips_total": None,
                                "eta_seconds": None,
                            }
                        )
        return {"active": active, "days": days_list}

    def _render_counters(self, key: tuple[str, str, int], record: dict) -> dict:
        """The three render-progress fields for one day, as ``status_for`` reports them.

        All three are ``None`` unless the day is ``rendering``: a queued day has
        no plan yet and a finished one has nothing left to wait for, so a
        percentage there would be a lie rather than a zero. The ETA is recomputed
        at status time — the render is a multi-minute coroutine, so anything a
        caller cached on its last poll is stale by the time it is read
        (tunatale-hbnd).
        """
        if record["state"] != "rendering":
            return {"clips_done": None, "clips_total": None, "eta_seconds": None}
        progress = self._render_progress.get(key)
        return {
            "clips_done": record.get("clips_done"),
            "clips_total": record.get("clips_total"),
            "eta_seconds": progress.eta_seconds(time.time()) if progress is not None else None,
        }

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
                    srs_db=self._srs_dbs.get(language_code),
                    review_pressure=curriculum.review_pressure(),
                )
            except (StoryGenerationError, LLMError) as e:
                # LLMError: opt-in fallback means complete() now raises a bare 429/HTTP
                # error instead of degrading to Ollama junk-JSON (a StoryGenerationError).
                # Both funnel here so the rate-limit backoff below runs for a real 429
                # rather than escaping to the worker's generic 'Unexpected pipeline error'.
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

            # Tag BEFORE saving, write, prewarm and render scheduling all live in
            # publish_lesson; its module docstring explains why the ordering is
            # load-bearing.
            srs_db = self._srs_dbs.get(language_code)
            upos_kwargs: dict[str, object] = {}
            if self._lemmatizer is not None:
                upos_kwargs["lemmatizer"] = self._lemmatizer
                upos_kwargs["model_version"] = self._model_version
            lesson_id = await publish_lesson(
                lesson,
                target=CurriculumDayTarget(
                    store=store,
                    language_code=language_code,
                    curriculum_id=curriculum_id,
                    day=day,
                    pipeline=self,
                ),
                srs_db=srs_db,
                lemmatizer_kwargs=upos_kwargs,
                replace=record["force"],
                llm=self._llm_client,
            )
            record["lesson_id"] = lesson_id

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

        # A Cebuano render is minutes of throttled TTS, and "Rendering audio" is
        # not a progress report (tunatale-hbnd). The renderer knows the whole
        # clip plan within milliseconds of the first request, so the counts
        # below go from nothing to a real percentage, and the rate projection
        # turns them into an ETA once there is enough of a sample.
        key = (language_code, curriculum_id, day)
        progress = RenderProgress(started_at=time.time())
        self._render_progress[key] = progress

        def on_progress(done: int, total: int) -> None:
            progress.update(done, total, time.time())
            record["clips_done"] = done
            record["clips_total"] = total

        try:
            result = await render_lesson_audio(
                store=store,
                renderer=self._renderer,
                audio_dir=self._audio_dir,
                lesson_id=lesson_id,
                lesson=lesson,
                on_progress=on_progress,
            )
        except Exception as e:
            record["state"] = "failed"
            record["error"] = str(e)
            record["retryable"] = True
            record["updated_at"] = time.time()
            self._clear_render_progress(key, record)
            self._activity_log.record_pipeline(curriculum_id, day, "failed", str(e))
            return

        record["state"] = "ready"
        record["detail"] = None
        record["error"] = None
        record["updated_at"] = time.time()
        self._clear_render_progress(key, record)
        self._activity_log.record_pipeline(
            curriculum_id, day, "ready", f"Audio rendered ({len(result.get('sections', []))} sections)"
        )

    def _clear_render_progress(self, key: tuple[str, str, int], record: dict) -> None:
        """Drop the clip counts and the rate the moment the day leaves ``rendering``.

        Both exits of a render go through here, and the failed one matters most:
        a bar frozen at 116/171 on a day whose audio was never written is a
        claim about work that did not happen.
        """
        self._render_progress.pop(key, None)
        record["clips_done"] = None
        record["clips_total"] = None
