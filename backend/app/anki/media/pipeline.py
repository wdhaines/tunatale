"""Media pipeline: fetch audio (Forvo → TTS) and image (Pixabay) for an Anki card."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import anyio

from app.languages import get_tts_voice

from .choose_llm import choose_image_hit
from .forvo import fetch_forvo_audio
from .normalize import normalize_audio
from .pixabay import PixabaySearch, _tag_overlap, best_hit, build_query, download_hit, search_pixabay
from .tts import generate_tts_audio


@dataclass
class MediaResult:
    audio_bytes: bytes | None = None
    audio_source: str | None = None
    image_bytes: bytes | None = None
    image_ext: str | None = None
    image_url: str | None = None
    image_status: str | None = None
    image_query_used: str | None = None
    image_chooser: str | None = None


def _compute_retry_query(primary_query: str, english: str) -> str | None:
    """Compute the retry query per the brief's pinned rules."""
    fallback = build_query(english)
    if fallback != primary_query:
        return fallback
    # first two words of parenthetical-stripped gloss
    stripped = re.sub(r"\s*\(.*?\)", "", english)
    words = stripped.split()[:2]
    retry = " ".join(words) if words else None
    if retry and retry != primary_query:
        return retry
    return None


def _has_overlap(hits: list[dict], query: str) -> bool:
    """True if any hit's tags overlap the query tokens (delegates to pixabay._tag_overlap)."""
    tokens = frozenset(query.lower().split())
    return any(_tag_overlap(tokens, hit.get("tags", "")) > 0 for hit in hits)


async def fetch_card_media(
    word: str,
    english: str,
    *,
    pixabay_key: str,
    language_code: str = "sl",
    http_client: Any = None,
    tts_voice: str | None = None,
    normalize: bool = True,
    used_image_urls: set[str] | None = None,
    image_query: str | None = None,
    llm: Any = None,
    _forvo_fn: Callable[..., bytes | None] | None = None,
    _tts_fn: Callable[..., Awaitable[bytes | None]] | None = None,
    _search_fn: Callable[..., Any] | None = None,
    _download_fn: Callable[..., Any] | None = None,
    _choose_fn: Callable[..., Awaitable[Any]] | None = None,
    _normalize_fn: Callable[..., bytes] | None = None,
) -> MediaResult:
    """Fetch audio and image for a vocabulary card.

    Tries Forvo first, falls back to edge-tts. Image from Pixabay.
    Pass used_image_urls (a shared set) across cards to prevent duplicate images.

    ``image_query`` controls image selection (see ``query_llm`` contract):
      * ``None`` — legacy: Pixabay derives the query from ``english``.
      * ``""``   — skip the image entirely (abstract word, no depiction).
      * non-empty — sent to Pixabay verbatim as a sense-disambiguated query.
    """
    forvo_fn = _forvo_fn or fetch_forvo_audio
    tts_fn = _tts_fn or generate_tts_audio
    search_fn = _search_fn or search_pixabay
    download_fn = _download_fn or download_hit
    choose_fn = _choose_fn or choose_image_hit
    norm_fn = _normalize_fn or normalize_audio
    # Resolve the synthesis voice from the card's language so a non-Slovene card
    # never gets Slovene TTS. Callers may still override explicitly (tests).
    voice = tts_voice or get_tts_voice(language_code)

    result = MediaResult()

    # Forvo / normalize are synchronous (httpx.Client, ffmpeg
    # subprocess) — offload to a worker thread so a slow fetch doesn't block
    # the event loop and stall every other in-flight request.
    audio = await anyio.to_thread.run_sync(
        partial(forvo_fn, word, language_code=language_code, http_client=http_client)
    )
    if audio is not None:
        result.audio_source = "forvo"
        result.audio_bytes = audio
    else:
        audio = await tts_fn(word, voice=voice)
        if audio is not None:
            result.audio_source = "tts"
            result.audio_bytes = audio

    if result.audio_bytes is not None and normalize:
        result.audio_bytes = await anyio.to_thread.run_sync(norm_fn, result.audio_bytes)

    # image_query == "" is the explicit "abstract word, no image" skip sentinel.
    if image_query == "":
        result.image_status = "skipped"
        return result

    used_urls = frozenset(used_image_urls) if used_image_urls is not None else frozenset()

    # Primary query
    effective_query = image_query or build_query(english)
    result.image_query_used = effective_query

    # Search in a thread
    search_result: PixabaySearch = await anyio.to_thread.run_sync(
        partial(search_fn, effective_query, api_key=pixabay_key, http_client=http_client)
    )
    status = search_result.status
    hits = search_result.hits

    # Filter hits by used_image_urls
    available = [h for h in hits if h.get("webformatURL", "") not in used_urls]

    # Retry logic (max one)
    if status in ("rate_limited", "api_error"):
        pass  # never retry
    elif status == "no_results" or (status == "ok" and not _has_overlap(available, effective_query)):
        retry_query = _compute_retry_query(effective_query, english)
        if retry_query and retry_query != effective_query:
            retry_result: PixabaySearch = await anyio.to_thread.run_sync(
                partial(search_fn, retry_query, api_key=pixabay_key, http_client=http_client)
            )
            status = retry_result.status
            hits = retry_result.hits
            available = [h for h in hits if h.get("webformatURL", "") not in used_urls]
            result.image_query_used = retry_query

    # Chooser
    chosen_hit = None
    if llm is not None and available:
        chosen_hit = await choose_fn(word, english, result.image_query_used, available, llm=llm)

    if chosen_hit is not None:
        final_hit = chosen_hit
        result.image_chooser = "llm"
    else:
        final_hit = best_hit(available, result.image_query_used) if available else None
        result.image_chooser = "tag_overlap" if final_hit is not None else None

    # Download in a thread
    if final_hit is not None:
        download_result = await anyio.to_thread.run_sync(partial(download_fn, final_hit, http_client=http_client))
        if download_result is not None:
            result.image_bytes, result.image_ext, result.image_url = download_result
            result.image_status = "ok"
            if used_image_urls is not None and result.image_url is not None:
                used_image_urls.add(result.image_url)
        else:
            result.image_status = "api_error"
    else:
        result.image_status = status if status != "ok" else "no_results"

    return result
