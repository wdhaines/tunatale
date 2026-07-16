"""Slovene syllabifier — onset-maximization with Slovene phonotactics."""

from __future__ import annotations

from app.generation.syllabify import syllabify as _syllabify

_VOWELS = frozenset("aeiou")

# Valid consonant clusters that can begin a Slovene syllable.
# Onset maximization: the longest matching suffix of a consonant cluster
# that appears here goes with the following vowel.
_VALID_ONSETS = frozenset(
    [
        # Three-consonant onsets
        "str",
        "spr",
        "skl",
        "štr",
        "škl",
        # Two-consonant onsets — stop + liquid
        "pr",
        "pl",
        "br",
        "bl",
        "tr",
        "dr",
        "kr",
        "kl",
        "gr",
        "gl",
        "fr",
        "fl",
        # Two-consonant onsets — fricative + liquid / nasal
        "vr",
        "vl",
        "sr",
        "sl",
        "zr",
        "zl",
        "šr",
        "šl",
        "žr",
        "žl",
        "čr",
        "čl",
        # Two-consonant onsets — obstruent sequences
        "hv",
        "st",
        "sk",
        "sp",
        "šk",
        "šp",
        "št",
        "šč",
        "zg",
        "zd",
        "zm",
        "zn",
        "mn",
        "gn",
        "ps",
        "pn",
    ]
)


def syllabify_slovene_word(word: str) -> list[str]:
    """Split a Slovene word into syllables using Slovene phonotactics."""
    return _syllabify(word, _VOWELS, _VALID_ONSETS)
