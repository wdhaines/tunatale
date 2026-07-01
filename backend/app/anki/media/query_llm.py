"""LLM-generated, sense-disambiguated image search queries.

The legacy :func:`app.anki.media.pixabay.build_query` is a ~400-entry hand-curated
map keyed on the English gloss; everything outside it falls back to the raw gloss.
New cards (especially from the lemma pipeline) are exactly the ones *not* in the
map, so they get the worst-quality query — and an ambiguous gloss ("court",
"ring", "bill") pulls whatever sense the stock-photo tags happen to favour.

This module asks the project's existing LLM (Groq via ``app.state.llm``) for a
concrete, depictable, sense-disambiguated query, using the context the card
already carries (example sentence + grammar). Every production card should get
an image — even abstract words get a best-effort representative query, and a
human fixes a bad one rather than the pipeline skipping it. Results are cached
per-word in ``image_query_cache`` (one LLM call per new word, never per render),
mirroring the persistent lemma-analysis cache.

Resilience contract — two outcomes:
  * non-empty ``str`` → use this query (the LLM's best concrete depiction, or the
                        English gloss as a best-effort fallback — never skip)
  * ``None``         → no opinion; caller falls back to ``build_query`` (LLM
                       unavailable or failed — never block card creation on it)
"""

from __future__ import annotations

import logging
import re
from typing import Protocol

logger = logging.getLogger(__name__)

# Bump when the prompt or target model changes so the cache invalidates.
IMAGE_QUERY_MODEL_VERSION = "img-query-v2"

IMAGE_QUERY_SYSTEM_PROMPT = (
    "You write short image-search queries for a stock-photo site to illustrate a "
    "single language-learning flashcard. Given a word, its English meaning, and "
    "optionally an example sentence, reply with 2 to 4 plain English words naming a "
    "CONCRETE, photographable object or scene that depicts the word's meaning in "
    "that sense. Prefer literal, everyday depictions a stock-photo library would "
    "actually have, and use the example sentence to pick the right sense of an "
    "ambiguous word. Even for an abstract, grammatical, or function word, give your "
    "best representative concrete scene (e.g. 'forgive' -> two people hugging; "
    "'because' -> falling dominoes; 'very' -> giant size comparison): every card "
    "should get an image, and a human will refine it later. Reply with only the "
    "query words — no quotes, no punctuation, no explanation, never NONE."
)

_LABEL_RE = re.compile(r"^(?:image\s+)?(?:search\s+)?query\s*[:\-]\s*", re.IGNORECASE)
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_MAX_QUERY_WORDS = 6


class _LLM(Protocol):
    async def complete(
        self,
        prompt: str,
        system_prompt: str | None = ...,
        temperature: float = ...,
        max_tokens: int = ...,
    ) -> str: ...


def build_image_query_prompt(
    word: str,
    english: str,
    *,
    source_sentence: str = "",
    grammar: str = "",
) -> str:
    """Assemble the user prompt for one card's image query."""
    lines = [f"Word: {word}", f"English meaning: {english}"]
    if grammar.strip():
        lines.append(f"Grammar: {grammar.strip()}")
    if source_sentence.strip():
        lines.append(f"Example sentence: {source_sentence.strip()}")
    lines.append("Image search query:")
    return "\n".join(lines)


def parse_image_query_response(raw: str) -> str:
    """Clean an LLM reply into a query string. ``""`` means "no image".

    Returns the empty-string skip sentinel for NONE / blank / unusable replies.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    first = _LABEL_RE.sub("", first).strip().strip("\"'`").strip()
    words = _WORD_RE.findall(first)
    if not words or words[0].upper() == "NONE":
        return ""
    return " ".join(words[:_MAX_QUERY_WORDS])


async def generate_image_query(
    word: str,
    english: str,
    *,
    llm: _LLM | None,
    db: object | None = None,
    source_sentence: str = "",
    grammar: str = "",
    model_version: str = IMAGE_QUERY_MODEL_VERSION,
) -> str | None:
    """Return a sense-aware Pixabay query for a card. See module docstring contract."""
    # Best-effort fallback so every card gets an image attempt — never skip. The
    # English gloss is a usable Pixabay query when the LLM gives nothing concrete.
    fallback = english.strip()
    if db is not None:
        cached = db.get_image_query(word, english, model_version)
        if cached is not None:
            return cached or fallback or None
    if llm is None:
        return None
    prompt = build_image_query_prompt(word, english, source_sentence=source_sentence, grammar=grammar)
    try:
        # 256 (not 32) so a gpt-oss reasoning model has room for its reasoning
        # tokens before the short search-phrase content; a 32-token ceiling would be
        # fully consumed by reasoning and return empty. Instruct models still emit
        # only the few tokens the phrase needs.
        raw = await llm.complete(prompt, system_prompt=IMAGE_QUERY_SYSTEM_PROMPT, temperature=0.0, max_tokens=256)
    except Exception as exc:  # noqa: BLE001 — never block card creation on the LLM
        logger.warning("image query generation failed for %r: %s", word, exc)
        return None
    query = parse_image_query_response(raw) or fallback
    if db is not None:
        db.set_image_query(word, english, model_version, query)
    return query or None
