"""Async LLM client — Groq primary, fallback_client secondary, Ollama offline fallback."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import subprocess
import time
from collections.abc import Callable

import httpx

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
OLLAMA_DEFAULT_URL = "http://localhost:11434"
OLLAMA_DEFAULT_MODEL = "llama3.2"


def reasoning_params_for_model(model: str) -> dict | None:
    """Extra Groq body params a given model needs, or None for plain instruct models.

    gpt-oss models are reasoning models: at the default effort they spend the
    entire ``max_completion_tokens`` budget on hidden reasoning tokens and return
    empty ``content``. Pinning ``reasoning_effort="low"`` (the floor Groq accepts —
    only low/medium/high are valid) keeps reasoning small enough that the JSON
    payload actually gets emitted. Setting this also flips the client to send
    ``max_completion_tokens`` instead of ``max_tokens`` (see ``_call_groq``).
    """
    if "gpt-oss" in model:
        return {"reasoning_effort": "low"}
    return None


def _parse_reset_duration(s: str) -> float:
    """Parse Groq's x-ratelimit-reset-requests header, e.g. '2s', '500ms', '1m30s' → seconds."""
    total = 0.0
    m = re.fullmatch(r"(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s)?(?:(\d+)ms)?", s.strip())
    if m and any(m.groups()):
        if m.group(1):
            total += int(m.group(1)) * 60
        if m.group(2):
            total += float(m.group(2))
        if m.group(3):
            total += int(m.group(3)) / 1000
    return total


class LLMError(Exception):
    """Raised when all LLM backends fail."""

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
        ollama_url: str = OLLAMA_DEFAULT_URL,
        ollama_model: str = OLLAMA_DEFAULT_MODEL,
        groq_extra_body_params: dict | None = None,
        on_call: Callable[[dict], None] | None = None,
        fallback_client: LLMClient | None = None,
    ) -> None:
        self.groq_api_key = groq_api_key
        self.groq_model = groq_model
        self.timeout = timeout
        self.max_retries_429 = max_retries_429
        self.max_retry_after_s = max_retry_after_s
        self.ollama_url = ollama_url
        self.ollama_model = ollama_model
        self.groq_extra_body_params = groq_extra_body_params
        self.on_call = on_call
        self.fallback_client = fallback_client
        self.last_provider: str | None = None
        self._next_call_at: float = 0.0
        self._groq_call_delay: float = 0.0
        self._last_429_at: float = 0.0

    def _fire_callback(
        self,
        *,
        provider: str,
        model: str,
        latency_ms: int,
        status: str | int,
        prompt: str = "",
        response_text: str | None = None,
        error: str | None = None,
        rate_limits: dict | None = None,
        is_fallback: bool = False,
    ) -> None:
        if self.on_call is None:
            return
        info: dict = {
            "timestamp": time.time(),
            "provider": provider,
            "model": model,
            "latency_ms": latency_ms,
            "status": status,
            "is_fallback": is_fallback,
        }
        if prompt:
            info["prompt_preview"] = prompt[:80]
        if response_text is not None:
            info["response_preview"] = response_text[:200]
        if error is not None:
            info["error"] = error
        if rate_limits is not None:
            info["rate_limits"] = rate_limits
        if self.groq_extra_body_params and "reasoning_effort" in self.groq_extra_body_params:
            info["reasoning_effort"] = self.groq_extra_body_params["reasoning_effort"]
        self.on_call(info)

    @staticmethod
    def _make_attempt(provider: str, model: str, status: str | int, error: str, latency_ms: int) -> dict:
        return {"provider": provider, "model": model, "status": status, "error": error, "latency_ms": latency_ms}

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """Try Groq, then fallback_client, then Ollama; raise LLMError if all fail."""
        if not self.groq_api_key:
            raise LLMError("No GROQ_API_KEY configured")

        attempts: list[dict] = []

        try:
            return await self._call_groq(
                prompt, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens
            )
        except LLMError as e:
            attempts.extend(e.attempts)
            logger.warning("Groq failed, trying fallback: %s", e)
            if self.fallback_client is not None:
                try:
                    return await self.fallback_client.complete(
                        prompt, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens
                    )
                except LLMError as fe:
                    attempts.extend(fe.attempts)
                    logger.warning("Fallback client also failed: %s", fe)

        try:
            return await self._call_ollama(
                prompt,
                max_tokens,
                system_prompt=system_prompt,
                temperature=temperature,
                is_fallback=True,
            )
        except LLMError as e:
            attempts.extend(e.attempts)
            logger.warning("Ollama also failed: %s", e)

        msgs = "; ".join(f"{a['provider']}: {a['error']}" for a in attempts)
        raise LLMError(f"All LLM backends failed: {msgs}", attempts)

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

        body: dict = {
            "model": self.groq_model,
            "messages": messages,
            "temperature": temperature,
        }
        if self.groq_extra_body_params:
            body.update(self.groq_extra_body_params)
            if "max_completion_tokens" not in body:
                body["max_completion_tokens"] = max_tokens
        else:
            body["max_tokens"] = max_tokens

        async with httpx.AsyncClient(timeout=self.timeout) as http:
            for attempt in range(self.max_retries_429 + 1):
                if self._groq_call_delay > 0 and time.monotonic() - self._last_429_at > 60:
                    self._groq_call_delay = 0.0
                wait = self._next_call_at - time.monotonic()
                if wait > 0:
                    logger.info("Groq RPM pacing: waiting %.1fs", wait)
                    await asyncio.sleep(wait)

                start = time.monotonic()
                try:
                    response = await http.post(GROQ_API_URL, headers=headers, json=body)
                except httpx.TimeoutException as err:
                    latency_ms = int((time.monotonic() - start) * 1000)
                    msg = f"Groq timed out after {self.timeout}s"
                    self._fire_callback(
                        provider="groq",
                        model=self.groq_model,
                        latency_ms=latency_ms,
                        status="timeout",
                        prompt=prompt,
                        error=msg,
                    )
                    raise LLMError(
                        msg, [self._make_attempt("groq", self.groq_model, "timeout", msg, latency_ms)]
                    ) from err
                latency_ms = int((time.monotonic() - start) * 1000)

                # Log rate-limit headers
                rl_tokens_remaining = response.headers.get("x-ratelimit-remaining-tokens", "?")
                rl_tokens_limit = response.headers.get("x-ratelimit-limit-tokens", "?")
                rl_requests_remaining = response.headers.get("x-ratelimit-remaining-requests", "?")
                rl_requests_limit = response.headers.get("x-ratelimit-limit-requests", "?")
                logger.info(
                    "Groq rate-limit: tokens=%s/%s requests=%s/%s",
                    rl_tokens_remaining,
                    rl_tokens_limit,
                    rl_requests_remaining,
                    rl_requests_limit,
                )

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
                        self._fire_callback(
                            provider="groq",
                            model=self.groq_model,
                            latency_ms=latency_ms,
                            status=429,
                            prompt=prompt,
                            error=msg,
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    self._fire_callback(
                        provider="groq",
                        model=self.groq_model,
                        latency_ms=latency_ms,
                        status=429,
                        prompt=prompt,
                        error=msg,
                    )
                    raise LLMError(msg, [self._make_attempt("groq", self.groq_model, 429, msg, latency_ms)])

                if not response.is_success:
                    msg = f"Groq returned HTTP {response.status_code}"
                    self._fire_callback(
                        provider="groq",
                        model=self.groq_model,
                        latency_ms=latency_ms,
                        status=response.status_code,
                        prompt=prompt,
                        error=msg,
                    )
                    raise LLMError(
                        msg, [self._make_attempt("groq", self.groq_model, response.status_code, msg, latency_ms)]
                    )

                data = response.json()
                content = data["choices"][0]["message"]["content"]
                content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
                self.last_provider = "groq"
                logger.info("Groq success: model=%s latency=%dms", self.groq_model, latency_ms)

                if self.on_call:
                    _rl: dict = {}
                    if rl_tokens_remaining.isdigit():
                        _rl["tokens_remaining"] = int(rl_tokens_remaining)
                    if rl_tokens_limit.isdigit():
                        _rl["tokens_limit"] = int(rl_tokens_limit)
                    if rl_requests_remaining.isdigit():
                        _rl["requests_remaining"] = int(rl_requests_remaining)
                    if rl_requests_limit.isdigit():
                        _rl["requests_limit"] = int(rl_requests_limit)
                    self._fire_callback(
                        provider="groq",
                        model=self.groq_model,
                        latency_ms=latency_ms,
                        status="success",
                        prompt=prompt,
                        response_text=content,
                        rate_limits=_rl if _rl else None,
                    )

                # Proactive pacing: RPM + TPM
                proactive_delay = 0.0
                rem_req_raw = response.headers.get("x-ratelimit-remaining-requests", "")
                rst_req_raw = response.headers.get("x-ratelimit-reset-requests", "")
                if rem_req_raw.isdigit() and rst_req_raw:
                    rem_req = int(rem_req_raw)
                    rst_req_s = _parse_reset_duration(rst_req_raw)
                    if rem_req == 0 and rst_req_s > 0:
                        proactive_delay = rst_req_s
                    elif rem_req > 0 and rst_req_s > 0:
                        proactive_delay = rst_req_s / rem_req

                rem_tok_raw = response.headers.get("x-ratelimit-remaining-tokens", "")
                rst_tok_raw = response.headers.get("x-ratelimit-reset-tokens", "")
                lim_tok_raw = response.headers.get("x-ratelimit-limit-tokens", "")
                if rem_tok_raw.isdigit() and rst_tok_raw and lim_tok_raw.isdigit():
                    rem_tok = int(rem_tok_raw)
                    rst_tok_s = _parse_reset_duration(rst_tok_raw)
                    lim_tok = int(lim_tok_raw)
                    if rem_tok == 0 and rst_tok_s > 0:
                        proactive_delay = max(proactive_delay, rst_tok_s)
                    elif rem_tok > 0 and rst_tok_s > 0 and lim_tok > 0 and rem_tok < lim_tok * 0.20:
                        tokens_per_call = body.get("max_completion_tokens") or body.get("max_tokens") or max_tokens
                        calls_left = max(rem_tok / max(tokens_per_call, 1), 1.0)
                        tok_delay = rst_tok_s / calls_left
                        proactive_delay = max(proactive_delay, tok_delay)

                if proactive_delay > 0.5:
                    logger.info(
                        "Groq proactive pacing: req=%s/%s tok=%s/%s → %.2fs delay",
                        rem_req_raw,
                        rst_req_raw,
                        rem_tok_raw,
                        rst_tok_raw,
                        proactive_delay,
                    )
                delay = max(self._groq_call_delay, proactive_delay)
                self._next_call_at = time.monotonic() + delay
                return content

        raise LLMError("Groq call loop exhausted", [])  # pragma: no cover

    async def _start_ollama(self) -> bool:  # pragma: no cover
        """Try to start 'ollama serve' in the background. Returns True if started."""
        if shutil.which("ollama") is None:
            logger.warning("ollama binary not found in PATH")
            return False
        try:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info("Started 'ollama serve', waiting for it to be ready...")
            for _ in range(20):  # up to 10s
                await asyncio.sleep(0.5)
                try:
                    async with httpx.AsyncClient(timeout=2.0) as http:
                        resp = await http.get(f"{self.ollama_url}/api/tags")
                        if resp.is_success:
                            logger.info("Ollama is ready")
                            return True
                except Exception:
                    pass
            logger.warning("Ollama started but not ready after 10s")
            return False
        except OSError as e:
            logger.warning("Failed to start ollama: %s", e)
            return False

    async def _call_ollama(
        self,
        prompt: str,
        max_tokens: int,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        is_fallback: bool = False,
    ) -> str:
        body: dict = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if system_prompt:
            body["system"] = system_prompt

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                response = await http.post(f"{self.ollama_url}/api/generate", json=body)
        except httpx.ConnectError:
            if await self._start_ollama():  # pragma: no cover
                start = time.monotonic()  # pragma: no cover
                try:  # pragma: no cover
                    async with httpx.AsyncClient(timeout=self.timeout) as http:  # pragma: no cover
                        response = await http.post(f"{self.ollama_url}/api/generate", json=body)  # pragma: no cover
                except (httpx.ConnectError, httpx.TimeoutException) as err:  # pragma: no cover
                    latency_ms = int((time.monotonic() - start) * 1000)  # pragma: no cover
                    status = (
                        "timeout" if isinstance(err, httpx.TimeoutException) else "connect_error"
                    )  # pragma: no cover
                    msg = f"Ollama {status} at {self.ollama_url} (after auto-start)"  # pragma: no cover
                    self._fire_callback(
                        provider="ollama",
                        model=self.ollama_model,
                        latency_ms=latency_ms,  # pragma: no cover
                        status=status,
                        prompt=prompt,
                        error=msg,
                        is_fallback=is_fallback,
                    )
                    raise LLMError(
                        msg, [self._make_attempt("ollama", self.ollama_model, status, msg, latency_ms)]
                    ) from err  # pragma: no cover
            else:
                latency_ms = int((time.monotonic() - start) * 1000)
                msg = f"Ollama connection refused at {self.ollama_url} (auto-start failed)"
                self._fire_callback(
                    provider="ollama",
                    model=self.ollama_model,
                    latency_ms=latency_ms,
                    status="connect_error",
                    prompt=prompt,
                    error=msg,
                    is_fallback=is_fallback,
                )
                raise LLMError(
                    msg, [self._make_attempt("ollama", self.ollama_model, "connect_error", msg, latency_ms)]
                ) from None
        except httpx.TimeoutException as err:
            latency_ms = int((time.monotonic() - start) * 1000)
            msg = f"Ollama timed out after {self.timeout}s"
            self._fire_callback(
                provider="ollama",
                model=self.ollama_model,
                latency_ms=latency_ms,
                status="timeout",
                prompt=prompt,
                error=msg,
                is_fallback=is_fallback,
            )
            raise LLMError(msg, [self._make_attempt("ollama", self.ollama_model, "timeout", msg, latency_ms)]) from err

        latency_ms = int((time.monotonic() - start) * 1000)

        if not response.is_success:
            msg = f"Ollama returned HTTP {response.status_code}"
            self._fire_callback(
                provider="ollama",
                model=self.ollama_model,
                latency_ms=latency_ms,
                status=response.status_code,
                prompt=prompt,
                error=msg,
                is_fallback=is_fallback,
            )
            raise LLMError(
                msg, [self._make_attempt("ollama", self.ollama_model, response.status_code, msg, latency_ms)]
            )

        data = response.json()
        result = data["response"].strip()
        logger.info("Ollama success: model=%s latency=%dms", self.ollama_model, latency_ms)
        self.last_provider = "ollama"
        self._fire_callback(
            provider="ollama",
            model=self.ollama_model,
            latency_ms=latency_ms,
            status="success",
            prompt=prompt,
            response_text=result,
            is_fallback=is_fallback,
        )
        return result

    async def health(self) -> dict:
        """Return which backends are available."""
        result = {"groq": bool(self.groq_api_key), "ollama": False}
        try:
            async with httpx.AsyncClient(timeout=3.0) as http:
                response = await http.get(f"{self.ollama_url}/api/tags")
                result["ollama"] = response.is_success
        except Exception:
            pass
        return result
