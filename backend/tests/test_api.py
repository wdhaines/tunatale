"""API endpoint tests."""

from datetime import UTC, datetime, time
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

    async def test_get_lesson_includes_day(self):
        """GET /api/story/{id} exposes the curriculum day so the UI can regenerate it."""
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        mock_lesson = Lesson(title="Day 4", language_code="sl", sections=[])
        store.save_lesson("lesson-day4", "curriculum-1", 4, mock_lesson)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/story/lesson-day4")

        assert response.status_code == 200
        assert response.json()["day"] == 4

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

    async def test_listen_same_lemma_in_multiple_phrases_dedup_sentence(self):
        """When the same lemma appears in multiple NATURAL_SPEED phrases, only
        the first sentence is stored as source_sentence."""
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
                        Phrase(text="Kje je center?", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        # kje and je appear in both phrases, but only first sentence stored
        kje = db.get_collocation_by_lemma("kje")
        assert kje is not None
        assert kje.syntactic_unit.source_sentence == "Kje je banka?"

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
        db.set_enable_cloze_cards(True)
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

    async def test_listen_creates_collocations_with_source_llm_and_no_anki_link(self):
        """Layer 34 spec: /listen creates TT collocations with source='llm' and
        anki_note_id=NULL — guaranteeing the next sync_create_new picks them up
        and pushes them as proper Slovene Vocabulary notes (or links via guid if
        Anki already has them)."""
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
        db.set_enable_cloze_cards(True)
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # Inspect the persisted collocation
        with db._get_conn() as conn:
            row = conn.execute("SELECT text, source, anki_note_id FROM collocations WHERE text='dober dan'").fetchone()
        assert row is not None
        assert row["source"] == "llm"
        assert row["anki_note_id"] is None

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
        db.set_enable_cloze_cards(True)
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
        db.set_enable_cloze_cards(True)
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        # 2 cloze (kje, je) + 1 vocab (banka)
        assert data["registered"] == 3
        assert db.get_collocation_by_lemma("kje") is not None
        assert db.get_collocation_by_lemma("je") is not None
        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        assert banka.syntactic_unit.card_type == "vocab"

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
        db.set_enable_cloze_cards(True)
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        assert item.syntactic_unit.translation == "good day"

    async def test_listen_lemma_auto_filled_on_single_word_collocation(self):
        """Single-word collocations now auto-fill lemma = casefolded text,
        so get_collocation_by_lemma finds the pre-existing row and no duplicate is created."""
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
        # Pre-insert with text="banka" — lemma is auto-filled by add_collocation
        db.add_collocation(SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus"))
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        assert db.count_collocations() == 1
        # Lemma was auto-filled, so the pre-existing row is found
        assert db.get_collocation_by_lemma("banka") is not None

    async def test_listen_auto_added_cards_have_null_anki_note_id(self):
        """Auto-added key_phrase collocations have anki_note_id IS NULL and appear in list_items_without_anki_note."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="prosim kavo", translation="a coffee please"),
            ],
        )

        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        items_without_anki = db.list_items_without_anki_note()
        assert len(items_without_anki) == 2
        for _guid, item, _coll_id in items_without_anki:
            assert item.anki_note_id is None

    async def test_listen_key_phrases_state_is_new_on_first_listen(self):
        """Key phrases get state=NEW on first listen — no FSRS grade (soft exposure only)."""
        from app.models.srs_item import SRSState
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )

        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        assert item.state == SRSState.NEW
        assert item.reps == 0

    async def test_listen_key_phrases_do_not_duplicate_on_relisten(self):
        """Second listen with same key_phrases does not create duplicate collocations."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="prosim kavo", translation="a coffee please"),
            ],
        )

        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
            second = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert first.status_code == 200
        assert second.status_code == 200
        assert db.count_collocations() == 2

    async def test_listen_empty_word_ratings_does_not_crash(self):
        """Explicitly verify that word_ratings={} (as sent by the frontend) works."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )

        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/listen",
                json={"lesson_id": "lesson-1", "word_ratings": {}},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["registered"] == 1


class TestListenClozeIntegration:
    """Phase F: /listen as recognition-exposure event (Layer 1 redesign)."""

    async def _setup_lesson(
        self,
        phrase_text: str = "Kje je banka?",
        language_code: str = "sl",
        cloze_enabled: bool = True,
    ):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code=language_code,
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(
                            text=phrase_text,
                            voice_id="female-1",
                            language_code=language_code,
                            role="female-1",
                        ),
                    ],
                )
            ],
            key_phrases=[],
        )
        db = SRSDatabase(":memory:")
        if cloze_enabled:
            db.set_enable_cloze_cards(True)
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store
        return db

    async def test_listen_creates_cloze_card_when_enabled(self):
        """Created cloze rows have state='new', reps=0, introduced_at IS NULL."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.card_type == "cloze"
        prod = item_kje.directions[Direction.PRODUCTION]
        assert prod.state == SRSState.NEW
        assert prod.reps == 0
        assert prod.introduced_at is None

        item_je = db.get_collocation_by_lemma("je")
        assert item_je is not None
        assert item_je.syntactic_unit.card_type == "cloze"
        assert item_je.syntactic_unit.source_sentence == "Kje je banka?"

    async def test_listen_creates_vocab_for_unknown_content_word(self):
        """Unknown content-word lemma → vocab row, state='new', both directions present."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation_by_lemma("banka")
        assert item is not None
        assert item.syntactic_unit.card_type == "vocab"
        assert Direction.RECOGNITION in item.directions
        assert Direction.PRODUCTION in item.directions
        assert item.directions[Direction.RECOGNITION].state == SRSState.NEW
        assert item.directions[Direction.RECOGNITION].reps == 0

    async def test_listen_grades_recognition_when_learning(self):
        """Pre-existing vocab with recognition state=LEARNING → grade recognition, production unchanged."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        # Force banka's recognition to LEARNING
        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.LEARNING
        rec.reps = 1
        db.update_collocation(banka)

        prod_before = banka.directions.get(Direction.PRODUCTION)
        prod_reps_before = prod_before.reps if prod_before else 0

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        rec = banka.directions[Direction.RECOGNITION]
        assert rec.reps == 2  # graded
        assert rec.state != SRSState.NEW

        prod = banka.directions.get(Direction.PRODUCTION)
        if prod:
            assert prod.reps == prod_reps_before

    async def test_listen_grades_recognition_when_review_first_time_today(self):
        """Pre-existing vocab with rec state=REVIEW, last_review 2 days ago → graded."""
        from datetime import timedelta

        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(days=2)
        rec.reps = 5
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 6

    async def test_listen_skips_recognition_when_review_already_today(self):
        """Pre-existing vocab with rec state=REVIEW, last_review today → no grade."""

        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC)
        rec.reps = 5
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 5  # unchanged

    # ── Case-cloze tests ────────────────────────────────────────────────

    async def _setup_case_cloze_lesson(
        self,
        phrase_text: str = "Grem v Ljubljano. Sem v hotelu.",
        case_clozes_enabled: bool = True,
        extra_morphology: list[dict] | None = None,
    ):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        morphology_focus = [
            {"lemma": "ljubljana", "surface": "Ljubljano", "feature": "noun:acc:sg", "gloss": "Ljubljana"},
            {"lemma": "hotel", "surface": "hotelu", "feature": "noun:loc:sg", "gloss": "hotel"},
        ]
        if extra_morphology:
            morphology_focus.extend(extra_morphology)

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(
                            text=phrase_text,
                            voice_id="female-1",
                            language_code="sl",
                            role="female-1",
                        ),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {
                    "grem": "I go",
                    "v": "in/to",
                    "ljubljana": "Ljubljana",
                    "sem": "I am",
                    "hotel": "hotel",
                },
                "sentence_translations": {
                    phrase_text: "I'm going to Ljubljana. I'm at the hotel.",
                },
                "morphology_focus": morphology_focus,
            },
        )
        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        if case_clozes_enabled:
            db.set_enable_case_clozes(True)
        store = ContentStore(":memory:")
        store.save_lesson("case-cloze-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store
        return db

    async def test_listen_creates_case_cloze_when_enabled(self):
        """Inflected surface forms get morphology-cloze rows with disambig_key set."""
        db = await self._setup_case_cloze_lesson(case_clozes_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-1"})
        assert response.status_code == 200

        # Vocab row for the content word (created by main loop from lemmatized surface)
        item = db.get_collocation_by_lemma("ljubljano")
        assert item is not None
        assert item.syntactic_unit.card_type == "vocab"

        # Morphology-cloze row for the inflected surface "Ljubljano" (Acc, Sing)
        with db._get_conn() as conn:
            row = conn.execute(
                "SELECT text, lemma, disambig_key, card_type, source_sentence "
                "FROM collocations WHERE text = ? AND disambig_key = ?",
                ("Ljubljano", "morph:noun-acc-sg"),
            ).fetchone()
        assert row is not None, "Morphology-cloze row should exist"
        assert row["lemma"] == "ljubljana"
        assert row["card_type"] == "cloze"
        assert row["disambig_key"] == "morph:noun-acc-sg"
        assert row["source_sentence"] == "Grem v {{c1::Ljubljano::ljubljana, acc sg}}. Sem v hotelu."

    async def test_listen_respects_case_cloze_toggle(self):
        """Morphology-cloze creation is skipped when enable_case_clozes is False."""
        db = await self._setup_case_cloze_lesson(case_clozes_enabled=False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-1"})
        assert response.status_code == 200

        with db._get_conn() as conn:
            row = conn.execute(
                "SELECT text, disambig_key FROM collocations WHERE text = ? AND disambig_key = ?",
                ("Ljubljano", "morph:noun-acc-sg"),
            ).fetchone()
        assert row is None, "Morphology-cloze row should NOT exist when toggle is off"

    async def test_listen_case_cloze_is_idempotent(self):
        """Second /listen with same lesson does not duplicate morphology-cloze rows."""
        db = await self._setup_case_cloze_lesson(case_clozes_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            r1 = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-1"})
            r2 = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-1"})
        assert r1.status_code == 200
        assert r2.status_code == 200

        with db._get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS cnt FROM collocations WHERE text = ? AND disambig_key = ?",
                ("Ljubljano", "morph:noun-acc-sg"),
            ).fetchall()
        assert rows[0]["cnt"] == 1

    async def test_listen_analyzer_recall_adds_missed_morphology(self, monkeypatch):
        """Analyzer-driven recall catches a form the LLM's morphology_focus missed."""
        from app.srs.lemmatizer import TokenAnalysis
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_lemma("Grem", "iti")
        stub.set_lemma("Sem", "biti")
        stub.set_analysis("hotelu", "hotel", case="Loc", number="Sing", upos="NOUN", gender="Masc")
        stub.set_analysis("študenta", "študent", case="Acc", number="Sing", upos="NOUN", gender="Masc")
        stub.set_analysis("Ljubljano", "Ljubljana", upos="PROPN")
        stub.set_analysis("grem", "iti", number="Sing", person="1", upos="VERB")
        stub.set_sentence(
            "Grem k študenta v Ljubljano. Sem v hotelu. Brez kave.",
            [
                TokenAnalysis(surface="Grem", lemma="iti", upos="VERB", case="", number="Sing", person="1", gender=""),
                TokenAnalysis(surface="k", lemma="k", upos="", case="", number="", person="", gender=""),
                TokenAnalysis(
                    surface="študenta",
                    lemma="študent",
                    upos="NOUN",
                    case="Acc",
                    number="Sing",
                    person="",
                    gender="Masc",
                ),
                TokenAnalysis(surface="v", lemma="v", upos="", case="", number="", person="", gender=""),
                TokenAnalysis(
                    surface="Ljubljano",
                    lemma="Ljubljana",
                    upos="PROPN",
                    case="Acc",
                    number="Sing",
                    person="",
                    gender="Fem",
                ),
                TokenAnalysis(surface="Sem", lemma="biti", upos="AUX", case="", number="Sing", person="1", gender=""),
                TokenAnalysis(surface="v", lemma="v", upos="", case="", number="", person="", gender=""),
                TokenAnalysis(
                    surface="hotelu", lemma="hotel", upos="NOUN", case="Loc", number="Sing", person="", gender="Masc"
                ),
                TokenAnalysis(surface="Brez", lemma="brez", upos="ADP", case="", number="", person="", gender=""),
                # Genitive noun: non-degenerate (kave≠kava), not a function word, but
                # gen is A2+ → ud_feats_to_tt_feature returns None → recall must skip it.
                TokenAnalysis(
                    surface="kave", lemma="kava", upos="NOUN", case="Gen", number="Sing", person="", gender="Fem"
                ),
            ],
        )

        # In morphology_focus: Ljubljano, študenta. Not in it: hotelu (analyzer
        # recall must add it) and kave (recall must reject it as non-A1 genitive).
        phrase_text = "Grem k študenta v Ljubljano. Sem v hotelu. Brez kave."
        db = await self._setup_case_cloze_lesson(
            phrase_text=phrase_text,
            case_clozes_enabled=True,
            extra_morphology=[],
        )
        # Override morphology_focus to include Ljubljano and študenta
        from app.storage.store import ContentStore

        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("case-cloze-1")
        lesson.generation_metadata["morphology_focus"] = [
            {"lemma": "ljubljana", "surface": "Ljubljano", "feature": "noun:acc:sg", "gloss": "Ljubljana"},
            {"lemma": "študent", "surface": "študenta", "feature": "noun:acc:sg", "gloss": "student"},
        ]
        store.save_lesson("case-cloze-1", "curriculum-1", 1, lesson)

        monkeypatch.setattr("app.api.srs._lemmatizer", stub)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-1"})
        assert response.status_code == 200

        # Ljubljano should have a morphology-cloze row (from LLM)
        with db._get_conn() as conn:
            ljubljano_row = conn.execute(
                "SELECT text, lemma, disambig_key FROM collocations WHERE text = ? AND disambig_key LIKE ?",
                ("Ljubljano", "morph:noun-acc-sg"),
            ).fetchone()
        assert ljubljano_row is not None, "Ljubljano should have a morphology-cloze row (from LLM)"

        # hotelu should also have a morphology-cloze row (from analyzer recall)
        with db._get_conn() as conn:
            hotelu_row = conn.execute(
                "SELECT text, lemma, disambig_key FROM collocations WHERE text = ? AND disambig_key LIKE ?",
                ("hotelu", "morph:noun-loc-sg"),
            ).fetchone()
        assert hotelu_row is not None, "hotelu should have a morphology-cloze row (from analyzer recall)"

        # študenta should have a morphology-cloze row (from morphology_focus directly)
        with db._get_conn() as conn:
            student_row = conn.execute(
                "SELECT text, lemma, disambig_key FROM collocations WHERE text = ? AND disambig_key LIKE ?",
                ("študenta", "morph:noun-acc-sg"),
            ).fetchone()
        assert student_row is not None, "študenta should have a morphology-cloze row (from LLM)"

        # kave (genitive singular) is A2+ morphology — recall must NOT create a
        # morphology cloze for it, even though it's non-degenerate and not a function word.
        with db._get_conn() as conn:
            kave_row = conn.execute(
                "SELECT 1 FROM collocations WHERE text = ? AND disambig_key LIKE 'morph:%'",
                ("kave",),
            ).fetchone()
        assert kave_row is None, "genitive 'kave' (A2+) must not get a morphology cloze"

    async def test_listen_analyzer_recall_skips_mismatched_language(self, monkeypatch):
        """Phrase with different language_code in NATURAL_SPEED is skipped in recall."""
        from app.models.lesson import Phrase
        from app.srs.lemmatizer import TokenAnalysis
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_sentence(
            "Grem v Ljubljano. Sem v hotelu.",
            [
                TokenAnalysis(surface="Grem", lemma="grem", upos="", case="", number="", person="", gender=""),
                TokenAnalysis(surface="v", lemma="v", upos="", case="", number="", person="", gender=""),
                TokenAnalysis(
                    surface="Ljubljano", lemma="ljubljano", upos="", case="", number="", person="", gender=""
                ),
                TokenAnalysis(surface="Sem", lemma="sem", upos="", case="", number="", person="", gender=""),
                TokenAnalysis(surface="v", lemma="v", upos="", case="", number="", person="", gender=""),
                TokenAnalysis(surface="hotelu", lemma="hotelu", upos="", case="", number="", person="", gender=""),
            ],
        )

        await self._setup_case_cloze_lesson(case_clozes_enabled=True)
        # Add a phrase with mismatched language_code to the 1st NATURAL_SPEED section
        from app.storage.store import ContentStore

        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("case-cloze-1")
        natural = next(s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED)
        natural.phrases.append(
            Phrase(text="Hello world.", voice_id="eng-1", language_code="en", role="eng-1"),
        )
        store.save_lesson("case-cloze-1", "curriculum-1", 1, lesson)

        monkeypatch.setattr("app.api.srs._lemmatizer", stub)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-1"})
        assert response.status_code == 200

    async def test_listen_case_cloze_skips_degenerate_lemma_equals_surface(self):
        """No morphology-cloze when generator reports lemma == surface (hint would reveal answer)."""
        db = await self._setup_case_cloze_lesson(
            phrase_text="Imam plačilno kartico.",
            case_clozes_enabled=True,
            extra_morphology=[
                {"lemma": "kartico", "surface": "kartico", "feature": "noun:acc:sg", "gloss": "card"},
            ],
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-1"})
        assert response.status_code == 200

        with db._get_conn() as conn:
            row = conn.execute(
                "SELECT text FROM collocations WHERE text = ? AND disambig_key = ?",
                ("kartico", "morph:noun-acc-sg"),
            ).fetchone()
        assert row is None, "Degenerate lemma==surface entry must not create a morphology-cloze"

    async def test_listen_case_cloze_respects_cap(self):
        """At most max_morph_clozes (5) morphology-cloze rows are created per /listen."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        phrase = "Grem v Ljubljano, Avstrijo, trgovino, kavarno, postajo in restavracijo."
        # Six non-degenerate accusatives (lemma != surface) → cap should clip to 5.
        morphology = [
            {"lemma": "ljubljana", "surface": "Ljubljano", "feature": "noun:acc:sg", "gloss": "Ljubljana"},
            {"lemma": "avstrija", "surface": "Avstrijo", "feature": "noun:acc:sg", "gloss": "Austria"},
            {"lemma": "trgovina", "surface": "trgovino", "feature": "noun:acc:sg", "gloss": "store"},
            {"lemma": "kavarna", "surface": "kavarno", "feature": "noun:acc:sg", "gloss": "cafe"},
            {"lemma": "postaja", "surface": "postajo", "feature": "noun:acc:sg", "gloss": "station"},
            {
                "lemma": "restavracija",
                "surface": "restavracijo",
                "feature": "noun:acc:sg",
                "gloss": "restaurant",
            },
        ]
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text=phrase, voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {},
                "sentence_translations": {},
                "morphology_focus": morphology,
            },
        )
        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        db.set_enable_case_clozes(True)
        store = ContentStore(":memory:")
        store.save_lesson("case-cloze-cap", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-cap"})
        assert response.status_code == 200

        with db._get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS cnt FROM collocations WHERE card_type = 'cloze' AND disambig_key LIKE 'morph:%'",
            ).fetchall()
        assert rows[0]["cnt"] == 5, f"Expected 5 morphology-cloze rows, got {rows[0]['cnt']}"

    async def test_listen_case_cloze_includes_verb_conjugation_of_function_word_lemma(self):
        """Verb conjugations are drilled even when surface or lemma is in the function-word set.

        `je`/`sem`/`si` are in the curated function-word list (they're high-frequency forms of
        `biti`), but conjugation drills are exactly what we want — so the function-word filter
        is intentionally not applied to morphology clozes.
        """
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        phrase = "On je tu. Jaz sem doma."
        morphology = [
            {"lemma": "biti", "surface": "je", "feature": "verb:3sg", "gloss": "is"},
            {"lemma": "biti", "surface": "sem", "feature": "verb:1sg", "gloss": "am"},
        ]
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text=phrase, voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {"biti": "to be"},
                "sentence_translations": {phrase: "He is here. I am at home."},
                "morphology_focus": morphology,
            },
        )
        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        db.set_enable_case_clozes(True)
        store = ContentStore(":memory:")
        store.save_lesson("case-cloze-verb", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-verb"})
        assert response.status_code == 200

        with db._get_conn() as conn:
            rows = conn.execute(
                "SELECT text, disambig_key FROM collocations "
                "WHERE card_type = 'cloze' AND disambig_key LIKE 'morph:verb-%' ORDER BY text",
            ).fetchall()
        assert len(rows) == 2, f"Expected 2 verb morphology-clozes, got {len(rows)}: {[dict(r) for r in rows]}"
        assert {r["text"] for r in rows} == {"je", "sem"}
        assert {r["disambig_key"] for r in rows} == {"morph:verb-3sg", "morph:verb-1sg"}

    async def test_listen_case_cloze_skips_surface_not_found(self):
        """morphology_focus entry whose surface isn't in any NATURAL_SPEED line is skipped."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        phrase = "Grem v Ljubljano s prijateljem."
        morphology = [
            {"lemma": "miza", "surface": "mize", "feature": "noun:acc:sg", "gloss": "table"},
        ]
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text=phrase, voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {},
                "sentence_translations": {},
                "morphology_focus": morphology,
            },
        )
        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        db.set_enable_case_clozes(True)
        store = ContentStore(":memory:")
        store.save_lesson("case-cloze-nf", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-nf"})
        assert response.status_code == 200

        with db._get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS cnt FROM collocations WHERE card_type = 'cloze' AND disambig_key LIKE 'morph:%'",
            ).fetchall()
        assert rows[0]["cnt"] == 0, "No morphology-cloze should be created when surface is missing"

    async def test_listen_case_cloze_skips_non_a1_features(self):
        """Features outside the A1 whitelist (gen/dat/ins, adj non-nom) are skipped."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        phrase = "Nimam časa za prijatelje s knjigami."
        morphology = [
            {"lemma": "čas", "surface": "časa", "feature": "noun:gen:sg", "gloss": "time"},
            {"lemma": "knjiga", "surface": "knjigami", "feature": "noun:ins:pl", "gloss": "books"},
            {"lemma": "prijatelj", "surface": "prijatelje", "feature": "noun:acc:pl", "gloss": "friends"},
        ]
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text=phrase, voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {},
                "sentence_translations": {},
                "morphology_focus": morphology,
            },
        )
        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        db.set_enable_case_clozes(True)
        store = ContentStore(":memory:")
        store.save_lesson("case-cloze-nona1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-nona1"})
        assert response.status_code == 200

        with db._get_conn() as conn:
            rows = conn.execute(
                "SELECT text, disambig_key FROM collocations "
                "WHERE card_type = 'cloze' AND disambig_key LIKE 'morph:%' ORDER BY text",
            ).fetchall()
        # Only the noun:acc:pl entry should pass the A1 whitelist.
        assert [dict(r) for r in rows] == [{"text": "prijatelje", "disambig_key": "morph:noun-acc-pl"}]

    async def test_listen_case_cloze_skips_empty_surface(self):
        """morphology_focus entry with empty surface/lemma is skipped."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        phrase = "Grem v Ljubljano. Sem v hotelu."
        morphology = [
            {"lemma": "ljubljana", "surface": "Ljubljano", "feature": "noun:acc:sg", "gloss": "Ljubljana"},
            {"lemma": "hotel", "surface": "hotelu", "feature": "noun:loc:sg", "gloss": "hotel"},
            {"lemma": "miza", "surface": "", "feature": "noun:acc:sg", "gloss": "table"},
            {"lemma": "", "surface": "kavo", "feature": "noun:acc:sg", "gloss": "coffee"},
        ]
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text=phrase, voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {},
                "sentence_translations": {},
                "morphology_focus": morphology,
            },
        )
        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        db.set_enable_case_clozes(True)
        store = ContentStore(":memory:")
        store.save_lesson("case-cloze-emtpy", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-emtpy"})
        assert response.status_code == 200

        with db._get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS cnt FROM collocations WHERE card_type = 'cloze' AND disambig_key LIKE 'morph:%'",
            ).fetchall()
        assert rows[0]["cnt"] == 2, "Only the two complete entries should create morphology-cloze rows"

    async def test_listen_tolerates_malformed_morphology_focus_entries(self):
        """Malformed morphology_focus entries must not 500 /listen (regression).

        The morphology_focus loop runs on every /listen for any lesson carrying the
        metadata — independent of the case-clozes toggle — so a non-dict entry, a
        null ``feature``, or a non-string ``feature`` (all reachable now that the
        model-agnostic parser accepts looser non-JSON-mode model output) would
        otherwise raise AttributeError. Valid entries beside the junk still create
        their rows.
        """
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        phrase = "Grem v Ljubljano. Sem v hotelu."
        morphology = [
            {"lemma": "ljubljana", "surface": "Ljubljano", "feature": "noun:acc:sg", "gloss": "Ljubljana"},
            {"lemma": "hotel", "surface": "hotelu", "feature": None, "gloss": "hotel"},  # null feature
            "not-a-dict",  # non-dict entry
            {"lemma": "x", "surface": "y", "feature": ["noun:acc:sg"]},  # non-string feature
        ]
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text=phrase, voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {},
                "sentence_translations": {},
                "morphology_focus": morphology,
            },
        )
        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        db.set_enable_case_clozes(True)
        store = ContentStore(":memory:")
        store.save_lesson("case-cloze-malformed", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-malformed"})
        assert response.status_code == 200

        with db._get_conn() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) AS cnt FROM collocations WHERE card_type = 'cloze' AND disambig_key LIKE 'morph:%'",
            ).fetchone()["cnt"]
        assert cnt == 1, "Only the single well-formed entry should create a morphology-cloze row"

    async def test_listen_tolerates_non_list_morphology_focus(self):
        """A non-list morphology_focus (model emits a bare string/dict) is ignored, not iterated char-by-char."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Grem v Ljubljano.", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {},
                "sentence_translations": {},
                "morphology_focus": "noun:acc:sg",  # not a list
            },
        )
        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        db.set_enable_case_clozes(True)
        store = ContentStore(":memory:")
        store.save_lesson("case-cloze-nonlist", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-nonlist"})
        assert response.status_code == 200

        with db._get_conn() as conn:
            cnt = conn.execute(
                "SELECT COUNT(*) AS cnt FROM collocations WHERE card_type = 'cloze' AND disambig_key LIKE 'morph:%'",
            ).fetchone()["cnt"]
        assert cnt == 0, "A non-list morphology_focus produces no morphology-cloze rows"

    async def test_listen_case_cloze_noop_without_natural_speed(self):
        """No NATURAL_SPEED section → surface_to_analysis stays empty → no morphology-clozes."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.TRANSLATED,
                    phrases=[
                        Phrase(text="Some text", voice_id="narrator", language_code="en", role="narrator"),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {},
                "sentence_translations": {},
                "morphology_focus": [
                    {"lemma": "miza", "surface": "mize", "feature": "noun:acc:sg", "gloss": "table"},
                ],
            },
        )
        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        db.set_enable_case_clozes(True)
        store = ContentStore(":memory:")
        store.save_lesson("case-cloze-no-ns", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "case-cloze-no-ns"})
        assert response.status_code == 200

        with db._get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS cnt FROM collocations WHERE card_type = 'cloze' AND disambig_key LIKE 'morph:%'",
            ).fetchall()
        assert rows[0]["cnt"] == 0, "No morphology-cloze should be created without NATURAL_SPEED"

    async def test_listen_never_grades_production(self):
        """Pre-existing vocab with production state=LEARNING → /listen does not touch production."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        if Direction.PRODUCTION in banka.directions:
            prod = banka.directions[Direction.PRODUCTION]
            prod.state = SRSState.LEARNING
            prod.reps = 3
            db.update_collocation(banka)  # saves RECOGNITION, not production

            # Directly update production in DB
            db.update_direction(banka.guid, Direction.PRODUCTION, prod)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        prod = banka.directions.get(Direction.PRODUCTION)
        if prod:
            assert prod.reps == 3  # unchanged
            assert prod.state == SRSState.LEARNING  # unchanged

    async def test_listen_never_grades_cloze(self):
        """Pre-existing cloze row → /listen never grades it."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        kje = db.get_collocation_by_lemma("kje")
        assert kje is not None
        assert kje.syntactic_unit.card_type == "cloze"
        prod = kje.directions[Direction.PRODUCTION]
        prod.state = SRSState.LEARNING
        prod.reps = 1
        db.update_direction(kje.guid, Direction.PRODUCTION, prod)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        kje = db.get_collocation_by_lemma("kje")
        prod = kje.directions[Direction.PRODUCTION]
        assert prod.reps == 1  # unchanged
        assert prod.state == SRSState.LEARNING  # unchanged

    async def test_listen_skips_existing_new_state(self):
        """Pre-existing vocab, recognition state=NEW → no grade."""
        from app.models.srs_item import Direction

        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        # Already state=NEW after creation
        rec_reps_before = banka.directions[Direction.RECOGNITION].reps

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == rec_reps_before

    async def test_listen_skips_function_word_when_flag_off(self):
        """Function-word lemma + cloze flag off → skip. Content words still created."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson(cloze_enabled=False)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # Function words NOT created
        assert db.get_collocation_by_lemma("kje") is None
        assert db.get_collocation_by_lemma("je") is None

        # Content word "banka" IS created as vocab
        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        assert banka.syntactic_unit.card_type == "vocab"
        assert banka.directions[Direction.RECOGNITION].state == SRSState.NEW

    async def test_listen_skips_cloze_and_non_slovene_content_still_created(self):
        """Non-Slovene lesson with cloze_enabled=False: content words created as vocab."""

        db = await self._setup_lesson(
            phrase_text="Where is the bank?",
            language_code="en",
            cloze_enabled=False,
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # English words are all "content" (no function-word list for en) → created as vocab
        for lemma in ("where", "is", "the", "bank"):
            item = db.get_collocation_by_lemma(lemma)
            assert item is not None, f"{lemma} should be created as vocab"
            assert item.syntactic_unit.card_type == "vocab"
        # Cloze card type should not appear
        assert not any(
            db.get_collocation_by_lemma(lemma).syntactic_unit.card_type == "cloze"
            for lemma in ("where", "is", "the", "bank")
        )

    async def test_listen_cloze_returns_card_type_and_source_sentence_via_api(self):
        """Cloze items expose card_type and source_sentence via the items API."""
        await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

            response = await client.get("/api/srs/items", params={"limit": 50})
        assert response.status_code == 200
        items = {i["text"]: i for i in response.json()["items"]}

        kje = items.get("kje")
        assert kje is not None
        assert kje["card_type"] == "cloze"
        assert kje["source_sentence"] == "Kje je banka?"

        banka = items.get("banka")
        assert banka is not None
        assert banka["card_type"] == "vocab"

    async def test_listen_cloze_response_state_reflects_production_direction(self):
        """`_item_to_dict` for cloze items reads state from PRODUCTION."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

            item = db.get_collocation_by_lemma("kje")
            assert item is not None
            item.directions[Direction.PRODUCTION].state = SRSState.LEARNING
            item.directions[Direction.PRODUCTION].reps = 2
            db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

            response = await client.get("/api/srs/items", params={"limit": 50})
        assert response.status_code == 200
        items = {i["text"]: i for i in response.json()["items"]}
        kje = items["kje"]
        assert kje["state"] == "learning"
        assert kje["reps"] == 2

    async def test_listen_with_flag_on_still_registers_key_phrases(self):
        from app.storage.store import ContentStore

        db = await self._setup_lesson(cloze_enabled=True)
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        assert lesson is not None
        lesson.key_phrases = [
            KeyPhraseInfo(phrase="dober dan", translation="good day"),
            KeyPhraseInfo(phrase="hvala lepa", translation="thank you very much"),
        ]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        kp1 = db.get_collocation("dober dan")
        assert kp1 is not None
        kp2 = db.get_collocation("hvala lepa")
        assert kp2 is not None

        assert db.get_collocation_by_lemma("kje") is not None
        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        assert banka.syntactic_unit.card_type == "vocab"

    # ── Key-phrase auto-grade tests ──────────────────────────────────────

    async def test_listen_grades_key_phrase_when_recognition_learning(self):
        """Pre-existing KP with rec state=LEARNING → reps incremented, production untouched."""
        from app.models.srs_item import Direction, SRSState
        from app.storage.store import ContentStore

        db = await self._setup_lesson(cloze_enabled=True)
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.LEARNING
        rec.reps = 1
        db.update_collocation(item)

        prod_before = item.directions.get(Direction.PRODUCTION)
        prod_reps_before = prod_before.reps if prod_before else 0

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        rec = item.directions[Direction.RECOGNITION]
        assert rec.reps == 2  # graded
        assert rec.state != SRSState.NEW

        prod = item.directions.get(Direction.PRODUCTION)
        if prod:
            assert prod.reps == prod_reps_before

    async def test_listen_grades_key_phrase_when_review_first_time_today(self):
        """Pre-existing KP with rec state=REVIEW, last_review 2 days ago → graded (reps+1)."""
        from datetime import timedelta

        from app.models.srs_item import Direction, SRSState
        from app.storage.store import ContentStore

        db = await self._setup_lesson(cloze_enabled=True)
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(days=2)
        rec.reps = 5
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.directions[Direction.RECOGNITION].reps == 6

    async def test_listen_skips_key_phrase_when_review_already_today(self):
        """Pre-existing KP with rec state=REVIEW, last_review today → no grade."""

        from app.models.srs_item import Direction, SRSState
        from app.storage.store import ContentStore

        db = await self._setup_lesson(cloze_enabled=True)
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC)
        rec.reps = 5
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.directions[Direction.RECOGNITION].reps == 5  # unchanged

    async def test_listen_skips_key_phrase_when_recognition_new(self):
        """Pre-existing KP with rec state=NEW → no grade."""
        from app.models.srs_item import Direction
        from app.storage.store import ContentStore

        db = await self._setup_lesson(cloze_enabled=True)
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec_reps_before = item.directions[Direction.RECOGNITION].reps

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.directions[Direction.RECOGNITION].reps == rec_reps_before  # unchanged

    async def test_listen_skips_key_phrase_when_recognition_known(self):
        """Pre-existing KP with rec state=KNOWN → no grade."""
        from app.models.srs_item import Direction, SRSState
        from app.storage.store import ContentStore

        db = await self._setup_lesson(cloze_enabled=True)
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.KNOWN
        rec.reps = 3
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.directions[Direction.RECOGNITION].reps == 3  # unchanged

    async def test_listen_creates_cloze_with_sentence_translation_from_metadata(self):
        """New cloze uses sentence_translations from lesson.generation_metadata."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson(cloze_enabled=True)
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.generation_metadata = {"sentence_translations": {"Kje je banka?": "Where is the bank?"}}
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.source_sentence_translation == "Where is the bank?"

    async def test_listen_creates_cloze_with_sentence_translation_from_translated_section(self):
        """When metadata is missing, /listen falls back to deriving from TRANSLATED section."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson(cloze_enabled=True)
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        # No sentence_translations in metadata; emulate pre-Layer-N stored lesson
        lesson.generation_metadata = {}
        lesson.sections.append(
            Section(
                section_type=SectionType.TRANSLATED,
                phrases=[
                    Phrase(text="Kje je banka?", voice_id="v", language_code="sl"),
                    Phrase(text="Where is the bank?", voice_id="v", language_code="en"),
                ],
            )
        )
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.source_sentence_translation == "Where is the bank?"

    async def test_listen_backfills_existing_cloze_sentence_translation(self):
        """Existing cloze with empty sentence_translation gets populated and marked dirty."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.source_sentence_translation == ""

        # Simulate "card already in Anki" so sync_push will see it: stamp anki_note_id.
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET anki_note_id = 9999 WHERE guid = ?", (item_kje.guid,))
            conn.commit()

        # Re-load lesson with TRANSLATED section so /listen has source data
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.generation_metadata = {"sentence_translations": {"Kje je banka?": "Where is the bank?"}}
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje.syntactic_unit.source_sentence_translation == "Where is the bank?"

        dirty = db.get_dirty_fields(item_kje.guid)
        assert "sentence_translation" in dirty.split(",")

    async def test_listen_skips_key_phrase_when_cloze(self):
        """Key phrase existing as cloze (defensive) → skip, no crash."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson(cloze_enabled=True)
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        # Manually change card_type to cloze
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET card_type = 'cloze' WHERE text = 'dober dan'")
            conn.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.syntactic_unit.card_type == "cloze"

    async def test_listen_skips_key_phrase_without_recognition_direction(self):
        """Key phrase existing without RECOGNITION direction (defensive) → skip, no crash."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson(cloze_enabled=True)
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        # Delete recognition direction
        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE collocation_id = (SELECT id FROM collocations WHERE text = 'dober dan') AND direction = 'recognition'"
            )
            conn.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

    async def test_listen_skips_existing_cloze_with_populated_sentence_translation(self):
        """Existing cloze already has sentence_translation → no re-backfill, dirty_fields unchanged."""
        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        # Manually set sentence_translation to simulate already-backfilled state
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET sentence_translation = 'already set' WHERE text = 'kje'")
            conn.commit()
        # Clear dirty fields to verify they aren't re-marked
        item = db.get_collocation_by_lemma("kje")
        db.set_dirty_fields(item.guid, "")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation_by_lemma("kje")
        assert item.syntactic_unit.source_sentence_translation == "already set"
        dirty = db.get_dirty_fields(item.guid)
        assert dirty == ""  # not re-marked

    # ── Edge cases for _listen_grade_eligible (lines 228, 233, 236, 238) ──

    async def test_listen_skips_known_state(self):
        """Pre-existing vocab, recognition state=KNOWN → skip (no grade)."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.KNOWN
        rec.reps = 3
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 3

    async def test_listen_grades_review_with_no_last_review(self):
        """REVIEW state with last_review=None → eligible for grade (line 233)."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = None
        rec.reps = 5
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 6

    async def test_listen_grades_review_with_suspended_state(self):
        """SUSPENDED state → skip (not eligible for grade), covers fallthrough at line 238."""
        from app.models.srs_item import Direction, SRSState

        db = await self._setup_lesson(cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.SUSPENDED
        rec.reps = 3
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 3

    async def test_listen_skips_vocab_without_recognition_direction(self):
        """Line 314 via /listen: vocab card without RECOGNITION direction → skip (no crash)."""

        db = await self._setup_lesson(phrase_text="testword", cloze_enabled=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE collocation_id = (SELECT id FROM collocations WHERE lemma = 'testword') AND direction = 'recognition'",
            )
            conn.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200


class TestListenGradeEligible:
    """Direct unit tests for _listen_grade_eligible edge cases."""

    def test_rec_is_none_returns_false(self):

        from app.api.srs import _listen_grade_eligible

        assert _listen_grade_eligible(None, datetime.now(UTC), datetime.now(UTC)) is False

    def test_legacy_date_last_review_returns_true(self):
        from datetime import date, timedelta

        from app.api.srs import _listen_grade_eligible
        from app.models.srs_item import Direction, DirectionState, SRSState

        rec = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            state=SRSState.REVIEW,
            reps=5,
            last_review=date(2026, 5, 1),  # legacy date, not datetime
        )
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        assert _listen_grade_eligible(rec, today_start, today_end) is True

    def test_non_cloze_without_recognition_skipped(self):
        """Line 314: existing item is vocab but has no RECOGNITION direction → skip (no crash)."""

        from app.models.srs_item import Direction
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(
            text="testword",
            translation="",
            word_count=1,
            difficulty=1,
            source="test",
            lemma="testword",
            card_type="vocab",
        )
        db.add_collocation(unit, language_code="sl")
        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE collocation_id = (SELECT id FROM collocations WHERE lemma = 'testword') AND direction = 'recognition'"
            )
            conn.commit()

        item = db.get_collocation_by_lemma("testword")
        assert item is not None
        assert Direction.RECOGNITION not in item.directions
        assert item.syntactic_unit.card_type == "vocab"

        from datetime import timedelta

        from app.api.srs import _listen_grade_eligible

        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        rec = item.directions.get(Direction.RECOGNITION)
        assert _listen_grade_eligible(rec, today_start, today_end) is False


class TestClozeSetting:
    """Tests for GET/PUT /api/srs/settings/cloze."""

    async def test_get_cloze_setting_defaults_false(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/settings/cloze")

        assert response.status_code == 200
        assert response.json() == {"enabled": False}

    async def test_put_cloze_setting_enables(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put(
                "/api/srs/settings/cloze",
                json={"enabled": True},
            )

            assert response.status_code == 200
            assert response.json() == {"enabled": True}

            # Subsequent GET returns True
            get_response = await client.get("/api/srs/settings/cloze")
            assert get_response.json() == {"enabled": True}

    async def test_put_cloze_setting_validates_body(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put(
                "/api/srs/settings/cloze",
                json={},
            )

        assert response.status_code == 422


class TestCaseClozeSetting:
    """Tests for GET/PUT /api/srs/settings/case-clozes."""

    async def test_get_case_cloze_setting_defaults_false(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/settings/case-clozes")

        assert response.status_code == 200
        assert response.json() == {"enabled": False}

    async def test_put_case_cloze_setting_enables(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put(
                "/api/srs/settings/case-clozes",
                json={"enabled": True},
            )

            assert response.status_code == 200
            assert response.json() == {"enabled": True}

            get_response = await client.get("/api/srs/settings/case-clozes")
            assert get_response.json() == {"enabled": True}

    async def test_put_case_cloze_setting_validates_body(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.put(
                "/api/srs/settings/case-clozes",
                json={},
            )

        assert response.status_code == 422


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

    async def test_queue_stats_review_uses_tt_distinct_collocation_count(self):
        """Review badge is driven by TT's distinct-collocation count
        (sibling-buried equivalent of Anki's COUNT(DISTINCT nid))."""
        from unittest.mock import patch

        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        with (
            patch("app.api.srs.resolve_daily_new_cap", return_value=(20, "default")),
            patch.object(db, "count_review_due_collocations", return_value=42),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/srs/queue-stats")

        data = response.json()
        assert data["review"] == 42


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
        assert store.get_audio_file_row(data["audio_id"]) is not None

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


class TestClozeTTSIntegration:
    """Tests for cloze TTS audio generation via /listen and /review-queue."""

    async def test_listen_creates_media_for_new_cloze(self, monkeypatch):
        """New cloze from /listen gets both audio_tts_sentence and audio_tts media rows."""
        import app.audio.cloze_tts as cloze_tts_mod

        async def _fake_tts(text, voice="sl-SI-PetraNeural"):
            return b"fake-mp3"

        monkeypatch.setattr(cloze_tts_mod, "generate_tts_audio", _fake_tts)

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
                        Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        store = ContentStore(":memory:")
        store.save_lesson("lesson-ct", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ct"})
        assert response.status_code == 200

        for lemma in ("kje", "je"):
            coll = db.get_collocation_by_lemma_with_id(lemma)
            assert coll is not None, f"{lemma} should exist"
            coll_id, _ = coll
            sent_fn = db.get_sentence_audio_filename(coll_id)
            word_fn = db.get_audio_filename(coll_id)
            assert sent_fn is not None, f"{lemma} missing sentence audio filename"
            assert word_fn is not None, f"{lemma} missing word audio filename"
            assert sent_fn.startswith("tts_sentence_")
            assert word_fn.startswith("tts_")

    async def test_review_queue_returns_word_audio_url_for_cloze(self):
        """Cloze cards in the review queue have word_audio_url set; vocab cards do not."""
        from datetime import date

        from app.models.srs_item import Direction, DirectionState, SRSState
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        app.state.srs_db = db

        # Cloze collocation
        cloze_unit = SyntacticUnit(
            text="je",
            translation="is",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="je",
            card_type="cloze",
            source_sentence="Kje je banka?",
        )
        cloze_dir = {
            Direction.PRODUCTION: DirectionState(
                Direction.PRODUCTION,
                date.today(),
                state=SRSState.NEW,
            )
        }
        cloze_id = db.upsert_by_guid(cloze_unit, "sl", cloze_dir)

        db.add_media(cloze_id, "audio_tts_sentence", "tts_sentence_abc.mp3", "/tmp/s.mp3", "", "s1", 100)
        db.add_media(cloze_id, "audio_tts", "tts_je.mp3", "/tmp/w.mp3", "", "w1", 100)

        # Vocab collocation
        vocab_unit = SyntacticUnit(
            text="banka",
            translation="bank",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="banka",
        )
        dirs = {
            Direction.RECOGNITION: DirectionState(Direction.RECOGNITION, date.today(), state=SRSState.NEW),
            Direction.PRODUCTION: DirectionState(Direction.PRODUCTION, date.today(), state=SRSState.NEW),
        }
        vocab_id = db.upsert_by_guid(vocab_unit, "sl", dirs)
        db.add_media(vocab_id, "audio_tts", "tts_banka.mp3", "/tmp/w.mp3", "", "w2", 100)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/review-queue", params={"session_start": "1"})
        assert response.status_code == 200

        items = response.json()["queue"]
        cloze_items = [i for i in items if i.get("card_type") == "cloze"]
        vocab_items = [i for i in items if i.get("card_type") == "vocab"]

        assert len(cloze_items) >= 1
        assert len(vocab_items) >= 1

        for ci in cloze_items:
            assert ci.get("word_audio_url") is not None, f"cloze {ci['text']} missing word_audio_url"
            assert ci.get("audio_url") is not None, f"cloze {ci['text']} missing audio_url"

        for vi in vocab_items:
            assert vi.get("word_audio_url") is None, f"vocab {vi['text']} should not have word_audio_url"
            assert vi.get("audio_url") is not None, f"vocab {vi['text']} missing audio_url"

    async def test_listen_tolerates_synthesizer_error_new_cloze(self, monkeypatch):
        """New cloze card (function-word and case-cloze) is created even if TTS fails."""
        import app.api.srs as srs_mod

        async def _broken_synth(db, collocation_id, sentence, word):
            raise RuntimeError("TTS failed")

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", _broken_synth)

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
                            text="Kje je banka v Ljubljani?", voice_id="female-1", language_code="sl", role="female-1"
                        ),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {
                    "kje": "where",
                    "je": "is",
                    "banka": "bank",
                    "v": "in",
                    "ljubljana": "Ljubljana",
                },
                "sentence_translations": {
                    "Kje je banka v Ljubljani?": "Where is the bank in Ljubljana?",
                },
                "morphology_focus": [
                    {
                        "lemma": "ljubljana",
                        "surface": "Ljubljani",
                        "feature": "noun:loc:sg",
                        "gloss": "Ljubljana",
                    },
                ],
            },
        )

        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        db.set_enable_case_clozes(True)

        store = ContentStore(":memory:")
        store.save_lesson("lesson-ct2", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ct2"})
        assert response.status_code == 200

        # Function-word cloze card should still exist
        coll = db.get_collocation_by_lemma("kje")
        assert coll is not None
        # Morphology-cloze row should also exist despite TTS failure
        with db._get_conn() as conn:
            row = conn.execute(
                "SELECT text, disambig_key FROM collocations WHERE text = ? AND disambig_key = ?",
                ("Ljubljani", "morph:noun-loc-sg"),
            ).fetchone()
        assert row is not None, "Morphology-cloze row should exist despite TTS failure"

    async def test_listen_tolerates_synthesizer_error_existing_cloze(self, monkeypatch):
        """Existing cloze card audio backfill failure doesn't crash the endpoint."""
        import app.api.srs as srs_mod

        calls = [0]

        async def _succeed_once_then_fail(db, collocation_id, sentence, word):
            calls[0] += 1
            if calls[0] > 1:
                raise RuntimeError("TTS failed on second call")
            return None

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", _succeed_once_then_fail)

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
                        Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        db.set_enable_cloze_cards(True)
        store = ContentStore(":memory:")
        store.save_lesson("lesson-ct3", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        # First listen creates cloze cards and succeeds at TTS
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ct3"})
        assert response.status_code == 200
        assert calls[0] >= 1  # at least one TTS call succeeded

        # Second listen should hit the existing cloze backfill path with failure
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-ct3"})
        assert response.status_code == 200

        # Cloze card should still exist
        coll = db.get_collocation_by_lemma("kje")
        assert coll is not None
