"""Slovene syllabification for Pimsleur breakdown generation."""

from __future__ import annotations

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
    """Split a Slovene word into syllables.

    Uses onset-maximization: for a consonant cluster between two vowels the
    longest suffix that is a recognised Slovene onset goes with the following
    vowel; the remainder closes the preceding syllable.

    Single-vowel and no-vowel words (including syllabic-r words like "prst")
    are returned as a single syllable.

    Args:
        word: Word to syllabify (case-insensitive; returned lowercased).

    Returns:
        List of syllables, lowercased.
    """
    word = word.lower().strip()
    if not word:
        return []

    vowel_positions = [i for i, ch in enumerate(word) if ch in _VOWELS]

    if len(vowel_positions) <= 1:
        return [word]

    syllables: list[str] = []
    start = 0

    for vi in range(len(vowel_positions) - 1):
        curr_v = vowel_positions[vi]
        next_v = vowel_positions[vi + 1]
        cluster = word[curr_v + 1 : next_v]

        if len(cluster) == 0:
            # Hiatus — split between adjacent vowels
            syllables.append(word[start : curr_v + 1])
            start = curr_v + 1
        elif len(cluster) == 1:
            # Single consonant → V-CV, consonant goes with following vowel
            syllables.append(word[start : curr_v + 1])
            start = curr_v + 1
        else:
            # Multiple consonants — find longest valid onset suffix
            split = _onset_split(cluster, curr_v + 1)
            syllables.append(word[start:split])
            start = split

    syllables.append(word[start:])
    return syllables


def _onset_split(cluster: str, cluster_start: int) -> int:
    """Return the index in the word where the onset begins.

    Tries progressively shorter suffixes of *cluster* (longest first) until a
    valid onset is found or only one consonant remains.
    """
    for onset_start in range(len(cluster)):
        candidate = cluster[onset_start:]
        if len(candidate) == 1 or candidate in _VALID_ONSETS:
            return cluster_start + onset_start
    # Fallback (should not be reached): first consonant closes preceding syllable
    return cluster_start + 1  # pragma: no cover
