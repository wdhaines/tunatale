"""Forvo pronunciations, via the official API.

Replaces an HTML scraper that fetched ``forvo.com/word/{word}/`` behind a
spoofed Chrome User-Agent and regex-parsed a base64 argument out of a ``Play()``
call. That was brittle by construction and a ToS violation, but the reason it
had to go is subtler and is the thing this module exists to fix:

**every failure looked the same.** A markup change, an IP block, and a word that
genuinely has no recording all returned a bare ``None`` through one ``except
Exception`` path. Vocabulary media could be quietly missing for any of those
reasons with nothing to distinguish them, so nothing ever surfaced it. Hence
:class:`ForvoOutcome`: callers can tell "Forvo answered, nobody has recorded
this word" (normal, expected, not worth a warning) from "we could not ask"
(a real problem someone should see).

There is deliberately no ``bytes | None`` convenience wrapper. The scraper's
signature was one, and collapsing every outcome into ``None`` at the boundary
is precisely how the failures above stayed invisible — a wrapper would be a
standing invitation to reintroduce that. The one caller
(``pipeline.fetch_card_media``) branches on ``outcome``.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass
from enum import StrEnum

import httpx

logger = logging.getLogger(__name__)

# Rewrites the key segment of a Forvo URL wherever it appears in a log record.
_KEY_SEGMENT_RE = re.compile(r"(/key/)[^/]+")


class _RedactForvoKeyFilter(logging.Filter):
    """Strip the Forvo API key out of httpx's own request log.

    ⚠️ This is not belt-and-braces, it is the actual fix for a real leak.

    Forvo authenticates with the key as a **path segment**, and ``httpx`` logs
    every request at INFO as ``HTTP Request: GET <full url>``. So the key lands
    in the application log via a logger we do not own, no matter how careful
    this module is with its own strings — caught by
    ``test_api_key_never_appears_in_detail_or_logs``, which failed on httpx's
    line while every string built here was already clean.

    Azure has no equivalent problem because its key travels in a header; that
    asymmetry is a property of the two APIs, not of the two clients.

    The pattern is matched rather than the key value, so a rotated or
    per-request key is covered without re-installing anything.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # ONLY tuple args are rewritten. ``record.args`` is a dict for
        # %(name)s-style logging, and coercing that to a tuple would break the
        # record's own formatting — a logging filter must never corrupt a record
        # it does not understand. httpx uses positional args, which is the case
        # that matters here.
        if isinstance(record.args, tuple):
            record.args = tuple(self._scrub(arg) for arg in record.args)
        if isinstance(record.msg, str):
            record.msg = _KEY_SEGMENT_RE.sub(r"\1***", record.msg)
        return True

    @staticmethod
    def _scrub(arg: object) -> object:
        """Redact *arg* if it looks like a keyed URL, else hand it back unchanged.

        Returning the original object rather than its ``str()`` matters: args are
        formatted later, and stringifying everything here would change how
        non-string values render.
        """
        text = str(arg)
        return _KEY_SEGMENT_RE.sub(r"\1***", text) if "/key/" in text else arg


_redaction_installed = False


def _install_key_redaction() -> None:
    """Attach the redaction filter to httpx's logger, once.

    Done lazily at call time rather than at import, because this package
    forbids module-level side effects — and because a process that never
    fetches from Forvo has no reason to carry the filter.
    """
    global _redaction_installed
    if not _redaction_installed:
        logging.getLogger("httpx").addFilter(_RedactForvoKeyFilter())
        _redaction_installed = True


# Free plan. The commercial plan is a different host (apicommercial.forvo.com);
# switching plans is a host change, not just a key change — see tunatale-kbb.1.2
# on the licence terms, which are scoped to single-user use.
_API_BASE = "https://apifree.forvo.com"

_METADATA_TIMEOUT_S = 15.0
_AUDIO_TIMEOUT_S = 20.0


class ForvoOutcome(StrEnum):
    """Why a fetch produced audio, or did not."""

    FOUND = "found"
    # Forvo answered normally and nobody has recorded this word in this
    # language. Expected, common, and NOT a failure.
    NO_PRONUNCIATION = "no_pronunciation"
    NO_API_KEY = "no_api_key"
    REQUEST_FAILED = "request_failed"
    # Metadata came back fine but the mp3 itself did not. Separate because it
    # points at a different thing (CDN/expiry) than a failed lookup does.
    AUDIO_FETCH_FAILED = "audio_fetch_failed"


@dataclass(frozen=True)
class ForvoResult:
    outcome: ForvoOutcome
    audio: bytes | None = None
    detail: str = ""

    @property
    def is_failure(self) -> bool:
        """True when we could not get an answer, as opposed to getting 'none'.

        This is the whole point of the type. ``NO_PRONUNCIATION`` is a valid
        answer and must not be logged as a problem, or the log fills with noise
        for every abstract word and stops being read.
        """
        return self.outcome not in (ForvoOutcome.FOUND, ForvoOutcome.NO_PRONUNCIATION)


def _make_client() -> httpx.Client:
    return httpx.Client()


def _redact(text: str, api_key: str) -> str:
    """Strip the API key from anything destined for a log or a detail string.

    The key travels as a PATH SEGMENT, so it is embedded in every request URL —
    and httpx puts the URL in the string form of most of its exceptions. Any
    naive ``str(exc)`` would therefore leak the key into the log. This is not
    theoretical tidiness; it is the reason detail strings are built by hand here
    rather than passed through.
    """
    return text.replace(api_key, "***") if api_key else text


def _build_url(word: str, language_code: str, api_key: str) -> str:
    """Forvo takes its parameters as path segments, not a query string.

    ``order/rate-desc`` + ``limit/1`` asks Forvo to rank by rating and return
    only the winner, which means this module depends on exactly ONE field of the
    response (``pathmp3``). Re-implementing the ranking here would mean also
    depending on ``rate`` and ``num_votes`` and their tie-break semantics — more
    surface to break, for a job the API already does.
    """
    return (
        f"{_API_BASE}"
        f"/key/{api_key}"
        "/format/json"
        "/action/word-pronunciations"
        f"/word/{urllib.parse.quote(word, safe='')}"
        f"/language/{urllib.parse.quote(language_code, safe='')}"
        "/order/rate-desc"
        "/limit/1/"
    )


def fetch_forvo_pronunciation(
    word: str,
    *,
    language_code: str | None = None,
    api_key: str | None = None,
    http_client: httpx.Client | None = None,
) -> ForvoResult:
    """Fetch the best-rated *word* pronunciation in *language_code*.

    Returns a :class:`ForvoResult` whose ``outcome`` distinguishes a missing
    recording from a failed request. Never raises: Forvo media is optional, so a
    failure degrades the card rather than breaking the run — but it degrades
    *visibly*, which the scraper did not.
    """
    from app.config import settings

    if language_code is None:
        language_code = settings.target_language
    if api_key is None:
        api_key = settings.forvo_api_key

    if not api_key:
        # Non-fatal by design — Forvo audio is a nice-to-have and TTS covers the
        # gap — but it must be visible, or an unconfigured deployment silently
        # produces TTS-only cards forever and looks like Forvo has no coverage.
        logger.warning("FORVO_API_KEY is not set; skipping Forvo lookup for %r (TTS will be used instead)", word)
        return ForvoResult(ForvoOutcome.NO_API_KEY, detail="FORVO_API_KEY is not set")

    _install_key_redaction()
    owned = http_client is None
    client = http_client or _make_client()
    try:
        try:
            response = client.get(_build_url(word, language_code, api_key), timeout=_METADATA_TIMEOUT_S)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # The quota-exceeded signature is NOT documented and has not been
            # observed here — the free plan's 500/day ceiling has never been hit
            # in testing. It is deliberately not special-cased on a guess; when
            # it is seen for real, give it its own outcome and record the actual
            # status and body here.
            detail = f"HTTP {exc.response.status_code} from Forvo"
            logger.warning("Forvo lookup failed for %r: %s", word, detail)
            return ForvoResult(ForvoOutcome.REQUEST_FAILED, detail=detail)
        except (httpx.TransportError, OSError) as exc:
            detail = _redact(f"{type(exc).__name__}: {exc}", api_key)
            logger.warning("Forvo lookup failed for %r: %s", word, detail)
            return ForvoResult(ForvoOutcome.REQUEST_FAILED, detail=detail)

        try:
            items = response.json()["items"]
        except (ValueError, KeyError, TypeError) as exc:
            # A response we cannot parse is a SHAPE CHANGE, and reading it as
            # "no pronunciation" is exactly the silence this module replaced.
            detail = f"unparseable Forvo response ({type(exc).__name__})"
            logger.warning("Forvo lookup failed for %r: %s", word, detail)
            return ForvoResult(ForvoOutcome.REQUEST_FAILED, detail=detail)

        if not items:
            logger.debug("Forvo has no %s pronunciation for %r", language_code, word)
            return ForvoResult(ForvoOutcome.NO_PRONUNCIATION, detail="no recordings for this word")

        mp3_url = items[0].get("pathmp3") if isinstance(items[0], dict) else None
        if not mp3_url:
            detail = "Forvo item carried no pathmp3"
            logger.warning("Forvo lookup failed for %r: %s", word, detail)
            return ForvoResult(ForvoOutcome.REQUEST_FAILED, detail=detail)

        try:
            audio_response = client.get(mp3_url, timeout=_AUDIO_TIMEOUT_S)
            audio_response.raise_for_status()
        except (httpx.HTTPError, OSError) as exc:
            detail = _redact(f"{type(exc).__name__}: {exc}", api_key)
            logger.warning("Forvo audio download failed for %r: %s", word, detail)
            return ForvoResult(ForvoOutcome.AUDIO_FETCH_FAILED, detail=detail)

        return ForvoResult(ForvoOutcome.FOUND, audio=audio_response.content)
    finally:
        # Only close what we opened. The pipeline shares one client across a
        # whole card-add run.
        if owned:
            client.close()
