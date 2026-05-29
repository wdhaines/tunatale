"""Slovene function words used by Phase F's cloze-card spike, plus morphology-cloze hint generation.

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


# ── Morphology-cloze hint helpers ────────────────────────────────────────


def _format_morphology_feature(feature: str) -> str:
    """Turn a feature key into a concise hint label.

    Examples:
      ``verb:1sg``      -> ``1sg``
      ``noun:loc:sg``   -> ``loc sg``
      ``noun:nom:f:pl`` -> ``nom f pl``
      ``adj:nom:m:sg``  -> ``nom m sg``

    The POS prefix is dropped — the hint is shown alongside the lemma, which
    already implies the part of speech. Returns ``""`` for empty/malformed.
    """
    if not feature or ":" not in feature:
        return ""
    return " ".join(p for p in feature.split(":")[1:] if p)


def make_morphology_cloze_text(
    surface: str,
    lemma: str,
    feature: str,
    source_sentence: str,
) -> str:
    """Wrap ``surface`` with a hinted cloze: ``{{c1::sem::biti, 1sg}}``.

    The hint (``::hint``) tells the learner which lemma + morphology to
    produce. Anki renders the blank as ``[biti, 1sg]``.

    Idempotent: already-clozed text passes through unchanged.
    Returns empty string when ``source_sentence`` is empty.
    """
    if not source_sentence:
        return ""
    if not surface:
        return source_sentence
    if _CLOZE_RE.search(source_sentence):
        return source_sentence
    label = _format_morphology_feature(feature)
    hint = f"{lemma}, {label}" if label else lemma
    pattern = re.compile(rf"\b{re.escape(surface)}\b", re.IGNORECASE)

    def _replacer(m: re.Match) -> str:
        return f"{{{{c1::{m.group(0)}::{hint}}}}}"

    return pattern.sub(_replacer, source_sentence)
