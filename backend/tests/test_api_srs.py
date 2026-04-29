"""Tests for /api/srs/queue-stats endpoint."""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.language import Language
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore


@pytest.fixture(autouse=True)
def _clean_app_state():
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    app.state.srs_db = db
    app.state.content_store = store
    app.state.language = Language.slovene()
    yield
    db.close()
    store.close()
    for attr in ("srs_db", "content_store", "language", "llm"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def _db() -> SRSDatabase:
    return app.state.srs_db


class TestQueueStats:
    async def test_queue_stats_includes_fsrs_source_default(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        assert resp.json()["fsrs_source"] == "default"

    async def test_queue_stats_includes_fsrs_source_cache(self):
        db = _db()
        db.set_anki_state_cache("fsrs_params", json.dumps({"weights": [0.0] * 19, "desired_retention": 0.9}))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        assert resp.json()["fsrs_source"] == "cache"
