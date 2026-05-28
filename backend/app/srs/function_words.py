"""Slovene function words used by Phase F's cloze-card spike, plus case-cloze hint generation.

This set was generated from the user's 7-day curriculum by
build_function_word_list.py and manually curated to remove obvious
content words. To extend after adding new lessons, re-run the generator
and merge new entries.
"""

from __future__ import annotations

import re

# Generated 2026-05-12 from curriculum 'arrival-in-ljubljana-5f8c0f52'; manually curated.
SLOVENE_FUNCTION_WORDS: frozenset[str] = frozenset(
    {
        "je",  # 38 occurrences across 7 lessons — copula "is"
        "kje",  # 17 / 6 — "where"
        "v",  # 12 / 6 — "in"
        "kaj",  # 10 / 6 — "what"
        "sem",  # 9 / 4 — "am"
        "si",  # 7 / 3 — "are" (singular)
        "da",  # 7 / 4 — "that", "yes"
        "za",  # 6 / 3 — "for"
        "tam",  # 6 / 5 — "there"
        "na",  # 6 / 3 — "on"
        "kako",  # 5 / 3 — "how"
        "ni",  # 5 / 3 — "is not"
        "se",  # 4 / 4 — reflexive pronoun
        "to",  # 3 / 2 — "this", "that"
        "vam",  # 3 / 3 — "you" (plural/formal dative)
        "z",  # 3 / 2 — "with"
        "mi",  # 3 / 2 — "me" (dative)
        "še",  # 2 / 2 — "still", "yet"
        "pa",  # 2 / 2 — "and", "but", "so"
        "ti",  # 2 / 2 — "you" (singular dative)
        "po",  # 2 / 2 — "after", "along"
    }
)


def is_function_word(lemma: str, language_code: str) -> bool:
    """Return True if *lemma* is a known function word in *language_code*.

    Phase F scope: Slovene only (language_code == "sl").
    Case-insensitive (casefold) lookup against the curated set.
    """
    if language_code == "sl":
        return lemma.casefold() in SLOVENE_FUNCTION_WORDS
    return False


_CLOZE_RE = re.compile(r"\{\{c1::")


def make_cloze_text(surface: str, source_sentence: str) -> str:
    """Wrap every occurrence of ``surface`` in ``source_sentence`` with {{c1::surface}}.

    Word boundaries respected (regex ``\\b``). Case-insensitive,
    case-preserving (e.g. ``\\bkJe\\b`` matches ``Kje`` → ``{{c1::Kje}}``).
    Idempotent: already-clozed text (``{{c1::...}}``) passes through unchanged.

    Returns empty string when ``source_sentence`` is empty (caller must skip).
    """
    if not source_sentence:
        return ""
    if not surface:
        return source_sentence
    if _CLOZE_RE.search(source_sentence):
        return source_sentence
    pattern = re.compile(rf"\b{re.escape(surface)}\b", re.IGNORECASE)

    def _replacer(m: re.Match) -> str:
        return f"{{{{c1::{m.group(0)}}}}}"

    return pattern.sub(_replacer, source_sentence)


# ── Case-cloze hint helpers ──────────────────────────────────────────────

_NUMBER_ABBR: dict[str, str] = {
    "Sing": "sg",
    "Dual": "du",
    "Plur": "pl",
}


def _hint_text(lemma: str, case: str, number: str) -> str:
    """Build the hint segment for a case-cloze: ``miza, gen sg``."""
    abbr_case = case.lower() if case else ""
    abbr_num = _NUMBER_ABBR.get(number, number.lower()) if number else ""
    parts = [p for p in (abbr_case, abbr_num) if p]
    if not parts:
        return lemma
    return f"{lemma}, {' '.join(parts)}"


def make_case_cloze_text(
    surface: str,
    lemma: str,
    case: str,
    number: str,
    source_sentence: str,
) -> str:
    """Wrap ``surface`` with a hinted cloze: ``{{c1::mize::miza, gen sg}}``.

    The hint (``::hint``) tells the learner which lemma + inflection
    to produce.  Anki renders the blank as ``[miza, gen sg]``.

    Idempotent: already-clozed text passes through unchanged.
    Returns empty string when ``source_sentence`` is empty.
    """
    if not source_sentence:
        return ""
    if not surface:
        return source_sentence
    if _CLOZE_RE.search(source_sentence):
        return source_sentence
    hint = _hint_text(lemma, case, number)
    pattern = re.compile(rf"\b{re.escape(surface)}\b", re.IGNORECASE)

    def _replacer(m: re.Match) -> str:
        return f"{{{{c1::{m.group(0)}::{hint}}}}}"

    return pattern.sub(_replacer, source_sentence)
