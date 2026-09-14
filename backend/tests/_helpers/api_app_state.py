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
        "review_renders",
        # Injected by the _injected_lemmatizer seam. Absent from this list until
        # 2026-09-14, so a test that installed a lemmatizer double leaked it into
        # every later test FILE: _injected_lemmatizer returns {} ("resolve for
        # real") only when app.state.lemmatizer is unset, so a stale double
        # silently replaced real resolution downstream.
        "lemmatizer",
        "model_version",
    ):
        if hasattr(app.state, attr):
            delattr(app.state, attr)
