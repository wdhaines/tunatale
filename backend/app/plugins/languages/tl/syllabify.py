"""Tagalog syllabifier — the pronunciation first, the spelling rules second.

:func:`syllabify_tagalog_word` cuts a word where its Wiktionary reading puts
the syllable boundaries (``app.plugins.languages.tl.pronunciation``), because
the key-phrase breakdown plays each piece as that reading's IPA and a caption
must name the syllable its audio says (tunatale-w4m7.16). ``siya`` is said in
one syllable, ``[ˈʃa]``, and ``kailan`` in two, ``kai-lan``; the rules below
split both by their written vowels.

A word Wiktionary does not list, or whose reading cannot be laid onto the
spelling (``mga`` is two syllables from one written vowel), falls back to
:func:`syllabify_tagalog_spelling`, the rules that follow.

The spelling rules are onset-maximization with Tagalog phonotactics, tuned for the Pimsleur audio buildup, so the target is how a word is SAID in
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

import re

from app.generation.syllabify import syllabify as _syllabify
from app.plugins.languages.tl.pronunciation import resolve_reading

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


# Leading and trailing characters that are not part of the word. The rules keep
# them on the edge syllables (``libing?`` → ``li|bing?``), and the breakdown only
# trusts a split whose pieces rejoin the lowercased word, so this must too.
_EDGE = re.compile(r"^(\W*)(.*?)(\W*)$", re.DOTALL)


def syllabify_tagalog_word(word: str) -> list[str]:
    """Split a Tagalog word where its pronunciation does, else by the spelling rules."""
    lead, core, trail = _EDGE.match(word.lower().strip()).groups()
    reading = resolve_reading(core) if core else None
    if reading is None:
        return syllabify_tagalog_spelling(word)
    pieces = list(reading.spelling)
    pieces[0] = lead + pieces[0]
    pieces[-1] = pieces[-1] + trail
    return pieces


def syllabify_tagalog_spelling(word: str) -> list[str]:
    """Split a Tagalog word into syllables using Tagalog phonotactics alone."""
    return _syllabify(
        word,
        _TL_VOWELS,
        _TL_VALID_ONSETS,
        initial_only_onsets=_TL_INITIAL_ONLY_ONSETS,
    )
