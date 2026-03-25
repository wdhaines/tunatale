"""LLM client tests using respx mocks."""

import pytest
import respx
from httpx import Response

from app.llm.client import GROQ_API_URL, LLMClient, LLMError


def _make_groq_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


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
        with pytest.raises(LLMError, match="429"):
            await client.complete("q")


@pytest.mark.asyncio
async def test_http_error_raises(client):
    with respx.mock:
        respx.post(GROQ_API_URL).mock(return_value=Response(500, json={}))
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
    import json

    parsed = json.loads(result)
    assert parsed["key"] == "value"
