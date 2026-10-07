"""A grammar day goes through the lesson pipeline without the story generator (tunatale-ve4p.5).

Every route into a day's lesson ends in ``LessonPipeline._generate``: the first
generate, a retry, a regenerate. For a grammar day that step builds the affix
drill from the learner's cards and lessons. If it called the story generator
instead, regenerating a drill would write an LLM story over it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from app.generation.pipeline import LessonPipeline
from app.languages import get_language
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.storage.store import ContentStore
from tests.test_pipeline import FakeLLMClient, FakeRenderer, FakeStoryGenerator, RecorderSleep, wait_for_job

_CID = "funeral"


def _understand(db, root: str) -> None:
    db.add_collocation(
        SyntacticUnit(text=root, translation=root, word_count=1, difficulty=1, source="test"), language_code="ceb"
    )
    db.update_direction(
        db.get_collocation(root).guid,
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            stability=5.0,
            difficulty=4.0,
            reps=5,
            state=SRSState.REVIEW,
        ),
    )


def _grammar_day(pattern: str = "mo-mi") -> CurriculumDay:
    return CurriculumDay(
        day=1,
        title="Affix drill: mo- / mi-",
        focus="f",
        collocations=["moinom"],
        learning_objective="o",
        kind="grammar",
        pattern=pattern,
    )


@pytest.fixture
def generator():
    return FakeStoryGenerator()


@pytest.fixture
def renderer():
    return FakeRenderer()


@pytest.fixture
def pipeline(generator, renderer, srs_db, activity_log, tmp_path):
    store = ContentStore(":memory:")
    return LessonPipeline(
        story_generator=generator,
        renderer=renderer,
        audio_dir=tmp_path,
        content_stores={"ceb": store},
        languages={"ceb": get_language("ceb")},
        srs_dbs={"ceb": srs_db},
        activity_log=activity_log,
        llm_client=FakeLLMClient(),
        sleep=RecorderSleep(),
    )


@pytest.fixture
def activity_log():
    from app.llm.activity import ActivityLog

    return ActivityLog(maxlen=100)


def _save(pipeline, day: CurriculumDay) -> ContentStore:
    store = pipeline._content_stores["ceb"]
    store.save_curriculum(_CID, Curriculum(id=_CID, topic="t", language_code="ceb", cefr_level="A1", days=[day]))
    return store


async def test_a_grammar_day_is_built_and_rendered_without_the_story_generator(
    pipeline, generator, renderer, srs_db, activity_log
):
    for root in ("inom", "adto", "anhi"):
        _understand(srs_db, root)
    store = _save(pipeline, _grammar_day())

    pipeline.start()
    pipeline.enqueue("ceb", _CID, 1, "generate", user_id=None)
    record = await wait_for_job(pipeline, "ceb", _CID, 1, "ready")

    assert generator.calls == []
    lesson_id, lesson = store.get_latest_lesson_by_day(_CID, 1)
    assert record["lesson_id"] == lesson_id
    assert lesson.kind == "grammar"
    assert [s.section_type for s in lesson.sections] == [SectionType.AFFIX_DRILL]
    assert lesson.generation_metadata["affix_drill"]["roots"] == ["inom", "adto", "anhi"]
    assert [call["lesson"].title for call in renderer.calls] == ["Affix drill: mo- / mi-"]
    events, _ = activity_log.events_since(0)
    assert [e["state"] for e in events if e["kind"] == "pipeline"] == ["queued", "generating", "rendering", "ready"]
    await pipeline.shutdown()


async def test_regenerating_a_grammar_day_rebuilds_the_drill_from_todays_cards(pipeline, generator, srs_db):
    """The plan is read again: a root understood since the first build joins the drill."""
    for root in ("inom", "adto"):
        _understand(srs_db, root)
    store = _save(pipeline, _grammar_day())
    pipeline.start()
    pipeline.enqueue("ceb", _CID, 1, "generate", user_id=None)
    await wait_for_job(pipeline, "ceb", _CID, 1, "ready")
    first_id, _ = store.get_latest_lesson_by_day(_CID, 1)

    _understand(srs_db, "anhi")
    assert pipeline.regenerate("ceb", _CID, 1, user_id=None) == "queued"
    await wait_for_job(pipeline, "ceb", _CID, 1, "ready")

    assert generator.calls == []
    second_id, lesson = store.get_latest_lesson_by_day(_CID, 1)
    assert second_id != first_id
    assert lesson.generation_metadata["affix_drill"]["roots"] == ["inom", "adto", "anhi"]
    assert store.list_audio_files_for_lesson(first_id) == []
    await pipeline.shutdown()


@pytest.mark.parametrize(
    ("day", "understood", "error"),
    [
        (_grammar_day(), ["inom"], "needs at least 2 roots you understand, and mo-mi has 1: inom"),
        (_grammar_day("um-in"), ["inom", "adto"], "Unknown affix pattern 'um-in' for ceb"),
    ],
)
async def test_a_grammar_day_that_cannot_be_built_fails_and_says_why(
    pipeline, generator, renderer, srs_db, day, understood, error
):
    """Not retryable: the same cards give the same answer, and no story may stand in."""
    for root in understood:
        _understand(srs_db, root)
    store = _save(pipeline, day)

    pipeline.start()
    pipeline.enqueue("ceb", _CID, 1, "generate", user_id=None)
    record = await wait_for_job(pipeline, "ceb", _CID, 1, "failed")

    assert error in record["error"]
    assert record["retryable"] is False
    assert generator.calls == [] and renderer.calls == []
    assert store.get_latest_lesson_by_day(_CID, 1) is None
    await pipeline.shutdown()


async def test_a_grammar_day_left_without_a_lesson_is_picked_up_even_in_manual_mode(pipeline, generator, srs_db):
    """Jobs live in memory, so a restart between "day appended" and "lesson built" loses the job.

    Manual mode stops reconcile from generating STORIES unasked. A grammar day
    was asked for and calls no model, so it is rebuilt; the story day beside it
    is still left alone.
    """
    for root in ("inom", "adto"):
        _understand(srs_db, root)
    store = pipeline._content_stores["ceb"]
    story_day = CurriculumDay(day=2, title="A story", focus="f", collocations=["c"], learning_objective="o")
    store.save_curriculum(
        _CID,
        Curriculum(
            id=_CID,
            topic="t",
            language_code="ceb",
            cefr_level="A1",
            days=[_grammar_day(), story_day],
            metadata={"generation_mode": "manual"},
        ),
    )

    pipeline.start()
    pipeline.reconcile("ceb", _CID, user_id=None)
    await wait_for_job(pipeline, "ceb", _CID, 1, "ready")

    assert store.get_latest_lesson_by_day(_CID, 1)[1].kind == "grammar"
    assert (None, "ceb", _CID, 2) not in pipeline._jobs
    assert generator.calls == []
    await pipeline.shutdown()


async def test_a_grammar_day_with_no_deck_to_read_fails(pipeline, generator, srs_db):
    _save(pipeline, _grammar_day())
    pipeline._srs_dbs.clear()

    pipeline.start()
    pipeline.enqueue("ceb", _CID, 1, "generate", user_id=None)
    record = await wait_for_job(pipeline, "ceb", _CID, 1, "failed")

    assert record["error"] == "A grammar lesson is built from your cards, and this deck has none to read"
    assert generator.calls == []
    await pipeline.shutdown()
