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

    async def test_list_curricula_returns_200(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/curriculum")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


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

    async def test_srs_feedback_returns_ok(self):
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(text="dober dan", translation="good day", word_count=2, difficulty=1, source="llm")
        db.add_collocation(unit)
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/feedback",
                json={"collocation_text": "dober dan", "signal": "no_help"},
            )
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

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


class TestAudioEndpoints:
    """Tests for audio render and retrieval endpoints."""

    async def test_audio_render_returns_202(self, tmp_path, monkeypatch):
        from app.storage.store import ContentStore

        mock_renderer = AsyncMock()
        mock_renderer.render = AsyncMock(side_effect=lambda lesson, path: path.write_bytes(b"audio"))

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
        # Verify audio file mapping was persisted
        assert store.get_audio_file(data["audio_id"]) is not None

    async def test_audio_get_returns_404_when_missing(self):
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/audio/nonexistent-id")
        assert response.status_code == 404
