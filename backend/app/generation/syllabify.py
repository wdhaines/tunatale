"""Syllabification for Pimsleur breakdown generation.

The onset-maximization algorithm itself is language-agnostic; each language
supplies its own vowel set and its set of valid syllable onsets. Slovene and
Norwegian are wired today; ``syllabify_word`` dispatches on the language code.
"""

from __future__ import annotations

from collections.abc import Callable

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


# Norwegian (Bokmål) vowels include y and the special letters æ/ø/å.
_NO_VOWELS = frozenset("aeiouyæøå")

# Valid consonant clusters that can begin a Norwegian syllable (onset
# maximization). Germanic phonotactics: stop/fricative + liquid/glide,
# s-clusters, and the palatal digraphs (kj/gj/sj/skj/tj/fj).
_NO_VALID_ONSETS = frozenset(
    [
        # Three-consonant onsets
        "str",
        "spr",
        "skr",
        "skv",
        "spl",
        "skj",
        "stj",
        # Stop/fricative + liquid
        "bl",
        "br",
        "dr",
        "fl",
        "fr",
        "gl",
        "gr",
        "kl",
        "kr",
        "pl",
        "pr",
        "tr",
        "vr",
        # s-clusters
        "sk",
        "sl",
        "sm",
        "sn",
        "sp",
        "st",
        "sv",
        # Stop/fricative + glide or nasal, palatal digraphs
        "kn",
        "kv",
        "gn",
        "kj",
        "gj",
        "sj",
        "tj",
        "fj",
        "hj",
        "hv",
        "pj",
        "bj",
        "dv",
        "tv",
    ]
)


def _syllabify(word: str, vowels: frozenset[str], valid_onsets: frozenset[str]) -> list[str]:
    """Onset-maximization syllabifier parameterised by language phonotactics.

    For a consonant cluster between two vowels the longest suffix that is a
    recognised onset goes with the following vowel; the remainder closes the
    preceding syllable. Single-vowel and no-vowel words (including syllabic-r
    words like Slovene "prst") are returned as a single syllable.

    Args:
        word: Word to syllabify (case-insensitive; returned lowercased).
        vowels: The language's vowel set.
        valid_onsets: The language's set of valid syllable onsets.

    Returns:
        List of syllables, lowercased.
    """
    word = word.lower().strip()
    if not word:
        return []

    vowel_positions = [i for i, ch in enumerate(word) if ch in vowels]

    if len(vowel_positions) <= 1:
        return [word]

    syllables: list[str] = []
    start = 0

    for vi in range(len(vowel_positions) - 1):
        curr_v = vowel_positions[vi]
        next_v = vowel_positions[vi + 1]
        cluster = word[curr_v + 1 : next_v]

        if len(cluster) <= 1:
            # Hiatus (adjacent vowels) or a single consonant → the consonant,
            # if any, goes with the following vowel (V-CV).
            syllables.append(word[start : curr_v + 1])
            start = curr_v + 1
        else:
            # Multiple consonants — find longest valid onset suffix
            split = _onset_split(cluster, curr_v + 1, valid_onsets)
            syllables.append(word[start:split])
            start = split

    syllables.append(word[start:])
    return syllables


def _onset_split(cluster: str, cluster_start: int, valid_onsets: frozenset[str]) -> int:
    """Return the index in the word where the onset begins.

    Tries progressively shorter suffixes of *cluster* (longest first) until a
    valid onset is found or only one consonant remains.
    """
    for onset_start in range(len(cluster)):
        candidate = cluster[onset_start:]
        if len(candidate) == 1 or candidate in valid_onsets:
            return cluster_start + onset_start
    # Fallback (should not be reached): first consonant closes preceding syllable
    return cluster_start + 1  # pragma: no cover


def syllabify_slovene_word(word: str) -> list[str]:
    """Split a Slovene word into syllables using Slovene phonotactics."""
    return _syllabify(word, _VOWELS, _VALID_ONSETS)


def syllabify_norwegian_word(word: str) -> list[str]:
    """Split a Norwegian (Bokmål) word into syllables."""
    return _syllabify(word, _NO_VOWELS, _NO_VALID_ONSETS)


_SYLLABIFIERS: dict[str, Callable[[str], list[str]]] = {
    "sl": syllabify_slovene_word,
    "no": syllabify_norwegian_word,
}


def syllabify_word(word: str, language_code: str) -> list[str]:
    """Syllabify *word* using the rules for *language_code*.

    Unknown codes fall back to the Slovene onset rules (the breakdown is a
    pedagogical audio aid, so a reasonable default is preferable to raising).
    """
    return _SYLLABIFIERS.get(language_code, syllabify_slovene_word)(word)
