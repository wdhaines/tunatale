"""Tests for S3.10: POST /api/anki/sync-create-new endpoint."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.anki.anki_connect import AnkiConnectUnavailable
from app.main import app
from app.srs.database import SRSDatabase


@pytest.fixture(autouse=True)
def _clean_app_state():
    db = SRSDatabase(":memory:")
    app.state.srs_db = db
    yield
    db.close()
    if hasattr(app.state, "srs_db"):
        delattr(app.state, "srs_db")


class _PingOkClient:
    def __init__(self, url):
        pass

    def ping(self):
        return 6


class _PingFailClient:
    def __init__(self, url):
        pass

    def ping(self):
        raise AnkiConnectUnavailable("not reachable")


class _FakeWriter:
    def __init__(self, client, db):
        pass


class TestSyncCreateNewEndpoint:
    async def test_returns_count(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "anki_model_name", "Slovene Vocabulary")
        monkeypatch.setattr("app.api.anki.AnkiConnectClient", _PingOkClient)
        monkeypatch.setattr("app.api.anki.OnlineWriter", _FakeWriter)

        async def fake_create_new(self, *, deck_name, model_name, dry_run=False, _media_fn=None):
            return 3

        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_create_new", fake_create_new)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync-create-new")

        assert response.status_code == 200
        assert response.json() == {"count": 3, "dry_run": False}

    async def test_dry_run_forwarded(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "anki_model_name", "Slovene Vocabulary")
        monkeypatch.setattr("app.api.anki.AnkiConnectClient", _PingOkClient)
        monkeypatch.setattr("app.api.anki.OnlineWriter", _FakeWriter)

        received: list[bool] = []

        async def fake_create_new(self, *, deck_name, model_name, dry_run=False, _media_fn=None):
            received.append(dry_run)
            return 0

        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_create_new", fake_create_new)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync-create-new?dry_run=true")

        assert response.status_code == 200
        assert response.json()["dry_run"] is True
        assert received == [True]

    async def test_503_when_anki_connect_unavailable(self, monkeypatch):
        monkeypatch.setattr("app.api.anki.AnkiConnectClient", _PingFailClient)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync-create-new")

        assert response.status_code == 503
        assert "AnkiConnect" in response.json()["detail"]

    async def test_empty_model_name_discovers_via_anki_connect(self, monkeypatch):
        """When settings.anki_model_name is '', endpoint must discover it first."""
        from app.config import settings

        monkeypatch.setattr(settings, "anki_model_name", "")
        monkeypatch.setattr("app.api.anki.AnkiConnectClient", _PingOkClient)
        monkeypatch.setattr("app.api.anki.OnlineWriter", _FakeWriter)
        monkeypatch.setattr(
            "app.anki.model_discovery.get_or_discover_model_name",
            lambda client: "Slovene Vocabulary",
        )

        received: list[str] = []

        async def fake_create_new(self, *, deck_name, model_name, dry_run=False, _media_fn=None):
            received.append(model_name)
            return 0

        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_create_new", fake_create_new)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync-create-new")

        assert response.status_code == 200
        assert received == ["Slovene Vocabulary"]

    async def test_empty_model_name_and_discovery_fails_returns_409(self, monkeypatch):
        """Settings empty + discovery empty → 409 Conflict (cannot proceed)."""
        from app.config import settings

        monkeypatch.setattr(settings, "anki_model_name", "")
        monkeypatch.setattr("app.api.anki.AnkiConnectClient", _PingOkClient)
        monkeypatch.setattr("app.api.anki.OnlineWriter", _FakeWriter)
        monkeypatch.setattr(
            "app.anki.model_discovery.get_or_discover_model_name",
            lambda client: "",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync-create-new")

        assert response.status_code == 409
        assert "model" in response.json()["detail"].lower()
