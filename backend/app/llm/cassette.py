"""VCR-style cassette recording/replay for LLMClient.

Ported from voynich-encoder's llm_cassette.py — hash-based lookup (not sequential),
so multiple test scenarios can share one cassette without interfering.

Modes:
  mock   — replay only; raise RuntimeError on cache miss
  record — call real LLM and save all responses
  live   — call real LLM without saving
  patch  — replay known; call real LLM for new prompts and save them
"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import LLMClient


def _hash_prompt(prompt: str) -> str:
    return "sha256:" + hashlib.sha256(prompt.encode()).hexdigest()[:16]


class CassetteLLMClient:
    """LLMClient wrapper with cassette-based mock/live/record/patch modes."""

    def __init__(
        self,
        mode: str,  # "mock" | "live" | "record" | "patch"
        cassette_path: Path,
        real_client: LLMClient | None = None,
    ) -> None:
        self._mode = mode
        self._cassette_path = cassette_path
        self._real_client = real_client
        self.last_provider: str | None = None

        self._calls: list[dict] = []
        self._playback_by_hash: dict[str, list[dict]] = {}
        self._playback_used: dict[str, int] = {}

        if mode in ("mock", "patch"):
            data = json.loads(cassette_path.read_text())
            for entry in data["calls"]:
                h = entry["prompt_hash"]
                self._playback_by_hash.setdefault(h, []).append(entry)
            if mode == "patch":
                self._calls = list(data["calls"])

    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 256,
    ) -> str:
        if self._mode == "mock":
            return self._replay(prompt)
        if self._mode == "patch":
            return await self._patch(
                prompt, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens
            )
        assert self._real_client is not None, "real_client required for live/record mode"
        response = await self._real_client.complete(
            prompt, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens
        )
        self.last_provider = self._real_client.last_provider
        if self._mode == "record":
            self._calls.append(
                {
                    "prompt_hash": _hash_prompt(prompt),
                    "prompt_preview": prompt[:80].replace("\n", " "),
                    "max_tokens": max_tokens,
                    "response": response,
                    "provider": self.last_provider,
                }
            )
            self.save()
        return response

    def _replay(self, prompt: str) -> str:
        h = _hash_prompt(prompt)
        entries = self._playback_by_hash.get(h)
        if not entries:
            raise RuntimeError(
                f"Cassette has no entry for prompt hash {h}.\n  Preview: {prompt[:80]!r}\nRe-record with --llm-mode=record."
            )
        idx = self._playback_used.get(h, 0)
        if idx >= len(entries):
            raise RuntimeError(
                f"Cassette entry {h!r} used {idx} times but only {len(entries)} recorded.\n  Preview: {prompt[:80]!r}"
            )
        entry = entries[idx]
        self._playback_used[h] = idx + 1
        self.last_provider = entry.get("provider", "groq")
        return entry["response"]

    async def _patch(self, prompt: str, **kwargs) -> str:
        h = _hash_prompt(prompt)
        entries = self._playback_by_hash.get(h)
        if entries:
            idx = self._playback_used.get(h, 0)
            if idx < len(entries):
                entry = entries[idx]
                self._playback_used[h] = idx + 1
                self.last_provider = entry.get("provider", "groq")
                return entry["response"]

        assert self._real_client is not None, "real_client required for patch mode"
        response = await self._real_client.complete(prompt, **kwargs)
        self.last_provider = self._real_client.last_provider
        new_entry = {
            "prompt_hash": h,
            "prompt_preview": prompt[:80].replace("\n", " "),
            "max_tokens": kwargs.get("max_tokens", 256),
            "response": response,
            "provider": self.last_provider,
        }
        self._calls.append(new_entry)
        self._playback_by_hash.setdefault(h, []).append(new_entry)
        self.save()
        return response

    def save(self) -> None:
        if self._mode not in ("record", "patch"):
            return
        self._cassette_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "recorded_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "calls": self._calls,
        }
        self._cassette_path.write_text(json.dumps(data, indent=2) + "\n")
