"""Tests for FastAPI application lifespan startup and shutdown."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI


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
