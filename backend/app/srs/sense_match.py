"""Does a lesson's gloss name the same sense as a card's translation?

A deliberately small test: the two share a word. It exists to CHOOSE
among the cards one spelling has (``transcript.py::choose_lemma_card``), which
it does well — ``ura`` glossed "the clock" shares a word with exactly one of
"hour" / "clock". It is NOT a judge of whether a single card is right: a probe
over 11 live Norwegian lessons (2026-10-06) flagged 149 headwords whose gloss
shares nothing with their card, and about 20 were real sense conflicts — the
rest were inflection ("went" / "go") and synonym ("the city" / "town"). Do not
reach for this to answer that question (bd tunatale-en6n).

Glosses and translations are the learner's L1, so the stop list is English.
"""

from __future__ import annotations

import re

# Articles are never evidence: "to the" and "the hall" are not one meaning.
_DROP = frozenset({"a", "an", "the"})
# Grammar words are WEAK evidence — they count only when no content word
# decides. They cannot simply be dropped: for a function-word card they ARE the
# sense. Norwegian `vår` is "spring" (noun) / "our" (determinative), and with
# "our" discarded the gloss "our" matched neither card (live deck, 2026-10-06).
# But "to go" must pick the card "go" over the card "to", hence two tiers.
_WEAK = frozenset(
    [
        "to",
        "of",
        "in",
        "on",
        "at",
        "for",
        "from",
        "by",
        "with",
        "and",
        "or",
        "not",
        "be",
        "is",
        "am",
        "are",
        "was",
        "were",
        "been",
        "being",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "its",
        "our",
        "their",
        "this",
        "that",
        "these",
        "those",
        "do",
        "does",
        "did",
        "will",
        "would",
        "shall",
        "should",
        "can",
        "could",
        "may",
        "might",
        "must",
        "have",
        "has",
        "had",
    ]
)
# Longest first, so "wondered" loses "ed" rather than only "d".
_SUFFIXES = ("ing", "ed", "es", "s", "d")
_MIN_STEM = 3


def _stem(word: str) -> str:
    """Enough to join "hours" to "hour" and "wondered" to "wonder" — nothing more."""
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[: -len(suffix)]
    return word


def _words(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """(content words, grammar words) of a gloss or translation, casefolded;
    content words lightly stemmed, articles gone."""
    tokens = [w for w in re.findall(r"[^\W\d_]+", text.casefold()) if w not in _DROP]
    return (
        frozenset(_stem(w) for w in tokens if w not in _WEAK),
        frozenset(w for w in tokens if w in _WEAK),
    )


def sense_overlap(gloss: str, translation: str) -> tuple[int, int]:
    """(content words shared, grammar words shared) — compare as a tuple, so a
    shared content word outranks any number of shared grammar words.
    ``(0, 0)`` is no evidence at all."""
    gloss_content, gloss_weak = _words(gloss)
    card_content, card_weak = _words(translation)
    return len(gloss_content & card_content), len(gloss_weak & card_weak)
