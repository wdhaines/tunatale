"""Creating a grammar lesson over HTTP (tunatale-ve4p.5).

``POST /api/curriculum/{id}/grammar-lessons`` appends a grammar day and hands
it to the pipeline; ``GET .../grammar-patterns`` says which patterns this
learner's cards can carry. The real pipeline runs here, with only the story
generator and the renderer replaced, so "a day was appended" and "a drill
lesson came out of it" are one assertion chain rather than two halves.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.models import CreateGrammarLessonResponse, GrammarPatternOption
from app.generation.pipeline import LessonPipeline
from app.languages import get_language
from app.llm.activity import ActivityLog
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests.test_pipeline import FakeRenderer, FakeStoryGenerator, wait_for_job

_CID = "funeral"
_CEB = {"X-TT-Language": "ceb"}
_NARRATOR = "en-US-DavisMultilingualNeural"


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


def _wake() -> Lesson:
    line = Phrase(text="Mouban ko ugma.", voice_id="ceb-PH-CharonGemini", language_code="ceb", role="male-1")
    english = Phrase(text="I will come along tomorrow.", voice_id=_NARRATOR, language_code="en", role="narrator")
    return Lesson(
        title="An Evening Wake",
        language_code="ceb",
        sections=[
            Section(section_type=SectionType.NATURAL_SPEED, phrases=[line]),
            Section(section_type=SectionType.TRANSLATED, phrases=[line, english]),
        ],
    )


@pytest.fixture
def deck(srs_db, tmp_path):
    """A Cebuano learner with one story day, three understood roots, and a live pipeline."""
    store = ContentStore(":memory:")
    store.save_curriculum(
        _CID,
        Curriculum(
            id=_CID,
            topic="Attending a funeral",
            language_code="ceb",
            cefr_level="A1",
            days=[CurriculumDay(day=1, title="A wake", focus="f", collocations=["haya"], learning_objective="o")],
        ),
    )
    store.save_lesson("wake-1", _CID, 1, _wake())
    for root in ("inom", "adto", "uban"):
        _understand(srs_db, root)
    generator = FakeStoryGenerator()
    pipeline = LessonPipeline(
        story_generator=generator,
        renderer=FakeRenderer(),
        audio_dir=tmp_path,
        content_stores={"ceb": store},
        languages={"ceb": get_language("ceb")},
        srs_dbs={"ceb": srs_db},
        activity_log=ActivityLog(maxlen=100),
        llm_client=None,
    )
    app.state.content_store = store
    app.state.srs_db = srs_db
    app.state.language = get_language("ceb")
    app.state.pipeline = pipeline
    return store, pipeline, generator


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_CEB)


async def test_creating_a_grammar_lesson_appends_a_day_and_the_pipeline_builds_the_drill(deck):
    store, pipeline, generator = deck
    pipeline.start()

    async with _client() as client:
        response = await client.post(f"/api/curriculum/{_CID}/grammar-lessons", json={"pattern": "mo-mi"})

    assert response.status_code == 201
    body = CreateGrammarLessonResponse.model_validate(response.json())
    assert body.model_dump() == {
        "day": 2,
        "position": 2,
        "title": "Affix drill: mo- / mi-",
        "pattern": "mo-mi",
        "roots": ["uban", "inom", "adto"],
        "new_roots": [],
        "lines": [{"text": "Mouban ko ugma.", "translation": "I will come along tomorrow."}],
    }
    day = store.get_curriculum(_CID).days[-1]
    assert (day.day, day.kind, day.pattern) == (2, "grammar", "mo-mi")

    await wait_for_job(pipeline, "ceb", _CID, 2, "ready")
    _, lesson = store.get_latest_lesson_by_day(_CID, 2)
    assert lesson.kind == "grammar"
    assert [s.section_type for s in lesson.sections] == [SectionType.AFFIX_DRILL]
    assert generator.calls == []
    await pipeline.shutdown()


async def test_the_lesson_is_served_with_the_table_of_what_it_drills(deck):
    """The page under the player has no transcript to show; this is what it shows instead."""
    store, pipeline, _ = deck
    pipeline.start()
    async with _client() as client:
        await client.post(f"/api/curriculum/{_CID}/grammar-lessons", json={"pattern": "mo-mi"})
        await wait_for_job(pipeline, "ceb", _CID, 2, "ready")
        lesson = (await client.get(f"/api/curriculum/{_CID}/days/2/lesson")).json()
        story = (await client.get(f"/api/curriculum/{_CID}/days/1/lesson")).json()
    await pipeline.shutdown()

    assert lesson["drill"] == {
        "pattern": "mo-mi",
        "title": "Affix drill: mo- / mi-",
        "roots": [
            {
                "root": "uban",
                "english": "accompany",
                "new": False,
                "forms": [
                    {"form": "mouban", "english": "will go along"},
                    {"form": "miuban", "english": "went along"},
                ],
            },
            {
                "root": "inom",
                "english": "drink",
                "new": False,
                "forms": [{"form": "moinom", "english": "will drink"}, {"form": "miinom", "english": "drank"}],
            },
            {
                "root": "adto",
                "english": "go",
                "new": False,
                "forms": [{"form": "moadto", "english": "will go"}, {"form": "miadto", "english": "went"}],
            },
        ],
    }
    assert "drill" not in story


async def test_the_new_day_is_served_with_its_kind_and_pattern(deck):
    async with _client() as client:
        await client.post(f"/api/curriculum/{_CID}/grammar-lessons", json={"pattern": "mo-mi"})
        curriculum = (await client.get(f"/api/curriculum/{_CID}")).json()
        source = (await client.get(f"/api/curriculum/{_CID}/source")).json()

    assert [(d["position"], d["kind"], d["pattern"]) for d in curriculum["days"]] == [
        (1, "thematic", ""),
        (2, "grammar", "mo-mi"),
    ]
    assert [(d["kind"], d["pattern"]) for d in source["days"]] == [("thematic", ""), ("grammar", "mo-mi")]


async def test_an_exported_plan_with_a_grammar_day_imports_again(deck):
    """The plan file is edited by hand and re-imported; a grammar day must survive the trip."""
    async with _client() as client:
        await client.post(f"/api/curriculum/{_CID}/grammar-lessons", json={"pattern": "mo-mi"})
        source = (await client.get(f"/api/curriculum/{_CID}/source")).json()
        response = await client.post("/api/curriculum/import", json=source)

    assert response.status_code == 201, response.text
    store, _, _ = deck
    assert [(d.kind, d.pattern) for d in store.get_curriculum(_CID).days] == [("thematic", ""), ("grammar", "mo-mi")]


async def test_a_grammar_lesson_is_created_even_when_stories_are_made_by_hand(deck):
    """Manual mode keeps the MODEL from being called unasked. A drill calls no model, and was asked for."""
    store, pipeline, _ = deck
    curriculum = store.get_curriculum(_CID)
    curriculum.metadata["generation_mode"] = "manual"
    store.save_curriculum(_CID, curriculum)
    pipeline.start()

    async with _client() as client:
        response = await client.post(f"/api/curriculum/{_CID}/grammar-lessons", json={"pattern": "mo-mi"})

    assert response.status_code == 201
    await wait_for_job(pipeline, "ceb", _CID, 2, "ready")
    await pipeline.shutdown()


async def test_with_no_pipeline_running_the_day_is_still_appended(deck):
    """The pipeline's reconcile builds it later; the request must not fail for want of a worker."""
    store, _, _ = deck
    del app.state.pipeline

    async with _client() as client:
        response = await client.post(f"/api/curriculum/{_CID}/grammar-lessons", json={"pattern": "mo-mi"})

    assert response.status_code == 201
    assert store.get_curriculum(_CID).days[-1].kind == "grammar"


async def test_too_few_understood_roots_is_refused_and_nothing_is_appended(deck):
    store, _, _ = deck

    async with _client() as client:
        response = await client.post(f"/api/curriculum/{_CID}/grammar-lessons", json={"pattern": "mag-nag"})

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "An affix drill needs at least 2 roots you understand, and mag-nag has 1: uban"
    )
    assert len(store.get_curriculum(_CID).days) == 1


async def test_an_unknown_pattern_is_refused(deck):
    async with _client() as client:
        response = await client.post(f"/api/curriculum/{_CID}/grammar-lessons", json={"pattern": "um-in"})

    assert response.status_code == 422
    assert response.json()["detail"] == "Unknown affix pattern 'um-in' for ceb"


async def test_an_unknown_curriculum_is_not_found(deck):
    async with _client() as client:
        created = await client.post("/api/curriculum/nope/grammar-lessons", json={"pattern": "mo-mi"})
        listed = await client.get("/api/curriculum/nope/grammar-patterns")

    assert (created.status_code, listed.status_code) == (404, 404)


async def test_the_patterns_say_which_this_learner_is_ready_for(deck):
    async with _client() as client:
        response = await client.get(f"/api/curriculum/{_CID}/grammar-patterns")

    assert response.status_code == 200
    options = [GrammarPatternOption.model_validate(o).model_dump() for o in response.json()]
    assert options == [
        {"key": "mo-mi", "title": "Affix drill: mo- / mi-", "roots": ["inom", "adto", "uban"], "ready": True},
        {"key": "mag-nag", "title": "Affix drill: mag- / nag-", "roots": ["uban"], "ready": False},
        {"key": "ma-na", "title": "Affix drill: ma- / na-", "roots": [], "ready": False},
    ]


async def test_a_language_with_no_patterns_offers_none(srs_db):
    store = ContentStore(":memory:")
    store.save_curriculum("c", Curriculum(id="c", topic="t", language_code="no", cefr_level="A1", days=[]))
    app.state.content_store = store
    app.state.srs_db = srs_db
    app.state.language = get_language("no")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers={"X-TT-Language": "no"}
    ) as client:
        response = await client.get("/api/curriculum/c/grammar-patterns")

    assert (response.status_code, response.json()) == (200, [])
