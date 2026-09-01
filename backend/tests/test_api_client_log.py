"""Tests for the client-log endpoint.

The browser has no durable channel. Every frontend signal dies in a console
nobody can read — which is the same defect fixed twice on the backend today
(PRODUCTION_MINT and PRESTAGE_IMAGES both reached only the ephemeral logger),
left standing on the other side. A bug that happens only on the user's phone is
currently undiagnosable for exactly that reason: Playwright's tap() dispatches
at a geometric centre with no fuzzy targeting, so emulation cannot reproduce a
real fingertip losing a hit-test, and nothing survives the device.

This is a WRITE endpoint fed by an untrusted browser, so the tests below are
mostly about its limits rather than its happy path.
"""

from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app


async def _post(payload):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post("/api/client-log", json=payload)


class TestDisabledByDefault:
    """Off unless explicitly switched on."""

    async def test_returns_404_and_writes_nothing_when_disabled(self, tmp_path, monkeypatch):
        """404 rather than 403: a disabled debug channel should not advertise
        that it exists."""
        log = tmp_path / "client.log"
        monkeypatch.setattr(settings, "client_log", log)
        monkeypatch.setattr(settings, "client_log_enabled", False)

        resp = await _post({"lines": ["touchstart target=BUTTON.gloss"]})

        assert resp.status_code == 404
        assert not log.exists()

    async def test_the_default_is_off(self):
        """Pinned as a value, not a habit. An always-on endpoint that appends
        attacker-controlled text to a file on disk is not something to enable by
        forgetting to disable it."""
        from app.config import Settings

        assert Settings().client_log_enabled is False


class TestAppending:
    async def test_lines_are_appended_with_a_timestamp(self, tmp_path, monkeypatch):
        log = tmp_path / "logs" / "client.log"
        monkeypatch.setattr(settings, "client_log", log)
        monkeypatch.setattr(settings, "client_log_enabled", True)

        resp = await _post({"lines": ["touchstart target=BUTTON.gloss", "click target=BUTTON.skip"]})

        assert resp.status_code == 200
        assert resp.json()["accepted"] == 2
        written = log.read_text().splitlines()
        assert len(written) == 2
        assert "touchstart target=BUTTON.gloss" in written[0]
        assert "click target=BUTTON.skip" in written[1]
        # Timestamped like sync.log, so two sources can be read side by side.
        assert written[0].startswith("20")

    async def test_appends_rather_than_truncates(self, tmp_path, monkeypatch):
        log = tmp_path / "client.log"
        monkeypatch.setattr(settings, "client_log", log)
        monkeypatch.setattr(settings, "client_log_enabled", True)

        await _post({"lines": ["first"]})
        await _post({"lines": ["second"]})

        assert len(log.read_text().splitlines()) == 2


class TestTheLimitsThatMakeItSafe:
    """A browser can send anything. Each limit below has a failure behind it."""

    async def test_a_newline_cannot_forge_extra_log_entries(self, tmp_path, monkeypatch):
        """LOG INJECTION. One client string containing newlines would otherwise
        write several lines, letting a page fabricate entries that look like
        they came from somewhere else — including timestamps that never
        happened. Strip them so one submitted line is exactly one log line."""
        log = tmp_path / "client.log"
        monkeypatch.setattr(settings, "client_log", log)
        monkeypatch.setattr(settings, "client_log_enabled", True)

        await _post({"lines": ["real\n2026-01-01T00:00:00 FORGED admin did a thing\nmore"]})

        written = log.read_text().splitlines()
        assert len(written) == 1, f"one submitted line must be one log line, got {written}"
        assert "FORGED" in written[0]  # kept as content...
        assert not written[0].startswith("2026-01-01"), "...but never as its own entry"

    async def test_a_long_line_is_truncated(self, tmp_path, monkeypatch):
        log = tmp_path / "client.log"
        monkeypatch.setattr(settings, "client_log", log)
        monkeypatch.setattr(settings, "client_log_enabled", True)

        await _post({"lines": ["x" * 10_000]})

        assert len(log.read_text().splitlines()[0]) < 400

    async def test_too_many_lines_are_capped_not_rejected(self, tmp_path, monkeypatch):
        """Capped rather than 422: a debug client that oversends should lose the
        excess, not the whole batch — the first entries are the ones nearest the
        event being chased."""
        log = tmp_path / "client.log"
        monkeypatch.setattr(settings, "client_log", log)
        monkeypatch.setattr(settings, "client_log_enabled", True)

        resp = await _post({"lines": [f"line{i}" for i in range(500)]})

        assert resp.status_code == 200
        written = log.read_text().splitlines()
        assert len(written) <= 50
        assert resp.json()["accepted"] == len(written)
        assert "line0" in written[0]

    async def test_an_empty_batch_is_accepted_and_writes_nothing(self, tmp_path, monkeypatch):
        log = tmp_path / "client.log"
        monkeypatch.setattr(settings, "client_log", log)
        monkeypatch.setattr(settings, "client_log_enabled", True)

        resp = await _post({"lines": []})

        assert resp.status_code == 200
        assert resp.json()["accepted"] == 0
        assert not log.exists()
