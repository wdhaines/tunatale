"""Cebuano syllabifier: onset maximization with Cebuano phonotactics.

The key-phrase breakdown plays a word piece by piece, so these pieces are how
the word is SAID in chunks. Cebuano's phonotactics match Tagalog's spelling
rules, and three facts carry the design:

* ``y`` and ``w`` are CONSONANTS, never vowels, so a glide lands in a coda or
  an onset by the ordinary rule: ``ba|lay``, ``a|sa|wa``, ``ma|a|yong``.
* ``ng`` is ONE consonant (/ŋ/) and a legal onset: ``pa|nga|lan``, never
  ``pan|ga|lan``. That was the generic default's cut before this module existed
  (tunatale-u8nz.6).
* Loan clusters (Spanish and English) open a syllable: ``tra|ba|ho``,
  ``es|kwe|la|han``. Consonant + ``y`` opens a word (``syu|dad``) but splits
  medially.

Unlike Tagalog there is no pronunciation-first pass: no Cebuano reading table is
wired in, so the spelling rules are the whole answer.
"""

from __future__ import annotations

from app.generation.syllabify import syllabify as _syllabify

_CEB_VOWELS = frozenset("aeiou")

# Clusters that may open a syllable anywhere in the word: the ``ng`` digraph and
# the loan clusters speakers pronounce as onsets.
_CEB_VALID_ONSETS = frozenset(
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
        # consonant + y: valid onsets, but word-initially only (below)
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

# Consonant + y opens a word (``syudad``, ``dyip``) but splits medially.
_CEB_INITIAL_ONLY_ONSETS = frozenset(["by", "dy", "ky", "ly", "my", "ny", "py", "sy", "ty"])


def syllabify_cebuano_word(word: str) -> list[str]:
    """Split a Cebuano word into syllables using Cebuano phonotactics."""
    return _syllabify(
        word,
        _CEB_VOWELS,
        _CEB_VALID_ONSETS,
        initial_only_onsets=_CEB_INITIAL_ONLY_ONSETS,
    )
