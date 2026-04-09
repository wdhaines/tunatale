"""Tests for FastAPI application lifespan startup and shutdown."""

from __future__ import annotations

from fastapi import FastAPI


async def test_lifespan_populates_app_state(tmp_path, monkeypatch):
    """Running through the lifespan context wires all app.state attributes."""
    from app.config import settings
    from app.main import lifespan

    # Point the DB at a temp file so the lifespan doesn't touch the real DB on disk
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")

    test_app = FastAPI()

    async with lifespan(test_app):
        assert test_app.state.srs_db is not None
        assert test_app.state.content_store is not None
        assert test_app.state.language is not None
        assert test_app.state.curriculum_generator is not None
        assert test_app.state.story_generator is not None
        assert test_app.state.renderer is not None
        assert test_app.state.audio_dir is not None

    # After exiting the context the databases should be closed cleanly (no exception)
