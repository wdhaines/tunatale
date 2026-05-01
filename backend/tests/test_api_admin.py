"""Tests for admin endpoints."""

from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from app.main import app


class TestRefreshMediaEndpoint:
    @patch("app.api.admin.import_seed")
    async def test_maps_counts_correctly(self, mock_import_seed, api_app_state):
        mock_import_seed.return_value = {
            "updated_media": 3,
            "unchanged_media": 100,
            "new_media": 5,
            "skipped_guid_collisions": 1,
            "skipped_non_vocab": 2,
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/admin/refresh-media")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"updated": 3, "unchanged": 100, "new": 5, "errors": 1}

    @patch("app.api.admin.import_seed")
    async def test_errors_only_counts_guid_collisions(self, mock_import_seed, api_app_state):
        mock_import_seed.return_value = {
            "updated_media": 0,
            "unchanged_media": 50,
            "new_media": 0,
            "skipped_guid_collisions": 0,
            "skipped_non_vocab": 10,
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/admin/refresh-media")
        assert resp.status_code == 200
        assert resp.json()["errors"] == 0

    @patch("app.api.admin.import_seed")
    async def test_raises_500_on_runtime_error(self, mock_import_seed, api_app_state):
        mock_import_seed.side_effect = RuntimeError("broken")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/admin/refresh-media")
        assert resp.status_code == 500
        assert "broken" in resp.json()["detail"]
