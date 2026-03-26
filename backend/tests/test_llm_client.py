"""LLM client tests using respx mocks."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from httpx import Response

from app.llm.client import GROQ_API_URL, OLLAMA_DEFAULT_URL, LLMClient, LLMError

OLLAMA_GENERATE_URL = f"{OLLAMA_DEFAULT_URL}/api/generate"
OLLAMA_TAGS_URL = f"{OLLAMA_DEFAULT_URL}/api/tags"


def _make_groq_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _make_ollama_response(content: str) -> dict:
    return {"response": content}


@pytest.fixture
def client():
    return LLMClient(groq_api_key="test-key", max_retries_429=1, max_retry_after_s=5.0)


@pytest.mark.asyncio
async def test_complete_success(client):
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_make_groq_response("hello")))
        result = await client.complete("say hello", system_prompt=None)
    assert result == "hello"


@pytest.mark.asyncio
async def test_complete_with_system_prompt(client):
    with respx.mock:
        route = respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_make_groq_response("ok")))
        await client.complete("prompt", system_prompt="You are a helpful assistant.")
    body = route.calls[0].request.content
    import json

    payload = json.loads(body)
    assert any(m["role"] == "system" for m in payload["messages"])


@pytest.mark.asyncio
async def test_complete_strips_think_tags(client):
    content_with_think = "<think>reasoning here</think>actual answer"
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_make_groq_response(content_with_think)))
        result = await client.complete("q")
    assert result == "actual answer"
    assert "<think>" not in result


@pytest.mark.asyncio
async def test_rate_limit_retry_succeeds(client):
    with respx.mock:
        respx.post(GROQ_API_URL).mock(
            side_effect=[
                Response(429, headers={"retry-after": "0"}, json={}),
                Response(200, json=_make_groq_response("done")),
            ]
        )
        result = await client.complete("q")
    assert result == "done"


@pytest.mark.asyncio
async def test_rate_limit_exhausted_raises(client):
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(429, headers={"retry-after": "0"}, json={}))
        respx.post(OLLAMA_GENERATE_URL).mock(return_value=Response(500, json={}))
        with pytest.raises(LLMError, match="429"):
            await client.complete("q")


@pytest.mark.asyncio
async def test_http_error_raises(client):
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(500, json={}))
        respx.post(OLLAMA_GENERATE_URL).mock(return_value=Response(500, json={}))
        with pytest.raises(LLMError, match="500"):
            await client.complete("q")


@pytest.mark.asyncio
async def test_no_api_key_raises():
    client = LLMClient(groq_api_key=None)
    with pytest.raises(LLMError):
        await client.complete("q")


@pytest.mark.asyncio
async def test_json_response_parsing(client):
    json_content = '{"key": "value"}'
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_make_groq_response(json_content)))
        result = await client.complete("q")
    parsed = json.loads(result)
    assert parsed["key"] == "value"


# ── Fallback chain tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fallback_client_used_when_groq_fails():
    fallback = MagicMock()
    fallback.complete = AsyncMock(return_value="fallback response")
    client = LLMClient(groq_api_key="test-key", fallback_client=fallback, max_retries_429=0)
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(500, json={}))
        result = await client.complete("q")
    assert result == "fallback response"
    fallback.complete.assert_called_once()


@pytest.mark.asyncio
async def test_ollama_fallback_when_groq_fails():
    client = LLMClient(groq_api_key="test-key", max_retries_429=0)
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(500, json={}))
        respx.post(OLLAMA_GENERATE_URL).mock(return_value=Response(200, json=_make_ollama_response("ollama resp")))
        result = await client.complete("q")
    assert result == "ollama resp"


@pytest.mark.asyncio
async def test_ollama_fallback_when_all_groq_fail():
    fallback = MagicMock()
    fallback.complete = AsyncMock(
        side_effect=LLMError(
            "fallback failed",
            [{"provider": "groq", "model": "x", "status": 500, "error": "err", "latency_ms": 0}],
        )
    )
    client = LLMClient(groq_api_key="test-key", fallback_client=fallback, max_retries_429=0)
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(500, json={}))
        respx.post(OLLAMA_GENERATE_URL).mock(return_value=Response(200, json=_make_ollama_response("ollama resp")))
        result = await client.complete("q")
    assert result == "ollama resp"


@pytest.mark.asyncio
async def test_groq_extra_body_params_sent():
    client = LLMClient(
        groq_api_key="test-key",
        groq_extra_body_params={"reasoning_effort": "medium"},
    )
    with respx.mock:
        route = respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_make_groq_response("ok")))
        await client.complete("q")
    payload = json.loads(route.calls[0].request.content)
    assert payload.get("reasoning_effort") == "medium"


@pytest.mark.asyncio
async def test_groq_extra_body_params_uses_max_completion_tokens():
    client = LLMClient(
        groq_api_key="test-key",
        groq_extra_body_params={"reasoning_effort": "medium"},
    )
    with respx.mock:
        route = respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_make_groq_response("ok")))
        await client.complete("q", max_tokens=1024)
    payload = json.loads(route.calls[0].request.content)
    assert "max_completion_tokens" in payload
    assert "max_tokens" not in payload


@pytest.mark.asyncio
async def test_health_returns_groq_and_ollama_status():
    client = LLMClient(groq_api_key="test-key")
    with respx.mock:
        respx.get(OLLAMA_TAGS_URL).mock(return_value=Response(200, json={"models": []}))
        result = await client.health()
    assert result["groq"] is True
    assert result["ollama"] is True


@pytest.mark.asyncio
async def test_complete_system_prompt_preserved_with_extra_params():
    client = LLMClient(
        groq_api_key="test-key",
        groq_extra_body_params={"reasoning_effort": "medium"},
    )
    with respx.mock:
        route = respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_make_groq_response("ok")))
        await client.complete("q", system_prompt="You are helpful.")
    payload = json.loads(route.calls[0].request.content)
    assert any(m["role"] == "system" for m in payload["messages"])


# ── Additional coverage tests ─────────────────────────────────────────────


def test_parse_reset_duration_seconds():
    from app.llm.client import _parse_reset_duration

    assert _parse_reset_duration("2s") == 2.0
    assert _parse_reset_duration("500ms") == 0.5
    assert _parse_reset_duration("1m30s") == 90.0
    assert _parse_reset_duration("0s") == 0.0
    assert _parse_reset_duration("unknown") == 0.0


def test_pacing_info_defaults(client):
    info = client.pacing_info()
    assert "call_delay_s" in info
    assert info["call_delay_s"] == 0.0
    assert info["last_429_ago_s"] is None


@pytest.mark.asyncio
async def test_on_call_fires_on_success():
    calls = []
    client = LLMClient(groq_api_key="test-key", on_call=calls.append)
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_make_groq_response("hi")))
        await client.complete("q")
    assert len(calls) == 1
    assert calls[0]["status"] == "success"
    assert calls[0]["provider"] == "groq"


@pytest.mark.asyncio
async def test_on_call_fires_on_groq_error():
    calls = []
    client = LLMClient(groq_api_key="test-key", on_call=calls.append, max_retries_429=0)
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(500, json={}))
        respx.post(OLLAMA_GENERATE_URL).mock(return_value=Response(200, json=_make_ollama_response("ok")))
        await client.complete("q")
    assert any(c["status"] == 500 for c in calls)


@pytest.mark.asyncio
async def test_ollama_http_error_in_fallback():
    client = LLMClient(groq_api_key="test-key", max_retries_429=0)
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(500, json={}))
        respx.post(OLLAMA_GENERATE_URL).mock(return_value=Response(503, json={}))
        with pytest.raises(LLMError, match="All LLM backends failed"):
            await client.complete("q")


@pytest.mark.asyncio
async def test_health_ollama_unavailable():
    client = LLMClient(groq_api_key="test-key")
    with respx.mock:
        respx.get(OLLAMA_TAGS_URL).mock(return_value=Response(503, json={}))
        result = await client.health()
    assert result["groq"] is True
    assert result["ollama"] is False


@pytest.mark.asyncio
async def test_health_no_groq_key():
    client = LLMClient(groq_api_key=None)
    with respx.mock:
        respx.get(OLLAMA_TAGS_URL).mock(return_value=Response(200, json={"models": []}))
        result = await client.health()
    assert result["groq"] is False
    assert result["ollama"] is True


@pytest.mark.asyncio
async def test_groq_proactive_pacing_applied():
    """Verify rate-limit headers trigger proactive pacing."""
    client = LLMClient(groq_api_key="test-key")
    headers = {
        "x-ratelimit-remaining-requests": "1",
        "x-ratelimit-reset-requests": "10s",
    }
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(200, headers=headers, json=_make_groq_response("ok")))
        await client.complete("q")
    assert client._next_call_at > 0


@pytest.mark.asyncio
async def test_groq_proactive_pacing_req_exhausted():
    """Verify rem_req==0 triggers full window wait."""
    client = LLMClient(groq_api_key="test-key")
    headers = {
        "x-ratelimit-remaining-requests": "0",
        "x-ratelimit-reset-requests": "2s",
    }
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(200, headers=headers, json=_make_groq_response("ok")))
        await client.complete("q")
    assert client._next_call_at > 0


@pytest.mark.asyncio
async def test_groq_tpm_pacing_applied():
    """Verify token rate-limit headers trigger TPM pacing."""
    client = LLMClient(groq_api_key="test-key")
    headers = {
        "x-ratelimit-remaining-tokens": "0",
        "x-ratelimit-reset-tokens": "5s",
        "x-ratelimit-limit-tokens": "10000",
    }
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(200, headers=headers, json=_make_groq_response("ok")))
        await client.complete("q")
    assert client._next_call_at > 0


@pytest.mark.asyncio
async def test_groq_tpm_pacing_low_budget():
    """Verify token budget <20% triggers aggressive pacing."""
    client = LLMClient(groq_api_key="test-key")
    headers = {
        "x-ratelimit-remaining-tokens": "1000",  # 10% of 10000
        "x-ratelimit-reset-tokens": "5s",
        "x-ratelimit-limit-tokens": "10000",
    }
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(200, headers=headers, json=_make_groq_response("ok")))
        await client.complete("q")
    assert client._next_call_at > 0


@pytest.mark.asyncio
async def test_groq_rate_limit_headers_in_callback():
    """Verify numeric rate-limit headers are included in on_call."""
    calls = []
    client = LLMClient(groq_api_key="test-key", on_call=calls.append)
    headers = {
        "x-ratelimit-remaining-tokens": "5000",
        "x-ratelimit-limit-tokens": "10000",
        "x-ratelimit-remaining-requests": "10",
        "x-ratelimit-limit-requests": "30",
    }
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(200, headers=headers, json=_make_groq_response("ok")))
        await client.complete("q")
    assert calls[0].get("rate_limits") is not None
    assert calls[0]["rate_limits"]["tokens_remaining"] == 5000


@pytest.mark.asyncio
async def test_on_call_fires_with_reasoning_effort():
    calls = []
    client = LLMClient(
        groq_api_key="test-key",
        groq_extra_body_params={"reasoning_effort": "medium"},
        on_call=calls.append,
    )
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_make_groq_response("ok")))
        await client.complete("q")
    assert calls[0].get("reasoning_effort") == "medium"


@pytest.mark.asyncio
async def test_429_non_numeric_retry_after():
    """Verify non-numeric retry-after header is handled gracefully."""
    client = LLMClient(groq_api_key="test-key", max_retries_429=0)
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(429, headers={"retry-after": "not-a-number"}, json={}))
        respx.post(OLLAMA_GENERATE_URL).mock(return_value=Response(200, json=_make_ollama_response("ok")))
        result = await client.complete("q")
    assert result == "ok"


@pytest.mark.asyncio
async def test_ollama_connect_error_no_binary():
    """Verify Ollama ConnectError with no binary raises LLMError."""
    client = LLMClient(groq_api_key="test-key", max_retries_429=0)
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(500, json={}))
        respx.post(OLLAMA_GENERATE_URL).mock(side_effect=httpx.ConnectError("refused"))
        with patch("shutil.which", return_value=None), pytest.raises(LLMError, match="auto-start failed"):
            await client.complete("q")


@pytest.mark.asyncio
async def test_groq_timeout_raises():
    """Verify Groq timeout raises LLMError and falls back to Ollama."""
    client = LLMClient(groq_api_key="test-key", max_retries_429=0)
    with respx.mock:
        respx.post(GROQ_API_URL).mock(side_effect=httpx.TimeoutException("timeout"))
        respx.post(OLLAMA_GENERATE_URL).mock(return_value=Response(200, json=_make_ollama_response("fallback")))
        result = await client.complete("q")
    assert result == "fallback"


@pytest.mark.asyncio
async def test_health_raises_exception():
    """Verify health() handles exception in Ollama check."""
    client = LLMClient(groq_api_key="test-key")
    with respx.mock:
        respx.get(OLLAMA_TAGS_URL).mock(side_effect=httpx.ConnectError("refused"))
        result = await client.health()
    assert result["groq"] is True
    assert result["ollama"] is False


@pytest.mark.asyncio
async def test_pacing_wait_applied():
    """Verify _next_call_at causes a wait before the request."""
    import time

    client = LLMClient(groq_api_key="test-key")
    client._next_call_at = time.monotonic() + 0.01  # tiny wait
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_make_groq_response("ok")))
        result = await client.complete("q")
    assert result == "ok"


@pytest.mark.asyncio
async def test_pacing_reset_after_60s():
    """Verify _groq_call_delay is reset after 60s with no 429."""
    import time

    client = LLMClient(groq_api_key="test-key")
    client._groq_call_delay = 2.0  # pretend we had a 429
    client._last_429_at = time.monotonic() - 65  # 65 seconds ago — triggers reset
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_make_groq_response("ok")))
        await client.complete("q")
    assert client._groq_call_delay == 0.0


@pytest.mark.asyncio
async def test_ollama_system_prompt_sent():
    """Verify system_prompt is included in Ollama request body."""
    client = LLMClient(groq_api_key="test-key", max_retries_429=0)
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(500, json={}))
        route = respx.post(OLLAMA_GENERATE_URL).mock(return_value=Response(200, json=_make_ollama_response("ok")))
        await client.complete("q", system_prompt="Be helpful.")
    payload = json.loads(route.calls[0].request.content)
    assert payload.get("system") == "Be helpful."


@pytest.mark.asyncio
async def test_ollama_timeout_raises():
    """Verify Ollama TimeoutException raises LLMError."""
    client = LLMClient(groq_api_key="test-key", max_retries_429=0)
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(500, json={}))
        respx.post(OLLAMA_GENERATE_URL).mock(side_effect=httpx.TimeoutException("timeout"))
        with pytest.raises(LLMError, match="timed out"):
            await client.complete("q")
