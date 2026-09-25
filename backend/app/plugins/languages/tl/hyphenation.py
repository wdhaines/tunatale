"""Tagalog affix-hyphen normalization (tunatale-w4m7.11).

The LLM writes ``mag‑kape`` (often with U+2011 NON-BREAKING HYPHEN) where
standard Tagalog spelling is ``magkape``. A hyphen after the verbal prefixes
``mag`` / ``nag`` / ``pag`` / ``mang`` / ``nang`` is correct only before a
vowel (``mag-aral``) or a capital letter (a loan proper noun: ``nag-Facebook``,
``mag-Ingles``); before a lowercase consonant it is a spelling error and the
hyphen is dropped.

The rule applies ONLY to a hyphen immediately after one of those prefixes at a
whole-word start (start of string or preceded by a non-letter). Any other
hyphen (``araw-araw``, ``damag-kape`` — where ``mag`` is mid-word) is never
touched, and U+2011/U+2010 elsewhere stays as-is.
"""

from __future__ import annotations

# Longest first — none is a prefix of another, but the ordering makes the
# whole-word-start scanning below self-evidently correct.
_PREFIXES = ("mang", "nang", "mag", "nag", "pag")
_HYPHENS = frozenset(("-", "\u2010", "\u2011"))
_VOWELS = frozenset("aeiouAEIOU")


def _at_word_start(text: str, index: int) -> bool:
    """True when *index* is the start of a word: string start or after a non-letter."""
    return index == 0 or not text[index - 1].isalpha()


def normalize_affix_hyphens(text: str) -> str:
    """Drop or ASCII-normalize a hyphen immediately after a Tagalog affix prefix.

    For a whole-word-start prefix from ``mag nag pag mang nang`` (any case)
    followed by ``-`` / U+2010 / U+2011:

    * next char is a lowercase consonant letter → remove the hyphen (join)
    * next char is a vowel (a e i o u, either case) → keep, as ASCII ``-``
      (an uppercase vowel lands here — same output as the uppercase branch)
    * next char is an uppercase letter → keep, as ASCII ``-``
    * anything else (digit, space, end of string) → leave the text exactly as-is

    The prefix keeps its own case (``Mag‑kape`` → ``Magkape``). Hyphens not
    immediately after one of these prefixes are never touched.
    """
    result: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        matched = False
        for prefix in _PREFIXES:
            end = i + len(prefix)
            if end >= n or not _at_word_start(text, i):
                continue
            if text[i:end].lower() != prefix or text[end] not in _HYPHENS:
                continue
            j = end + 1
            if j >= n:
                # End of string: nothing after the hyphen — leave as-is.
                break
            nxt = text[j]
            if nxt in _VOWELS or (nxt.isupper() and nxt.isalpha()):
                result.append(text[i:end])
                result.append("-")
                result.append(nxt)
                i = j + 1
            elif nxt.islower() and nxt.isalpha():
                result.append(text[i:end])
                result.append(nxt)
                i = j + 1
            else:
                # Digit, space, symbol… — leave exactly as-is.
                break
            matched = True
            break
        if not matched:
            result.append(text[i])
            i += 1
    return "".join(result)
