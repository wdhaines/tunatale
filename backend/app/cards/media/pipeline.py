"""Media pipeline: fetch audio (Forvo → TTS) and image (Pixabay) for an Anki card."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal

import anyio

from app.audio.synth_plan import word_readings
from app.languages import card_surface_variants, get_ipa_for_card_words, get_phoneme_planner, get_tts_voice

from .choose_llm import choose_image_hit
from .forvo import ForvoOutcome, ForvoResult, fetch_forvo_pronunciation
from .langeek import choose_langeek_sense, download_langeek, langeek_term, search_langeek
from .normalize import normalize_audio
from .pixabay import PixabaySearch, _tag_overlap, best_hit, build_query, download_hit, search_pixabay
from .tts import generate_tts_audio

#: How much audio a caller wants. ``"full"`` is Forvo, then TTS when Forvo has
#: nothing. ``"forvo"`` stops after Forvo: a human recording is free, a metered
#: TTS render is not, and a card minted by a sync may not be seen for weeks.
#: ``"none"`` makes no audio request at all — for a caller that wants a picture.
AudioMode = Literal["full", "forvo", "none"]


@dataclass
class MediaResult:
    audio_bytes: bytes | None = None
    audio_source: str | None = None
    # Why Forvo did or didn't supply the audio — a ForvoOutcome value. Mirrors
    # image_status, and exists so "TTS was used" carries its reason instead of
    # being indistinguishable from "Forvo had nothing".
    audio_status: str | None = None
    image_bytes: bytes | None = None
    image_ext: str | None = None
    image_url: str | None = None
    image_status: str | None = None
    image_query_used: str | None = None
    image_chooser: str | None = None
    image_source: str | None = None


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


async def _langeek_image(
    word: str,
    english: str,
    used_image_urls: set[str] | None,
    *,
    llm: Any,
    source_sentence: str,
    http_client: Any,
    search_fn: Callable[..., Any],
    download_fn: Callable[..., Any],
    choose_fn: Callable[..., Awaitable[Any]],
) -> tuple[tuple[bytes, str, str], str] | None:
    """LanGeek's illustration for the card, with how its sense was chosen, or ``None``.

    ``None`` sends the caller on to Pixabay: no lookup word, nothing illustrated,
    every illustration already used this run, no sense the card means, or a
    failed download.
    """
    term = langeek_term(english)
    if term is None:
        return None
    found = await anyio.to_thread.run_sync(partial(search_fn, term, http_client=http_client))
    senses = [s for s in found.senses if s.photo_url not in (used_image_urls or ())]
    sense = await choose_fn(word, english, senses, llm=llm, source_sentence=source_sentence) if senses else None
    if sense is None:
        return None
    image = await anyio.to_thread.run_sync(partial(download_fn, sense, http_client=http_client))
    if image is None:
        return None
    return image, "langeek" if len(senses) == 1 else "langeek_llm"


def _card_word_readings(word: str, language_code: str) -> dict[str, str] | None:
    """The reading a card's word is voiced with, for a language that asked for one."""
    if not get_ipa_for_card_words(language_code):
        return None
    return word_readings(word, get_phoneme_planner(language_code))


async def fetch_card_media(
    word: str,
    english: str,
    *,
    pixabay_key: str,
    language_code: str | None = None,
    http_client: Any = None,
    tts_voice: str | None = None,
    normalize: bool = True,
    used_image_urls: set[str] | None = None,
    image_query: str | None = None,
    llm: Any = None,
    forvo_enabled: bool | None = None,
    audio: AudioMode = "full",
    source_sentence: str = "",
    _forvo_fn: Callable[..., ForvoResult] | None = None,
    _tts_fn: Callable[..., Awaitable[bytes | None]] | None = None,
    _search_fn: Callable[..., Any] | None = None,
    _download_fn: Callable[..., Any] | None = None,
    _choose_fn: Callable[..., Awaitable[Any]] | None = None,
    _normalize_fn: Callable[..., bytes] | None = None,
    _langeek_search_fn: Callable[..., Any] | None = None,
    _langeek_download_fn: Callable[..., Any] | None = None,
    _langeek_choose_fn: Callable[..., Awaitable[Any]] | None = None,
) -> MediaResult:
    """Fetch audio and image for a vocabulary card.

    Tries Forvo first, falls back to TTS. Image from Pixabay.
    Pass used_image_urls (a shared set) across cards to prevent duplicate images.

    ``audio`` narrows the audio half (see :data:`AudioMode`); the image half is
    unaffected by it.

    ``image_query`` controls image selection (see ``query_llm`` contract):
      * ``None`` — legacy: Pixabay derives the query from ``english``.
      * ``""``   — skip the image entirely (abstract word, no depiction).
      * non-empty — sent to Pixabay verbatim as a sense-disambiguated query.
    """
    forvo_fn = _forvo_fn or fetch_forvo_pronunciation
    tts_fn = _tts_fn or generate_tts_audio
    search_fn = _search_fn or search_pixabay
    download_fn = _download_fn or download_hit
    choose_fn = _choose_fn or choose_image_hit
    norm_fn = _normalize_fn or normalize_audio
    langeek_search_fn = _langeek_search_fn or search_langeek
    langeek_download_fn = _langeek_download_fn or download_langeek
    langeek_choose_fn = _langeek_choose_fn or choose_langeek_sense
    if language_code is None:
        from app.config import settings

        language_code = settings.target_language
    # Resolve the synthesis voice from the card's language so a non-Slovene card
    # never gets Slovene TTS. Callers may still override explicitly (tests).
    voice = tts_voice or get_tts_voice(language_code)
    # A front listing spellings of one word ('manong, nong') is voiced as its first,
    # the full form, as 'dili, di' is (the owner, 2026-10-23). A phrase stays whole.
    spoken = card_surface_variants(language_code, word)[0]

    result = MediaResult()

    # Forvo / normalize are synchronous (httpx.Client, ffmpeg
    # subprocess) — offload to a worker thread so a slow fetch doesn't block
    # the event loop and stall every other in-flight request.
    if forvo_enabled is None:
        from app.config import settings

        forvo_enabled = settings.forvo_enabled

    if audio == "none":
        # The caller wants no audio: no Forvo request and no TTS render. Its own
        # status, so a card left without audio on purpose is never read as one
        # whose fetch failed.
        forvo = ForvoResult(ForvoOutcome.NO_PRONUNCIATION)
        result.audio_status = "skipped"
    elif not forvo_enabled:
        # Skip the request entirely rather than make a doomed one. Forvo blocks
        # datacenter IPs (measured 2026-09-11: found from home, HTTP 403 + an
        # anti-bot challenge from the production box), and production accepts
        # TTS-only. "disabled" is its own status so it is never confused with
        # `blocked`, which means Forvo refused a call we expected to work.
        forvo = ForvoResult(ForvoOutcome.NO_PRONUNCIATION)
        result.audio_status = "disabled"
    else:
        forvo = await anyio.to_thread.run_sync(
            partial(forvo_fn, spoken, language_code=language_code, http_client=http_client)
        )
        result.audio_status = forvo.outcome.value
    if forvo.outcome is ForvoOutcome.FOUND:
        result.audio_source = "forvo"
        result.audio_bytes = forvo.audio
    elif audio == "full":
        # Every non-FOUND outcome still falls back to TTS — the card must not be
        # blocked on a nice-to-have. audio_status is what keeps that fallback
        # from being silent.
        audio = await tts_fn(spoken, voice=voice, phonemes=_card_word_readings(spoken, language_code))
        if audio is not None:
            result.audio_source = "tts"
            result.audio_bytes = audio

    if result.audio_bytes is not None and normalize:
        result.audio_bytes = await anyio.to_thread.run_sync(norm_fn, result.audio_bytes)

    # LanGeek first, and BEFORE the "" skip below: the abstract words that skip
    # gives up on are the ones its drawn illustrations exist for (tunatale-xsbh).
    langeek = await _langeek_image(
        word,
        english,
        used_image_urls,
        llm=llm,
        source_sentence=source_sentence,
        http_client=http_client,
        search_fn=langeek_search_fn,
        download_fn=langeek_download_fn,
        choose_fn=langeek_choose_fn,
    )
    if langeek is not None:
        (result.image_bytes, result.image_ext, result.image_url), result.image_chooser = langeek
        result.image_status, result.image_source = "ok", "langeek"
        if used_image_urls is not None:
            used_image_urls.add(result.image_url)
        return result

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
            result.image_source = "pixabay"
            if used_image_urls is not None and result.image_url is not None:
                used_image_urls.add(result.image_url)
        else:
            result.image_status = "api_error"
    else:
        result.image_status = status if status != "ok" else "no_results"

    return result
