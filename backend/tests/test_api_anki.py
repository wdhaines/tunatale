"""Tests for Anki API endpoints (peer-sync + media generator)."""

from __future__ import annotations

from unittest.mock import ANY, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.sync_orchestrator import PeerSyncError
from app.srs.database import SRSDatabase


@pytest.fixture(autouse=True)
def _clean_app_state():
    db = SRSDatabase(":memory:")
    app.state.srs_db = db
    yield
    db.close()
    if hasattr(app.state, "srs_db"):
        delattr(app.state, "srs_db")


# ── POST /api/anki/peer-sync (AnkiWeb peer sync; works with Anki open) ─────────


class TestPeerSync:
    """POST /api/anki/peer-sync — drives app.plugins.anki_sync.sync_orchestrator.peer_sync."""

    async def test_returns_report(self):
        from app.plugins.anki_sync.sync_orchestrator import PeerSyncReport

        report = PeerSyncReport(auth_success=True, pull_required=0, push_required=1, tt_push_pull_exit=0, dry_run=False)
        with patch("app.plugins.anki_sync.sync_orchestrator.peer_sync", return_value=report) as mock_ps:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                # X-TT-Language must reach peer_sync as language_code, or the Sync
                # button always syncs the .env default language's deck/db regardless
                # of the UI selection (Phase-5 multi-language regression). Asserted
                # here rather than in a dedicated test to avoid a 4th grandfathered
                # patch("...peer_sync") (the mock-boundary ledger is shrink-only).
                response = await c.post("/api/anki/peer-sync", headers={"X-TT-Language": "no"})

        assert response.status_code == 200
        assert response.json() == {
            "auth_success": True,
            "pull_required": 0,
            "push_required": 1,
            "tt_push_pull_exit": 0,
            "dry_run": False,
        }
        mock_ps.assert_called_once_with(False, media_fn=ANY, language_code="no")

    async def test_forwards_dry_run(self):
        from app.plugins.anki_sync.sync_orchestrator import PeerSyncReport

        with patch(
            "app.plugins.anki_sync.sync_orchestrator.peer_sync", return_value=PeerSyncReport(dry_run=True)
        ) as mock_ps:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.post("/api/anki/peer-sync?dry_run=true")

        assert response.status_code == 200
        assert response.json()["dry_run"] is True
        mock_ps.assert_called_once_with(True, media_fn=ANY, language_code=ANY)

    @pytest.mark.parametrize(
        ("exc", "status", "needle"),
        [
            # Expected failure (no creds / FULL_SYNC) → 409 with the message.
            (PeerSyncError("No AnkiWeb password found."), 409, "No AnkiWeb password"),
            # Unexpected failure (e.g. sqlite IntegrityError mid-reconcile) → 500 with the
            # real reason, NOT a bare "Internal Server Error" the user can't act on.
            (RuntimeError("UNIQUE constraint failed: collocations.text"), 500, "Sync failed"),
        ],
    )
    async def test_sync_failure_surfaces_detail(self, exc, status, needle):
        """Both expected and unexpected sync failures pass a useful reason through to
        the UI. One ``patch`` call site (the grandfathered seam) covers both branches."""
        with patch("app.plugins.anki_sync.sync_orchestrator.peer_sync", side_effect=exc):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
                response = await c.post("/api/anki/peer-sync")

        assert response.status_code == status
        assert needle in response.json()["detail"]


# ── _build_media_fn (shared media generator) ──────────────────────────────────


class TestBuildMediaFn:
    """Unit tests for _build_media_fn — the create-time media generator."""

    async def test_media_fn_called_during_create_new(self, monkeypatch):
        from app.api.anki import _build_media_fn
        from app.config import settings

        db = app.state.srs_db
        db.add_collocation(SyntacticUnit(text="voda", translation="water", word_count=1, difficulty=1, source="corpus"))
        monkeypatch.setattr(settings, "pixabay_api_key", "test-key")

        media_calls: list[str] = []

        async def fake_fetch(word, english, *, pixabay_key, used_image_urls, **kw):
            media_calls.append(word)
            return None

        with patch("app.api.anki.fetch_card_media", fake_fetch):
            media_fn = _build_media_fn(None, db)
            await media_fn("voda", "water", used_image_urls=set())

        assert media_calls == ["voda"]

    async def test_llm_image_query_threads_into_media_fetch(self, monkeypatch):
        from app.api.anki import _build_media_fn
        from app.config import settings

        db = app.state.srs_db
        db.add_collocation(
            SyntacticUnit(
                text="sodišče",
                translation="court",
                word_count=1,
                difficulty=1,
                source="corpus",
                source_sentence="Šel je na sodišče.",
                grammar="noun, neuter",
            )
        )
        monkeypatch.setattr(settings, "pixabay_api_key", "test-key")

        class _FakeLLM:
            def __init__(self) -> None:
                self.prompts: list[str] = []

            async def complete(self, prompt, system_prompt=None, temperature=0.7, max_tokens=256):
                self.prompts.append(prompt)
                return "courtroom interior"

        fake_llm = _FakeLLM()

        captured: list[str | None] = []

        async def fake_fetch(word, english, *, pixabay_key, used_image_urls, image_query=None, **kw):
            captured.append(image_query)
            return None

        with patch("app.api.anki.fetch_card_media", fake_fetch):
            media_fn = _build_media_fn(fake_llm, db)
            await media_fn(
                "sodišče",
                "court",
                used_image_urls=set(),
                source_sentence="Šel je na sodišče.",
                grammar="noun, neuter",
            )

        assert captured == ["courtroom interior"]
        assert "Šel je na sodišče." in fake_llm.prompts[0]
        assert "noun, neuter" in fake_llm.prompts[0]

        from app.anki.media.query_llm import IMAGE_QUERY_MODEL_VERSION

        assert db.get_image_query("sodišče", "court", IMAGE_QUERY_MODEL_VERSION) == "courtroom interior"
