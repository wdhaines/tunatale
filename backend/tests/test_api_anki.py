"""Tests for Anki API endpoints (peer-sync + media generator)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.syntactic_unit import SyntacticUnit
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


@pytest.fixture(autouse=True)
def _clear_auth_cache():
    """``_AUTH_CACHE`` and the driver process are process-globals; reset them
    around every test so login state doesn't leak between cases."""
    import app.plugins.anki_sync.sync_orchestrator as so

    so._AUTH_CACHE = None
    yield
    so._AUTH_CACHE = None


async def _post_peer_sync(**kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.post("/api/anki/peer-sync", **kwargs)


class TestPeerSync:
    """POST /api/anki/peer-sync — the ONLY HTTP sync path.

    These drive the REAL ``peer_sync`` against a real on-disk collection, with
    only ``_run_driver`` (the driver subprocess — the designated boundary in
    ``mock_allowlist.txt``) faked. Previously ``peer_sync`` itself was mocked at
    all three call sites, so the endpoint and the reconcile were each tested
    against a fake of the other: the b0a4b8a shape, in the module b0a4b8a was
    about.
    """

    @pytest.mark.usefixtures("sociable_tt_collection")
    async def test_returns_report(self, fake_driver):
        """A real sync completes and its report is serialised to the UI's shape."""
        response = await _post_peer_sync()

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"auth_success", "pull_required", "push_required", "tt_push_pull_exit", "dry_run"}
        assert body["auth_success"] is True
        assert body["dry_run"] is False
        assert body["tt_push_pull_exit"] == 0
        # The real bracket ran through the driver boundary. Nothing is pending, so
        # the push leg is correctly skipped — login + the pull sync only.
        assert [c["op"] for c in fake_driver] == ["login", "sync"]

    async def test_language_header_selects_that_languages_db(self, monkeypatch, tmp_path, fake_driver):
        """``X-TT-Language`` must reach peer_sync and select that language's db+deck.

        Without it the Sync button always reconciled the .env default language's
        deck/db regardless of the UI selection (the Phase-5 multi-language
        regression). The old test asserted ``language_code="no"`` was *passed* to
        a mocked peer_sync — which could not have caught a peer_sync that took the
        argument and reconciled the default db anyway, i.e. the bug itself.

        Here the evidence is which database actually got reconciled: an unlinked
        collocation in the Norwegian db comes back linked, and the Slovene one
        does not.
        """
        from app.config import settings
        from app.languages import get_deck_name
        from tests.anki_oracle.synthetic_collection import SyntheticCollection

        no_deck = get_deck_name("no")
        sl_db_path = tmp_path / "tt_sl.db"
        no_db_path = tmp_path / "tt_no.db"
        monkeypatch.setattr(
            settings,
            "database_urls",
            {"sl": f"sqlite:///{sl_db_path}", "no": f"sqlite:///{no_db_path}"},
        )

        coll = SyntheticCollection(settings.tt_collection_path)
        coll.set_deck(no_deck, 1)
        coll.add_notetype(1704067201, "Cloze", ("Text", "Back Extra"), template_count=1)
        coll.save()
        monkeypatch.setattr(settings, "anki_model_name", "Cloze")

        def _seed(path):
            db = SRSDatabase(str(path))
            db.add_collocation(
                SyntacticUnit(
                    text="Kava je dobra",
                    translation="Coffee is good",
                    word_count=3,
                    difficulty=2,
                    source="test",
                    source_sentence="Kava je dobra, ampak čaj je boljši.",
                    card_type="cloze",
                ),
                language_code="no",
            )
            return db

        no_db = _seed(no_db_path)
        sl_db = _seed(sl_db_path)

        response = await _post_peer_sync(headers={"X-TT-Language": "no"})
        assert response.status_code == 200

        # The Norwegian db is the one that was reconciled…
        assert SRSDatabase(str(no_db_path)).get_collocation("Kava je dobra").anki_note_id is not None
        # …and the Slovene db was left alone.
        assert SRSDatabase(str(sl_db_path)).get_collocation("Kava je dobra").anki_note_id is None
        no_db.close()
        sl_db.close()

    @pytest.mark.usefixtures("sociable_tt_collection")
    async def test_forwards_dry_run(self, fake_driver):
        """``?dry_run=true`` reaches peer_sync and suppresses the push leg.

        ``dry_run: true`` in the body alone would not prove much — the real
        evidence is the driver op log losing its second ``sync`` (the push).
        """
        response = await _post_peer_sync(params={"dry_run": "true"})

        assert response.status_code == 200
        assert response.json()["dry_run"] is True
        ops = [c["op"] for c in fake_driver]
        assert "sync" in ops
        assert ops.count("sync") == 1, f"dry run must not push; got {ops}"

    @pytest.mark.usefixtures("sociable_tt_collection")
    async def test_missing_credentials_surfaces_409(self, monkeypatch):
        """An expected failure (no AnkiWeb creds) → 409 carrying the reason.

        Triggered for real through ``_resolve_sync_password``: no env password and
        no keychain entry. The keychain lookup is a genuine OS boundary and is
        allowlisted as such.
        """
        from app.config import settings

        monkeypatch.setattr(settings, "sync_password", "")
        with patch("app.plugins.anki_sync.sync_orchestrator._keychain_password", return_value=None):
            response = await _post_peer_sync()

        assert response.status_code == 409
        assert "No AnkiWeb password" in response.json()["detail"]

    async def test_unexpected_failure_surfaces_500_with_reason(self, fake_driver, monkeypatch):
        """An unexpected failure → 500 with the real reason, NOT a bare
        "Internal Server Error" the user cannot act on.

        Triggered for real: ``tt_collection_path`` is a DIRECTORY, so the sync
        stack raises an OS/sqlite error that is not a ``PeerSyncError`` and falls
        to the catch-all. A corrupt *file* is not a valid trigger — that path is
        already wrapped into a ``PeerSyncError`` and returns 409 (verified while
        writing this).
        """
        from app.config import settings

        settings.tt_collection_path.mkdir(parents=True, exist_ok=True)

        response = await _post_peer_sync()

        assert response.status_code == 500
        assert "Sync failed" in response.json()["detail"]


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

        from app.cards.media.query_llm import IMAGE_QUERY_MODEL_VERSION

        assert db.get_image_query("sodišče", "court", IMAGE_QUERY_MODEL_VERSION) == "courtroom interior"
