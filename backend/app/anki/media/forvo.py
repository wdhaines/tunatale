"""Forvo audio scraper — fetches pronunciations for a given language."""

from __future__ import annotations

import base64
import re
import urllib.parse

import httpx

_FORVO_BASE = "https://forvo.com"
_AUDIO_BASE = "https://audio00.forvo.com/mp3"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Referer": "https://forvo.com/",
}


def _make_client() -> httpx.Client:
    return httpx.Client(headers=_DEFAULT_HEADERS)


def _extract_mp3_url(html: str, word: str, *, language_code: str = "sl") -> str | None:
    """Parse Forvo HTML for a Play() call in *language_code*'s section. URL or None."""
    container = f"language-container-{language_code}"
    lang_idx = max(
        html.find(f"id='{container}'"),
        html.find(f'id="{container}"'),
    )
    if lang_idx == -1:
        return None
    chunk = html[lang_idx : lang_idx + 3000]
    if "<article" not in chunk:
        return None
    match = re.search(r"Play\([^,]+,'([A-Za-z0-9+/=]+)'", chunk)
    if not match:
        return None
    try:
        path = base64.b64decode(match.group(1)).decode("utf-8")
    except Exception:
        return None
    return f"{_AUDIO_BASE}/{path}"


def fetch_forvo_audio(word: str, *, language_code: str = "sl", http_client: httpx.Client | None = None) -> bytes | None:
    """Download the *language_code* pronunciation from Forvo. MP3 bytes or None."""
    owned = http_client is None
    client = http_client or _make_client()
    try:
        encoded = urllib.parse.quote(word)
        resp = client.get(f"{_FORVO_BASE}/word/{encoded}/", timeout=15)
        resp.raise_for_status()
        mp3_url = _extract_mp3_url(resp.text, word, language_code=language_code)
        if mp3_url is None:
            return None
        r = client.get(mp3_url, timeout=20)
        r.raise_for_status()
        return r.content
    except Exception:
        return None
    finally:
        if owned:
            client.close()
