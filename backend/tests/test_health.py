"""Tests for the /api/health dependency checks.

Every failure here is produced by breaking a REAL dependency — a closed sqlite
connection, a directory that does not exist, a directory with the write bit off,
a probe that genuinely outruns its timeout. Nothing is patched. That is a
requirement rather than a preference: the endpoint's entire value is that it
goes red when a real dependency is broken, and a mocked failure path would prove
only that the mock works.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.health import FAIL, OK, STATUS_OK, STATUS_UNHEALTHY, check_health
from app.config import settings
from app.main import app
from app.srs.database import SRSDatabase
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

CHECK_NAMES = {"database", "content_store", "audio_dir", "media_dir"}


@pytest.fixture
def healthy_dirs(monkeypatch, tmp_path):
    """Point audio_dir/media_dir at writable tmp dirs.

    Not optional, and not merely hygiene: settings.audio_dir resolves to
    backend/output/audio and media_dir to backend/media. Both exist on a dev
    machine and NEITHER is tracked in git, so both are absent in CI. A test that
    leaned on the ambient directories would pass locally and fail in CI.

    The 2-arg object form on `settings` is the idiom conftest.py's
    `_settings_overrides` already uses; check_mock_boundaries.py polices only the
    string form `monkeypatch.setattr("app.…", …)`.
    """
    audio = tmp_path / "audio"
    media = tmp_path / "media"
    audio.mkdir()
    media.mkdir()
    monkeypatch.setattr(settings, "audio_dir", audio)
    monkeypatch.setattr(settings, "media_dir", media)
    return audio, media


async def _get_health():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get("/api/health")


class TestCheckHealth:
    """The unit level: check_health against really-broken dependencies."""

    async def test_all_dependencies_good_is_ok(self, tmp_path):
        with SRSDatabase(":memory:") as db, ContentStore(":memory:") as store:
            status, checks = await check_health(
                srs_dbs=[db],
                content_stores=[store],
                audio_dir=tmp_path,
                media_dir=tmp_path,
            )
        assert status == STATUS_OK
        assert checks == dict.fromkeys(CHECK_NAMES, OK)

    async def test_absent_dependency_fails_rather_than_skipping(self, tmp_path):
        """No connections configured is unhealthy, not vacuously healthy.

        This is the bug the endpoint exists to remove: an unmounted volume or a
        lifespan that never ran leaves nothing to check, and "nothing to check"
        must never read as green.
        """
        status, checks = await check_health(
            srs_dbs=[],
            content_stores=[],
            audio_dir=tmp_path,
            media_dir=tmp_path,
        )
        assert status == STATUS_UNHEALTHY
        assert checks["database"] == FAIL
        assert checks["content_store"] == FAIL

    async def test_closed_database_connection_fails(self, tmp_path):
        """A real closed connection: SRSDatabase.close() then a real query."""
        db = SRSDatabase(":memory:")
        db.close()
        with ContentStore(":memory:") as store:
            status, checks = await check_health(
                srs_dbs=[db],
                content_stores=[store],
                audio_dir=tmp_path,
                media_dir=tmp_path,
            )
        assert status == STATUS_UNHEALTHY
        assert checks["database"] == FAIL
        assert checks["content_store"] == OK

    async def test_closed_content_store_fails(self, tmp_path):
        store = ContentStore(":memory:")
        store.close()
        with SRSDatabase(":memory:") as db:
            status, checks = await check_health(
                srs_dbs=[db],
                content_stores=[store],
                audio_dir=tmp_path,
                media_dir=tmp_path,
            )
        assert status == STATUS_UNHEALTHY
        assert checks["content_store"] == FAIL
        assert checks["database"] == OK

    async def test_one_bad_connection_among_several_fails_the_aggregate(self, tmp_path):
        """Aggregation is all-or-nothing: a single broken language sinks it."""
        good = SRSDatabase(":memory:")
        bad = SRSDatabase(":memory:")
        bad.close()
        with ContentStore(":memory:") as store:
            status, checks = await check_health(
                srs_dbs=[good, bad],
                content_stores=[store],
                audio_dir=tmp_path,
                media_dir=tmp_path,
            )
        good.close()
        assert status == STATUS_UNHEALTHY
        assert checks["database"] == FAIL

    async def test_missing_directory_fails(self, tmp_path):
        """A path that was never created — the unmounted-volume shape."""
        with SRSDatabase(":memory:") as db, ContentStore(":memory:") as store:
            status, checks = await check_health(
                srs_dbs=[db],
                content_stores=[store],
                audio_dir=tmp_path / "never-created",
                media_dir=tmp_path,
            )
        assert status == STATUS_UNHEALTHY
        assert checks["audio_dir"] == FAIL
        assert checks["media_dir"] == OK

    @pytest.mark.skipif(
        hasattr(os, "geteuid") and os.geteuid() == 0,
        reason="root bypasses the write bit, so chmod 0o500 is not a real breakage for root",
    )
    async def test_unwritable_directory_fails(self, tmp_path):
        """Exists but read-only. os.access-style checks pass here; a real write does not."""
        ro = tmp_path / "readonly"
        ro.mkdir()
        os.chmod(ro, 0o500)
        try:
            with SRSDatabase(":memory:") as db, ContentStore(":memory:") as store:
                status, checks = await check_health(
                    srs_dbs=[db],
                    content_stores=[store],
                    audio_dir=tmp_path,
                    media_dir=ro,
                )
        finally:
            os.chmod(ro, 0o700)
        assert status == STATUS_UNHEALTHY
        assert checks["media_dir"] == FAIL

    async def test_directory_probe_leaves_nothing_behind(self, tmp_path):
        """The writability probe must not litter the volume it is testing."""
        before = set(os.listdir(tmp_path))
        with SRSDatabase(":memory:") as db, ContentStore(":memory:") as store:
            await check_health(
                srs_dbs=[db],
                content_stores=[store],
                audio_dir=tmp_path,
                media_dir=tmp_path,
            )
        assert set(os.listdir(tmp_path)) == before

    async def test_a_hung_dependency_times_out_instead_of_hanging(self, tmp_path):
        """A real slow probe against a real (small) budget — no patched clock."""

        class SlowDB:
            def get_collocation_by_guid(self, guid):
                time.sleep(0.5)

        with ContentStore(":memory:") as store:
            started = time.monotonic()
            status, checks = await check_health(
                srs_dbs=[SlowDB()],
                content_stores=[store],
                audio_dir=tmp_path,
                media_dir=tmp_path,
                timeout=0.05,
            )
            elapsed = time.monotonic() - started

        assert status == STATUS_UNHEALTHY
        assert checks["database"] == FAIL
        assert elapsed < 0.5, "the timeout did not bound the probe"

    async def test_checks_run_concurrently_with_the_event_loop(self, tmp_path):
        """The blocking probes are off-loop, so the loop stays responsive."""
        ticks = 0

        async def ticker():
            nonlocal ticks
            for _ in range(3):
                await asyncio.sleep(0.01)
                ticks += 1

        class SlowDB:
            def get_collocation_by_guid(self, guid):
                time.sleep(0.1)

        with ContentStore(":memory:") as store:
            await asyncio.gather(
                check_health(
                    srs_dbs=[SlowDB()],
                    content_stores=[store],
                    audio_dir=tmp_path,
                    media_dir=tmp_path,
                ),
                ticker(),
            )
        assert ticks == 3, "the event loop was blocked by a dependency probe"


class TestHealthEndpoint:
    """The route level: status codes and the no-leak contract."""

    async def test_healthy_app_returns_200_with_every_dependency_named(self, api_app_state, healthy_dirs):
        response = await _get_health()
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == STATUS_OK
        assert set(body["checks"]) == CHECK_NAMES
        assert all(v == OK for v in body["checks"].values())

    async def test_multi_language_state_checks_every_connection(self, healthy_dirs):
        """The plural app.state maps — what a real multi-language deployment sets.

        The singular attributes are the test-only fallback; the lifespan always
        populates the plural ones, so this is the shape production actually runs.
        The active code must be a real key — the X-TT-Language middleware
        resolves it against these maps and KeyErrors otherwise. The SECOND code
        is never resolved by the middleware; it exists so check_health has more
        than one connection to aggregate over, which is the point of the test.
        """
        from app.languages import get_language

        active = settings.target_language
        other = f"{active}-second"
        dbs = {active: SRSDatabase(":memory:"), other: SRSDatabase(":memory:")}
        stores = {active: ContentStore(":memory:"), other: ContentStore(":memory:")}
        app.state.srs_dbs = dbs
        app.state.content_stores = stores
        app.state.languages = {active: get_language(active)}
        try:
            healthy = await _get_health()
            # Now break exactly one language's connection: the aggregate must sink.
            dbs[other].close()
            broken = await _get_health()
        finally:
            for d in dbs.values():
                d.close()
            for s in stores.values():
                s.close()
            del app.state.srs_dbs
            del app.state.content_stores
            del app.state.languages

        assert healthy.status_code == 200
        assert healthy.json()["checks"]["database"] == OK
        assert broken.status_code == 503
        assert broken.json()["checks"]["database"] == FAIL
        assert set(broken.json()["checks"]) == CHECK_NAMES, "no per-language keys may leak"

    async def test_broken_dependency_returns_503(self, api_app_state, healthy_dirs, monkeypatch):
        """Break the media volume for real; the monitor must see a non-2xx."""
        monkeypatch.setattr(settings, "media_dir", healthy_dirs[1] / "unmounted")
        response = await _get_health()
        assert response.status_code == 503
        assert response.json()["status"] == STATUS_UNHEALTHY
        assert response.json()["checks"]["media_dir"] == FAIL

    async def test_body_leaks_no_paths_versions_or_language_names(self, api_app_state, healthy_dirs):
        """The route is unauthenticated, so the body is status and nothing else."""
        response = await _get_health()
        raw = response.text
        assert set(response.json()) == {"status", "checks"}
        assert all(v in (OK, FAIL) for v in response.json()["checks"].values())
        for leak in ("/", "sqlite", "0.1.0", settings.target_language):
            assert leak not in raw, f"health body leaked {leak!r}"

    async def test_response_is_fast_enough_to_poll(self, api_app_state, healthy_dirs):
        """Comfortably inside a container healthcheck timeout under normal load."""
        started = time.monotonic()
        response = await _get_health()
        elapsed = time.monotonic() - started
        assert response.status_code == 200
        assert elapsed < 1.0, f"health took {elapsed:.3f}s"


class TestSqliteBreakageIsReal:
    """Control: prove the breakage the tests rely on is real breakage.

    Without this, `test_closed_database_connection_fails` could pass for the
    wrong reason — e.g. if close() were a no-op and the probe failed for some
    unrelated cause. A closed sqlite connection must genuinely raise.
    """

    def test_a_closed_sqlite_connection_raises(self):
        conn = sqlite3.connect(":memory:")
        conn.close()
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
