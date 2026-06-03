"""Tests for POST /api/anki/sync and GET /api/anki/status endpoints."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.anki.sync import CreateNewReport, PullReport, PushReport
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


# ── POST /api/anki/sync (offline / all-sqlite) ────────────────────────────────


def _make_fake_safe_open(conn):
    """Return a context-manager callable that yields a fake AnkiContext."""
    from contextlib import contextmanager
    from pathlib import Path

    from app.anki.safety import AnkiContext

    @contextmanager
    def _fake_safe_open(*args, **kwargs):
        yield AnkiContext(conn=conn, backup_path=Path("/fake/bak"), source_sha256="abc")

    return _fake_safe_open


def _make_minimal_anki_conn():
    """Minimal in-memory collection.anki2 with a col table only."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, "
        "dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT)"
    )
    conn.execute("INSERT INTO col VALUES (1, 0, 0, 0, 18, 0, 0, 0, '{}', '{}', '{}', '{}', '{}')")
    conn.execute(
        "CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, "
        "usn INTEGER, tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT)"
    )
    conn.execute(
        "CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, "
        "mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, "
        "factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER, "
        "odue INTEGER, odid INTEGER, flags INTEGER, data TEXT)"
    )
    conn.commit()
    return conn


class TestSyncOfflineEndpoint:
    """POST /api/anki/sync — all-sqlite path."""

    async def test_409_when_anki_running(self, monkeypatch):
        from contextlib import contextmanager

        from app.anki.safety import AnkiRunningError

        @contextmanager
        def _raises(*a, **kw):
            raise AnkiRunningError("test: Anki is running")
            yield  # noqa: B901

        monkeypatch.setattr("app.anki.safety.safe_open", _raises)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync")

        assert response.status_code == 409
        assert "Close Anki" in response.json()["detail"]

    @patch("app.anki.import_seed.refresh_media_for_deck")
    async def test_returns_offline_mode_and_counters(self, mock_refresh_media_for_deck, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "anki_model_name", "Slovene Vocabulary")
        monkeypatch.setattr(settings, "anki_collection_path", "/fake/collection.anki2")

        conn = _make_minimal_anki_conn()
        monkeypatch.setattr("app.anki.safety.safe_open", _make_fake_safe_open(conn))

        async def fake_create_new(self, *, deck_name, model_name, dry_run=False, _media_fn=None):
            return CreateNewReport(count=2, created=2)

        def fake_push(self, dry_run=False, force_fsrs=False):
            return PushReport(directions_pushed=3)

        def fake_pull(self, dry_run=False):
            return PullReport(directions_updated=4)

        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_create_new", fake_create_new)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_push", fake_push)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_pull", fake_pull)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync")

        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "offline"
        assert data["created"] == 2
        assert data["directions_pushed"] == 3
        assert data["directions_pulled"] == 4
        assert data["recompute_divergences"] == 0
        assert data["dry_run"] is False

    @patch("app.anki.import_seed.refresh_media_for_deck")
    async def test_passes_col_crt_to_anki_sync(self, mock_refresh_media_for_deck, monkeypatch):
        """Layer 4 regression: sync_push needs col.crt to compute the day_index
        used by bump_deck_new_today. The /api/anki/sync route must read col.crt
        and pass it as _anki_col_crt — without this, the bump path is silently
        skipped by the `self._anki_col_crt is not None` guard."""
        from app.anki import sync as sync_mod
        from app.config import settings

        monkeypatch.setattr(settings, "anki_model_name", "Slovene Vocabulary")
        monkeypatch.setattr(settings, "anki_collection_path", "/fake/collection.anki2")

        conn = _make_minimal_anki_conn()
        conn.execute("UPDATE col SET crt = 1704067200 WHERE id = 1")
        conn.commit()
        monkeypatch.setattr("app.anki.safety.safe_open", _make_fake_safe_open(conn))

        captured: dict = {}
        real_init = sync_mod.AnkiSync.__init__

        def capturing_init(self, *args, **kwargs):
            captured["_anki_col_crt"] = kwargs.get("_anki_col_crt")
            captured["_anki_col_ver"] = kwargs.get("_anki_col_ver")
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(sync_mod.AnkiSync, "__init__", capturing_init)

        async def fake_create_new(self, *, deck_name, model_name, dry_run=False, _media_fn=None):
            return CreateNewReport(count=0, created=0)

        def fake_push(self, dry_run=False, force_fsrs=False):
            return PushReport()

        def fake_pull(self, dry_run=False):
            return PullReport()

        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_create_new", fake_create_new)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_push", fake_push)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_pull", fake_pull)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync")

        assert response.status_code == 200
        assert captured["_anki_col_crt"] == 1704067200
        assert captured["_anki_col_ver"] == 18

    async def test_409_when_orphan_threshold_exceeded(self, monkeypatch):
        """When detect_and_reset_orphans aborts (likely a misconfigured deck
        path), the endpoint surfaces a 409 instead of letting the exception
        bubble up as a 500."""
        from app.anki.sync import OrphanThresholdExceededError
        from app.config import settings

        monkeypatch.setattr(settings, "anki_model_name", "Slovene Vocabulary")
        monkeypatch.setattr(settings, "anki_collection_path", "/fake/collection.anki2")

        conn = _make_minimal_anki_conn()
        monkeypatch.setattr("app.anki.safety.safe_open", _make_fake_safe_open(conn))

        def fake_detect(self):
            raise OrphanThresholdExceededError(
                "Refusing to reset 50 orphaned anki_card_ids (50% of 100). "
                "Check that anki_collection_path points at the right deck."
            )

        monkeypatch.setattr("app.anki.sync.AnkiSync.detect_and_reset_orphans", fake_detect)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync")

        assert response.status_code == 409
        assert "orphan" in response.json()["detail"].lower()

    async def test_409_when_model_name_empty_and_not_discoverable(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "anki_model_name", "")
        monkeypatch.setattr(settings, "anki_collection_path", "/fake/collection.anki2")

        conn = _make_minimal_anki_conn()
        monkeypatch.setattr("app.anki.safety.safe_open", _make_fake_safe_open(conn))
        monkeypatch.setattr(
            "app.anki.model_discovery.get_or_discover_model_name_offline",
            lambda conn, deck: "",
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync")

        assert response.status_code == 409
        assert "model" in response.json()["detail"].lower()

    async def test_dry_run_propagates(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "anki_model_name", "Slovene Vocabulary")
        monkeypatch.setattr(settings, "anki_collection_path", "/fake/collection.anki2")

        conn = _make_minimal_anki_conn()
        monkeypatch.setattr("app.anki.safety.safe_open", _make_fake_safe_open(conn))

        dry_runs_seen: list[bool] = []

        async def fake_create_new(self, *, deck_name, model_name, dry_run=False, _media_fn=None):
            dry_runs_seen.append(dry_run)
            return CreateNewReport()

        def fake_push(self, dry_run=False, force_fsrs=False):
            dry_runs_seen.append(dry_run)
            return PushReport()

        def fake_pull(self, dry_run=False):
            dry_runs_seen.append(dry_run)
            return PullReport()

        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_create_new", fake_create_new)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_push", fake_push)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_pull", fake_pull)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync?dry_run=true")

        assert response.status_code == 200
        assert response.json()["dry_run"] is True
        assert dry_runs_seen == [True, True, True]

    @patch("app.anki.import_seed.refresh_media_for_deck")
    async def test_sync_calls_refresh_media_and_returns_media_counts(self, mock_refresh_media_for_deck, monkeypatch):
        from app.config import settings

        mock_refresh_media_for_deck.return_value = {
            "updated_media": 3,
            "unchanged_media": 100,
            "new_media": 5,
            "skipped_guid_collisions": 1,
            "skipped_non_vocab": 2,
        }

        monkeypatch.setattr(settings, "anki_model_name", "Slovene Vocabulary")
        monkeypatch.setattr(settings, "anki_collection_path", "/fake/collection.anki2")

        conn = _make_minimal_anki_conn()
        monkeypatch.setattr("app.anki.safety.safe_open", _make_fake_safe_open(conn))

        async def fake_create_new(self, *, deck_name, model_name, dry_run=False, _media_fn=None):
            return CreateNewReport(count=2, created=2)

        def fake_push(self, dry_run=False, force_fsrs=False):
            return PushReport(directions_pushed=3)

        def fake_pull(self, dry_run=False):
            return PullReport(directions_updated=4)

        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_create_new", fake_create_new)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_push", fake_push)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_pull", fake_pull)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync")

        assert response.status_code == 200
        data = response.json()
        assert data["media_updated"] == 3
        assert data["media_unchanged"] == 100
        assert data["media_new"] == 5

    @patch("app.anki.import_seed.refresh_media_for_deck")
    async def test_sync_dry_run_skips_media_refresh(self, mock_refresh_media_for_deck, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "anki_model_name", "Slovene Vocabulary")
        monkeypatch.setattr(settings, "anki_collection_path", "/fake/collection.anki2")

        conn = _make_minimal_anki_conn()
        monkeypatch.setattr("app.anki.safety.safe_open", _make_fake_safe_open(conn))

        async def fake_create_new(self, *, deck_name, model_name, dry_run=False, _media_fn=None):
            return CreateNewReport()

        def fake_push(self, dry_run=False, force_fsrs=False):
            return PushReport()

        def fake_pull(self, dry_run=False):
            return PullReport()

        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_create_new", fake_create_new)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_push", fake_push)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_pull", fake_pull)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            await c.post("/api/anki/sync?dry_run=true")

        mock_refresh_media_for_deck.assert_not_called()

    async def test_dry_run_response_has_zero_media_counts(self, monkeypatch):
        from app.config import settings

        monkeypatch.setattr(settings, "anki_model_name", "Slovene Vocabulary")
        monkeypatch.setattr(settings, "anki_collection_path", "/fake/collection.anki2")

        conn = _make_minimal_anki_conn()
        monkeypatch.setattr("app.anki.safety.safe_open", _make_fake_safe_open(conn))

        async def fake_create_new(self, *, deck_name, model_name, dry_run=False, _media_fn=None):
            return CreateNewReport()

        def fake_push(self, dry_run=False, force_fsrs=False):
            return PushReport()

        def fake_pull(self, dry_run=False):
            return PullReport()

        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_create_new", fake_create_new)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_push", fake_push)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_pull", fake_pull)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync?dry_run=true")

        assert response.status_code == 200
        data = response.json()
        assert data["media_updated"] == 0
        assert data["media_unchanged"] == 0
        assert data["media_new"] == 0


# ── GET /api/anki/status ──────────────────────────────────────────────────────


class TestAnkiStatusEndpoint:
    async def test_returns_not_running_when_lock_free(self, monkeypatch):
        monkeypatch.setattr("app.anki.safety.probe_lock", lambda path: False)
        monkeypatch.setattr("app.config.settings.anki_collection_path", "/fake/collection.anki2")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.get("/api/anki/status")

        assert response.status_code == 200
        data = response.json()
        assert data["anki_running"] is False
        assert data["lock_acquirable"] is True

    async def test_returns_running_when_locked(self, monkeypatch):
        monkeypatch.setattr("app.anki.safety.probe_lock", lambda path: True)
        monkeypatch.setattr("app.config.settings.anki_collection_path", "/fake/collection.anki2")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.get("/api/anki/status")

        assert response.status_code == 200
        data = response.json()
        assert data["anki_running"] is True
        assert data["lock_acquirable"] is False

    @patch("app.anki.import_seed.refresh_media_for_deck")
    async def test_media_fn_called_during_create_new(self, mock_refresh_media_for_deck, monkeypatch):
        """_media_fn closure inside trigger_sync must be invoked for new SRS items."""
        from app.config import settings

        db = app.state.srs_db
        db.add_collocation(SyntacticUnit(text="voda", translation="water", word_count=1, difficulty=1, source="corpus"))

        monkeypatch.setattr(settings, "anki_model_name", "Slovene Vocabulary")
        monkeypatch.setattr(settings, "anki_collection_path", "/fake/collection.anki2")
        monkeypatch.setattr(settings, "pixabay_api_key", "test-key")

        conn = _make_minimal_anki_conn()
        monkeypatch.setattr("app.anki.safety.safe_open", _make_fake_safe_open(conn))

        media_calls: list[str] = []

        async def fake_fetch(word, english, *, pixabay_key, used_image_urls, **kw):
            media_calls.append(word)
            return None

        monkeypatch.setattr("app.api.anki.fetch_card_media", fake_fetch)

        class _FakeOW:
            def __init__(self, *a, **kw):
                pass

            def create_note(self, deck, model, fields, tags):
                return 9001

            def get_cards_for_note(self, nid):
                return {0: 90010}

            def store_media_file(self, fn, data):
                pass

            def update_note_fields(self, *a):
                pass

            def suspend(self, *a):
                pass

            def unsuspend(self, *a):
                pass

            def set_due_date(self, *a):
                pass

            def write_revlog(self, **kw):
                pass

            def set_specific_value_of_card(self, *a):
                pass

            def find_notes(self, q):
                return []

        monkeypatch.setattr("app.anki.sync.OfflineWriter", _FakeOW)

        def fake_push(self, dry_run=False, force_fsrs=False):
            return PushReport()

        def fake_pull(self, dry_run=False):
            return PullReport()

        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_push", fake_push)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_pull", fake_pull)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync")

        assert response.status_code == 200
        assert media_calls == ["voda"]

    @patch("app.srs.queue_stats.refresh_daily_review_cap")
    @patch("app.anki.import_seed.refresh_media_for_deck")
    async def test_sync_calls_refresh_daily_review_cap(
        self, mock_refresh_media_for_deck, mock_refresh_daily_review_cap, monkeypatch
    ):
        """refresh_daily_review_cap is invoked during a non-dry-run sync."""
        from app.config import settings

        monkeypatch.setattr(settings, "anki_model_name", "Slovene Vocabulary")
        monkeypatch.setattr(settings, "anki_collection_path", "/fake/collection.anki2")

        conn = _make_minimal_anki_conn()
        monkeypatch.setattr("app.anki.safety.safe_open", _make_fake_safe_open(conn))

        async def fake_create_new(self, *, deck_name, model_name, dry_run=False, _media_fn=None):
            return CreateNewReport(count=0, created=0)

        def fake_push(self, dry_run=False, force_fsrs=False):
            return PushReport()

        def fake_pull(self, dry_run=False):
            return PullReport()

        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_create_new", fake_create_new)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_push", fake_push)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_pull", fake_pull)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync")

        assert response.status_code == 200
        mock_refresh_daily_review_cap.assert_called_once()

    @patch("app.srs.queue_stats.refresh_daily_review_cap")
    @patch("app.anki.import_seed.refresh_media_for_deck")
    async def test_sync_dry_run_skips_refresh_daily_review_cap(
        self, mock_refresh_media_for_deck, mock_refresh_daily_review_cap, monkeypatch
    ):
        """refresh_daily_review_cap is NOT invoked during a dry-run sync."""
        from app.config import settings

        monkeypatch.setattr(settings, "anki_model_name", "Slovene Vocabulary")
        monkeypatch.setattr(settings, "anki_collection_path", "/fake/collection.anki2")

        conn = _make_minimal_anki_conn()
        monkeypatch.setattr("app.anki.safety.safe_open", _make_fake_safe_open(conn))

        async def fake_create_new(self, *, deck_name, model_name, dry_run=False, _media_fn=None):
            return CreateNewReport(count=0, created=0)

        def fake_push(self, dry_run=False, force_fsrs=False):
            return PushReport()

        def fake_pull(self, dry_run=False):
            return PullReport()

        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_create_new", fake_create_new)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_push", fake_push)
        monkeypatch.setattr("app.anki.sync.AnkiSync.sync_pull", fake_pull)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            response = await c.post("/api/anki/sync?dry_run=true")

        assert response.status_code == 200
        mock_refresh_daily_review_cap.assert_not_called()
