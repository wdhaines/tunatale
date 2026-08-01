"""Tests for core health + curriculum endpoints."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.api.models import (
    CurriculumDayWithPosition,
    CurriculumProgressEntry,
    CurriculumSourceDay,
    CurriculumSourceResponse,
    CurriculumSummary,
    GetCurriculumResponse,
    HealthResponse,
    ImportCurriculumPlanResponse,
    LessonResponse,
    ProposedBatch,
)
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


class TestHealth:
    """Tests for the /api/health endpoint."""

    async def test_health_returns_ok(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    async def test_health_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (bp-ledger-burndown stage 3)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/health")
        assert set(response.json().keys()) == {"status"}
        assert set(HealthResponse.model_fields) == {"status"}


class TestCurriculumEndpoints:
    """Tests for curriculum CRUD endpoints."""

    async def test_get_curriculum_returns_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/nonexistent-id")
        assert response.status_code == 404

    async def test_get_curriculum_returns_200_with_data(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="test-c",
            topic="coffee",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Day 1",
                    focus="greetings",
                    learning_objective="greet",
                    story_guidance="café",
                    collocations=["zdravo"],
                )
            ],
        )
        store.save_curriculum("coffee-abc", curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/coffee-abc")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "coffee-abc"
        assert data["topic"] == "coffee"
        assert data["cefr_level"] == "A2"
        assert data["proposed"] is None
        assert data["days"] == [
            {
                "day": 1,
                "position": 1,
                "title": "Day 1",
                "focus": "greetings",
                "collocations": ["zdravo"],
                "learning_objective": "greet",
                "story_guidance": "café",
            }
        ]

    async def test_get_curriculum_positions_close_gaps_from_deleted_days(self):
        """``day`` keeps its gaps (it keys lessons/pipeline jobs); ``position`` is contiguous."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="test-c",
            topic="coffee",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=d, title=f"t{d}", focus="f", learning_objective="o", collocations=[])
                for d in (1, 2, 4, 7)
            ],
        )
        store.save_curriculum("coffee-abc", curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/coffee-abc")

        data = response.json()
        assert [(d["day"], d["position"]) for d in data["days"]] == [(1, 1), (2, 2), (4, 3), (7, 4)]

    async def test_get_curriculum_proposed_positions_continue_after_committed(self):
        """An uncommitted batch numbered against gappy days still shows contiguous positions."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="test-c",
            topic="coffee",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=d, title=f"t{d}", focus="f", learning_objective="o", collocations=[])
                for d in (1, 2, 4)
            ],
            metadata={
                "planner": {
                    "chat": [],
                    "feedback": [],
                    "proposed": {
                        "start_day": 5,
                        "days": [
                            {
                                "day": 5,
                                "title": "t5",
                                "focus": "f",
                                "collocations": [],
                                "learning_objective": "o",
                                "story_guidance": "",
                            },
                            {
                                "day": 6,
                                "title": "t6",
                                "focus": "f",
                                "collocations": [],
                                "learning_objective": "o",
                                "story_guidance": "",
                            },
                        ],
                    },
                }
            },
        )
        store.save_curriculum("coffee-abc", curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/coffee-abc")

        data = response.json()
        assert [(d["day"], d["position"]) for d in data["proposed"]["days"]] == [(5, 4), (6, 5)]

    async def test_get_curriculum_proposed_absent_needs_no_positions(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_curriculum(
            "coffee-abc", Curriculum(id="test-c", topic="coffee", language_code="sl", cefr_level="A2")
        )
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/coffee-abc")

        assert response.json()["proposed"] is None

    async def test_get_curriculum_days_sorted_and_proposed_exposed(self):
        """days come back sorted by day number; a pending proposal is included."""
        from app.storage.store import ContentStore

        proposed = {
            "start_day": 3,
            "days": [
                {
                    "day": 3,
                    "title": "Day 3",
                    "focus": "food",
                    "collocations": ["kava"],
                    "learning_objective": "order",
                    "story_guidance": "",
                }
            ],
        }
        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="test-c",
            topic="coffee",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(day=2, title="Day 2", focus="f2", learning_objective="o2", collocations=["b"]),
                CurriculumDay(day=1, title="Day 1", focus="f1", learning_objective="o1", collocations=["a"]),
            ],
            metadata={"planner": {"chat": [], "proposed": proposed, "feedback": []}},
        )
        store.save_curriculum("coffee-abc", curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/coffee-abc")

        data = response.json()
        assert [d["day"] for d in data["days"]] == [1, 2]
        assert data["proposed"]["start_day"] == 3
        assert [d["title"] for d in data["proposed"]["days"]] == ["Day 3"]

    async def test_get_curriculum_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (BP ledger drain), written against
        the UNFILTERED handler output — covers both the envelope and the nested
        day/proposed shapes in one call so ``proposed`` is non-null."""
        from app.storage.store import ContentStore

        proposed = {
            "start_day": 3,
            "days": [
                {
                    "day": 3,
                    "title": "Day 3",
                    "focus": "food",
                    "collocations": ["kava"],
                    "learning_objective": "order",
                    "story_guidance": "",
                }
            ],
        }
        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="test-c",
            topic="coffee",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Day 1",
                    focus="greetings",
                    learning_objective="greet",
                    story_guidance="café",
                    collocations=["zdravo"],
                )
            ],
            metadata={"planner": {"chat": [], "proposed": proposed, "feedback": []}},
        )
        store.save_curriculum("coffee-abc", curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/coffee-abc")

        data = response.json()
        assert set(data.keys()) == {"id", "topic", "language_code", "cefr_level", "days", "proposed", "generation_mode"}
        assert set(GetCurriculumResponse.model_fields) == {
            "id",
            "topic",
            "language_code",
            "cefr_level",
            "days",
            "proposed",
            "generation_mode",
        }

        day_keys = {"day", "title", "focus", "collocations", "learning_objective", "story_guidance", "position"}
        assert set(data["days"][0].keys()) == day_keys
        assert set(CurriculumDayWithPosition.model_fields) == day_keys

        assert set(data["proposed"].keys()) == {"start_day", "days"}
        assert set(ProposedBatch.model_fields) == {"start_day", "days"}
        assert set(data["proposed"]["days"][0].keys()) == day_keys

    async def test_list_curricula_returns_200(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_curriculum("c1", Curriculum(id="c1", topic="coffee", language_code="sl", cefr_level="A2"))
        app.state.content_store = store
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["id"] == "c1"
        assert data[0]["topic"] == "coffee"
        assert "created_at" in data[0]

    async def test_list_curricula_response_keys_match_model_exactly(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_curriculum("c1", Curriculum(id="c1", topic="coffee", language_code="sl", cefr_level="A2"))
        app.state.content_store = store
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum")
        data = response.json()
        assert set(data[0].keys()) == {"id", "topic", "created_at"}
        assert set(CurriculumSummary.model_fields) == {"id", "topic", "created_at"}

    async def test_get_lesson_by_day_returns_full_lesson(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        lesson = Lesson(
            title="Day 1: Coffee",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="kavo prosim", role="female-1", voice_id="sl-SI-PetraNeural", language_code="sl"),
                    ],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="kavo prosim", translation="a coffee please")],
        )
        store.save_lesson("lesson-day1", "curriculum-abc", 1, lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/curriculum-abc/days/1/lesson")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "lesson-day1"
        assert data["title"] == "Coffee"
        assert data["language_code"] == "sl"
        assert len(data["sections"]) == 1
        assert data["sections"][0]["phrases"][0]["text"] == "kavo prosim"
        assert data["key_phrases"][0]["translation"] == "a coffee please"

    async def test_get_lesson_by_day_response_keys_match_model(self):
        """Oracle for the response_model flip (openapi ledger batch 7).

        Unlike GET /api/story/{lesson_id}, this route serializes WITHOUT a day
        (``serialize_lesson`` is called with no ``day``), so ``day`` must NOT
        appear in the payload — the shared LessonResponse model keeps it
        optional and the route drops it via ``response_model_exclude_unset``.
        """
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        lesson = Lesson(
            title="Day 1: Coffee",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="kavo prosim", role="female-1", voice_id="sl-SI-PetraNeural", language_code="sl"),
                    ],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="kavo prosim", translation="a coffee please")],
        )
        store.save_lesson("lesson-day1", "curriculum-abc", 1, lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/curriculum-abc/days/1/lesson")

        assert response.status_code == 200
        data = response.json()
        assert set(data.keys()) == {"id", "title", "language_code", "key_phrases", "sections"}
        # The shared model still has `day` (get_lesson sets it); this route
        # simply doesn't emit it — the two assertions must not match.
        assert set(LessonResponse.model_fields) == {
            "id",
            "title",
            "language_code",
            "key_phrases",
            "sections",
            "day",
        }

    async def test_get_lesson_by_day_returns_most_recent(self):
        """When two lessons exist for the same day, the newer one is returned."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        old = Lesson(title="Old", language_code="sl", sections=[], key_phrases=[])
        new = Lesson(title="New", language_code="sl", sections=[], key_phrases=[])
        store.save_lesson("lesson-old", "curriculum-abc", 1, old)
        store.save_lesson("lesson-new", "curriculum-abc", 1, new)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/curriculum-abc/days/1/lesson")

        assert response.status_code == 200
        assert response.json()["id"] == "lesson-new"

    async def test_get_lesson_by_day_returns_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/no-such-curriculum/days/1/lesson")
        assert response.status_code == 404

    async def test_get_curriculum_progress_returns_lesson_days(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="c1",
            topic="coffee",
            language_code="sl",
            cefr_level="A2",
        )
        store.save_curriculum("c1", curriculum)
        lesson = Lesson(title="Day 1", language_code="sl", sections=[], key_phrases=[])
        store.save_lesson("l1", "c1", 1, lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/c1/progress")

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["day"] == 1
        assert data[0]["lesson_id"] == "l1"
        assert data[0]["position"] == 1

    async def test_get_curriculum_progress_response_keys_match_model_exactly(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_curriculum("c1", Curriculum(id="c1", topic="coffee", language_code="sl", cefr_level="A2"))
        store.save_lesson("l1", "c1", 1, Lesson(title="Day 1", language_code="sl", sections=[], key_phrases=[]))
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/c1/progress")

        data = response.json()
        assert set(data[0].keys()) == {"day", "lesson_id", "position"}
        assert set(CurriculumProgressEntry.model_fields) == {"day", "lesson_id", "position"}

    async def test_get_curriculum_progress_positions_close_gaps(self):
        """The home card's "Continue → Day N" reads position, so it must skip the gap too."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_curriculum(
            "c1",
            Curriculum(
                id="c1",
                topic="coffee",
                language_code="sl",
                cefr_level="A2",
                days=[
                    CurriculumDay(day=d, title=f"t{d}", focus="f", learning_objective="o", collocations=[])
                    for d in (1, 3)
                ],
            ),
        )
        store.save_lesson("l1", "c1", 1, Lesson(title="One", language_code="sl"))
        store.save_lesson("l3", "c1", 3, Lesson(title="Three", language_code="sl"))
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/c1/progress")

        assert [(d["day"], d["position"]) for d in response.json()] == [(1, 1), (3, 2)]

    async def test_get_curriculum_progress_orphan_lesson_falls_back_to_its_day(self):
        """A lesson whose day is gone from the plan still gets a stable label."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_curriculum("c1", Curriculum(id="c1", topic="coffee", language_code="sl", cefr_level="A2", days=[]))
        store.save_lesson("l9", "c1", 9, Lesson(title="Orphan", language_code="sl"))
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/c1/progress")

        assert [(d["day"], d["position"]) for d in response.json()] == [(9, 9)]

    async def test_get_curriculum_progress_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/nonexistent/progress")
        assert response.status_code == 404


class TestCurriculumPlanIOEndpoints:
    """Tests for curriculum plan source/import endpoints."""

    async def test_source_returns_200(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="test-c",
            topic="coffee",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Day 1",
                    focus="greetings",
                    learning_objective="greet",
                    collocations=["zdravo"],
                )
            ],
        )
        store.save_curriculum("test-c", curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/test-c/source")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "test-c"
        assert data["topic"] == "coffee"
        assert len(data["days"]) == 1
        assert "metadata" not in data

    async def test_source_response_keys_match_model_exactly(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="test-c",
            topic="coffee",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Day 1",
                    focus="greetings",
                    learning_objective="greet",
                    collocations=["zdravo"],
                )
            ],
        )
        store.save_curriculum("test-c", curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/test-c/source")

        data = response.json()
        assert set(data.keys()) == {"id", "topic", "language_code", "cefr_level", "days"}
        assert set(CurriculumSourceResponse.model_fields) == {"id", "topic", "language_code", "cefr_level", "days"}

        day_keys = {"day", "title", "focus", "collocations", "learning_objective", "story_guidance"}
        assert set(data["days"][0].keys()) == day_keys
        assert set(CurriculumSourceDay.model_fields) == day_keys

    async def test_source_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/no-such/source")

        assert response.status_code == 404
        assert response.json()["detail"] == "Curriculum not found"

    async def test_import_response_keys_match_model_exactly(self):
        """Oracle for the response_model flip (bp-ledger-burndown stage 3)."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        app.state.content_store = store

        body = {
            "topic": "ordering coffee",
            "language_code": "sl",
            "cefr_level": "A2",
            "days": [
                {
                    "day": 1,
                    "title": "Day 1",
                    "focus": "greetings",
                    "collocations": ["dober dan"],
                    "learning_objective": "say hello",
                    "story_guidance": "",
                },
            ],
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/import", json=body)

        assert set(response.json().keys()) == {"id", "topic", "language_code", "days"}
        assert set(ImportCurriculumPlanResponse.model_fields) == {"id", "topic", "language_code", "days"}

    async def test_import_returns_201(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        app.state.content_store = store

        body = {
            "topic": "ordering coffee",
            "language_code": "sl",
            "cefr_level": "A2",
            "days": [
                {
                    "day": 1,
                    "title": "Day 1",
                    "focus": "greetings",
                    "collocations": ["dober dan"],
                    "learning_objective": "say hello",
                    "story_guidance": "",
                },
            ],
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/import", json=body)

        assert response.status_code == 201
        data = response.json()
        assert data["id"].startswith("ordering-coffee-")
        assert data["topic"] == "ordering coffee"
        assert data["language_code"] == "sl"
        assert data["days"] == 1

        restored = store.get_curriculum(data["id"])
        assert restored is not None

    async def test_import_with_id_updates_existing(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        existing = Curriculum(
            id="existing-id",
            topic="old topic",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Old Day",
                    focus="old",
                    learning_objective="old",
                    collocations=["old"],
                )
            ],
            metadata={"planner": {"chat": [], "proposed": None, "feedback": []}},
        )
        store.save_curriculum("existing-id", existing)
        app.state.content_store = store

        body = {
            "id": "existing-id",
            "topic": "new topic",
            "language_code": "sl",
            "cefr_level": "B1",
            "days": [
                {
                    "day": 1,
                    "title": "New Day",
                    "focus": "new",
                    "collocations": ["new phrase"],
                    "learning_objective": "new objective",
                    "story_guidance": "",
                },
            ],
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/import", json=body)

        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "existing-id"
        assert data["topic"] == "new topic"
        assert data["days"] == 1

        restored = store.get_curriculum("existing-id")
        assert restored is not None
        assert restored.metadata == {"planner": {"chat": [], "proposed": None, "feedback": []}}

    async def test_import_422_on_bad_days(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")

        body = {
            "topic": "test",
            "language_code": "sl",
            "cefr_level": "A2",
            "days": [
                {
                    "day": 0,
                    "title": "Bad",
                    "focus": "bad",
                    "collocations": ["x"],
                    "learning_objective": "bad",
                    "story_guidance": "",
                },
            ],
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/import", json=body)

        assert response.status_code == 422
        assert "day" in response.json()["detail"]

    async def test_import_404_when_id_not_found(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")

        body = {
            "id": "no-such-id",
            "topic": "test",
            "language_code": "sl",
            "cefr_level": "A2",
            "days": [
                {
                    "day": 1,
                    "title": "Day 1",
                    "focus": "test",
                    "collocations": ["x"],
                    "learning_objective": "test",
                    "story_guidance": "",
                },
            ],
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/curriculum/import", json=body)

        assert response.status_code == 404
        assert "no-such-id" in response.json()["detail"]
