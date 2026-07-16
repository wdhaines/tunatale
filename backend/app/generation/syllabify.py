"""Syllabification for Pimsleur breakdown generation.

The onset-maximization algorithm itself is language-agnostic; each language
supplies its own vowel set and its set of valid syllable onsets via the
plugin syllabifier modules (``app.plugins.languages.{sl,no}.syllabify``).
``syllabify_word`` dispatches through the language registry
(``app.languages.get_syllabifier``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def syllabify(
    word: str,
    vowels: frozenset[str],
    valid_onsets: frozenset[str],
    diphthongs: frozenset[str] = frozenset(),
    initial_only_onsets: frozenset[str] = frozenset(),
) -> list[str]:
    """Onset-maximization syllabifier parameterised by language phonotactics.

    For a consonant cluster between two nuclei the longest suffix that is a
    recognised onset goes with the following vowel; the remainder closes the
    preceding syllable. Single-nucleus and no-vowel words (including syllabic-r
    words like Slovene "prst") are returned as a single syllable. A *diphthong*
    counts as one nucleus, so its glide stays attached to the preceding vowel
    rather than splitting off (``bøy|de``, not ``bø|y|de``).

    Args:
        word: Word to syllabify (case-insensitive; returned lowercased).
        vowels: The language's vowel set.
        valid_onsets: The language's set of valid syllable onsets.
        diphthongs: Vowel+glide pairs that form a single nucleus (empty for
            languages, like Slovene, that don't merge any).
        initial_only_onsets: Onsets valid word-initially but never as a medial
            onset (empty for languages that don't distinguish).

    Returns:
        List of syllables, lowercased.
    """
    word = word.lower().strip()
    if not word:
        return []

    nuclei = _nuclei(word, vowels, diphthongs)

    if len(nuclei) <= 1:
        return [word]

    syllables: list[str] = []
    start = 0

    for ni in range(len(nuclei) - 1):
        curr_end = nuclei[ni][1]
        next_start = nuclei[ni + 1][0]
        cluster = word[curr_end + 1 : next_start]

        if len(cluster) <= 1:
            # Hiatus (adjacent nuclei) or a single consonant → the consonant,
            # if any, goes with the following vowel (V-CV).
            syllables.append(word[start : curr_end + 1])
            start = curr_end + 1
        else:
            # Multiple consonants — find longest valid onset suffix. The split
            # is always medial, so word-initial-only onsets are never eligible.
            split = _onset_split(cluster, curr_end + 1, valid_onsets - initial_only_onsets)
            syllables.append(word[start:split])
            start = split

    syllables.append(word[start:])
    return syllables


def _nuclei(word: str, vowels: frozenset[str], diphthongs: frozenset[str]) -> list[tuple[int, int]]:
    """Return each syllable nucleus as an ``(start, end)`` index pair.

    A monophthong nucleus is a single vowel (``start == end``); a diphthong
    (vowel + glide, e.g. ``øy``/``ei``/``au``) spans two indices so the glide is
    not treated as a separate nucleus. With an empty *diphthongs* set every
    vowel is its own nucleus, so the caller behaves exactly as a naïve
    vowel-position scan (Slovene is unchanged).
    """
    nuclei: list[tuple[int, int]] = []
    i = 0
    n = len(word)
    while i < n:
        if word[i] in vowels:
            end = i + 1 if word[i : i + 2] in diphthongs else i
            nuclei.append((i, end))
            i = end + 1
        else:
            i += 1
    return nuclei


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


# Default fallback syllabifier: generic engine with English-like vowels and
# no valid onsets.  Used when a language code has no syllabifier_fn wired.
_DEFAULT_VOWELS = frozenset("aeiou")


def default_syllabifier(word: str) -> list[str]:
    """Fallback syllabifier for unknown languages — generic engine, no onset rules."""
    return syllabify(word, _DEFAULT_VOWELS, frozenset())


def syllabify_word(word: str, language_code: str) -> list[str]:
    """Syllabify *word* using the rules for *language_code*.

    Dispatches through the language registry (``app.languages.get_syllabifier``).
    Unknown codes fall back to the default syllabifier (the breakdown is a
    pedagogical audio aid, so a reasonable default is preferable to raising).
    """
    from app.languages import get_syllabifier

    return get_syllabifier(language_code)(word)
