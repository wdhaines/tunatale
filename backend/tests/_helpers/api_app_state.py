"""Autouse fixture: clear app.state after every test."""

from __future__ import annotations

import pytest

from app.main import app


@pytest.fixture(autouse=True)
def _clean_app_state():
    yield
    for attr in (
        "content_store",
        "language",
        "story_generator",
        "renderer",
        "audio_dir",
        "srs_db",
        "pipeline",
    ):
        if hasattr(app.state, attr):
            delattr(app.state, attr)
