"""The daily-budget refusal: TT declines the call rather than spending a request
to be told no.

Groq's TPD has no response header, and RPD only appears in a header AFTER a call
— so between calls the only thing that knows the day budget is TT's own ledger.
These tests lock the refusal semantics: either ceiling alone trips it, no HTTP
request is made, and the error names which ceiling and when it comes back.

Every timestamp is an ABSOLUTE constant. A ledger seeded relative to now passes
or fails depending on the hour the suite runs.
"""

from unittest.mock import AsyncMock

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from app.languages import get_language
from app.llm.client import GROQ_API_URL, LLMClient, LLMError, LLMQuotaExceededError
from app.llm.usage_ledger import DAY_S, UsageLedger
from app.main import app
from app.models.curriculum import Curriculum, CurriculumDay
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

T0 = 1_700_000_000.0
TOKEN_LIMIT = 200_000
REQUEST_LIMIT = 1_000


def _client(ledger, **kw):
    return LLMClient(
        groq_api_key="test-key",
        usage_ledger=ledger,
        tokens_per_day_limit=TOKEN_LIMIT,
        requests_per_day_limit=REQUEST_LIMIT,
        **kw,
    )


def _ok_response():
    return {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


class TestRefusal:
    async def test_token_ceiling_refuses_without_calling_groq(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(TOKEN_LIMIT, now=T0)
        client = _client(ledger)
        with respx.mock:
            route = respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_ok_response()))
            with pytest.raises(LLMQuotaExceededError) as exc:
                await client.complete("q", now=T0)
        # The whole point: we did not spend a request to be told no.
        assert not route.called
        assert "tokens per day" in str(exc.value)

    async def test_request_ceiling_alone_refuses(self, tmp_path):
        """Many tiny completions — the token budget still reads healthy."""
        ledger = UsageLedger(tmp_path / "usage.log")
        for _ in range(REQUEST_LIMIT):
            ledger.record(1, now=T0)
        client = _client(ledger)
        with respx.mock:
            route = respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_ok_response()))
            with pytest.raises(LLMQuotaExceededError) as exc:
                await client.complete("q", now=T0)
        assert not route.called
        assert "requests per day" in str(exc.value)

    async def test_message_names_the_ceiling_and_when_it_returns(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(TOKEN_LIMIT, now=T0)
        client = _client(ledger)
        with respx.mock:
            respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_ok_response()))
            with pytest.raises(LLMQuotaExceededError) as exc:
                await client.complete("q", now=T0)
        msg = str(exc.value)
        assert "200,000" in msg  # the real TPD, not the old half-value
        assert "24h" in msg or "24.0h" in msg  # full refill ETA

    async def test_message_avoids_the_pipeline_backoff_trigger(self, tmp_path):
        """`pipeline.py` retries on 'rate-limited'/'Ollama' in the message.

        A day-budget refusal must NOT match: retrying it in 15s is pointless, and
        the generation pipeline should fail it terminally instead.
        """
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(TOKEN_LIMIT, now=T0)
        client = _client(ledger)
        with respx.mock:
            respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_ok_response()))
            with pytest.raises(LLMQuotaExceededError) as exc:
                await client.complete("q", now=T0)
        msg = str(exc.value)
        assert "rate-limited" not in msg
        assert "Ollama" not in msg

    async def test_quota_error_is_an_llm_error(self, tmp_path):
        """Existing `except LLMError` handlers must not stop catching it."""
        assert issubclass(LLMQuotaExceededError, LLMError)

    async def test_budget_below_the_ceiling_calls_normally(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(TOKEN_LIMIT // 2, now=T0)
        client = _client(ledger)
        with respx.mock:
            route = respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_ok_response()))
            assert await client.complete("q", now=T0) == "ok"
        assert route.called

    async def test_drained_budget_lets_calls_through_again(self, tmp_path):
        """A full day later the bucket has refilled and the refusal lifts."""
        ledger = UsageLedger(tmp_path / "usage.log")
        ledger.record(TOKEN_LIMIT, now=T0)
        client = _client(ledger)
        with respx.mock:
            route = respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_ok_response()))
            assert await client.complete("q", now=T0 + DAY_S) == "ok"
        assert route.called

    async def test_no_ledger_never_refuses(self, tmp_path):
        """A client constructed without a ledger (most tests) is unaffected."""
        client = LLMClient(groq_api_key="test-key")
        with respx.mock:
            respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_ok_response()))
            assert await client.complete("q") == "ok"


class TestRequestsAreTallied:
    """RPD is spent by every call that reaches Groq, not just successful ones."""

    async def test_successful_call_records_one_request(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        client = _client(ledger)
        with respx.mock:
            respx.post(GROQ_API_URL).mock(return_value=Response(200, json=_ok_response()))
            await client.complete("q")
        assert ledger.requests_used(REQUEST_LIMIT) == 1
        assert ledger.tokens_used(TOKEN_LIMIT) == 15

    async def test_http_error_still_spends_a_request(self, tmp_path):
        ledger = UsageLedger(tmp_path / "usage.log")
        client = _client(ledger)
        with respx.mock:
            respx.post(GROQ_API_URL).mock(return_value=Response(500, text="boom"))
            with pytest.raises(LLMError):
                await client.complete("q")
        assert ledger.requests_used(REQUEST_LIMIT) == 1
        assert ledger.tokens_used(TOKEN_LIMIT) == 0

    async def test_every_429_retry_spends_a_request(self, tmp_path):
        """The retry loop's posts each hit Groq's RPD counter."""
        ledger = UsageLedger(tmp_path / "usage.log")
        client = _client(ledger, max_retries_429=2)
        with respx.mock:
            respx.post(GROQ_API_URL).mock(return_value=Response(429, headers={"retry-after": "0"}))
            with pytest.raises(LLMError):
                await client.complete("q")
        assert ledger.requests_used(REQUEST_LIMIT) == 3  # initial + 2 retries

    async def test_malformed_2xx_body_still_spends_a_request(self, tmp_path):
        """A 200 whose body will not parse still reached Groq's RPD counter."""
        ledger = UsageLedger(tmp_path / "usage.log")
        client = _client(ledger)
        with respx.mock:
            respx.post(GROQ_API_URL).mock(return_value=Response(200, json={"unexpected": "shape"}))
            with pytest.raises(LLMError):
                await client.complete("q")
        assert ledger.requests_used(REQUEST_LIMIT) == 1
        assert ledger.tokens_used(TOKEN_LIMIT) == 0

    async def test_transport_error_records_nothing(self, tmp_path):
        """No response means the request never reached Groq's counter."""
        import httpx

        ledger = UsageLedger(tmp_path / "usage.log")
        client = _client(ledger)
        with respx.mock:
            respx.post(GROQ_API_URL).mock(side_effect=httpx.ConnectError("down"))
            with pytest.raises(LLMError):
                await client.complete("q")
        assert ledger.requests_used(REQUEST_LIMIT) == 0


class TestQuotaRefusalOverHttp:
    """The refusal must reach the user as a clear 429, never a 500 traceback and
    never the 502 that means "the upstream provider failed" — nothing upstream
    failed here, TT declined to call.

    Same setup as ``test_generate_story_llm_error_502`` in test_api_story.py,
    which pins the neighbouring LLMError→502 mapping.
    """

    def _store_with_day_1(self):
        from app.storage.store import ContentStore

        store = ContentStore(":memory:")
        store.save_curriculum(
            "c1",
            Curriculum(
                id="c1",
                topic="coffee",
                language_code="sl",
                cefr_level="A2",
                days=[
                    CurriculumDay(
                        day=1,
                        title="Day 1",
                        focus="greetings",
                        learning_objective="greet",
                        story_guidance="café",
                        collocations=["zdravo"],
                    )
                ],
            ),
        )
        return store

    async def test_generate_story_quota_refusal_is_429(self):
        mock_generator = AsyncMock()
        mock_generator.generate = AsyncMock(
            side_effect=LLMQuotaExceededError(
                "Daily Groq budget exhausted: tokens per day (200,000 of 200,000); full budget restored in 24h0m"
            )
        )
        app.state.content_store = self._store_with_day_1()
        app.state.story_generator = mock_generator
        app.state.language = get_language("sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            response = await http.post(
                "/api/story/generate",
                json={"curriculum_id": "c1", "day": 1, "strategy": "WIDER"},
            )
        assert response.status_code == 429
        detail = response.json()["detail"]
        assert "tokens per day" in detail
        assert "24h" in detail
