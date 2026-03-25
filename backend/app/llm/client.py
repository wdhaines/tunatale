"""Async LLM client — Groq via OpenAI-compatible API."""

from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"


class LLMError(Exception):
    """Raised when the LLM call fails."""

    def __init__(self, message: str, attempts: list[dict] | None = None) -> None:
        super().__init__(message)
        self.attempts = attempts or []


class LLMClient:
    def __init__(
        self,
        groq_api_key: str | None = None,
        groq_model: str = GROQ_DEFAULT_MODEL,
        timeout: float = 30.0,
        max_retries_429: int = 3,
        max_retry_after_s: float = 10.0,
    ) -> None:
        self.groq_api_key = groq_api_key
        self.groq_model = groq_model
        self.timeout = timeout
        self.max_retries_429 = max_retries_429
        self.max_retry_after_s = max_retry_after_s
        self.last_provider: str | None = None
        self._next_call_at: float = 0.0
        self._groq_call_delay: float = 0.0
        self._last_429_at: float = 0.0

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        if not self.groq_api_key:
            raise LLMError("No GROQ_API_KEY configured")
        return await self._call_groq(
            prompt, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens
        )

    async def _call_groq(
        self,
        prompt: str,
        system_prompt: str | None,
        temperature: float,
        max_tokens: int,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.groq_api_key}",
            "Content-Type": "application/json",
        }
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body = {
            "model": self.groq_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as http:
            for attempt in range(self.max_retries_429 + 1):
                if self._groq_call_delay > 0 and time.monotonic() - self._last_429_at > 60:
                    self._groq_call_delay = 0.0
                wait = self._next_call_at - time.monotonic()
                if wait > 0:
                    await asyncio.sleep(wait)

                start = time.monotonic()
                response = await http.post(GROQ_API_URL, headers=headers, json=body)
                latency_ms = int((time.monotonic() - start) * 1000)

                if response.status_code == 429:
                    retry_after_raw = response.headers.get("retry-after", "2")
                    try:
                        retry_after = float(retry_after_raw)
                    except ValueError:
                        retry_after = 2.0

                    msg = f"Groq returned 429 Too Many Requests (retry after {retry_after_raw}s)"

                    if retry_after <= self.max_retry_after_s:
                        self._last_429_at = time.monotonic()
                        self._groq_call_delay = retry_after

                    if attempt < self.max_retries_429 and retry_after <= self.max_retry_after_s:
                        logger.warning(
                            "Groq 429, retry %d/%d after %.1fs", attempt + 1, self.max_retries_429, retry_after
                        )
                        await asyncio.sleep(retry_after)
                        continue

                    raise LLMError(
                        msg,
                        [
                            {
                                "provider": "groq",
                                "model": self.groq_model,
                                "status": 429,
                                "error": msg,
                                "latency_ms": latency_ms,
                            }
                        ],
                    )

                if not response.is_success:
                    msg = f"Groq returned HTTP {response.status_code}"
                    raise LLMError(
                        msg,
                        [
                            {
                                "provider": "groq",
                                "model": self.groq_model,
                                "status": response.status_code,
                                "error": msg,
                                "latency_ms": latency_ms,
                            }
                        ],
                    )

                data = response.json()
                content = data["choices"][0]["message"]["content"]
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                self.last_provider = "groq"
                logger.info("Groq success: model=%s latency=%dms", self.groq_model, latency_ms)
                return content

        raise LLMError("Groq call loop exhausted", [])  # pragma: no cover
