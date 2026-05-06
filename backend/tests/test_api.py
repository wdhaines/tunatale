"""API endpoint tests."""

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.language import Language
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType


@pytest.fixture(autouse=True)
def _clean_app_state():
    yield
    for attr in (
        "content_store",
        "language",
        "curriculum_generator",
        "story_generator",
        "renderer",
        "audio_dir",
        "srs_db",
    ):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


class TestHealth:
    """Tests for the /api/health endpoint."""

    async def test_health_returns_ok(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestCurriculumEndpoints:
    """Tests for curriculum CRUD endpoints."""

    async def test_generate_curriculum_returns_201(self, monkeypatch):
        from app.storage.store import ContentStore

        mock_curriculum = Curriculum(
            id="test-id",
            topic="ordering coffee",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="First day",
                    focus="greetings",
                    learning_objective="say hello",
                    story_guidance="use dober dan",
                    collocations=["dober dan"],
                )
            ],
        )

        mock_generator = AsyncMock()
        mock_generator.generate = AsyncMock(return_value=mock_curriculum)

        app.state.curriculum_generator = mock_generator
        app.state.language = Language.slovene()
        app.state.content_store = ContentStore(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/curriculum/generate",
                json={"topic": "ordering coffee", "cefr_level": "A2", "num_days": 1},
            )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["id"].startswith("ordering-coffee"), f"Expected slug prefix, got: {data['id']}"
        assert data["topic"] == "ordering coffee"
        # Verify persisted
        restored = app.state.content_store.get_curriculum(data["id"])
        assert restored is not None
        assert restored.topic == "ordering coffee"

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
        assert data["days"] == 1

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
        assert data["title"] == "Day 1: Coffee"
        assert data["language_code"] == "sl"
        assert len(data["sections"]) == 1
        assert data["sections"][0]["phrases"][0]["text"] == "kavo prosim"
        assert data["key_phrases"][0]["translation"] == "a coffee please"

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

    async def test_get_curriculum_progress_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum/nonexistent/progress")
        assert response.status_code == 404


class TestStoryEndpoints:
    """Tests for story/lesson generation endpoints."""

    async def test_get_lesson_returns_full_script(self):
        from app.storage.store import ContentStore

        mock_lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[
                        Phrase(text="dober dan", role="female-1", voice_id="sl-SI-PetraNeural", language_code="sl"),
                    ],
                ),
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="kako ste", role="male-1", voice_id="sl-SI-RokNeural", language_code="sl"),
                    ],
                ),
            ],
        )

        store = ContentStore(":memory:")
        store.save_lesson("lesson-abc", "some-curriculum-id", 1, mock_lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/story/lesson-abc")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "lesson-abc"
        assert data["title"] == "Day 1"
        assert len(data["sections"]) == 2
        phrase = data["sections"][0]["phrases"][0]
        assert phrase["text"] == "dober dan"
        assert phrase["role"] == "female-1"
        assert phrase["language_code"] == "sl"
        assert phrase["voice_id"] == "sl-SI-PetraNeural"

    async def test_get_lesson_includes_key_phrases(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        mock_lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="prosim kavo", translation="a coffee please"),
            ],
        )
        store.save_lesson("lesson-kp", "curriculum-1", 1, mock_lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/story/lesson-kp")

        assert response.status_code == 200
        data = response.json()
        assert len(data["key_phrases"]) == 2
        assert data["key_phrases"][0] == {"phrase": "dober dan", "translation": "good day"}
        assert data["key_phrases"][1] == {"phrase": "prosim kavo", "translation": "a coffee please"}

    async def test_get_lesson_returns_empty_key_phrases_for_old_lesson(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        mock_lesson = Lesson(title="Day 1", language_code="sl", sections=[])
        store.save_lesson("lesson-old", "curriculum-1", 1, mock_lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/story/lesson-old")

        assert response.status_code == 200
        assert response.json()["key_phrases"] == []

    async def test_get_lesson_returns_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/story/nonexistent-lesson-id")
        assert response.status_code == 404

    async def test_generate_story_returns_404_curriculum_not_found(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "nonexistent", "day": 1, "strategy": "WIDER"},
            )
        assert response.status_code == 404

    async def test_generate_story_returns_404_day_not_found(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        curriculum = Curriculum(
            id="c1",
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
        store.save_curriculum("c1", curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "c1", "day": 99, "strategy": "WIDER"},
            )
        assert response.status_code == 404

    async def test_generate_story_returns_201(self, monkeypatch):
        from app.storage.store import ContentStore

        mock_lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
                )
            ],
        )

        mock_generator = AsyncMock()
        mock_generator.generate = AsyncMock(return_value=mock_lesson)

        mock_curriculum = Curriculum(
            id="test-id",
            topic="test",
            language_code="sl",
            cefr_level="A2",
            days=[
                CurriculumDay(
                    day=1,
                    title="Day 1",
                    focus="greetings",
                    learning_objective="greet",
                    story_guidance="greet each other",
                    collocations=["dober dan"],
                )
            ],
        )
        app.state.story_generator = mock_generator
        app.state.language = Language.slovene()

        store = ContentStore(":memory:")
        curriculum_id = "test-curriculum-id"
        store.save_curriculum(curriculum_id, mock_curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": curriculum_id, "day": 1, "strategy": "WIDER"},
            )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["id"].startswith("day-1"), f"Expected slug prefix, got: {data['id']}"
        assert "sections" in data
        # Verify lesson was persisted
        assert store.get_lesson(data["id"]) is not None


class TestSRSEndpoints:
    """Tests for SRS due/stats/feedback/new endpoints."""

    async def test_srs_due_returns_200(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/due")

        assert response.status_code == 200
        data = response.json()
        assert "due" in data
        assert isinstance(data["due"], list)

    async def test_srs_stats_returns_200(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/stats")

        assert response.status_code == 200
        data = response.json()
        assert "total" in data

    async def test_srs_new_returns_200(self):
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        db.add_collocation(
            SyntacticUnit(text="dober dan", translation="good day", word_count=2, difficulty=1, source="llm")
        )
        db.add_collocation(
            SyntacticUnit(text="prosim kavo", translation="a coffee please", word_count=2, difficulty=1, source="llm")
        )
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/new")

        assert response.status_code == 200
        data = response.json()
        assert "new" in data
        assert len(data["new"]) == 2
        assert all("text" in item and "translation" in item for item in data["new"])

    async def test_srs_new_returns_empty_when_no_new_cards(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/new")

        assert response.json()["new"] == []

    async def test_srs_new_respects_limit(self):
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        for i in range(15):
            db.add_collocation(
                SyntacticUnit(text=f"phrase {i}", translation=f"trans {i}", word_count=2, difficulty=1, source="llm")
            )
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/new")

        assert len(response.json()["new"]) == 10

    async def test_listen_skips_phrases_with_wrong_language_code(self):
        """NATURAL_SPEED phrases whose language_code != lesson language_code are skipped."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        # Lesson is Slovene but one NATURAL_SPEED phrase is English (narrator)
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="At the café", voice_id="narrator", language_code="en", role="narrator"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-foreign", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-foreign"})

        assert response.status_code == 200
        # The English phrase should contribute 0 lemmas (registered = 0 lemmas + 0 key phrases)
        assert db.count_collocations() == 0

    async def test_listen_registers_collocations(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
                )
            ],
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="prosim kavo", translation="a coffee please"),
            ],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["registered"] == 2
        assert db.count_collocations() == 2
        assert db.get_collocation("dober dan") is not None
        assert db.get_collocation("prosim kavo") is not None

    async def test_listen_is_idempotent(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        assert db.count_collocations() == 1

    async def test_listen_returns_404_for_missing_lesson(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        app.state.srs_db = SRSDatabase(":memory:")
        app.state.content_store = ContentStore(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "nonexistent"})

        assert response.status_code == 404

    async def test_listen_registers_all_l2_words(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        # 3 unique words: kje, je, banka
        assert data["registered"] == 3
        assert db.get_collocation_by_lemma("kje") is not None
        assert db.get_collocation_by_lemma("je") is not None
        assert db.get_collocation_by_lemma("banka") is not None

    async def test_listen_default_rating_is_good(self):
        from app.models.srs_item import SRSState
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation_by_lemma("banka")
        assert item is not None
        # After GOOD rating on a NEW item, state advances to LEARNING
        assert item.state in (SRSState.LEARNING, SRSState.REVIEW)
        assert item.reps == 1

    async def test_listen_with_word_rating_override_hard(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/listen",
                json={"lesson_id": "lesson-1", "word_ratings": {"banka": "hard"}},
            )

        assert response.status_code == 200
        item = db.get_collocation_by_lemma("banka")
        assert item is not None
        assert item.reps == 1

    async def test_listen_deduplicates_lemmas(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="banka banka banka", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        # Even though "banka" appears 3 times, it should only be scheduled once
        assert db.count_collocations() == 1
        item = db.get_collocation_by_lemma("banka")
        assert item.reps == 1

    async def test_listen_key_phrases_translation_preserved(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        assert item.syntactic_unit.translation == "good day"

    async def test_listen_word_translations_from_gloss_map(self):
        """Word-level SRS items get their translation from generation_metadata['token_glosses']."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="banka", voice_id="sl-SI-PetraNeural", language_code="sl")],
                )
            ],
            generation_metadata={"token_glosses": {"banka": "bank"}},
        )
        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation_by_lemma("banka")
        assert item is not None
        assert item.syntactic_unit.translation == "bank"

    async def test_listen_is_idempotent_with_word_tracking(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        assert db.count_collocations() == 1

    async def test_listen_skips_rating_when_lemma_collides_without_lemma_field(self):
        """If a pre-existing row has the same text as a lemma but lemma=NULL,
        get_collocation_by_lemma returns None → if item is not None: False branch (124->113)."""
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        # Pre-insert with text="banka" but lemma=None (lemma column stays NULL)
        db.add_collocation(SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus"))
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        # Row still exists but was not rated (INSERT OR IGNORE left lemma=NULL)
        assert db.count_collocations() == 1
        assert db.get_collocation_by_lemma("banka") is None


class TestQueueStatsEndpoint:
    """Tests for GET /api/srs/queue-stats."""

    async def test_queue_stats_returns_200_with_shape(self):
        from unittest.mock import patch

        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        with patch("app.api.srs.resolve_daily_new_cap", return_value=(20, "default")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        assert response.status_code == 200
        data = response.json()
        assert "new" in data
        assert "learning" in data
        assert "review" in data
        assert "due" not in data
        assert "daily_new_cap" in data
        assert "cap_source" in data

    async def test_queue_stats_new_is_clamped_at_cap(self):
        from unittest.mock import patch

        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        for i in range(5):
            db.add_collocation(
                SyntacticUnit(text=f"word{i}", translation="t", word_count=1, difficulty=1, source="corpus"),
                language_code="sl",
            )
        app.state.srs_db = db

        with patch("app.api.srs.resolve_daily_new_cap", return_value=(3, "default")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        data = response.json()
        assert data["new"] == 3
        assert data["daily_new_cap"] == 3
        assert data["cap_source"] == "default"

    async def test_queue_stats_cap_source_from_anki(self):
        from unittest.mock import patch

        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        with patch("app.api.srs.resolve_daily_new_cap", return_value=(30, "anki")):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        data = response.json()
        assert data["cap_source"] == "anki"
        assert data["daily_new_cap"] == 30


class TestTranscriptEndpoint:
    """Tests for GET /api/srs/lesson/{lesson_id}/transcript."""

    async def test_returns_200_with_correct_shape(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Zdravo.", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="Zdravo", translation="Hello")],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/lesson-1/transcript")

        assert response.status_code == 200
        data = response.json()
        assert data["lesson_id"] == "lesson-1"
        assert isinstance(data["key_phrases"], list)
        assert isinstance(data["dialogue_lines"], list)

    async def test_returns_404_for_missing_lesson(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        app.state.srs_db = SRSDatabase(":memory:")
        app.state.content_store = ContentStore(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/nonexistent/transcript")

        assert response.status_code == 404

    async def test_l2_filter_excludes_english_narrator(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Scene: At the market", voice_id="narrator", language_code="en", role="narrator"),
                        Phrase(text="Zdravo.", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/lesson-1/transcript")

        data = response.json()
        assert len(data["dialogue_lines"]) == 1
        assert data["dialogue_lines"][0]["role"] == "female-1"

    async def test_known_word_has_correct_srs_state(self):
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="llm", lemma="banka")
        db.add_collocation(unit, language_code="sl")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/lesson/lesson-1/transcript")

        data = response.json()
        word = data["dialogue_lines"][0]["words"][0]
        assert word["srs_state"] == "new"
        assert word["lemma"] == "banka"
        assert word["surface"] == "banka"


def _make_mock_lesson_with_sections() -> Lesson:
    return Lesson(
        title="Day 1: Ordering Coffee",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.KEY_PHRASES,
                phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
            ),
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text="hvala", voice_id="sl-SI-PetraNeural", language_code="sl")],
            ),
        ],
    )


def _fake_render(lesson, full_path, section_paths=None):
    """Fake renderer.render: writes minimal audio bytes to all output paths."""
    full_path.write_bytes(b"audio")
    if section_paths:
        for sp in section_paths:
            sp.write_bytes(b"section audio")


class TestAudioEndpoints:
    """Tests for audio render and retrieval endpoints."""

    async def test_render_audio_returns_404_for_missing_lesson(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/audio/render", json={"lesson_id": "nonexistent"})
        assert response.status_code == 404

    async def test_audio_render_returns_202(self, tmp_path):
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "test-lesson-id"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/audio/render", json={"lesson_id": lesson_id})

        assert response.status_code == 202
        data = response.json()
        assert "audio_id" in data
        assert store.get_audio_file(data["audio_id"]) is not None

    async def test_render_returns_sections_in_response(self, tmp_path):
        """POST /api/audio/render response includes a sections array."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-sections-test"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/audio/render", json={"lesson_id": lesson_id})

        assert response.status_code == 202
        data = response.json()
        assert "sections" in data
        assert len(data["sections"]) == len(mock_lesson.sections)

        sec = data["sections"][0]
        assert "audio_id" in sec
        assert sec["section_index"] == 0
        assert sec["section_type"] == "key_phrases"
        assert sec["title"] == "Key Phrases"

    async def test_get_lesson_audio_endpoint(self, tmp_path):
        """GET /api/audio/lesson/{lesson_id} returns existing audio files list."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        lesson_id = "lesson-lookup-test"
        store.save_lesson(lesson_id, "some-curriculum-id", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        # First render
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            response = await client.get(f"/api/audio/lesson/{lesson_id}")

        assert response.status_code == 200
        data = response.json()
        assert "audio_id" in data
        assert "sections" in data
        assert len(data["sections"]) == len(mock_lesson.sections)

    async def test_get_lesson_audio_returns_404_when_not_rendered(self):
        """GET /api/audio/lesson/{lesson_id} returns 404 when no audio exists."""
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/never-rendered-id")
        assert response.status_code == 404

    async def test_get_audio_sets_content_disposition(self, tmp_path):
        """GET /api/audio/{audio_id} sets Content-Disposition with sanitized filename."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c-dl", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c-dl", curriculum)
        lesson_id = "lesson-download-test"
        store.save_lesson(lesson_id, "c-dl", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            render_resp = await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            data = render_resp.json()

            # Check full lesson download filename
            full_audio_id = data["audio_id"]
            response = await client.get(f"/api/audio/{full_audio_id}")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert ".wav" in cd
        assert "ordering_coffee" in cd.lower()

    async def test_get_audio_section_content_disposition(self, tmp_path):
        """GET /api/audio/{section_audio_id} includes topic, day, and section type in filename."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c-sec-dl", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c-sec-dl", curriculum)
        lesson_id = "lesson-sec-dl-test"
        store.save_lesson(lesson_id, "c-sec-dl", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            render_resp = await client.post("/api/audio/render", json={"lesson_id": lesson_id})
            sec_audio_id = render_resp.json()["sections"][0]["audio_id"]
            response = await client.get(f"/api/audio/{sec_audio_id}")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert "Key_Phrases" in cd
        assert "ordering_coffee" in cd.lower()
        assert "Day01" in cd

    async def test_audio_get_returns_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/nonexistent-id")
        assert response.status_code == 404

    async def test_get_lesson_audio_returns_404_when_no_full_row(self):
        """GET /audio/lesson/{id} returns 404 when only section rows exist (no full-lesson row)."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        # Save a section-only row (section_index=0, no full row)
        store.save_audio_file("sec-1", "lesson-x", "/tmp/sec.wav", section_index=0, section_type="key_phrases")
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-x")

        assert response.status_code == 404
        assert "Full lesson audio" in response.json()["detail"]

    async def test_get_lesson_audio_falls_back_for_unknown_section_type(self):
        """GET /audio/lesson/{id} gracefully uses raw string when section_type is unrecognized."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        # Full row
        store.save_audio_file("full-1", "lesson-y", "/tmp/full.wav")
        # Section row with unknown section_type
        store.save_audio_file("sec-2", "lesson-y", "/tmp/sec.wav", section_index=0, section_type="unknown_custom")
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-y")

        assert response.status_code == 200
        data = response.json()
        # The title should fall back to the raw section_type string
        assert data["sections"][0]["title"] == "unknown_custom"

    async def test_get_audio_returns_404_when_file_missing_on_disk(self, tmp_path):
        """GET /api/audio/{audio_id} returns 404 when DB row exists but file is absent."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        nonexistent_path = str(tmp_path / "does_not_exist.wav")
        store.save_audio_file("audio-gone", "lesson-z", nonexistent_path)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/audio-gone")

        assert response.status_code == 404
        assert "missing" in response.json()["detail"]

    # ── ZIP download endpoint tests ───────────────────────────────────────

    async def test_zip_download_returns_zip_with_sections(self, tmp_path):
        """GET /api/audio/lesson/{id}/zip returns a ZIP containing all section WAVs."""
        import io
        import zipfile

        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c1", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c1", curriculum)
        store.save_lesson("lesson-zip-1", "c1", 1, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": "lesson-zip-1"})
            response = await client.get("/api/audio/lesson/lesson-zip-1/zip")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        z = zipfile.ZipFile(io.BytesIO(response.content))
        names = z.namelist()
        # full lesson + one per section
        assert len(names) == len(mock_lesson.sections) + 1

    async def test_zip_download_filenames_include_topic_and_day(self, tmp_path):
        """ZIP filenames include sanitized curriculum topic and zero-padded day."""
        import io
        import zipfile

        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c2", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c2", curriculum)
        store.save_lesson("lesson-zip-2", "c2", 3, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": "lesson-zip-2"})
            response = await client.get("/api/audio/lesson/lesson-zip-2/zip")

        assert response.status_code == 200
        z = zipfile.ZipFile(io.BytesIO(response.content))
        names = z.namelist()
        for name in names:
            assert "ordering_coffee" in name.lower()
            assert "Day03" in name
        # full file sorts first (00), then sections (01, 02…)
        assert names[0].endswith("_00_Full.wav")
        assert names[1].endswith("_01_Key_Phrases.wav")
        assert names[2].endswith("_02_Natural_Speed.wav")

    async def test_zip_download_content_disposition_header(self, tmp_path):
        """ZIP Content-Disposition includes topic and day in the filename."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        curriculum = Curriculum(id="c3", topic="ordering coffee", language_code="sl", cefr_level="A2")
        store.save_curriculum("c3", curriculum)
        store.save_lesson("lesson-zip-3", "c3", 2, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": "lesson-zip-3"})
            response = await client.get("/api/audio/lesson/lesson-zip-3/zip")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert ".zip" in cd
        assert "ordering_coffee" in cd.lower()
        assert "Day02" in cd

    async def test_zip_download_returns_404_when_no_audio(self):
        """GET /api/audio/lesson/{id}/zip returns 404 when no audio exists."""
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/no-audio/zip")
        assert response.status_code == 404

    async def test_zip_download_returns_404_when_no_sections(self):
        """GET /api/audio/lesson/{id}/zip returns 404 when only a full-lesson row exists."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_audio_file("full-only", "lesson-no-sec", "/tmp/full.wav")
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-no-sec/zip")
        assert response.status_code == 404
        assert "section" in response.json()["detail"].lower()

    async def test_zip_download_falls_back_when_curriculum_missing(self, tmp_path):
        """ZIP endpoint uses lesson title as fallback when curriculum is not found."""
        import io
        import zipfile

        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        # Save lesson with a curriculum_id that has no corresponding curriculum row
        store.save_lesson("lesson-zip-fallback", "missing-c", 5, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/audio/render", json={"lesson_id": "lesson-zip-fallback"})
            response = await client.get("/api/audio/lesson/lesson-zip-fallback/zip")

        assert response.status_code == 200
        z = zipfile.ZipFile(io.BytesIO(response.content))
        names = z.namelist()
        # full lesson + sections
        assert len(names) == len(mock_lesson.sections) + 1

    async def test_zip_download_returns_404_when_section_file_missing_on_disk(self, tmp_path):
        """ZIP endpoint returns 404 when a section file is absent from disk."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_audio_file("full-x", "lesson-missing-file", str(tmp_path / "full.wav"))
        store.save_audio_file(
            "sec-x", "lesson-missing-file", str(tmp_path / "missing.wav"), section_index=0, section_type="key_phrases"
        )
        # Write the full file but NOT the section file
        (tmp_path / "full.wav").write_bytes(b"audio")
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-missing-file/zip")
        assert response.status_code == 404
        assert "missing" in response.json()["detail"].lower()

    async def test_zip_download_falls_back_to_defaults_when_lesson_row_missing(self, tmp_path):
        """ZIP uses fallback topic/day when no lesson row exists in the DB."""
        import io
        import zipfile

        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        # Save only audio rows — no lesson row in lessons table
        sec_path = tmp_path / "sec.wav"
        sec_path.write_bytes(b"audio")
        store.save_audio_file(
            "sec-no-lesson", "lesson-ghost", str(sec_path), section_index=0, section_type="key_phrases"
        )
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/lesson/lesson-ghost/zip")

        assert response.status_code == 200
        z = zipfile.ZipFile(io.BytesIO(response.content))
        assert len(z.namelist()) == 1

    async def test_build_section_filename_falls_back_for_unknown_section_type(self, tmp_path):
        """_build_section_filename uses the raw string for unrecognized section types."""
        from app.api.audio import _build_section_filename

        name = _build_section_filename("topic", 1, 0, "custom_unknown")
        assert "custom_unknown" in name
        assert name.endswith(".wav")

    async def test_get_audio_falls_back_when_no_lesson_row(self, tmp_path):
        """GET /api/audio/{id} uses fallback name when lesson row is absent."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"data")
        # Save audio row but no lesson row
        store.save_audio_file("audio-no-lesson", "ghost-lesson", str(wav))
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/audio-no-lesson")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert "audio" in cd
        assert ".wav" in cd

    async def test_get_audio_uses_lesson_title_when_curriculum_missing(self, tmp_path):
        """GET /api/audio/{id} falls back to lesson title when curriculum row is absent."""
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=_fake_render)

        mock_lesson = _make_mock_lesson_with_sections()
        store = ContentStore(":memory:")
        # Save lesson with no matching curriculum
        store.save_lesson("lesson-no-c", "nonexistent-curriculum", 2, mock_lesson)

        app.state.renderer = mock_renderer
        app.state.audio_dir = tmp_path
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            render_resp = await client.post("/api/audio/render", json={"lesson_id": "lesson-no-c"})
            sec_id = render_resp.json()["sections"][0]["audio_id"]
            response = await client.get(f"/api/audio/{sec_id}")

        assert response.status_code == 200
        cd = response.headers.get("content-disposition", "")
        assert ".wav" in cd
        # Lesson title is "Day 1: Ordering Coffee" → sanitized
        assert "Day_1" in cd or "Ordering_Coffee" in cd


class TestCreatePhraseIntegration:
    """Integration tests for multi-word phrase creation via POST /api/srs/items."""

    async def test_create_multiword_item_returns_201(self):
        """POST /api/srs/items with word_count=2 creates a SyntacticUnit with lemma=None."""
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/items",
                json={"text": "centru mesta", "language_code": "sl", "word_count": 2, "translation": ""},
            )

        assert response.status_code == 201
        data = response.json()
        assert data["text"] == "centru mesta"
        # Multi-word items have no lemma
        assert data.get("translation") == ""

    async def test_create_multiword_item_duplicate_returns_409(self):
        """Second POST with same text returns 409 Conflict."""
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/api/srs/items",
                json={"text": "centru mesta", "language_code": "sl", "word_count": 2, "translation": ""},
            )
            response = await client.post(
                "/api/srs/items",
                json={"text": "centru mesta", "language_code": "sl", "word_count": 2, "translation": ""},
            )

        assert response.status_code == 409

    async def test_create_phrase_then_transcript_shows_collocation_span(self):
        """After creating 'centru mesta', transcript tokens for that phrase
        share a collocation_span_id and collocation_lemma='centru mesta'."""
        from app.models.lesson import Lesson, Phrase, Section, SectionType
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(
                            text="v centru mesta",
                            voice_id="female-1",
                            language_code="sl",
                            role="female-1",
                        )
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-phrase", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Create the phrase
            create_resp = await client.post(
                "/api/srs/items",
                json={"text": "centru mesta", "language_code": "sl", "word_count": 2, "translation": "city centre"},
            )
            assert create_resp.status_code == 201

            # Fetch transcript — match_spans should pick up the new collocation
            transcript_resp = await client.get("/api/srs/lesson/lesson-phrase/transcript")
            assert transcript_resp.status_code == 200

        data = transcript_resp.json()
        words = data["dialogue_lines"][0]["words"]

        # Find centru and mesta tokens
        centru = next((w for w in words if w["surface"] == "centru"), None)
        mesta = next((w for w in words if w["surface"] == "mesta"), None)

        assert centru is not None, "centru token not found"
        assert mesta is not None, "mesta token not found"
        assert centru["collocation_span_id"] is not None
        assert mesta["collocation_span_id"] is not None
        assert centru["collocation_span_id"] == mesta["collocation_span_id"]
        assert centru["collocation_lemma"] == "centru mesta"
