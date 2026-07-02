"""Tests for FastAPI application lifespan startup and shutdown."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI

from app.models.lesson import Lesson, Phrase, Section, SectionType


async def test_lifespan_populates_app_state(tmp_path, monkeypatch):
    """Running through the lifespan context wires all app.state attributes (mock mode)."""
    from app.config import settings
    from app.llm.cassette import CassetteLLMClient
    from app.main import lifespan

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "llm_mode", "mock")

    test_app = FastAPI()

    async with lifespan(test_app):
        assert test_app.state.srs_db is not None
        assert test_app.state.content_store is not None
        assert test_app.state.language is not None
        assert test_app.state.curriculum_generator is not None
        assert test_app.state.curriculum_planner is not None
        assert test_app.state.story_generator is not None
        assert test_app.state.renderer is not None
        assert test_app.state.audio_dir is not None
        # In mock mode, the LLM client should be wrapped with CassetteLLMClient
        assert isinstance(test_app.state.curriculum_generator._llm, CassetteLLMClient)

    # After exiting the context the databases should be closed cleanly (no exception)


async def test_lifespan_live_mode_uses_raw_client(tmp_path, monkeypatch):
    """In live mode, lifespan uses an unwrapped LLMClient."""
    from app.config import settings
    from app.llm.cassette import CassetteLLMClient
    from app.main import lifespan

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "llm_mode", "live")

    test_app = FastAPI()

    async with lifespan(test_app):
        assert not isinstance(test_app.state.curriculum_generator._llm, CassetteLLMClient)


async def test_lifespan_warmup_failure_does_not_abort(tmp_path, monkeypatch):
    """A lemmatizer warm-up that raises must log a warning but not abort startup."""
    from app.config import settings
    from app.main import lifespan

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "llm_mode", "mock")

    mock_lemmatizer = MagicMock()
    mock_lemmatizer.lemmatize.side_effect = RuntimeError("classla model missing")
    monkeypatch.setattr("app.main.get_lemmatizer", lambda: mock_lemmatizer)

    test_app = FastAPI()

    async with lifespan(test_app):
        assert test_app.state.srs_db is not None
        # The warm-up failure must not prevent other app state from being wired
        assert test_app.state.content_store is not None


async def test_warm_from_lessons_populates_cache():
    """_warm_from_lessons fills the lemma_analysis_cache for stored lessons."""
    from app.main import _warm_from_lessons
    from app.srs.database import SRSDatabase
    from app.srs.lemmatizer import LowercaseLemmatizer
    from app.storage.store import ContentStore

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

    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

    srs_db = SRSDatabase(":memory:")
    try:
        _warm_from_lessons(store.list_lessons(), srs_db, _CachingLemmatizer(), "test-v1")

        for text in ("Dober dan", "Kako si"):
            cached = srs_db.get_sentence_analysis(text, "sl", "test-v1")
            assert cached is not None, f"Expected cache entry for {text}"
    finally:
        srs_db.close()
        store.close()


async def test_warm_lemmatizer_swallows_exception(monkeypatch):
    """_warm_lemmatizer logs a warning but does not re-raise."""
    from app.main import _warm_lemmatizer
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    monkeypatch.setattr("app.main.get_lemmatizer", lambda: MagicMock(_cache_version="test-v1"))

    # A content_store that raises when list_lessons is called
    store = ContentStore(":memory:")
    srs_db = SRSDatabase(":memory:")
    try:
        store.close()  # close the store so list_lessons fails
        await _warm_lemmatizer(srs_db, store)  # should not raise
    finally:
        srs_db.close()


async def test_warm_from_lessons_handles_edge_cases():
    """_warm_from_lessons skips lessons without NATURAL_SPEED and non-L2 phrases."""
    from app.main import _warm_from_lessons
    from app.srs.database import SRSDatabase
    from app.srs.lemmatizer import LowercaseLemmatizer

    class _CachingLemmatizer(LowercaseLemmatizer):
        _cache_version = "test-v2"

    lessons: list[tuple[str, str, int, Lesson]] = [
        (
            "no-ns",
            "c1",
            1,
            Lesson(  # no NATURAL_SPEED section → hits line 73 continue
                title="No NS",
                language_code="sl",
                sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=[])],
            ),
        ),
        (
            "mixed-lang",
            "c1",
            2,
            Lesson(  # phrase with non-matching language_code → hits line 76 continue
                title="Mixed",
                language_code="sl",
                sections=[
                    Section(
                        section_type=SectionType.NATURAL_SPEED,
                        phrases=[
                            Phrase(text="Hello", voice_id="v1", language_code="en", role="A"),
                        ],
                    ),
                ],
            ),
        ),
        (
            "valid",
            "c1",
            3,
            Lesson(  # normal lesson that should produce a cache entry
                title="Valid",
                language_code="sl",
                sections=[
                    Section(
                        section_type=SectionType.NATURAL_SPEED,
                        phrases=[
                            Phrase(text="Živjo", voice_id="v1", language_code="sl", role="A"),
                        ],
                    ),
                ],
            ),
        ),
    ]

    srs_db = SRSDatabase(":memory:")
    try:
        _warm_from_lessons(lessons, srs_db, _CachingLemmatizer(), "test-v2")
        cached = srs_db.get_sentence_analysis("Živjo", "sl", "test-v2")
        assert cached is not None, "Expected cache entry for valid L2 phrase"
        # Edge case lessons should not cause errors and the non-L2 phrase
        # should NOT have been cached
        cached_en = srs_db.get_sentence_analysis("Hello", "en", "test-v2")
        assert cached_en is None, "Non-L2 phrase should not be cached"
    finally:
        srs_db.close()


async def test_warm_lemmatizer_skips_cheap_lemmatizer():
    """_warm_lemmatizer is a no-op for LowercaseLemmatizer (no _cache_version)."""
    from app.main import _warm_lemmatizer
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    store = ContentStore(":memory:")
    srs_db = SRSDatabase(":memory:")
    try:
        await _warm_lemmatizer(srs_db, store)  # should not raise
    finally:
        srs_db.close()
        store.close()
