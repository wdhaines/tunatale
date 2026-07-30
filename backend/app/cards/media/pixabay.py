"""Pixabay image fetcher with ranked result selection."""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_PIXABAY_API = "https://pixabay.com/api/"

_QUERY_MAP_PATH = Path(__file__).parent / "data" / "image_query_map.json"


@lru_cache(maxsize=1)
def get_query_map() -> dict[str, str]:
    """English word -> Pixabay search query, flattened from the grouped data file.

    The table is 353 rows of vocabulary data, so it lives in
    ``data/image_query_map.json`` rather than in this module: it is content, not
    logic, matching how the language plugins keep function words (JSON) and style
    notes (Markdown) out of their Python. Extracting it also drained the last
    entry in ``language_literals_grandfather.txt`` — the checker flagged the key
    ``"no"`` (the English word, mapping to "thumbs down no refuse") as if it were
    the Norwegian language code. It was a genuine false positive, but the
    checker's trigger is "a bare string literal in app/**/*.py", not "a language
    code", and moving the data removed the trigger.

    Grouping is by category (Animals, Verbs, Pronouns, …) so the file keeps the
    structure the old inline ``# Animals`` comments carried; callers want it flat.
    Loaded lazily and cached — no module-level file I/O at import.
    """
    grouped: dict[str, dict[str, str]] = json.loads(_QUERY_MAP_PATH.read_text(encoding="utf-8"))
    return {word: query for category in grouped.values() for word, query in category.items()}


def build_query(english: str) -> str:
    """Return best Pixabay search query for an English word."""
    query_map = get_query_map()
    if english in query_map:
        return query_map[english]
    return re.sub(r"\s*\(.*?\)", "", english).strip()


@dataclass
class PixabaySearch:
    """Result of a Pixabay search API call with status classification."""

    hits: list[dict]
    status: str  # exactly one of: "ok" | "no_results" | "rate_limited" | "api_error"


def search_pixabay(
    query: str,
    *,
    api_key: str,
    http_client: httpx.Client | None = None,
    per_page: int = 50,
) -> PixabaySearch:
    """Search Pixabay for images matching *query*. Returns classified status.

    Only HTTP calls get try/except — a programming error must raise, not be
    swallowed.
    """
    owned = http_client is None
    client = http_client or httpx.Client()
    try:
        resp = client.get(
            _PIXABAY_API,
            params={
                "key": api_key,
                "q": query,
                "image_type": "photo",
                "safesearch": "true",
                "per_page": per_page,
                "min_width": 300,
            },
            timeout=10,
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        if not hits:
            return PixabaySearch(hits=[], status="no_results")
        return PixabaySearch(hits=hits, status="ok")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            return PixabaySearch(hits=[], status="rate_limited")
        logger.warning("Pixabay search failed for %r: %s", query, exc)
        return PixabaySearch(hits=[], status="api_error")
    except (httpx.TransportError, OSError) as exc:
        logger.warning("Pixabay search failed for %r: %s", query, exc)
        return PixabaySearch(hits=[], status="api_error")
    finally:
        if owned:
            client.close()


def download_hit(hit: dict, *, http_client: httpx.Client | None = None) -> tuple[bytes, str, str] | None:
    """Download a single Pixabay hit's webformat image. Returns (bytes, ext, url) or None."""
    img_url = hit.get("webformatURL", "")
    if not img_url:
        return None
    owned = http_client is None
    client = http_client or httpx.Client()
    try:
        r = client.get(img_url, timeout=15)
        r.raise_for_status()
        # jpg is Pixabay's dominant format, so it's the default; only a real
        # .png path (query string aside) gets png. The old `"jpg" in url`
        # substring check mislabelled .jpeg files as png.
        ext = "png" if img_url.lower().split("?")[0].endswith(".png") else "jpg"
        return r.content, ext, img_url
    except httpx.HTTPStatusError, httpx.TransportError, OSError:
        logger.warning("Pixabay image download failed for %s", img_url)
        return None
    finally:
        if owned:
            client.close()


_RELEVANCE_WEIGHT = 10.0
_EDITORS_CHOICE_BONUS = 1.0


def _tag_overlap(query_tokens: frozenset[str], tags_str: str) -> float:
    tag_words = {t.strip().lower() for t in tags_str.split(",") if t.strip()}
    return float(len(query_tokens & tag_words))


def score_hit(hit: dict, query_tokens: frozenset[str]) -> float:
    """Rank a Pixabay hit by query relevance first, engagement as a tiebreaker.

    Tag overlap dominates: each query token that appears in the hit's tags is
    worth ``_RELEVANCE_WEIGHT`` (10). Engagement (likes/views) is squashed into
    ``[0, 1)`` so a single on-topic tag always outranks any amount of likes on an
    off-topic photo — the old engagement-weighted formula routinely picked a
    viral but irrelevant stock photo over the right one. ``editors_choice`` adds
    a small bonus that only breaks ties within the same relevance tier.
    """
    likes = hit.get("likes", 0) or 0
    views = hit.get("views", 0) or 0
    tags = hit.get("tags", "") or ""
    overlap = _tag_overlap(query_tokens, tags)
    engagement_raw = 0.5 * math.log(likes + 1) + 0.3 * math.log(views + 1)
    engagement = engagement_raw / (engagement_raw + 1.0)  # squash to [0, 1)
    editors = _EDITORS_CHOICE_BONUS if hit.get("editors_choice") or hit.get("editorsChoice") else 0.0
    return _RELEVANCE_WEIGHT * overlap + editors + engagement


def best_hit(hits: list[dict], query: str) -> dict | None:
    """Return highest-scoring hit, preferring photos over illustrations."""
    if not hits:
        return None
    photo_hits = [h for h in hits if h.get("imageType") == "photo" or h.get("type") == "photo"]
    candidates = photo_hits if photo_hits else hits
    tokens = frozenset(query.lower().split())
    return max(candidates, key=lambda h: score_hit(h, tokens))


def fetch_pixabay_image(
    english: str,
    *,
    api_key: str,
    http_client: httpx.Client | None = None,
    used_urls: frozenset[str] = frozenset(),
    query: str | None = None,
) -> tuple[bytes, str, str] | None:
    """Fetch best-ranked Pixabay image. Returns (image_bytes, ext, url) or None.

    Hits whose webformatURL is in used_urls are excluded before ranking.

    ``query`` overrides the search string: when a non-empty value is given it is
    sent verbatim (e.g. an LLM-generated, sense-disambiguated query); otherwise
    the legacy :func:`build_query` mapping derived from ``english`` is used.
    """
    effective_query = query or build_query(english)
    owned = http_client is None
    client = http_client or httpx.Client()
    try:
        search = search_pixabay(effective_query, api_key=api_key, http_client=client)
        if search.status != "ok" or not search.hits:
            return None
        available = [h for h in search.hits if h.get("webformatURL", "") not in used_urls]
        hit = best_hit(available, effective_query)
        if hit is None:
            return None
        return download_hit(hit, http_client=client)
    finally:
        if owned:
            client.close()
