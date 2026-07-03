"""Tests for the interactive curriculum-planner endpoints (stub planner, no LLM)."""

from dataclasses import asdict, dataclass, field

import pytest
from httpx import ASGITransport, AsyncClient

from app.generation.planner import PlannerError, PlannerTurn
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language
from app.storage.store import ContentStore


@pytest.fixture(autouse=True)
def _clean_app_state():
    yield
    for attr in ("content_store", "srs_db"):
        resource = getattr(app.state, attr, None)
        if resource is not None:
            resource.close()
    for attr in ("content_store", "language", "srs_db", "curriculum_planner"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


@dataclass
class StubPlanner:
    """Canned-turn planner stub — NOT a mock/patch, passes the boundary check."""

    result: PlannerTurn | None = None
    error: str | None = None
    calls: list[dict] = field(default_factory=list)

    async def turn(self, *, curriculum, user_message, batch_size, learner_snapshot, language):
        self.calls.append(
            {
                "curriculum": curriculum,
                "user_message": user_message,
                "batch_size": batch_size,
                "learner_snapshot": learner_snapshot,
                "language": language,
            }
        )
        if self.error is not None:
            raise PlannerError(self.error)
        return self.result


def _day(day: int) -> CurriculumDay:
    return CurriculumDay(
        day=day,
        title=f"Day {day}",
        focus=f"Focus {day}",
        collocations=["dober dan"],
        learning_objective=f"Objective {day}",
    )


def _setup(curriculum: Curriculum | None = None, planner: StubPlanner | None = None) -> ContentStore:
    from app.srs.database import SRSDatabase

    store = ContentStore(":memory:")
    if curriculum is not None:
        store.save_curriculum(curriculum.id, curriculum)
    app.state.content_store = store
    app.state.language = Language.slovene()
    app.state.srs_db = SRSDatabase(":memory:")
    if planner is not None:
        app.state.curriculum_planner = planner
    return store


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _planned_curriculum(**planner_state) -> Curriculum:
    state = {"chat": [], "proposed": None, "feedback": []}
    state.update(planner_state)
    return Curriculum(
        id="trip",
        topic="Visiting Ljubljana",
        language_code="sl",
        cefr_level="A2",
        metadata={"planner": state},
    )


class TestStartPlan:
    async def test_start_plan_creates_empty_curriculum(self):
        store = _setup()
        async with _client() as client:
            response = await client.post("/api/curriculum/plan", json={"topic": "Visiting Ljubljana"})
        assert response.status_code == 201
        data = response.json()
        assert data["id"].startswith("visiting-ljubljana-")
        assert data["topic"] == "Visiting Ljubljana"
        assert data["cefr_level"] == "A2"
        assert data["days"] == 0

        saved = store.get_curriculum(data["id"])
        assert saved is not None
        assert saved.days == []
        assert saved.metadata["planner"] == {"chat": [], "proposed": None, "feedback": []}

    async def test_start_plan_custom_cefr(self):
        store = _setup()
        async with _client() as client:
            response = await client.post("/api/curriculum/plan", json={"topic": "market", "cefr_level": "B1"})
        assert response.status_code == 201
        assert store.get_curriculum(response.json()["id"]).cefr_level == "B1"

    async def test_start_plan_missing_topic_422(self):
        _setup()
        async with _client() as client:
            response = await client.post("/api/curriculum/plan", json={})
        assert response.status_code == 422


class TestPlanTurn:
    @pytest.mark.parametrize("bad_size", [0, -1, 15])
    async def test_turn_batch_size_out_of_bounds_422(self, bad_size):
        """batch_size mirrors the frontend clamp (1..14); the API must reject
        what the UI can never send (0 days, or 500 days into a 5500-token budget)."""
        _setup(
            curriculum=_planned_curriculum(), planner=StubPlanner(result=PlannerTurn(reply="hi", proposed_days=None))
        )
        async with _client() as client:
            response = await client.post(
                "/api/curriculum/trip/plan/turn", json={"message": "hi", "batch_size": bad_size}
            )
        assert response.status_code == 422

    async def test_turn_batch_size_bounds_accepted(self):
        planner = StubPlanner(result=PlannerTurn(reply="hi", proposed_days=None))
        _setup(curriculum=_planned_curriculum(), planner=planner)
        async with _client() as client:
            for ok_size in (1, 14):
                response = await client.post(
                    "/api/curriculum/trip/plan/turn", json={"message": "hi", "batch_size": ok_size}
                )
                assert response.status_code == 200
        assert [c["batch_size"] for c in planner.calls] == [1, 14]

    async def test_turn_unknown_curriculum_404(self):
        _setup(planner=StubPlanner(result=PlannerTurn(reply="hi", proposed_days=None)))
        async with _client() as client:
            response = await client.post("/api/curriculum/no-such/plan/turn", json={"message": "hi"})
        assert response.status_code == 404

    async def test_pure_chat_turn_appends_chat_and_keeps_proposed(self):
        existing_proposed = {"start_day": 1, "days": [asdict(_day(1))]}
        curriculum = _planned_curriculum(proposed=existing_proposed)
        stub = StubPlanner(result=PlannerTurn(reply="Sounds good!", proposed_days=None))
        store = _setup(curriculum, stub)

        async with _client() as client:
            response = await client.post("/api/curriculum/trip/plan/turn", json={"message": "thoughts?"})

        assert response.status_code == 200
        data = response.json()
        assert data["reply"] == "Sounds good!"
        assert data["proposed"] == existing_proposed  # pure-chat turn leaves proposed alone

        saved = store.get_curriculum("trip").metadata["planner"]
        assert saved["chat"] == [
            {"role": "user", "content": "thoughts?"},
            {"role": "planner", "content": "Sounds good!"},
        ]
        assert saved["proposed"] == existing_proposed

    async def test_proposing_turn_replaces_proposed(self):
        stale = {"start_day": 1, "days": [asdict(_day(1))]}
        curriculum = _planned_curriculum(proposed=stale)
        proposed_days = [_day(1), _day(2)]
        stub = StubPlanner(result=PlannerTurn(reply="Here you go", proposed_days=proposed_days))
        store = _setup(curriculum, stub)

        async with _client() as client:
            response = await client.post(
                "/api/curriculum/trip/plan/turn", json={"message": "plan 2 days", "batch_size": 2}
            )

        assert response.status_code == 200
        expected = {"start_day": 1, "days": [asdict(d) for d in proposed_days]}
        assert response.json()["proposed"] == expected
        assert store.get_curriculum("trip").metadata["planner"]["proposed"] == expected

    async def test_turn_passes_snapshot_batch_size_and_language(self):
        stub = StubPlanner(result=PlannerTurn(reply="ok", proposed_days=None))
        _setup(_planned_curriculum(), stub)

        async with _client() as client:
            await client.post("/api/curriculum/trip/plan/turn", json={"message": "hi", "batch_size": 4})

        (call,) = stub.calls
        assert call["batch_size"] == 4
        assert call["user_message"] == "hi"
        # Empty SRS DB → the beginner snapshot string, built server-side.
        assert call["learner_snapshot"] == "(no tracked vocabulary yet — assume a beginner at the stated CEFR level)"
        assert call["language"].code == "sl"
        assert call["curriculum"].id == "trip"

    async def test_batch_size_defaults_to_5(self):
        stub = StubPlanner(result=PlannerTurn(reply="ok", proposed_days=None))
        _setup(_planned_curriculum(), stub)
        async with _client() as client:
            await client.post("/api/curriculum/trip/plan/turn", json={"message": "hi"})
        assert stub.calls[0]["batch_size"] == 5

    async def test_planner_error_502_and_nothing_persisted(self):
        curriculum = _planned_curriculum(chat=[{"role": "user", "content": "old"}])
        stub = StubPlanner(error="Expected 3 days, got 1")
        store = _setup(curriculum, stub)

        async with _client() as client:
            response = await client.post("/api/curriculum/trip/plan/turn", json={"message": "plan"})

        assert response.status_code == 502
        assert "Expected 3 days" in response.json()["detail"]
        saved = store.get_curriculum("trip").metadata["planner"]
        assert saved["chat"] == [{"role": "user", "content": "old"}]  # failed turn not persisted
        assert saved["proposed"] is None

    async def test_turn_works_for_pre_planner_curriculum(self):
        """A curriculum without metadata['planner'] gets defaults, not a crash."""
        curriculum = Curriculum(id="old", topic="t", language_code="sl", cefr_level="A2")
        stub = StubPlanner(result=PlannerTurn(reply="hello", proposed_days=None))
        store = _setup(curriculum, stub)

        async with _client() as client:
            response = await client.post("/api/curriculum/old/plan/turn", json={"message": "hi"})

        assert response.status_code == 200
        assert store.get_curriculum("old").metadata["planner"]["chat"][0]["content"] == "hi"


class TestPlanCommit:
    async def test_commit_unknown_curriculum_404(self):
        _setup()
        async with _client() as client:
            response = await client.post("/api/curriculum/no-such/plan/commit", json={})
        assert response.status_code == 404

    async def test_commit_without_proposal_409(self):
        _setup(_planned_curriculum())
        async with _client() as client:
            response = await client.post("/api/curriculum/trip/plan/commit", json={})
        assert response.status_code == 409

    async def test_commit_stale_proposal_409_and_days_unchanged(self):
        """A proposal numbered against a day list that has since changed (e.g. a
        plan re-import removed days) must 409, not append colliding day numbers."""
        stale = {"start_day": 3, "days": [asdict(_day(3))]}  # curriculum only has day 1
        curriculum = _planned_curriculum(proposed=stale)
        curriculum.days.append(_day(1))
        store = _setup(curriculum)

        async with _client() as client:
            response = await client.post("/api/curriculum/trip/plan/commit", json={})

        assert response.status_code == 409
        assert "stale" in response.json()["detail"].lower()
        saved = store.get_curriculum("trip")
        assert [d.day for d in saved.days] == [1]
        # The stale proposal is left in place — the user re-proposes via chat.
        assert saved.metadata["planner"]["proposed"] == stale

    async def test_commit_appends_days_clears_proposed_adds_event(self):
        proposed = {"start_day": 3, "days": [asdict(_day(3)), asdict(_day(4))]}
        curriculum = _planned_curriculum(proposed=proposed)
        curriculum.days.extend([_day(1), _day(2)])
        store = _setup(curriculum)

        async with _client() as client:
            response = await client.post("/api/curriculum/trip/plan/commit", json={})

        assert response.status_code == 200
        assert response.json() == {"id": "trip", "days": 4}

        saved = store.get_curriculum("trip")
        assert [d.day for d in saved.days] == [1, 2, 3, 4]
        assert saved.days[2].title == "Day 3"
        planner_state = saved.metadata["planner"]
        assert planner_state["proposed"] is None
        assert planner_state["chat"] == [{"role": "event", "content": "Committed days 3-4."}]

    async def test_commit_single_day_event_message(self):
        proposed = {"start_day": 1, "days": [asdict(_day(1))]}
        store = _setup(_planned_curriculum(proposed=proposed))

        async with _client() as client:
            await client.post("/api/curriculum/trip/plan/commit", json={})

        chat = store.get_curriculum("trip").metadata["planner"]["chat"]
        assert chat == [{"role": "event", "content": "Committed day 1."}]


class TestPlanFeedback:
    async def test_feedback_unknown_curriculum_404(self):
        _setup()
        async with _client() as client:
            response = await client.post("/api/curriculum/no-such/plan/feedback", json={"day": 1, "note": "x"})
        assert response.status_code == 404

    async def test_feedback_unknown_day_404(self):
        curriculum = _planned_curriculum()
        curriculum.days.append(_day(1))
        _setup(curriculum)
        async with _client() as client:
            response = await client.post("/api/curriculum/trip/plan/feedback", json={"day": 2, "note": "x"})
        assert response.status_code == 404
        assert "day" in response.json()["detail"].lower()

    async def test_feedback_appended_and_persisted(self):
        curriculum = _planned_curriculum(feedback=[{"day": 1, "note": "old note"}])
        curriculum.days.extend([_day(1), _day(2)])
        store = _setup(curriculum)

        async with _client() as client:
            response = await client.post("/api/curriculum/trip/plan/feedback", json={"day": 2, "note": "too fast"})

        assert response.status_code == 200
        expected = [{"day": 1, "note": "old note"}, {"day": 2, "note": "too fast"}]
        assert response.json() == {"feedback": expected}
        assert store.get_curriculum("trip").metadata["planner"]["feedback"] == expected
