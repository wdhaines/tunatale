"""LLM-based image candidate chooser.

Given a flashcard word, its English meaning, a search query, and a list of
Pixabay hit dicts, ask the project's existing LLM which candidate photo best
depicts the meaning.

Resilience contract — two outcomes:
  * ``dict``        → chosen hit dict (a subset of a Pixabay JSON hit)
  * ``None``        → no usable opinion; caller falls back to ``best_hit``
                       (LLM unavailable, failed, empty hits, unparseable reply,
                       or "0" / out-of-range — never block card creation on it)
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

logger = logging.getLogger(__name__)


class _LLM(Protocol):
    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = ...,
        temperature: float = ...,
        max_tokens: int = ...,
    ) -> str: ...


IMAGE_CHOICE_SYSTEM_PROMPT = (
    "You are a stock-photo selector for a language-learning flashcard. "
    "Given a word, its English meaning, the search query used, and a numbered "
    "list of stock-photo candidates described by their tags, reply with ONLY "
    "the number of the candidate whose photo best depicts the word's meaning "
    "in the given sense. If none of the candidates are a good fit, reply 0. "
    "Reply with only the number — no words, no punctuation, no explanation."
)


def build_image_choice_prompt(
    word: str,
    english: str,
    query: str,
    hits: list[dict],
    *,
    max_candidates: int = 12,
) -> str:
    lines = [
        f"Word: {word}",
        f"Meaning: {english}",
        f"Query: {query}",
    ]
    for i, hit in enumerate(hits[:max_candidates], 1):
        tags = hit.get("tags", "")
        w = hit.get("imageWidth", 0)
        h = hit.get("imageHeight", 0)
        likes = hit.get("likes", 0)
        lines.append(f"{i}. tags: {tags} ({w}x{h}, {likes} likes)")
    return "\n".join(lines)


def parse_image_choice_response(raw: str | None) -> int | None:
    if not raw:
        return None
    match = re.search(r"\d+", raw)
    if not match:
        return None
    return int(match.group())


async def choose_image_hit(
    word: str,
    english: str,
    query: str,
    hits: list[dict],
    *,
    llm: _LLM | None,
    max_candidates: int = 12,
) -> dict | None:
    if llm is None or not hits:
        return None
    prompt = build_image_choice_prompt(word, english, query, hits, max_candidates=max_candidates)
    try:
        raw = await llm.complete(
            prompt,
            system_prompt=IMAGE_CHOICE_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=256,
        )
    except Exception as exc:  # noqa: BLE001 — never block card creation on the LLM
        logger.warning("image chooser failed for %r: %s", word, exc)
        return None
    idx = parse_image_choice_response(raw)
    if idx is None or idx == 0:
        return None
    if idx < 1 or idx > min(len(hits), max_candidates):
        return None
    return hits[idx - 1]
