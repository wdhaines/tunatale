"""Forvo audio scraper — fetches pronunciations for a given language.

⚠️ This scrapes HTML; there is no API key involved. Forvo sells an API
(apifree.forvo.com, Non-Profit tier $2/mo, 500 req/day) and the migration to it
is written and green on the branch ``feat/forvo-official-api`` / PR #16,
deferred on cost. If you are here because Forvo stopped working, read that PR
before rebuilding any of it.

WHY THIS MODULE REPORTS *WHY* IT FAILED
---------------------------------------
It used to return a bare ``None`` for every failure mode, so an IP block, a
Forvo markup change and "nobody has recorded this word" were indistinguishable
at the call site. Forvo audio is optional — TTS covers the gap — so the fallback
worked, which is exactly what made the silence dangerous: the app could stop
getting Forvo audio and nothing would say so.

On 2026-08-15 a probe from a non-home network got HTTP 403 + a Cloudflare
"Just a moment..." interstitial on every request, while the last real fetch
(2026-08-06) had succeeded and no fetch had been attempted in between. Whether
that block follows the network or the scraper is still open — see
``scripts/local/forvo_block_probe.py``. Under the old code that question would
have been unanswerable after the fact, because nothing was recorded.
"""

from __future__ import annotations

import base64
import logging
import re
import urllib.parse
from dataclasses import dataclass
from enum import StrEnum

import httpx

logger = logging.getLogger(__name__)

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


def _extract_mp3_url(html: str, *, language_code: str | None = None) -> str | None:
    """Parse Forvo HTML for a Play() call in *language_code*'s section. URL or None."""
    if language_code is None:
        from app.config import settings

        language_code = settings.target_language
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


class ForvoOutcome(StrEnum):
    """Why a fetch produced audio, or did not.

    The split that matters is ``is_failure``: FOUND and NO_PRONUNCIATION are both
    *answers*, everything else means we never got one.
    """

    FOUND = "found"
    # Forvo answered and nobody has recorded this word in this language.
    # Expected, common, and NOT a failure — must never warn, or the log fills
    # with noise for every abstract word and stops being read.
    NO_PRONUNCIATION = "no_pronunciation"
    # 200, but no language-container anywhere: we can no longer parse Forvo.
    MARKUP_CHANGED = "markup_changed"
    # An anti-bot interstitial. Its own outcome because the response is a
    # decision about *us*, not about the word, and the fix is different.
    BLOCKED = "blocked"
    REQUEST_FAILED = "request_failed"
    # Metadata parsed, the mp3 host failed. Separate because it points at a
    # CDN/expiry problem rather than at the lookup.
    AUDIO_FETCH_FAILED = "audio_fetch_failed"


@dataclass(frozen=True)
class ForvoResult:
    outcome: ForvoOutcome
    audio: bytes | None = None
    detail: str = ""

    @property
    def is_failure(self) -> bool:
        """True when we could not get an answer, as opposed to getting 'none'."""
        return self.outcome not in (ForvoOutcome.FOUND, ForvoOutcome.NO_PRONUNCIATION)


# Cloudflare's managed-challenge page. Matched on the title rather than a
# header because the challenge is served as an ordinary 403 body.
_CHALLENGE_MARKERS = ("Just a moment...", "cf-browser-verification", "cf_chl_opt")


def _classify_page(status: int, html: str, language_code: str) -> tuple[ForvoOutcome, str]:
    """Map a word-page response onto an outcome. Never raises."""
    if status != 200:
        if any(marker in html for marker in _CHALLENGE_MARKERS):
            return ForvoOutcome.BLOCKED, f"HTTP {status} with an anti-bot challenge page"
        return ForvoOutcome.REQUEST_FAILED, f"HTTP {status}"
    if f"language-container-{language_code}" in html:
        # Present but unparseable is handled by the caller via _extract_mp3_url.
        return ForvoOutcome.FOUND, ""
    if "language-container-" in html:
        # Other languages rendered, ours did not: nobody recorded it.
        # NB: not f"no {language_code} …" — that f-string's literal chunk strips
        # to the bare code "no" and trips scripts/check_language_literals.py.
        return ForvoOutcome.NO_PRONUNCIATION, f"page carries no section for {language_code}"
    return ForvoOutcome.MARKUP_CHANGED, "no language-container markup anywhere on a 200 response"


def fetch_forvo_pronunciation(
    word: str, *, language_code: str | None = None, http_client: httpx.Client | None = None
) -> ForvoResult:
    """Scrape the *language_code* pronunciation for *word*.

    Never raises — Forvo audio is optional, so a failure degrades the card rather
    than breaking card creation. But it degrades *visibly*: the outcome says why,
    and a real failure logs a warning naming the cause.
    """
    if language_code is None:
        from app.config import settings

        language_code = settings.target_language
    owned = http_client is None
    client = http_client or _make_client()
    try:
        result = _fetch(client, word, language_code)
    except Exception as exc:  # noqa: BLE001 — every transport error is a fetch failure
        result = ForvoResult(ForvoOutcome.REQUEST_FAILED, detail=f"{type(exc).__name__}: {exc}")
    finally:
        if owned:
            client.close()

    if result.is_failure:
        logger.warning(
            "Forvo lookup failed for %r (%s): %s — falling back to TTS",
            word,
            result.outcome.value,
            result.detail,
        )
    return result


def _fetch(client: httpx.Client, word: str, language_code: str) -> ForvoResult:
    encoded = urllib.parse.quote(word)
    resp = client.get(f"{_FORVO_BASE}/word/{encoded}/", timeout=15)
    outcome, detail = _classify_page(resp.status_code, resp.text, language_code)
    if outcome is not ForvoOutcome.FOUND:
        return ForvoResult(outcome, detail=detail)

    mp3_url = _extract_mp3_url(resp.text, language_code=language_code)
    if mp3_url is None:
        # Our section is on the page but carries no playable entry.
        return ForvoResult(ForvoOutcome.NO_PRONUNCIATION, detail=f"{language_code} section has no Play() entry")

    audio = client.get(mp3_url, timeout=20)
    if audio.status_code != 200:
        return ForvoResult(ForvoOutcome.AUDIO_FETCH_FAILED, detail=f"mp3 GET returned HTTP {audio.status_code}")
    return ForvoResult(ForvoOutcome.FOUND, audio=audio.content)
