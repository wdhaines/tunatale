"""Tests for GET /api/llm/rate-limit and POST /api/llm/rate-limit/probe.

The endpoint reports the Groq rate-limit state captured passively from the most
recent call's headers, relative-time-shifted server-side (age_s / *_reset_in_s)
so the frontend can count down without trusting the browser clock, plus the
TT-side 24h token tally (the TPD cap has no header).
"""

import time

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from app.config import settings
from app.llm.client import GROQ_API_URL, LLMClient
from app.llm.usage_ledger import UsageLedger
from app.main import app

_RL_HEADERS = {
    "x-ratelimit-limit-requests": "1000",
    "x-ratelimit-remaining-requests": "998",
    "x-ratelimit-reset-requests": "60s",
    "x-ratelimit-limit-tokens": "8000",
    "x-ratelimit-remaining-tokens": "7000",
    "x-ratelimit-reset-tokens": "5s",
}


@pytest.fixture(autouse=True)
def _clean_llm_state():
    yield
    if hasattr(app.state, "llm"):
        delattr(app.state, "llm")


async def _get_status() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get("/api/llm/rate-limit")
    assert response.status_code == 200
    return response.json()


class TestRateLimitStatus:
    async def test_fresh_client_returns_nulls(self):
        app.state.llm = LLMClient(groq_api_key="test-key")
        body = await _get_status()
        assert body["snapshot"] is None
        assert body["last_429"] is None
        assert body["tokens_used_24h"] is None  # no ledger wired
        assert body["model"] == "openai/gpt-oss-120b"
        assert body["tokens_per_day_limit"] > 0

    async def test_snapshot_reported_with_relative_times(self):
        client = LLMClient(groq_api_key="test-key")
        client.last_rate_limits = {
            "captured_at": time.time() - 10,
            "requests_limit": 1000,
            "requests_remaining": 998,
            "requests_reset_s": 60.0,
            "tokens_limit": 8000,
            "tokens_remaining": 7000,
            "tokens_reset_s": 5.0,
        }
        app.state.llm = client
        snap = (await _get_status())["snapshot"]
        assert snap["requests_limit"] == 1000
        assert snap["requests_remaining"] == 998
        assert snap["tokens_limit"] == 8000
        assert snap["tokens_remaining"] == 7000
        assert snap["age_s"] == pytest.approx(10, abs=3)
        assert snap["requests_reset_in_s"] == pytest.approx(50, abs=3)
        # tokens_reset was 5s as of 10s ago → already elapsed, clamps to 0
        assert snap["tokens_reset_in_s"] == 0

    async def test_snapshot_none_reset_stays_none(self):
        client = LLMClient(groq_api_key="test-key")
        client.last_rate_limits = {
            "captured_at": time.time(),
            "requests_limit": None,
            "requests_remaining": None,
            "requests_reset_s": None,
            "tokens_limit": 8000,
            "tokens_remaining": 7000,
            "tokens_reset_s": None,
        }
        app.state.llm = client
        snap = (await _get_status())["snapshot"]
        assert snap["requests_reset_in_s"] is None
        assert snap["tokens_reset_in_s"] is None

    async def test_last_429_reported_with_retry_countdown(self):
        client = LLMClient(groq_api_key="test-key")
        client.last_429 = {"at": time.time() - 2, "retry_after_s": 30.0}
        app.state.llm = client
        body = await _get_status()
        assert body["last_429"]["ago_s"] == pytest.approx(2, abs=3)
        assert body["last_429"]["retry_in_s"] == pytest.approx(28, abs=3)

    async def test_stale_429_retry_clamps_to_zero(self):
        client = LLMClient(groq_api_key="test-key")
        client.last_429 = {"at": time.time() - 100, "retry_after_s": 30.0}
        app.state.llm = client
        assert (await _get_status())["last_429"]["retry_in_s"] == 0

    async def test_tokens_used_24h_from_ledger(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(1234)
        app.state.llm = LLMClient(groq_api_key="test-key", usage_ledger=ledger)
        assert (await _get_status())["tokens_used_24h"] == 1234

    async def test_unwraps_cassette_client(self, tmp_path):
        import json as jsonlib

        from app.llm.cassette import CASSETTE_VERSION, CassetteLLMClient

        real = LLMClient(groq_api_key="test-key")
        real.last_rate_limits = {
            "captured_at": time.time(),
            "requests_limit": 1000,
            "requests_remaining": 42,
            "requests_reset_s": None,
            "tokens_limit": 8000,
            "tokens_remaining": 7000,
            "tokens_reset_s": None,
        }
        cassette_path = tmp_path / "cassette.json"
        cassette_path.write_text(jsonlib.dumps({"version": CASSETTE_VERSION, "calls": []}))
        app.state.llm = CassetteLLMClient(mode="mock", cassette_path=cassette_path, real_client=real)
        assert (await _get_status())["snapshot"]["requests_remaining"] == 42


class TestRateLimitProbe:
    async def test_probe_refreshes_and_returns_status(self):
        app.state.llm = LLMClient(groq_api_key="test-key")
        with respx.mock:
            respx.post(GROQ_API_URL).mock(
                return_value=Response(
                    200,
                    headers=_RL_HEADERS,
                    json={"choices": [{"message": {"content": "ok"}}]},
                )
            )
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
                response = await http.post("/api/llm/rate-limit/probe")
        assert response.status_code == 200
        assert response.json()["snapshot"]["tokens_remaining"] == 7000

    async def test_probe_without_api_key_returns_503(self):
        app.state.llm = LLMClient(groq_api_key=None)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.post("/api/llm/rate-limit/probe")
        assert response.status_code == 503


async def _get_health() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        response = await http.get("/api/llm/health")
    assert response.status_code == 200
    return response.json()


class TestLlmHealth:
    async def test_healthy_when_no_failures(self):
        app.state.llm = LLMClient(groq_api_key="test-key")
        body = await _get_health()
        assert body["healthy"] is True
        assert body["consecutive_failures"] == 0
        assert body["last_error"] is None
        assert body["fallback_allowed"] is False
        assert body["llm_mode"] == settings.llm_mode

    async def test_unhealthy_when_two_consecutive_failures(self):
        # Health only reflects real failure state outside mock mode; in mock mode
        # the endpoint short-circuits to healthy=True (see test_mock_mode_always_healthy).
        # Pin a non-mock mode so this runs identically in CI (LLM_MODE=mock) and dev.
        original_mode = settings.llm_mode
        settings.llm_mode = "live"
        try:
            client = LLMClient(groq_api_key="test-key", max_retries_429=0)
            client.consecutive_primary_failures = 3
            client.last_primary_error = {"status": 401, "message": "Groq returned HTTP 401", "at": time.time() - 10}
            app.state.llm = client
            body = await _get_health()
            assert body["healthy"] is False
            assert body["consecutive_failures"] == 3
            assert body["last_error"]["status"] == 401
            assert body["last_error"]["message"] == "Groq returned HTTP 401"
            assert body["last_error"]["ago_s"] == pytest.approx(10, abs=3)
            assert body["fallback_allowed"] is False
        finally:
            settings.llm_mode = original_mode

    async def test_mock_mode_always_healthy(self):
        original_mode = settings.llm_mode
        settings.llm_mode = "mock"
        try:
            client = LLMClient(groq_api_key="test-key")
            client.consecutive_primary_failures = 99
            app.state.llm = client
            body = await _get_health()
            assert body["healthy"] is True
            assert body["consecutive_failures"] == 0
        finally:
            settings.llm_mode = original_mode
