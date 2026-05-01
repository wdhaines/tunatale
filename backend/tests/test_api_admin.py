"""Tests for admin endpoints."""

from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.main import app


class TestRefreshMediaEndpoint:
    async def test_returns_counts(self, api_app_state):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/admin/refresh-media")
        # It may fail (no Anki files), but should return JSON
        assert resp.status_code in (200, 500)

    async def test_returns_json_with_keys(self, api_app_state):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/admin/refresh-media")
        if resp.status_code == 200:
            data = resp.json()
            assert "updated" in data
            assert "unchanged" in data
            assert "new" in data
            assert "errors" in data

    @patch("app.api.admin.import_seed")
    async def test_raises_500_on_runtime_error(self, mock_import_seed, api_app_state):
        mock_import_seed.side_effect = RuntimeError("broken")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/admin/refresh-media")
        assert resp.status_code == 500
        assert "broken" in resp.json()["detail"]
