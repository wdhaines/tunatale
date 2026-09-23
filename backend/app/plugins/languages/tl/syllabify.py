"""Tagalog syllabifier — onset-maximization with Tagalog phonotactics.

Tuned for the Pimsleur audio buildup, so the target is how a word is SAID in
chunks, not the Komisyon sa Wikang Filipino hyphenation convention. The two
differ on loan clusters: KWF hyphenates ``prob-le-ma`` and onset maximization
gives ``pro-ble-ma``, which is how the word is pronounced.

Three facts carry the whole design:

* ``y`` and ``w`` are CONSONANTS in Tagalog, never vowels, so ``bahay`` is
  ``ba|hay`` and ``kailan`` is ``ka|i|lan``. No diphthong set is needed: the
  glide lands in the coda or the onset by the ordinary rule.
* ``ng`` is ONE consonant (/ŋ/), so it is a valid onset: ``pangalan`` is
  ``pa|nga|lan``, never ``pan|ga|lan``. When it is followed by another
  consonant it closes the syllable (``sang|kap``, ``mang|ga``).
* Consonant + ``y`` clusters (``sy``, ``ny``, …) open a word (``syu|dad``) but
  split medially (``kan|yang``, ``is|tas|yon``).

``mga`` has a single vowel, so it is one syllable as spelled, even though it is
pronounced /maˈŋa/. The syllabifier must return pieces that rejoin the surface
form, so it cannot insert the unwritten vowel.
"""

from __future__ import annotations

from app.generation.syllabify import syllabify as _syllabify

_TL_VOWELS = frozenset("aeiou")

# Clusters that may open a syllable anywhere in the word: the ``ng`` digraph,
# and the loan clusters (Spanish/English) Tagalog speakers pronounce as onsets.
_TL_VALID_ONSETS = frozenset(
    [
        "ng",
        # stop / f + liquid
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
        # affricate
        "ts",
        # consonant + w
        "kw",
        "gw",
        "bw",
        "pw",
        "dw",
        "tw",
        "sw",
        # consonant + y — valid onsets, but word-initially only (below)
        "by",
        "dy",
        "ky",
        "ly",
        "my",
        "ny",
        "py",
        "sy",
        "ty",
    ]
)

# Consonant + y opens a word (``syudad``, ``dyip``) but splits medially
# (``kan|yang``, ``is|tas|yon``).
_TL_INITIAL_ONLY_ONSETS = frozenset(["by", "dy", "ky", "ly", "my", "ny", "py", "sy", "ty"])


def syllabify_tagalog_word(word: str) -> list[str]:
    """Split a Tagalog word into syllables using Tagalog phonotactics."""
    return _syllabify(
        word,
        _TL_VOWELS,
        _TL_VALID_ONSETS,
        initial_only_onsets=_TL_INITIAL_ONLY_ONSETS,
    )
