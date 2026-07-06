"""Tests for ActivityLog ring buffer and the GET /api/llm/activity endpoint."""

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from app.llm.activity import ActivityLog
from app.llm.client import GROQ_API_URL, OLLAMA_DEFAULT_URL, LLMClient, LLMError
from app.main import app

# ── Unit tests for ActivityLog ──────────────────────────────────────────────


class TestActivityLogUnit:
    def test_ring_bounds_at_maxlen(self):
        log = ActivityLog(maxlen=3)
        for i in range(5):
            log.record_llm_call({"timestamp": i, "provider": "groq"})
        events, latest = log.events_since(0)
        assert len(events) == 3
        assert latest == 5
        assert [e["seq"] for e in events] == [3, 4, 5]

    def test_seq_monotonic(self):
        log = ActivityLog(maxlen=100)
        for i in range(10):
            log.record_llm_call({"timestamp": i})
        events, latest = log.events_since(0)
        assert latest == 10
        assert [e["seq"] for e in events] == list(range(1, 11))

    def test_events_since_filter(self):
        log = ActivityLog(maxlen=100)
        for i in range(5):
            log.record_llm_call({"timestamp": i})
        events, latest = log.events_since(3)
        assert [e["seq"] for e in events] == [4, 5]

    def test_events_since_latest_returns_empty(self):
        log = ActivityLog(maxlen=100)
        for i in range(3):
            log.record_llm_call({"timestamp": i})
        events, latest = log.events_since(3)
        assert events == []

    def test_record_pipeline_event(self):
        log = ActivityLog(maxlen=100)
        log.record_pipeline("cur-1", 1, "generating", "Started generation")
        events, latest = log.events_since(0)
        assert len(events) == 1
        e = events[0]
        assert e["kind"] == "pipeline"
        assert e["curriculum_id"] == "cur-1"
        assert e["day"] == 1
        assert e["state"] == "generating"
        assert e["message"] == "Started generation"
        assert "timestamp" in e
        assert e["seq"] == 1

    def test_mixed_events(self):
        log = ActivityLog(maxlen=100)
        log.record_llm_call({"provider": "groq"})
        log.record_pipeline("cur-1", 1, "ready", "Done")
        events, latest = log.events_since(0)
        assert len(events) == 2
        assert events[0]["kind"] == "llm_call"
        assert events[1]["kind"] == "pipeline"


# ── LLMClient integration tests (respx) ────────────────────────────────────


async def test_llm_client_on_call_success():
    """A Groq 200 response lands a kind=llm_call status=success event."""
    log = ActivityLog(maxlen=100)
    client = LLMClient(groq_api_key="k", on_call=log.record_llm_call)
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(200, json={"choices": [{"message": {"content": "ok"}}]}))
        await client.complete("hello")
    events, latest = log.events_since(0)
    success = [e for e in events if e.get("status") == "success"]
    assert len(success) == 1
    assert success[0]["provider"] == "groq"


async def test_llm_client_on_call_429():
    """A 429 response records a kind=llm_call with status=429."""
    log = ActivityLog(maxlen=100)
    client = LLMClient(groq_api_key="k", on_call=log.record_llm_call, max_retries_429=0)
    OLLAMA_GENERATE_URL = f"{OLLAMA_DEFAULT_URL}/api/generate"
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(429, headers={"retry-after": "2"}, json={}))
        respx.post(OLLAMA_GENERATE_URL).mock(return_value=Response(500, json={"error": "mock ollama fail"}))
        with pytest.raises(LLMError):
            await client.complete("hello")
    events, latest = log.events_since(0)
    assert any(e.get("status") == 429 for e in events)


# ── GET /api/llm/activity endpoint tests ────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_activity_state():
    yield
    if hasattr(app.state, "activity_log"):
        delattr(app.state, "activity_log")


class TestActivityEndpoint:
    async def test_returns_events_since_seq(self):
        log = ActivityLog(maxlen=100)
        log.record_llm_call({"provider": "groq", "status": "success"})
        app.state.activity_log = log
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.get("/api/llm/activity?since=0")
        assert response.status_code == 200
        body = response.json()
        assert body["latest"] == 1
        assert len(body["events"]) == 1
        assert body["events"][0]["seq"] == 1

    async def test_no_activity_log_returns_empty(self):
        if hasattr(app.state, "activity_log"):
            del app.state.activity_log
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.get("/api/llm/activity?since=0")
        assert response.status_code == 200
        assert response.json() == {"latest": 0, "events": []}

    async def test_since_latest_returns_empty(self):
        log = ActivityLog(maxlen=100)
        log.record_llm_call({"provider": "groq"})
        app.state.activity_log = log
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.get("/api/llm/activity?since=1")
        assert response.status_code == 200
        assert response.json()["events"] == []
