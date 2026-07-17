"""Tests for story endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from app.languages import get_language
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


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

    async def test_generate_story_invalid_strategy_422(self):
        """An unknown strategy must be a validation error, not a KeyError 500."""
        from app.storage.store import ContentStore

        app.state.content_store = ContentStore(":memory:")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "c1", "day": 1, "strategy": "SIDEWAYS"},
            )
        assert response.status_code == 422

    async def test_generate_story_llm_failure_502(self):
        """Malformed LLM output (StoryGenerationError) maps to 502, mirroring
        how plan_turn maps PlannerError — never a raw 500 traceback."""
        from app.generation.story import StoryGenerationError
        from app.storage.store import ContentStore

        mock_generator = AsyncMock()
        mock_generator.generate = AsyncMock(side_effect=StoryGenerationError("LLM returned invalid JSON"))

        store = ContentStore(":memory:")
        store.save_curriculum(
            "c1",
            Curriculum(
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
            ),
        )
        app.state.content_store = store
        app.state.story_generator = mock_generator
        app.state.language = get_language("sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "c1", "day": 1, "strategy": "WIDER"},
            )
        assert response.status_code == 502
        assert "invalid JSON" in response.json()["detail"]

    async def test_generate_story_llm_error_502(self):
        """A raw LLMError (opt-in fallback: complete() no longer rescues a 429 via
        Ollama) must map to 502 with the error detail, not escape as a 500 traceback."""
        from app.llm.client import LLMError
        from app.storage.store import ContentStore

        mock_generator = AsyncMock()
        mock_generator.generate = AsyncMock(
            side_effect=LLMError("Groq returned 429 Too Many Requests (retry after 37s)")
        )

        store = ContentStore(":memory:")
        store.save_curriculum(
            "c1",
            Curriculum(
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
            ),
        )
        app.state.content_store = store
        app.state.story_generator = mock_generator
        app.state.language = get_language("sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "c1", "day": 1, "strategy": "WIDER"},
            )
        assert response.status_code == 502
        assert "429" in response.json()["detail"]

    async def test_generate_story_returns_201(self, monkeypatch):
        from app.generation.pipeline import LessonPipeline
        from app.llm.activity import ActivityLog
        from app.srs.database import SRSDatabase
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
        app.state.language = get_language("sl")
        app.state.srs_db = SRSDatabase(":memory:")

        store = ContentStore(":memory:")
        curriculum_id = "test-curriculum-id"
        store.save_curriculum(curriculum_id, mock_curriculum)
        app.state.content_store = store

        pipeline = LessonPipeline(
            story_generator=None,
            renderer=None,
            audio_dir=None,
            content_stores={"sl": store},
            languages={"sl": get_language("sl")},
            srs_dbs={},
            activity_log=ActivityLog(maxlen=100),
            llm_client=None,
        )
        app.state.pipeline = pipeline

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
        # Verify CurriculumDay.title was synced
        curriculum = store.get_curriculum(curriculum_id)
        assert curriculum is not None
        assert curriculum.days[0].title == "Day 1"
        # Verify pipeline enqueued a render job
        assert pipeline._jobs[("sl", "test-curriculum-id", 1)]["state"] == "queued"
        assert pipeline._jobs[("sl", "test-curriculum-id", 1)]["kind"] == "render"
        app.state.srs_db.close()

    async def test_generate_story_no_srs_db_still_succeeds(self, monkeypatch):
        """generate_story works when app.state has no srs_db (pre-warm is skipped)."""
        from app.storage.store import ContentStore

        mock_lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Dober dan", voice_id="v1", language_code="sl", role="A")],
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
        app.state.language = get_language("sl")

        store = ContentStore(":memory:")
        store.save_curriculum("cid", mock_curriculum)
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/story/generate",
                json={"curriculum_id": "cid", "day": 1, "strategy": "WIDER"},
            )

        assert response.status_code == 201
        assert "id" in response.json()

    async def test_prewarm_lesson_populates_cache(self, monkeypatch):
        """_prewarm_lesson fills the lemma_analysis_cache for a lesson's L2 phrases."""
        from app.api.generation import _prewarm_lesson
        from app.srs.database import SRSDatabase
        from app.srs.lemmatizer import LowercaseLemmatizer

        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Dober dan", voice_id="v1", language_code="sl", role="A"),
                        Phrase(text="Kako si", voice_id="v1", language_code="sl", role="B"),
                    ],
                ),
            ],
        )

        class _CachingLemmatizer(LowercaseLemmatizer):
            _cache_version = "test-v1"

        monkeypatch.setattr("app.api.generation.get_lemmatizer", lambda code: _CachingLemmatizer())

        srs_db = SRSDatabase(":memory:")
        try:
            await _prewarm_lesson(lesson, srs_db)

            for text in ("Dober dan", "Kako si"):
                cached = srs_db.get_sentence_analysis(text, "sl", "test-v1")
                assert cached is not None, f"Expected cache entry for {text}"
        finally:
            srs_db.close()

    async def test_prewarm_skips_cheap_lemmatizer(self, monkeypatch):
        """_prewarm_lesson is a no-op for LowercaseLemmatizer (no _cache_version)."""
        from app.api.generation import _prewarm_lesson
        from app.srs.database import SRSDatabase
        from app.srs.lemmatizer import LowercaseLemmatizer

        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Dober dan", voice_id="v1", language_code="sl", role="A")],
                ),
            ],
        )

        call_count = 0

        class _CountingLemmatizer(LowercaseLemmatizer):
            def analyze_sentence(self, sentence, language_code):
                nonlocal call_count
                call_count += 1
                return super().analyze_sentence(sentence, language_code)

        monkeypatch.setattr("app.api.generation.get_lemmatizer", lambda code: _CountingLemmatizer())

        srs_db = SRSDatabase(":memory:")
        try:
            await _prewarm_lesson(lesson, srs_db)
            # Cheap lemmatizer → no caching → no analyze_sentence calls (early return)
            assert call_count == 0
        finally:
            srs_db.close()

    async def test_prewarm_skips_no_natural_speed(self, monkeypatch):
        """_prewarm_lesson returns early when lesson has no NATURAL_SPEED section (line 47)."""
        from app.api.generation import _prewarm_lesson
        from app.srs.database import SRSDatabase
        from app.srs.lemmatizer import LowercaseLemmatizer

        lesson = Lesson(
            title="No NS",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="dober dan", voice_id="v1", language_code="sl")],
                ),
            ],
        )

        class _CachingLemmatizer(LowercaseLemmatizer):
            _cache_version = "test-v1"

        monkeypatch.setattr("app.api.generation.get_lemmatizer", lambda code: _CachingLemmatizer())

        srs_db = SRSDatabase(":memory:")
        try:
            await _prewarm_lesson(lesson, srs_db)  # should not raise
        finally:
            srs_db.close()

    async def test_prewarm_swallows_exception(self, monkeypatch):
        """_prewarm_lesson logs and swallows exceptions from get_lemmatizer (lines 52-53)."""
        from app.api.generation import _prewarm_lesson
        from app.srs.database import SRSDatabase

        lesson = Lesson(
            title="Test",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Dober dan", voice_id="v1", language_code="sl", role="A")],
                ),
            ],
        )

        def _raise(code):
            raise RuntimeError("boom")

        monkeypatch.setattr("app.api.generation.get_lemmatizer", _raise)

        srs_db = SRSDatabase(":memory:")
        try:
            await _prewarm_lesson(lesson, srs_db)  # should not raise
        finally:
            srs_db.close()
