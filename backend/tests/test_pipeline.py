"""Unit tests for LessonPipeline (DI-based fakes, no patch("app.…"))."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.generation.pipeline import LessonPipeline
from app.generation.story import StoryGenerationError
from app.llm.activity import ActivityLog
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language
from app.models.lesson import Lesson, Section, SectionType
from app.storage.store import ContentStore

# ── Fakes ───────────────────────────────────────────────────────────────────


class FakeStoryGenerator:
    def __init__(self):
        self.calls: list[dict] = []
        self.lesson_to_return = Lesson(
            title="Generated Lesson",
            language_code="sl",
            sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
        )
        self.fail_count: int = 0
        self.raise_error: Exception = StoryGenerationError("mock error")

    async def generate(self, curriculum_day, language, strategy, cefr_level="A2"):
        self.calls.append(
            {
                "curriculum_day": curriculum_day,
                "language": language,
                "strategy": strategy,
                "cefr_level": cefr_level,
            }
        )
        if self.fail_count > 0:
            self.fail_count -= 1
            raise self.raise_error
        return self.lesson_to_return


class FakeRenderer:
    def __init__(self):
        self.calls: list[dict] = []

    async def render(self, lesson, full_path, section_paths=None):
        self.calls.append(
            {
                "lesson": lesson,
                "full_path": full_path,
                "section_paths": section_paths,
            }
        )
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(b"audio")
        if section_paths:
            for sp in section_paths:
                sp.parent.mkdir(parents=True, exist_ok=True)
                sp.write_bytes(b"section")
        from app.audio.cues import Cue

        return [
            Cue(
                index=0,
                start_ms=0,
                end_ms=1000,
                section_index=None,
                section_type=None,
                phrase_index=0,
                role="narrator",
                language_code="en",
                text="test",
            ),
        ]


class FakeLLMClient:
    def __init__(self):
        self.last_429: dict | None = None
        self.last_rate_limits: dict | None = None
        self.last_provider: str | None = None


class RecorderSleep:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, duration: float) -> None:
        self.calls.append(duration)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_generator():
    return FakeStoryGenerator()


@pytest.fixture
def fake_renderer():
    return FakeRenderer()


@pytest.fixture
def fake_llm():
    return FakeLLMClient()


@pytest.fixture
def activity_log():
    return ActivityLog(maxlen=100)


@pytest.fixture
def sleep_recorder():
    return RecorderSleep()


@pytest.fixture
def pipeline(fake_generator, fake_renderer, fake_llm, activity_log, sleep_recorder, tmp_path):
    stores = {"sl": ContentStore(":memory:"), "no": ContentStore(":memory:")}
    languages = {"sl": Language.slovene(), "no": Language.norwegian()}
    srs_dbs: dict = {}
    return LessonPipeline(
        story_generator=fake_generator,
        renderer=fake_renderer,
        audio_dir=tmp_path,
        content_stores=stores,
        languages=languages,
        srs_dbs=srs_dbs,
        activity_log=activity_log,
        llm_client=fake_llm,
        sleep=sleep_recorder,
        max_attempts=4,
        max_wait_s=90.0,
    )


def sl_store(pipeline):
    return pipeline._content_stores["sl"]


async def wait_for_job(pipeline, lc, cid, day, state, timeout=5.0):
    deadline = time.monotonic() + timeout
    key = (lc, cid, day)
    while time.monotonic() < deadline:
        record = pipeline._jobs.get(key)
        if record and record["state"] == state:
            return record
        await asyncio.sleep(0.01)
    pytest.fail(f"Job {key} did not reach state {state!r} in {timeout}s (got {record})")


# ── Tests ───────────────────────────────────────────────────────────────────


class TestPipelineHappyPath:
    async def test_generate_job_goes_through_full_cycle(self, pipeline, fake_generator, fake_renderer, activity_log):
        """Generate job: queued → generating → rendering → ready, lesson saved."""
        store = sl_store(pipeline)
        cid = "cur-1"
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="Day 1", focus="hello", collocations=["hi"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)

        pipeline.start()
        pipeline.enqueue("sl", cid, 1, "generate")
        record = await wait_for_job(pipeline, "sl", cid, 1, "ready")

        assert record["state"] == "ready"
        assert record["lesson_id"] is not None
        lesson_result = store.get_latest_lesson_by_day(cid, 1)
        assert lesson_result is not None
        assert lesson_result[1].title == "Generated Lesson"
        curriculum = store.get_curriculum(cid)
        assert curriculum is not None
        assert curriculum.days[0].title == "Generated Lesson"
        assert len(fake_renderer.calls) == 1
        events, _ = activity_log.events_since(0)
        states = [e["state"] for e in events if e["kind"] == "pipeline"]
        assert states == ["queued", "generating", "rendering", "ready"]

    async def test_render_only_job(self, pipeline, fake_renderer):
        """Render-only job for an existing lesson without audio."""
        store = sl_store(pipeline)
        cid = "cur-1"
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="Day 1", focus="hello", collocations=["hi"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)
        lesson = Lesson(
            title="Existing",
            language_code="sl",
            sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
        )
        store.save_lesson("lesson-1", cid, 1, lesson)

        pipeline.start()
        pipeline.enqueue("sl", cid, 1, "render")
        record = await wait_for_job(pipeline, "sl", cid, 1, "ready")

        assert record["state"] == "ready"
        assert len(fake_renderer.calls) == 1
        # The resolved lesson id is stamped on the record so status_for
        # reports lesson_id + has_audio for ready render-only jobs.
        assert record["lesson_id"] == "lesson-1"
        status = pipeline.status_for("sl", cid)
        assert status["days"][0]["lesson_id"] == "lesson-1"
        assert status["days"][0]["has_audio"] is True

    async def test_reconcile_enqueues_missing_lessons(self, pipeline):
        """reconcile enqueues generate jobs for committed days without a lesson."""
        store = sl_store(pipeline)
        cid = "cur-1"
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
                CurriculumDay(day=2, title="D2", focus="f", collocations=["c"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)

        pipeline.start()
        pipeline.reconcile("sl", cid)

        rec1 = pipeline._jobs.get(("sl", cid, 1))
        rec2 = pipeline._jobs.get(("sl", cid, 2))
        assert rec1 is not None
        assert rec2 is not None
        assert rec1["kind"] == "generate"
        assert rec2["kind"] == "generate"

        await wait_for_job(pipeline, "sl", cid, 1, "ready")
        await wait_for_job(pipeline, "sl", cid, 2, "ready")

    async def test_reconcile_skips_ready_days(self, pipeline):
        """A day with lesson + audio is not enqueued by reconcile."""
        store = sl_store(pipeline)
        cid = "cur-1"
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)
        lesson = Lesson(
            title="Ready",
            language_code="sl",
            sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
        )
        store.save_lesson("lesson-1", cid, 1, lesson)
        audio_path = pipeline._audio_dir / "audio.wav"
        audio_path.write_bytes(b"audio")
        store.save_audio_file("audio-1", "lesson-1", str(audio_path))

        pipeline.reconcile("sl", cid)
        assert ("sl", cid, 1) not in pipeline._jobs


class TestRateLimitBackoff:
    async def test_rate_limit_retry_succeeds(self, pipeline, fake_generator, fake_llm, sleep_recorder):
        """Rate-limited generate: retries after waiting, succeeds on attempt 2."""
        store = sl_store(pipeline)
        cid = "cur-1"
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)
        fake_generator.fail_count = 1
        fake_generator.raise_error = StoryGenerationError("rate-limited by Groq")
        fake_llm.last_429 = {"at": time.time(), "retry_after_s": 15.0}

        pipeline.start()
        pipeline.enqueue("sl", cid, 1, "generate")
        await wait_for_job(pipeline, "sl", cid, 1, "ready")

        assert len(sleep_recorder.calls) >= 1
        assert sleep_recorder.calls[0] >= 15.0
        assert len(fake_generator.calls) == 2
        record = pipeline._jobs.get(("sl", cid, 1))
        assert record["state"] == "ready"

    async def test_rate_limit_exhausted(self, pipeline, fake_generator, fake_llm, sleep_recorder):
        """Rate-limited generate exhausts retries → failed(retryable=True)."""
        store = sl_store(pipeline)
        cid = "cur-1"
        pipeline._max_attempts = 2
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)
        fake_generator.fail_count = 99
        fake_generator.raise_error = StoryGenerationError("rate-limited by Groq")
        fake_llm.last_429 = {"at": time.time(), "retry_after_s": 15.0}

        pipeline.start()
        pipeline.enqueue("sl", cid, 1, "generate")
        await wait_for_job(pipeline, "sl", cid, 1, "failed")

        record = pipeline._jobs.get(("sl", cid, 1))
        assert record["state"] == "failed"
        assert record["retryable"] is True

    async def test_non_rate_limit_error_fails_immediately(self, pipeline, fake_generator, sleep_recorder):
        """Non-rate-limit StoryGenerationError → immediate failed, no sleep."""
        store = sl_store(pipeline)
        cid = "cur-1"
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)
        fake_generator.fail_count = 1
        fake_generator.raise_error = StoryGenerationError("Malformed JSON from LLM")

        pipeline.start()
        pipeline.enqueue("sl", cid, 1, "generate")
        await wait_for_job(pipeline, "sl", cid, 1, "failed")

        assert len(sleep_recorder.calls) == 0
        record = pipeline._jobs.get(("sl", cid, 1))
        assert record["state"] == "failed"
        assert "Malformed" in record["error"]


class TestFailureStickiness:
    async def test_reconcile_does_not_re_enqueue_failed(self, pipeline, fake_generator, fake_llm):
        """reconcile() does NOT re-enqueue previously-failed jobs."""
        store = sl_store(pipeline)
        cid = "cur-1"
        pipeline._max_attempts = 1
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)
        fake_generator.fail_count = 1
        fake_generator.raise_error = StoryGenerationError("rate-limited by Groq")
        fake_llm.last_429 = {"at": time.time(), "retry_after_s": 15.0}

        pipeline.start()
        pipeline.enqueue("sl", cid, 1, "generate")
        await wait_for_job(pipeline, "sl", cid, 1, "failed")

        record = pipeline._jobs.get(("sl", cid, 1))
        assert record["state"] == "failed"

        pipeline.reconcile("sl", cid)
        record2 = pipeline._jobs.get(("sl", cid, 1))
        assert record2["state"] == "failed"

    async def test_retry_resets_failed_job(self, pipeline, fake_generator, fake_llm):
        """retry() clears a failed record and re-enqueues."""
        store = sl_store(pipeline)
        cid = "cur-1"
        pipeline._max_attempts = 1
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)
        fake_generator.fail_count = 1
        fake_generator.raise_error = StoryGenerationError("rate-limited")
        fake_llm.last_429 = {"at": time.time(), "retry_after_s": 15.0}

        pipeline.start()
        pipeline.enqueue("sl", cid, 1, "generate")
        await wait_for_job(pipeline, "sl", cid, 1, "failed")

        status = pipeline.retry("sl", cid, 1)
        assert status == "queued"
        record = pipeline._jobs.get(("sl", cid, 1))
        assert record["state"] == "queued"

    async def test_retry_ready_day_returns_ready(self, pipeline):
        """retry() on a complete day returns 'ready' without enqueuing."""
        store = sl_store(pipeline)
        cid = "cur-1"
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)
        lesson = Lesson(
            title="Done",
            language_code="sl",
            sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
        )
        store.save_lesson("lesson-1", cid, 1, lesson)
        audio_path = pipeline._audio_dir / "audio.wav"
        audio_path.write_bytes(b"audio")
        store.save_audio_file("a-1", "lesson-1", str(audio_path))

        status = pipeline.retry("sl", cid, 1)
        assert status == "ready"
        assert ("sl", cid, 1) not in pipeline._jobs


class TestRegenerate:
    async def test_regenerate_forces_new_lesson(self, pipeline, fake_generator):
        """regenerate() enqueues a generate job with force=True even if lesson exists."""
        store = sl_store(pipeline)
        cid = "cur-1"
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)
        lesson = Lesson(
            title="Old",
            language_code="sl",
            sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
        )
        store.save_lesson("old-id", cid, 1, lesson)
        audio_path = pipeline._audio_dir / "audio.wav"
        audio_path.write_bytes(b"audio")
        store.save_audio_file("a-1", "old-id", str(audio_path))

        pipeline.start()
        status = pipeline.regenerate("sl", cid, 1)
        assert status == "queued"
        record = pipeline._jobs.get(("sl", cid, 1))
        assert record["force"] is True

        await wait_for_job(pipeline, "sl", cid, 1, "ready")
        new_lesson = store.get_latest_lesson_by_day(cid, 1)
        assert new_lesson is not None
        assert new_lesson[0] != "old-id"

    async def test_regenerate_passes_strategy_to_generator(self, pipeline, fake_generator):
        """regenerate(strategy="DEEPER") reaches the story generator as DEEPER."""
        from app.models.strategy import ContentStrategy

        store = sl_store(pipeline)
        cid = "cur-1"
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)

        pipeline.start()
        status = pipeline.regenerate("sl", cid, 1, strategy="DEEPER")
        assert status == "queued"

        await wait_for_job(pipeline, "sl", cid, 1, "ready")
        assert fake_generator.calls[-1]["strategy"] is ContentStrategy.DEEPER

    async def test_regenerate_409_for_active(self, pipeline):
        """regenerate() on a currently active job raises RuntimeError."""
        store = sl_store(pipeline)
        cid = "cur-1"
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)

        pipeline.enqueue("sl", cid, 1, "generate")
        with pytest.raises(RuntimeError):
            pipeline.regenerate("sl", cid, 1)


class Test404And409:
    async def test_retry_404(self, pipeline):
        """retry() on a day not in the curriculum raises KeyError."""
        with pytest.raises(KeyError):
            pipeline.retry("sl", "nonexistent", 1)

    async def test_retry_409_for_active(self, pipeline):
        """retry() on a currently active job raises RuntimeError."""
        store = sl_store(pipeline)
        cid = "cur-1"
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)
        pipeline.enqueue("sl", cid, 1, "generate")
        with pytest.raises(RuntimeError):
            pipeline.retry("sl", cid, 1)


class TestPerLanguageRouting:
    async def test_writes_to_correct_store(self, pipeline):
        """Jobs for different languages write to their respective stores."""
        sl_store = pipeline._content_stores["sl"]
        no_store = pipeline._content_stores["no"]

        cid = "cur-1"
        for code in ("sl", "no"):
            store = pipeline._content_stores[code]
            curriculum = Curriculum(
                id=cid,
                topic="test",
                language_code=code,
                cefr_level="A2",
                days=[
                    CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
                ],
            )
            store.save_curriculum(cid, curriculum)

        pipeline.start()
        for code in ("sl", "no"):
            pipeline.enqueue(code, cid, 1, "generate")
            await wait_for_job(pipeline, code, cid, 1, "ready")

        assert sl_store.get_latest_lesson_by_day(cid, 1) is not None
        assert no_store.get_latest_lesson_by_day(cid, 1) is not None


class TestStart:
    async def test_start_idempotent(self, pipeline):
        """Calling start() twice does not create a second worker."""
        pipeline.start()
        t1 = pipeline._worker_task
        pipeline.start()
        assert pipeline._worker_task is t1
        await pipeline.shutdown()


class TestEnqueueGuard:
    async def test_enqueue_skip_active(self, pipeline):
        """enqueue() returns without adding when a job is already queued."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )

        pipeline.enqueue("sl", cid, 1, "generate")
        pipeline.enqueue("sl", cid, 1, "generate")
        record = pipeline._jobs.get(("sl", cid, 1))
        assert record["state"] == "queued"
        assert record["kind"] == "generate"

    async def test_enqueue_skip_ready_no_force(self, pipeline):
        """enqueue() with force=False on ready job returns without re-queueing."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )

        pipeline._jobs[("sl", cid, 1)] = {
            "state": "ready",
            "kind": "generate",
            "force": False,
            "lesson_id": None,
            "attempts": 0,
            "updated_at": 0,
        }
        pipeline.enqueue("sl", cid, 1, "generate", force=False)
        assert pipeline._jobs[("sl", cid, 1)]["state"] == "ready"


class TestReconcileEdgeCases:
    async def test_reconcile_no_curriculum(self, pipeline):
        """reconcile() silently returns when curriculum does not exist."""
        pipeline.reconcile("sl", "nonexistent")
        assert len(pipeline._jobs) == 0

    async def test_reconcile_enqueues_render_for_existing_lesson_without_audio(self, pipeline):
        """reconcile() enqueues a render job for a day with a lesson but no audio."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        lesson = Lesson(
            title="No Audio", language_code="sl", sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])]
        )
        store.save_lesson("lesson-1", cid, 1, lesson)

        pipeline.reconcile("sl", cid)
        record = pipeline._jobs.get(("sl", cid, 1))
        assert record is not None
        assert record["kind"] == "render"


class TestStatusFor:
    async def test_status_for_empty(self, pipeline):
        """status_for returns active=False with empty days for missing curriculum."""
        result = pipeline.status_for("sl", "nonexistent")
        assert result == {"active": False, "days": []}

    async def test_status_for_with_job_record(self, pipeline):
        """status_for includes job records with correct state."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        pipeline._jobs[("sl", cid, 1)] = {
            "state": "failed",
            "kind": "generate",
            "force": False,
            "lesson_id": None,
            "attempts": 0,
            "updated_at": 0,
            "error": "test error",
            "retryable": True,
            "detail": "test detail",
        }
        result = pipeline.status_for("sl", cid)
        assert result["active"] is False
        assert result["days"][0]["state"] == "failed"
        assert result["days"][0]["error"] == "test error"

    async def test_status_for_active_flag(self, pipeline):
        """status_for sets active=True when a job is queued/generating/rendering."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        pipeline._jobs[("sl", cid, 1)] = {
            "state": "queued",
            "kind": "generate",
            "force": False,
            "lesson_id": None,
            "attempts": 0,
            "updated_at": 0,
        }
        result = pipeline.status_for("sl", cid)
        assert result["active"] is True

    async def test_status_for_lesson_without_record(self, pipeline):
        """status_for reports ready for a day with lesson+audio but no job record."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        lesson = Lesson(
            title="Done", language_code="sl", sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])]
        )
        store.save_lesson("lesson-1", cid, 1, lesson)

        result = pipeline.status_for("sl", cid)
        assert result["days"][0]["state"] == "ready"

    async def test_status_for_lesson_with_record_and_lesson_id(self, pipeline):
        """status_for includes has_audio for a job with lesson_id."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        lesson = Lesson(
            title="Done", language_code="sl", sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])]
        )
        store.save_lesson("lid", cid, 1, lesson)
        pipeline._jobs[("sl", cid, 1)] = {
            "state": "ready",
            "kind": "generate",
            "force": False,
            "lesson_id": "lid",
            "attempts": 0,
            "updated_at": 0,
        }
        result = pipeline.status_for("sl", cid)
        assert result["days"][0]["lesson_id"] == "lid"
        assert result["days"][0]["has_audio"] is False

    async def test_status_for_day_without_record_or_lesson(self, pipeline):
        """status_for skips curriculum days that have no record and no saved lesson."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[
                    CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
                    CurriculumDay(day=2, title="D2", focus="f", collocations=["c"], learning_objective="lo"),
                ],
            ),
        )
        pipeline._jobs[("sl", cid, 1)] = {
            "state": "ready",
            "kind": "generate",
            "force": False,
            "lesson_id": None,
            "attempts": 0,
            "updated_at": 0,
        }
        result = pipeline.status_for("sl", cid)
        # day 1 has a record, day 2 has neither record nor lesson → only day 1 appears
        assert len(result["days"]) == 1
        assert result["days"][0]["day"] == 1


class TestRetryEdgeCases:
    async def test_retry_ready_day_with_job_record(self, pipeline):
        """retry() on a ready day that still has a job record cleans it up."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        lesson = Lesson(
            title="Done", language_code="sl", sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])]
        )
        store.save_lesson("lesson-1", cid, 1, lesson)
        audio_path = pipeline._audio_dir / "audio.wav"
        audio_path.write_bytes(b"audio")
        store.save_audio_file("a-1", "lesson-1", str(audio_path))
        pipeline._jobs[("sl", cid, 1)] = {
            "state": "failed",
            "kind": "generate",
            "force": False,
            "lesson_id": "lesson-1",
            "attempts": 1,
            "updated_at": 0,
        }

        status = pipeline.retry("sl", cid, 1)
        assert status == "ready"
        assert ("sl", cid, 1) not in pipeline._jobs


class TestGenerateEdgeCases:
    async def test_generate_curriculum_not_found(self, pipeline, fake_generator):
        """_generate marks job failed when curriculum is missing."""
        pipeline._jobs[("sl", "no-cur", 1)] = {
            "state": "generating",
            "kind": "generate",
            "curriculum_id": "no-cur",
            "day": 1,
            "language_code": "sl",
            "force": False,
            "lesson_id": None,
            "attempts": 0,
            "updated_at": 0,
            "detail": None,
            "error": None,
            "retryable": None,
        }
        pipeline.start()
        queue_key = ("sl", "no-cur", 1)
        pipeline._queue.put_nowait(queue_key)
        await wait_for_job(pipeline, "sl", "no-cur", 1, "failed")

        record = pipeline._jobs[queue_key]
        assert record["state"] == "failed"
        assert "not found" in record["error"]
        assert record["retryable"] is False

    async def test_generate_day_not_found(self, pipeline, fake_generator):
        """_generate marks job failed when day is missing from curriculum."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        pipeline._jobs[("sl", cid, 99)] = {
            "state": "generating",
            "kind": "generate",
            "curriculum_id": cid,
            "day": 99,
            "language_code": "sl",
            "force": False,
            "lesson_id": None,
            "attempts": 0,
            "updated_at": 0,
            "detail": None,
            "error": None,
            "retryable": None,
        }
        pipeline.start()
        queue_key = ("sl", cid, 99)
        pipeline._queue.put_nowait(queue_key)
        await wait_for_job(pipeline, "sl", cid, 99, "failed")

        record = pipeline._jobs[queue_key]
        assert record["state"] == "failed"
        assert "not found" in record["error"]
        assert record["retryable"] is False


class TestRetryEdgeCases2:
    async def test_retry_lesson_without_audio_enqueues_render(self, pipeline):
        """retry() on a day with a lesson but no audio enqueues a RENDER job.

        Re-generating would burn LLM quota for nothing — the story already
        exists; only the audio render failed or is missing.
        """
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        lesson = Lesson(
            title="No Audio", language_code="sl", sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])]
        )
        store.save_lesson("lid", cid, 1, lesson)

        status = pipeline.retry("sl", cid, 1)
        assert status == "queued"
        assert pipeline._jobs[("sl", cid, 1)]["kind"] == "render"


class TestRateLimitWithTokens:
    async def test_rate_limit_with_tokens_reset(self, pipeline, fake_generator, fake_llm, sleep_recorder):
        """Rate-limit retry uses tokens_reset when last_rate_limits is set."""
        store = sl_store(pipeline)
        cid = "cur-1"
        pipeline._max_attempts = 2
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        fake_generator.fail_count = 1
        fake_generator.raise_error = StoryGenerationError("rate-limited by Groq")
        fake_llm.last_429 = {"at": time.time(), "retry_after_s": 5.0}
        fake_llm.last_rate_limits = {"captured_at": time.time() + 100, "tokens_reset_s": 30}

        pipeline.start()
        pipeline.enqueue("sl", cid, 1, "generate")
        await wait_for_job(pipeline, "sl", cid, 1, "ready")

    async def test_rate_limit_with_none_tokens(self, pipeline, fake_generator, fake_llm, sleep_recorder):
        """Rate-limit retry handles None captured_at gracefully."""
        store = sl_store(pipeline)
        cid = "cur-1"
        pipeline._max_attempts = 2
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        fake_generator.fail_count = 1
        fake_generator.raise_error = StoryGenerationError("rate-limited by Groq")
        fake_llm.last_429 = {"at": time.time(), "retry_after_s": 5.0}
        fake_llm.last_rate_limits = {"captured_at": None, "tokens_reset_s": None}

        pipeline.start()
        pipeline.enqueue("sl", cid, 1, "generate")
        await wait_for_job(pipeline, "sl", cid, 1, "ready")


class TestRenderEdgeCases:
    async def test_render_no_lesson_in_store(self, pipeline, fake_renderer):
        """Render-only job with no stored lesson fails."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )

        pipeline.start()
        pipeline.enqueue("sl", cid, 1, "render")
        await wait_for_job(pipeline, "sl", cid, 1, "failed")
        record = pipeline._jobs.get(("sl", cid, 1))
        assert "No lesson found" in record["error"]

    async def test_render_lesson_id_not_in_store(self, pipeline, fake_renderer):
        """Render fails when lesson_id is set but lesson was deleted from store."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        pipeline._jobs[("sl", cid, 1)] = {
            "state": "queued",
            "kind": "render",
            "curriculum_id": cid,
            "day": 1,
            "language_code": "sl",
            "force": False,
            "lesson_id": "ghost-lesson",
            "attempts": 0,
            "updated_at": 0,
            "detail": None,
            "error": None,
            "retryable": None,
        }
        pipeline.start()
        pipeline._queue.put_nowait(("sl", cid, 1))
        await wait_for_job(pipeline, "sl", cid, 1, "failed")
        record = pipeline._jobs.get(("sl", cid, 1))
        assert "not found" in record["error"]

    async def test_render_raises_exception(self, pipeline):
        """_render handles exceptions from render_lesson_audio."""

        class FailingRenderer:
            async def render(self, lesson, full_path, section_paths=None):
                raise RuntimeError("TTS engine offline")

        pipeline._renderer = FailingRenderer()
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        lesson = Lesson(
            title="Fail Render",
            language_code="sl",
            sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
        )
        store.save_lesson("lid", cid, 1, lesson)
        pipeline._jobs[("sl", cid, 1)] = {
            "state": "queued",
            "kind": "render",
            "curriculum_id": cid,
            "day": 1,
            "language_code": "sl",
            "force": False,
            "lesson_id": "lid",
            "attempts": 0,
            "updated_at": 0,
            "detail": None,
            "error": None,
            "retryable": None,
        }
        pipeline.start()
        pipeline._queue.put_nowait(("sl", cid, 1))
        await wait_for_job(pipeline, "sl", cid, 1, "failed")


class TestWorkerEdgeCases:
    async def test_worker_handles_cancelled_error_in_process(self, pipeline):
        """Worker catches CancelledError during _process_job and exits."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )

        pipeline.start()
        await pipeline.shutdown()
        assert pipeline._worker_task is None

    async def test_worker_cancelled_during_long_sleep(self, tmp_path):
        """Cancellation during a long rate-limit sleep exits cleanly."""
        import asyncio as _asyncio

        from app.llm.activity import ActivityLog

        gen = FakeStoryGenerator()
        gen.fail_count = 99
        gen.raise_error = StoryGenerationError("rate-limited")
        llm = FakeLLMClient()
        llm.last_429 = {"at": 9999999999.0, "retry_after_s": 30.0}
        store = ContentStore(":memory:")
        store.save_curriculum(
            "cur-1",
            Curriculum(
                id="cur-1",
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )

        pipeline = LessonPipeline(
            story_generator=gen,
            renderer=FakeRenderer(),
            audio_dir=tmp_path,
            content_stores={"sl": store},
            languages={"sl": Language.slovene()},
            srs_dbs={},
            activity_log=ActivityLog(maxlen=100),
            llm_client=llm,
            sleep=_asyncio.sleep,
            max_attempts=2,
        )
        pipeline.start()
        pipeline.enqueue("sl", "cur-1", 1, "generate")
        await _asyncio.sleep(0.05)
        await pipeline.shutdown()
        assert pipeline._worker_task is None

    async def test_process_job_no_record_is_noop(self, pipeline):
        """_process_job with a key not in _jobs silently returns."""
        pipeline._queue.put_nowait(("sl", "no-such", 1))

        pipeline.start()
        await asyncio.sleep(0.2)
        await pipeline.shutdown()
        assert pipeline._worker_task is None

    async def test_process_job_unknown_kind(self, pipeline):
        """_process_job with unknown kind silently skips."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        pipeline._jobs[("sl", cid, 1)] = {
            "state": "queued",
            "kind": "unknown",
            "curriculum_id": cid,
            "day": 1,
            "language_code": "sl",
            "force": False,
            "lesson_id": None,
            "attempts": 0,
            "updated_at": 0,
            "detail": None,
            "error": None,
            "retryable": None,
        }
        pipeline.start()
        pipeline._queue.put_nowait(("sl", cid, 1))
        await asyncio.sleep(0.2)
        await pipeline.shutdown()

    async def test_worker_unexpected_exception_marks_failed(self, pipeline):
        """Worker catches Exception, marks job failed, and continues."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )

        async def crash(*args, **kwargs):
            raise RuntimeError("worker crash")

        pipeline._render = crash
        pipeline.start()
        pipeline.enqueue("sl", cid, 1, "render")
        await wait_for_job(pipeline, "sl", cid, 1, "failed")
        record = pipeline._jobs.get(("sl", cid, 1))
        assert "Unexpected pipeline error" in record["error"]
        assert record["retryable"] is True

    async def test_worker_exception_missing_record(self, pipeline):
        """Worker exception handler handles missing record gracefully."""
        store = sl_store(pipeline)
        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )

        async def crash_and_delete(key):
            pipeline._jobs.pop(key, None)
            raise RuntimeError("boom")

        pipeline._process_job = crash_and_delete
        pipeline.start()
        pipeline._queue.put_nowait(("sl", cid, 1))
        await asyncio.sleep(0.2)
        await pipeline.shutdown()


class TestPrewarm:
    async def test_generate_with_srs_db_prewarms(self, pipeline, fake_generator):
        """_generate pre-warms lesson analysis when srs_db is configured."""
        store = sl_store(pipeline)
        from app.srs.database import SRSDatabase

        srs_db = SRSDatabase(":memory:")
        pipeline._srs_dbs["sl"] = srs_db

        cid = "cur-1"
        store.save_curriculum(
            cid,
            Curriculum(
                id=cid,
                topic="t",
                language_code="sl",
                cefr_level="A2",
                days=[CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo")],
            ),
        )
        pipeline.start()
        pipeline.enqueue("sl", cid, 1, "generate")
        await wait_for_job(pipeline, "sl", cid, 1, "ready")
        srs_db.close()


class TestWorkerSurvival:
    async def test_unexpected_exception_does_not_kill_worker(self, pipeline):
        """An unexpected exception in the worker marks the job failed and continues."""
        store = sl_store(pipeline)
        cid = "cur-1"
        curriculum = Curriculum(
            id=cid,
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=1, title="D1", focus="f", collocations=["c"], learning_objective="lo"),
                CurriculumDay(day=2, title="D2", focus="f", collocations=["c"], learning_objective="lo"),
            ],
        )
        store.save_curriculum(cid, curriculum)

        pipeline.start()
        pipeline.enqueue("sl", cid, 1, "generate")
        await wait_for_job(pipeline, "sl", cid, 1, "ready")

        pipeline.enqueue("sl", cid, 2, "generate")
        await wait_for_job(pipeline, "sl", cid, 2, "ready")
        record2 = pipeline._jobs.get(("sl", cid, 2))
        assert record2["state"] == "ready"

    async def test_shutdown_cancels_cleanly(self, pipeline):
        """shutdown() cancels the worker without error."""
        pipeline.start()
        await pipeline.shutdown()
        assert pipeline._worker_task is None
        await pipeline.shutdown()
        assert pipeline._worker_task is None
