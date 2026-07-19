"""Tests for the interactive curriculum-planner endpoints (stub planner, no LLM)."""

from dataclasses import asdict, dataclass, field

import pytest
from httpx import ASGITransport, AsyncClient

from app.generation.planner import CurriculumPlanner, PlannerError, PlannerTurn
from app.generation.prompts import PLANNER_SYSTEM_PROMPT
from app.languages import get_language
from app.llm.activity import ActivityLog
from app.llm.client import LLMError
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.storage.store import ContentStore


@pytest.fixture(autouse=True)
def _clean_app_state():
    yield
    for attr in ("content_store", "srs_db"):
        resource = getattr(app.state, attr, None)
        if resource is not None:
            resource.close()
    for attr in ("content_store", "language", "srs_db", "curriculum_planner", "pipeline"):
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


@dataclass
class LLMErrorPlanner:
    """Planner stub that raises LLMError on turn() — boundary-safe (no patch)."""

    message: str = "Groq returned HTTP 413"
    calls: list[dict] = field(default_factory=list)

    async def turn(self, *, curriculum, user_message, batch_size, learner_snapshot, language):
        self.calls.append({"user_message": user_message})
        raise LLMError(self.message)


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
    app.state.language = get_language("sl")
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

    async def test_llm_error_502_and_nothing_persisted(self):
        """LLMError from planner.turn → 502, chat state unchanged."""
        curriculum = _planned_curriculum(chat=[{"role": "user", "content": "old"}])
        planner = LLMErrorPlanner(message="Groq returned HTTP 413")
        store = _setup(curriculum, planner)

        async with _client() as client:
            response = await client.post("/api/curriculum/trip/plan/turn", json={"message": "plan"})

        assert response.status_code == 502
        assert "413" in response.json()["detail"]
        saved = store.get_curriculum("trip").metadata["planner"]
        assert saved["chat"] == [{"role": "user", "content": "old"}]
        assert saved["proposed"] is None


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

    async def test_commit_appends_days_clears_proposed_adds_event(self, tmp_path):
        from app.generation.pipeline import LessonPipeline

        proposed = {"start_day": 3, "days": [asdict(_day(3)), asdict(_day(4))]}
        curriculum = _planned_curriculum(proposed=proposed)
        curriculum.days.extend([_day(1), _day(2)])
        store = _setup(curriculum)

        pipeline = LessonPipeline(
            story_generator=None,
            renderer=None,
            audio_dir=tmp_path,
            content_stores={"sl": store},
            languages={"sl": get_language("sl")},
            srs_dbs={},
            activity_log=ActivityLog(maxlen=100),
            llm_client=None,
        )
        app.state.pipeline = pipeline

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

        assert pipeline._jobs[("sl", "trip", 3)]["state"] == "queued"
        assert pipeline._jobs[("sl", "trip", 4)]["state"] == "queued"

    async def test_commit_single_day_event_message(self):
        proposed = {"start_day": 1, "days": [asdict(_day(1))]}
        store = _setup(_planned_curriculum(proposed=proposed))

        async with _client() as client:
            await client.post("/api/curriculum/trip/plan/commit", json={})

        chat = store.get_curriculum("trip").metadata["planner"]["chat"]
        assert chat == [{"role": "event", "content": "Committed day 1."}]


class TestPlanReset:
    async def test_reset_unknown_curriculum_404(self):
        _setup()
        async with _client() as client:
            response = await client.post("/api/curriculum/no-such/plan/reset", json={})
        assert response.status_code == 404

    async def test_reset_clears_chat_and_proposed_keeps_feedback_and_days(self):
        curriculum = _planned_curriculum(
            chat=[
                {"role": "user", "content": "plan 2 days"},
                {"role": "planner", "content": "Here you go"},
            ],
            proposed={"start_day": 1, "days": [asdict(_day(1))]},
            feedback=[{"day": 1, "note": "good"}],
        )
        curriculum.days.append(_day(1))
        store = _setup(curriculum)

        async with _client() as client:
            response = await client.post("/api/curriculum/trip/plan/reset", json={})

        assert response.status_code == 200
        assert response.json() == {"reply_count_cleared": 1}

        saved = store.get_curriculum("trip").metadata["planner"]
        assert saved["chat"] == []
        assert saved["proposed"] is None
        assert saved["feedback"] == [{"day": 1, "note": "good"}]
        assert [d.day for d in store.get_curriculum("trip").days] == [1]

    async def test_reset_idempotent_on_empty_state(self):
        store = _setup(_planned_curriculum())

        async with _client() as client:
            response = await client.post("/api/curriculum/trip/plan/reset", json={})

        assert response.status_code == 200
        assert response.json() == {"reply_count_cleared": 0}

        saved = store.get_curriculum("trip").metadata["planner"]
        assert saved["chat"] == []
        assert saved["proposed"] is None

    async def test_reset_counts_only_planner_replies(self):
        curriculum = _planned_curriculum(
            chat=[
                {"role": "user", "content": "hi"},
                {"role": "planner", "content": "hello"},
                {"role": "event", "content": "Committed day 1."},
                {"role": "user", "content": "more"},
                {"role": "planner", "content": "sure"},
            ],
            proposed={"start_day": 2, "days": [asdict(_day(2))]},
        )
        store = _setup(curriculum)

        async with _client() as client:
            response = await client.post("/api/curriculum/trip/plan/reset", json={})

        assert response.status_code == 200
        # 2 planner replies out of 5 entries
        assert response.json() == {"reply_count_cleared": 2}
        assert store.get_curriculum("trip").metadata["planner"]["chat"] == []


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


class TestDeleteCurriculum:
    async def test_delete_unknown_curriculum_404(self):
        _setup()
        async with _client() as client:
            response = await client.delete("/api/curriculum/no-such")
        assert response.status_code == 404

    async def test_delete_curriculum_removes_it_and_cascades(self):
        curriculum = _planned_curriculum()
        store = _setup(curriculum)
        from app.models.lesson import Lesson

        lesson = Lesson(
            title="Test Lesson",
            language_code="sl",
            generation_metadata={"source_prompt": "x", "model": "y"},
        )
        store.save_lesson("less_1", "trip", 1, lesson)
        store.save_audio_file("aud_1", "less_1", "/tmp/foo.mp3", section_index=0, section_type="dialogue")

        # Sanity: data exists before delete
        assert store.get_curriculum("trip") is not None
        assert store.get_lesson("less_1") is not None
        assert store.get_audio_file_row("aud_1") is not None

        async with _client() as client:
            response = await client.delete("/api/curriculum/trip")
        assert response.status_code == 200
        assert response.json() == {"deleted": "trip"}

        # Curriculum, lessons, and audio files are all gone
        assert store.get_curriculum("trip") is None
        assert store.get_lesson("less_1") is None
        assert store.get_audio_file_row("aud_1") is None


class TestDeleteDay:
    async def test_delete_day_removes_curriculum_day_and_lessons(self):
        """DELETE /{id}/days/{day} removes the day from curriculum.days and all its lessons."""
        curriculum = _planned_curriculum()
        curriculum.days.extend([_day(1), _day(2), _day(3)])
        store = _setup(curriculum)
        from app.models.lesson import Lesson

        lesson = Lesson(
            title="L1",
            language_code="sl",
            generation_metadata={"source_prompt": "x", "model": "y"},
        )
        store.save_lesson("less_1", "trip", 2, lesson)
        store.save_audio_file("aud_1", "less_1", "/tmp/foo.mp3", section_index=0, section_type="dialogue")

        async with _client() as client:
            response = await client.delete("/api/curriculum/trip/days/2")

        assert response.status_code == 200
        data = response.json()
        assert data["deleted_day"] == 2
        assert data["days"] == 2  # days 1 and 3 remain

        saved = store.get_curriculum("trip")
        assert [d.day for d in saved.days] == [1, 3]
        assert store.get_lesson("less_1") is None
        assert store.get_audio_file_row("aud_1") is None

    async def test_delete_day_appends_event_to_chat(self):
        """Day deletion appends a planner chat event."""
        curriculum = _planned_curriculum()
        curriculum.days.extend([_day(1), _day(2)])
        store = _setup(curriculum)

        async with _client() as client:
            await client.delete("/api/curriculum/trip/days/1")

        saved = store.get_curriculum("trip").metadata["planner"]
        assert len(saved["chat"]) == 1
        assert saved["chat"][0]["role"] == "event"
        assert "day 1" in saved["chat"][0]["content"].lower()

    async def test_delete_day_unknown_curriculum_404(self):
        _setup()
        async with _client() as client:
            response = await client.delete("/api/curriculum/no-such/days/1")
        assert response.status_code == 404

    async def test_delete_day_missing_day_404(self):
        curriculum = _planned_curriculum()
        curriculum.days.extend([_day(1)])
        _setup(curriculum)

        async with _client() as client:
            response = await client.delete("/api/curriculum/trip/days/99")
        assert response.status_code == 404

    async def test_delete_day_no_lessons_still_removes_day(self):
        """Day with no lessons still gets removed from curriculum.days."""
        curriculum = _planned_curriculum()
        curriculum.days.extend([_day(1), _day(2)])
        store = _setup(curriculum)

        async with _client() as client:
            response = await client.delete("/api/curriculum/trip/days/1")

        assert response.status_code == 200
        saved = store.get_curriculum("trip")
        assert [d.day for d in saved.days] == [2]


class _FakeLLMForPlanner:
    """Records complete() calls and returns canned responses (boundary-safe, no patch)."""

    def __init__(self, response: str = ""):
        self.calls: list[dict] = []
        self.response = response

    async def complete(self, prompt, *, system_prompt=None, temperature=0.7, max_tokens=5500):
        self.calls.append({"prompt": prompt, "system_prompt": system_prompt})
        return self.response


class TestPlanTurnManual:
    async def test_manual_turn_with_json_fence_persists_proposal(self):
        """pasted_response with prose + json fence of exactly batch_size days →
        200, proposal persisted, chat gains user+planner, LLM never called."""
        stub = StubPlanner()
        store = _setup(curriculum=_planned_curriculum(), planner=stub)

        paste = (
            "Here are the days.\n"
            "```json\n"
            '{"days": [{"title": "At the market", "focus": "Buying fruit", '
            '"collocations": ["jabolka, prosim"], "learning_objective": "Buy fruit"}]}\n'
            "```"
        )
        async with _client() as client:
            resp = await client.post(
                "/api/curriculum/trip/plan/turn",
                json={"message": "plan 1 day", "batch_size": 1, "pasted_response": paste},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["reply"].startswith("Here are the days.")
        assert data["proposed"] is not None
        assert len(data["proposed"]["days"]) == 1

        saved = store.get_curriculum("trip").metadata["planner"]
        assert len(saved["chat"]) == 2
        assert saved["chat"][0]["role"] == "user"
        assert saved["chat"][1]["role"] == "planner"
        assert saved["proposed"] == data["proposed"]
        assert stub.calls == []  # LLM never called

    async def test_manual_turn_wrong_day_count_502(self):
        """pasted_response with wrong number of days → 502, nothing persisted."""
        curriculum = _planned_curriculum(chat=[{"role": "user", "content": "old"}])
        stub = StubPlanner()
        store = _setup(curriculum=curriculum, planner=stub)

        paste = (
            "```json\n"
            '{"days": [{"title": "Only one day", "focus": "x", '
            '"collocations": ["dober dan"], "learning_objective": "x"}]}\n'
            "```"
        )
        async with _client() as client:
            resp = await client.post(
                "/api/curriculum/trip/plan/turn",
                json={"message": "plan 2 days", "batch_size": 2, "pasted_response": paste},
            )

        assert resp.status_code == 502
        assert "Expected 2 days" in resp.json()["detail"]

        saved = store.get_curriculum("trip").metadata["planner"]
        assert saved["chat"] == [{"role": "user", "content": "old"}]
        assert saved["proposed"] is None

    async def test_b5_pasted_reply_without_json_is_422_and_persists_nothing(self):
        """B5: pasted_response with no JSON fence → 422, chat state unchanged."""
        curriculum = _planned_curriculum(
            chat=[{"role": "user", "content": "old"}],
            proposed={"start_day": 1, "days": [asdict(_day(1))]},
        )
        stub = StubPlanner()
        store = _setup(curriculum=curriculum, planner=stub)

        async with _client() as client:
            resp = await client.post(
                "/api/curriculum/trip/plan/turn",
                json={"message": "thoughts?", "pasted_response": "Sounds good!"},
            )

        assert resp.status_code == 422
        assert "No plan JSON" in resp.json()["detail"]

        saved = store.get_curriculum("trip").metadata["planner"]
        assert saved["chat"] == [{"role": "user", "content": "old"}]
        assert saved["proposed"] == {"start_day": 1, "days": [asdict(_day(1))]}
        assert stub.calls == []  # LLM never called


class TestPlanTurnPrompt:
    async def test_prompt_returns_both_prompts(self):
        """POST /plan/turn/prompt → 200 with system_prompt and user_prompt."""
        _setup(curriculum=_planned_curriculum(), planner=StubPlanner())

        async with _client() as client:
            resp = await client.post(
                "/api/curriculum/trip/plan/turn/prompt",
                json={"message": "plan 2 days", "batch_size": 2},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["system_prompt"] == PLANNER_SYSTEM_PROMPT
        assert isinstance(data["user_prompt"], str)
        assert len(data["user_prompt"]) > 0
        # user_prompt contains the learner-snapshot marker
        assert "no tracked vocabulary" in data["user_prompt"]

    async def test_prompt_persists_nothing(self):
        """Follow-up GET shows chat/proposed unchanged after /plan/turn/prompt."""
        existing = _planned_curriculum(
            chat=[{"role": "user", "content": "old"}],
            proposed={"start_day": 1, "days": [asdict(_day(1))]},
        )
        store = _setup(curriculum=existing, planner=StubPlanner())

        async with _client() as client:
            await client.post(
                "/api/curriculum/trip/plan/turn/prompt",
                json={"message": "new idea", "batch_size": 2},
            )

        saved = store.get_curriculum("trip").metadata["planner"]
        assert saved["chat"] == [{"role": "user", "content": "old"}]
        assert saved["proposed"] == {"start_day": 1, "days": [asdict(_day(1))]}

    async def test_prompt_works_without_groq_key(self):
        """Endpoint works with no GROQ_API_KEY configured (just the stub planner)."""
        _setup(curriculum=_planned_curriculum(), planner=StubPlanner())

        async with _client() as client:
            resp = await client.post(
                "/api/curriculum/trip/plan/turn/prompt",
                json={"message": "test", "batch_size": 1},
            )

        assert resp.status_code == 200
        assert "system_prompt" in resp.json()
        assert "user_prompt" in resp.json()

    async def test_drift_guard_prompt_identical_to_llm_path(self):
        """user_prompt from /plan/turn/prompt is byte-identical to what
        CurriculumPlanner.turn() passes to llm.complete (same inputs)."""
        curriculum = _planned_curriculum()
        fake_llm = _FakeLLMForPlanner(response="ok")
        planner_obj = CurriculumPlanner(llm=fake_llm)
        _setup(curriculum=curriculum, planner=planner_obj)

        async with _client() as client:
            prompt_resp = await client.post(
                "/api/curriculum/trip/plan/turn/prompt",
                json={"message": "plan 3 days", "batch_size": 3},
            )
            turn_resp = await client.post(
                "/api/curriculum/trip/plan/turn",
                json={"message": "plan 3 days", "batch_size": 3},
            )

        assert prompt_resp.status_code == 200
        assert turn_resp.status_code == 200

        exported_user_prompt = prompt_resp.json()["user_prompt"]
        (llm_call,) = fake_llm.calls
        assert exported_user_prompt == llm_call["prompt"]


class TestGenerationMode:
    async def test_set_generation_mode_manual(self):
        store = _setup(_planned_curriculum())
        async with _client() as client:
            resp = await client.post(
                "/api/curriculum/trip/generation-mode",
                json={"mode": "manual"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"mode": "manual"}
        assert store.get_curriculum("trip").metadata["generation_mode"] == "manual"

    async def test_set_generation_mode_auto(self):
        curriculum = _planned_curriculum()
        curriculum.metadata["generation_mode"] = "manual"
        store = _setup(curriculum)
        async with _client() as client:
            resp = await client.post(
                "/api/curriculum/trip/generation-mode",
                json={"mode": "auto"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"mode": "auto"}
        assert store.get_curriculum("trip").metadata["generation_mode"] == "auto"

    async def test_set_generation_mode_unknown_curriculum_404(self):
        _setup()
        async with _client() as client:
            resp = await client.post(
                "/api/curriculum/no-such/generation-mode",
                json={"mode": "manual"},
            )
        assert resp.status_code == 404

    async def test_set_generation_mode_invalid_value_422(self):
        _setup(_planned_curriculum())
        async with _client() as client:
            resp = await client.post(
                "/api/curriculum/trip/generation-mode",
                json={"mode": "bogus"},
            )
        assert resp.status_code == 422


class TestGetCurriculumGenerationMode:
    async def test_absent_key_returns_auto(self):
        """A curriculum with no generation_mode key returns 'auto' (default)."""
        _setup(_planned_curriculum())
        async with _client() as client:
            resp = await client.get("/api/curriculum/trip")
        assert resp.status_code == 200
        assert resp.json()["generation_mode"] == "auto"

    async def test_manual_mode_returned(self):
        curriculum = _planned_curriculum()
        curriculum.metadata["generation_mode"] = "manual"
        _setup(curriculum)
        async with _client() as client:
            resp = await client.get("/api/curriculum/trip")
        assert resp.status_code == 200
        assert resp.json()["generation_mode"] == "manual"

    async def test_unknown_curriculum_404(self):
        _setup()
        async with _client() as client:
            resp = await client.get("/api/curriculum/no-such")
        assert resp.status_code == 404


class TestPlanCommitManualMode:
    async def test_manual_mode_commit_no_enqueue(self, tmp_path):
        """plan_commit in manual mode commits days but does NOT enqueue generate jobs."""
        from app.generation.pipeline import LessonPipeline

        proposed = {"start_day": 1, "days": [asdict(_day(1)), asdict(_day(2))]}
        curriculum = _planned_curriculum(proposed=proposed)
        curriculum.metadata["generation_mode"] = "manual"
        store = _setup(curriculum)

        pipeline = LessonPipeline(
            story_generator=None,
            renderer=None,
            audio_dir=tmp_path,
            content_stores={"sl": store},
            languages={"sl": get_language("sl")},
            srs_dbs={},
            activity_log=ActivityLog(maxlen=100),
            llm_client=None,
        )
        app.state.pipeline = pipeline

        async with _client() as client:
            resp = await client.post("/api/curriculum/trip/plan/commit", json={})

        assert resp.status_code == 200
        assert resp.json() == {"id": "trip", "days": 2}
        saved = store.get_curriculum("trip")
        assert [d.day for d in saved.days] == [1, 2]
        assert saved.metadata["planner"]["proposed"] is None
        # No pipeline jobs enqueued in manual mode
        assert len(pipeline._jobs) == 0

    async def test_auto_mode_commit_enqueues(self, tmp_path):
        """plan_commit in auto mode (explicit) enqueues generate jobs — same as default."""
        from app.generation.pipeline import LessonPipeline

        proposed = {"start_day": 1, "days": [asdict(_day(1))]}
        curriculum = _planned_curriculum(proposed=proposed)
        curriculum.metadata["generation_mode"] = "auto"
        store = _setup(curriculum)

        pipeline = LessonPipeline(
            story_generator=None,
            renderer=None,
            audio_dir=tmp_path,
            content_stores={"sl": store},
            languages={"sl": get_language("sl")},
            srs_dbs={},
            activity_log=ActivityLog(maxlen=100),
            llm_client=None,
        )
        app.state.pipeline = pipeline

        async with _client() as client:
            resp = await client.post("/api/curriculum/trip/plan/commit", json={})

        assert resp.status_code == 200
        assert pipeline._jobs[("sl", "trip", 1)]["state"] == "queued"

    async def test_absent_key_commit_enqueues(self, tmp_path):
        """plan_commit with no generation_mode key enqueues (default = auto)."""
        from app.generation.pipeline import LessonPipeline

        proposed = {"start_day": 1, "days": [asdict(_day(1))]}
        curriculum = _planned_curriculum(proposed=proposed)
        # No generation_mode key set
        store = _setup(curriculum)

        pipeline = LessonPipeline(
            story_generator=None,
            renderer=None,
            audio_dir=tmp_path,
            content_stores={"sl": store},
            languages={"sl": get_language("sl")},
            srs_dbs={},
            activity_log=ActivityLog(maxlen=100),
            llm_client=None,
        )
        app.state.pipeline = pipeline

        async with _client() as client:
            resp = await client.post("/api/curriculum/trip/plan/commit", json={})

        assert resp.status_code == 200
        assert pipeline._jobs[("sl", "trip", 1)]["state"] == "queued"
