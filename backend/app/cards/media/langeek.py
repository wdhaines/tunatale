"""LanGeek's picture dictionary as a card-image source, tried before Pixabay (tunatale-xsbh).

Pixabay is a stock-photo search, good at things a camera can see and poor at
abstract words. LanGeek draws one illustration per word SENSE, which is the
shape a vocab card wants: 117 of 124 glosses in the 2026-10-27 shared-photo
repair had an illustrated sense.

Access: there is no documented API. The site's own JSON endpoint answers
``?term=<word>&filter=,inCategory,photo`` with a list of entries, each with its
senses by part of speech and an illustration URL on the senses that have one.
It is a PREFIX search ("band" also returns bandage, bandana ...), so only the
entry spelled exactly as the term is used. Requests are throttled to one a
second, process-wide. The owner chose personal use (2026-10-27); filenames
start ``lgk_`` so the pictures can be found again if LanGeek's terms change.

``settings.langeek_api_url`` empty turns the source off without a request,
which is how the test suite keeps every card-creating test off the network.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any, NamedTuple

import httpx

from app.llm.call_sites import CallSite

logger = logging.getLogger(__name__)

#: The fewest seconds between two requests to LanGeek, across the process.
MIN_INTERVAL = 1.0
_TIMEOUT = 10.0


class LangeekSense(NamedTuple):
    sense_id: int
    pos: str
    definition: str
    photo_url: str


class LangeekSearch(NamedTuple):
    status: str  # "ok" | "no_results" | "api_error" | "disabled"
    senses: list[LangeekSense]


def langeek_term(english: str) -> str | None:
    """The lookup word in a card's English gloss: its first alternative, bare.

    ``"to want; to mean"`` -> ``"want"``; ``"be there (near you); there is"`` ->
    ``"be there"``; a gloss that is only a parenthetical has none.
    """
    first = re.split(r"[;,]", re.sub(r"\([^()]*\)", "", english), maxsplit=1)[0]
    term = re.sub(r"^to\s+", "", " ".join(first.split()), flags=re.IGNORECASE).casefold()
    # LanGeek's entries carry no punctuation ('Bye!', 'where?' found nothing, 2026-10-27)
    return term.strip("!?.") or None


# Irregular English forms a card gloss is likely to use, by base form. A gloss
# copies the lesson's English, so a past-tense line gives 'knew', which LanGeek
# has no entry for (hibalo, 2026-10-27); 'know' it illustrates twice.
_IRREGULAR = {
    form: base
    for base, forms in {
        "know": "knew known",
        "go": "went gone",
        "eat": "ate eaten",
        "see": "saw seen",
        "come": "came",
        "give": "gave given",
        "take": "took taken",
        "make": "made",
        "say": "said",
        "get": "got gotten",
        "buy": "bought",
        "bring": "brought",
        "think": "thought",
        "tell": "told",
        "find": "found",
        "leave": "left",
        "feel": "felt",
        "keep": "kept",
        "sleep": "slept",
        "meet": "met",
        "run": "ran",
        "sit": "sat",
        "stand": "stood",
        "understand": "understood",
        "write": "wrote written",
        "speak": "spoke spoken",
        "drink": "drank drunk",
        "sing": "sang sung",
        "swim": "swam swum",
        "begin": "began begun",
        "forget": "forgot forgotten",
        "become": "became",
        "child": "children",
        "man": "men",
        "woman": "women",
        "foot": "feet",
        "tooth": "teeth",
        "mouse": "mice",
    }.items()
    for form in forms.split()
}
#: At most this many base forms are looked up after a miss: one request each.
_BASE_FORMS = 3


def base_forms(term: str) -> list[str]:
    """Dictionary forms *term* may be inflected from, most likely first: the irregular
    form's base, else the word with a plural or verb ending removed (or with its e back,
    or a doubled consonant undone). A phrase gets none: which word inflects is a guess."""
    if " " in term:
        return []
    if term in _IRREGULAR:
        return [_IRREGULAR[term]]
    out: list[str] = []
    if term.endswith("ies") and len(term) > 4:
        out.append(term[:-3] + "y")
    elif term.endswith("es") and len(term) > 4 and re.search(r"(s|x|z|ch|sh)es$", term):
        out += [term[:-2], term[:-1]]
    elif term.endswith("s") and not term.endswith("ss") and len(term) > 3:
        out.append(term[:-1])
    for ending in ("ed", "ing"):
        stem = term[: -len(ending)]
        if term.endswith(ending) and len(stem) >= 3:
            out += [stem, stem + "e"]
            if len(stem) > 3 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
                out.append(stem[:-1])
    return list(dict.fromkeys(f for f in out if f != term))[:_BASE_FORMS]


class _Throttle:
    """Space calls at least *interval* seconds apart, across threads."""

    def __init__(self, interval: float, now: Callable[[], float], sleep: Callable[[float], None]) -> None:
        self.interval, self.now, self.sleep = interval, now, sleep
        self._last: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            if self._last is not None:
                wait = self.interval - (self.now() - self._last)
                if wait > 0:
                    self.sleep(wait)
            self._last = self.now()


_THROTTLE = _Throttle(MIN_INTERVAL, time.monotonic, time.sleep)


def _illustrated_senses(entries: list[dict[str, Any]], term: str) -> list[LangeekSense]:
    """The illustrated senses of the entry spelled exactly *term*, in the response's order."""
    exact = next((e for e in entries if str(e.get("entry", "")).casefold() == term.casefold()), None)
    if exact is None:
        return []
    return [
        LangeekSense(int(sense["id"]), pos, str(sense.get("translation") or ""), str(sense["wordPhoto"]["photo"]))
        for pos, senses in (exact.get("translations") or {}).items()
        for sense in senses
        if (sense.get("wordPhoto") or {}).get("photo")
    ]


def search_langeek(
    term: str,
    *,
    http_client: httpx.Client | None = None,
    api_url: str | None = None,
    throttle: _Throttle | None = None,
) -> LangeekSearch:
    """Look *term* up; ``senses`` are only the exact entry's illustrated ones. When
    *term* is no entry, its :func:`base_forms` are looked up in turn, one request each."""
    found = _search(term, http_client=http_client, api_url=api_url, throttle=throttle)
    for base in base_forms(term) if found.status == "no_results" else ():
        found = _search(base, http_client=http_client, api_url=api_url, throttle=throttle)
        if found.status != "no_results":
            break
    return found


def _search(
    term: str,
    *,
    http_client: httpx.Client | None,
    api_url: str | None,
    throttle: _Throttle | None,
) -> LangeekSearch:
    if api_url is None:
        from app.config import settings

        api_url = settings.langeek_api_url
    if not api_url:
        return LangeekSearch("disabled", [])
    (throttle or _THROTTLE).wait()
    client = http_client or httpx.Client(timeout=_TIMEOUT)
    try:
        resp = client.get(api_url, params={"term": term, "filter": ",inCategory,photo"}, timeout=_TIMEOUT)
        if resp.status_code != 200:
            logger.warning("LanGeek search %r: HTTP %d", term, resp.status_code)
            return LangeekSearch("api_error", [])
        senses = _illustrated_senses(resp.json(), term)
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
        logger.warning("LanGeek search %r failed: %s: %s", term, type(exc).__name__, exc)
        return LangeekSearch("api_error", [])
    finally:
        if http_client is None:
            client.close()
    return LangeekSearch("ok" if senses else "no_results", senses)


def download_langeek(sense: LangeekSense, *, http_client: httpx.Client | None = None) -> tuple[bytes, str, str] | None:
    """The sense's illustration as ``(bytes, ext, url)``, or ``None``."""
    client = http_client or httpx.Client(timeout=_TIMEOUT)
    try:
        resp = client.get(sense.photo_url, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        logger.warning("LanGeek download %s failed: %s", sense.photo_url, exc)
        return None
    finally:
        if http_client is None:
            client.close()
    if resp.status_code != 200 or not resp.content:
        logger.warning("LanGeek download %s: HTTP %d, %d bytes", sense.photo_url, resp.status_code, len(resp.content))
        return None
    is_png = "type=png" in sense.photo_url or "png" in resp.headers.get("content-type", "")
    return resp.content, "png" if is_png else "jpg", sense.photo_url


SENSE_CHOICE_SYSTEM_PROMPT = (
    "You are choosing a picture for a language-learning flashcard. Given a word, "
    "its English meaning, and a numbered list of dictionary definitions that each "
    "have their own illustration, reply with ONLY the number of the definition "
    "that is the card's meaning, in the sense the card uses. If none of them is "
    "that meaning, reply 0. Reply with only the number: no words, no punctuation."
)


def build_sense_choice_prompt(word: str, english: str, senses: list[LangeekSense], source_sentence: str = "") -> str:
    lines = [f"Word: {word}", f"Meaning: {english}"]
    if source_sentence:
        lines.append(f"Sentence: {source_sentence}")
    lines += [f"{i}. ({s.pos}) {s.definition}" for i, s in enumerate(senses, 1)]
    return "\n".join(lines)


async def choose_langeek_sense(
    word: str, english: str, senses: list[LangeekSense], *, llm: Any, source_sentence: str = ""
) -> LangeekSense | None:
    """One sense is taken as is; among several the LLM picks, and anything else is ``None``.

    ``None`` means "no LanGeek picture": the caller goes on to Pixabay, so a
    homograph's wrong illustration (band the strip, for band the musicians) is
    never guessed at without the model.
    """
    if len(senses) == 1:
        return senses[0]
    if llm is None or not senses:
        return None
    try:
        raw = await llm.complete(
            build_sense_choice_prompt(word, english, senses, source_sentence),
            system_prompt=SENSE_CHOICE_SYSTEM_PROMPT,
            temperature=0.0,
            max_tokens=256,
            call_site=CallSite.MEDIA_SENSE,
        )
    except Exception as exc:  # noqa: BLE001 — never block card creation on the LLM
        logger.warning("LanGeek sense chooser failed for %r: %s", word, exc)
        return None
    match = re.search(r"\d+", raw or "")
    idx = int(match.group()) if match else 0
    return senses[idx - 1] if 1 <= idx <= len(senses) else None
