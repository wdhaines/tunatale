"""A parked instance refuses to serve, and says where TunaTale is (tunatale-qyw0).

While learning runs on the laptop, production still holds a copy of the data
that the next hand-back will overwrite. Anything graded there would be lost
silently, so parking makes that copy unusable, not merely discouraged.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app

LAPTOP = "https://my-mac.tail1234.ts.net:5273"


async def _get(path: str):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


class TestParked:
    @pytest.mark.parametrize("path", ["/api/srs/stats", "/api/curriculum", "/api/auth/me", "/api/review-sessions"])
    async def test_every_api_route_answers_503_with_where_to_go(self, monkeypatch, path):
        monkeypatch.setattr(settings, "parked_at", LAPTOP)
        resp = await _get(path)
        assert resp.status_code == 503
        body = resp.json()
        assert body["parked_at"] == LAPTOP
        assert LAPTOP in body["detail"]

    async def test_health_is_not_parked(self, monkeypatch):
        """The container healthcheck and uptime monitoring poll it; a parked box
        is still a healthy box, and the healthcheck restarting it would be wrong."""
        monkeypatch.setattr(settings, "parked_at", LAPTOP)
        resp = await _get("/api/health")
        assert "parked_at" not in resp.json()

    async def test_a_lookalike_of_health_is_still_parked(self, monkeypatch):
        """Exact match, not a prefix: an exemption for `/api/health` must not
        open `/api/healthcheck-anything`."""
        monkeypatch.setattr(settings, "parked_at", LAPTOP)
        resp = await _get("/api/healthz")
        assert resp.status_code == 503
        assert resp.json()["parked_at"] == LAPTOP

    async def test_unparked_serves_normally(self, monkeypatch):
        monkeypatch.setattr(settings, "parked_at", "")
        resp = await _get("/api/health")
        assert "parked_at" not in resp.json()
        other = await _get("/api/healthz")
        assert other.status_code == 404
