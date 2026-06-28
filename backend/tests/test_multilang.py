"""Phase 5 — simultaneous multi-language: per-request connection resolution."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.main import _language_db_map, app
from app.models.language import Language
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore


class TestLanguageDbMap:
    def test_single_language_when_no_database_urls(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "database_urls", {})
        monkeypatch.setattr(settings, "target_language", "sl")
        monkeypatch.setattr(settings, "database_url", "sqlite:///./tunatale_sl.db")
        assert _language_db_map() == {"sl": "sqlite:///./tunatale_sl.db"}

    def test_multi_language_when_database_urls_set(self, monkeypatch):
        from app.config import settings

        urls = {"sl": "sqlite:///./tunatale_sl.db", "no": "sqlite:///./tunatale_no.db"}
        monkeypatch.setattr(settings, "database_urls", urls)
        assert _language_db_map() == urls


async def test_lifespan_opens_a_connection_per_language(tmp_path, monkeypatch):
    """Multi-language lifespan: a connection set per configured language, and the
    singular defaults bind to target_language (or the first entry when it's absent)."""
    from app.config import settings
    from app.main import lifespan

    urls = {
        "sl": f"sqlite:///{tmp_path / 'sl.db'}",
        "no": f"sqlite:///{tmp_path / 'no.db'}",
    }
    monkeypatch.setattr(settings, "database_urls", urls)
    monkeypatch.setattr(settings, "llm_mode", "mock")
    # target_language NOT in the map → default_code falls back to the first entry.
    monkeypatch.setattr(settings, "target_language", "zz")

    test_app = FastAPI()
    async with lifespan(test_app):
        assert set(test_app.state.srs_dbs) == {"sl", "no"}
        assert set(test_app.state.content_stores) == {"sl", "no"}
        assert test_app.state.languages["no"].code == "no"
        # default singular binds to the first configured language (sl).
        assert test_app.state.srs_db is test_app.state.srs_dbs["sl"]
        assert test_app.state.language.code == "sl"


class TestPerRequestIsolation:
    """The X-TT-Language header selects which connection serves the request —
    isolation is the connection, not a WHERE clause."""

    @pytest.fixture
    def two_language_app(self):
        db_sl = SRSDatabase(":memory:")
        db_no = SRSDatabase(":memory:")
        db_sl.add_collocation(
            SyntacticUnit(text="voda", translation="water", word_count=1, difficulty=1, source="corpus")
        )
        db_no.add_collocation(
            SyntacticUnit(text="vann", translation="water", word_count=1, difficulty=1, source="corpus")
        )
        app.state.srs_dbs = {"sl": db_sl, "no": db_no}
        app.state.content_stores = {"sl": ContentStore(":memory:"), "no": ContentStore(":memory:")}
        app.state.languages = {"sl": Language.slovene(), "no": Language.norwegian()}
        # Singular defaults (the active language) — used by the no-header path.
        app.state.srs_db = db_sl
        app.state.content_store = app.state.content_stores["sl"]
        app.state.language = Language.slovene()
        try:
            yield
        finally:
            db_sl.close()
            db_no.close()
            for attr in (
                "srs_dbs",
                "content_stores",
                "languages",
                "srs_db",
                "content_store",
                "language",
            ):
                if hasattr(app.state, attr):
                    delattr(app.state, attr)

    async def _texts(self, client, headers=None):
        resp = await client.get("/api/srs/items", headers=headers or {})
        assert resp.status_code == 200
        return {item["text"] for item in resp.json()["items"]}

    async def test_header_selects_norwegian_connection(self, two_language_app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert await self._texts(client, {"X-TT-Language": "no"}) == {"vann"}

    async def test_header_selects_slovene_connection(self, two_language_app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert await self._texts(client, {"X-TT-Language": "sl"}) == {"voda"}

    async def test_no_header_uses_default_language(self, two_language_app, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "target_language", "sl")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert await self._texts(client) == {"voda"}

    async def test_unknown_language_falls_back_to_default(self, two_language_app, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "target_language", "no")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # "de" isn't configured → falls back to target_language (no).
            assert await self._texts(client, {"X-TT-Language": "de"}) == {"vann"}

    async def test_languages_endpoint_lists_configured_languages(self, two_language_app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/languages", headers={"X-TT-Language": "no"})
        assert resp.status_code == 200
        body = resp.json()
        assert {lang["code"] for lang in body["languages"]} == {"sl", "no"}
        assert {lang["name"] for lang in body["languages"]} == {"Slovene", "Norwegian"}
        assert body["active"] == "no"

    async def test_grade_in_one_language_does_not_touch_the_other(self, two_language_app):
        """A write through one connection is invisible to the other (no shared DB)."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # Add a Norwegian item via the "no" connection.
            resp = await client.post(
                "/api/srs/items",
                headers={"X-TT-Language": "no"},
                json={"text": "hund", "translation": "dog", "language_code": "no", "word_count": 1},
            )
            assert resp.status_code == 201
            # Slovene connection is unaffected.
            assert await self._texts(client, {"X-TT-Language": "sl"}) == {"voda"}
            assert await self._texts(client, {"X-TT-Language": "no"}) == {"vann", "hund"}


class TestLanguagesEndpointFallbacks:
    """The /api/languages singular + empty fallbacks (no per-language maps)."""

    def _cleanup(self):
        for attr in ("languages", "language", "srs_dbs", "srs_db"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)

    async def test_single_language_uses_singular_app_state(self):
        app.state.language = Language.slovene()
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                body = (await client.get("/api/languages")).json()
            assert body["languages"] == [{"code": "sl", "name": "Slovene"}]
        finally:
            self._cleanup()

    async def test_no_language_configured_returns_empty_list(self):
        # Neither maps nor singular language set → empty list (no crash).
        self._cleanup()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            body = (await client.get("/api/languages")).json()
        assert body["languages"] == []
