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

Onset maximization alone claims too much in two places, and both are forced
cuts made before it runs (found from one lesson's ``maglakaw``, cut
``ma|gla|kaw``, 2026-10-05):

* A loan cluster may not reach back across a prefix. ``mag-``, ``nag-`` and
  ``pag-`` end in the ``g`` that ``gl``/``gr``/``gw`` would claim, so
  ``maglakaw`` is ``mag|la|kaw``. The same letters are ``ma-`` on a root that
  opens with the cluster (``ma|gwa|pa``), and only the lemma table can tell the
  two apart.
* ``ng`` is one consonant before a consonant as well, so it closes its syllable
  whole: ``nang|la|kaw``, where the cluster rule took its ``g``
  (``nan|gla|kaw``).

Unlike Tagalog there is no pronunciation-first pass: no Cebuano reading table is
wired in, so the spelling rules and these two cuts are the whole answer.
"""

from __future__ import annotations

import gzip
import re
from functools import cache
from pathlib import Path

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


# The verb prefixes that end in g, before a root in l, r or w: the three
# places a prefix's last letter and a root's first spell a loan cluster. These
# are every such boundary in the lemma table (395 forms each, measured
# 2026-10-05). tig- and ig- are left out on purpose: ``tigre`` and ``iglesya``
# open the same way, and the table records no boundary to tell them apart.
_G_PREFIX = re.compile(r"\W*[mnp]ag(?=[lrw])")
_G_CLUSTERS = ("gl", "gr", "gw")

# ng with a consonant after it.
_NG_CODA = re.compile(r"ng(?=[b-df-hj-np-tv-zñ])")

# Sentence punctuation a key phrase leaves on its last word; the lemma table
# lists the bare word.
_TRAILING = ".,;:!?…»\"')"

_LEMMA_TABLE = Path(__file__).parent / "data" / "cebuano_lemmas.tsv.gz"


@cache
def _table_evidence() -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    """What the lemma table says about a g between a prefix and a root.

    Returns the lemmas of every ``mag``/``nag``/``pag`` + l, r or w surface, and
    every surface that itself opens with g + l, r or w. Read once from the
    committed extract and held in memory (about 2,000 words), so the syllabifier
    keeps no database handle open.
    """
    lemmas: dict[str, set[str]] = {}
    cluster_words: set[str] = set()
    with gzip.open(_LEMMA_TABLE, "rt", encoding="utf-8") as fh:
        next(fh)  # the #source_version header
        for line in fh:
            surface, _upos, lemma, _is_default = line.rstrip("\n").lower().split("\t")
            if surface.startswith(_G_CLUSTERS):
                cluster_words.add(surface)
            elif _G_PREFIX.match(surface):
                lemmas.setdefault(surface, set()).add(lemma)
    return {surface: frozenset(found) for surface, found in lemmas.items()}, frozenset(cluster_words)


def _root_opens_with_cluster(word: str) -> bool:
    """Whether *word*, which starts ``mag``/``nag``/``pag`` + l, r or w, is
    really ``ma``/``na``/``pa`` on a root that opens with the cluster.

    The word's own lemma answers when the table can place it: ``magwapa`` is
    listed under ``gwapa`` and ``magwala`` under ``wala``. Otherwise the letters
    after the short prefix decide, when they are a word the table knows
    (``pagwapa``). A word the table cannot speak to is a g-final prefix on an
    ordinary root, which is what these letters nearly always are.
    """
    lemmas, cluster_words = _table_evidence()
    own = lemmas.get(word, frozenset())
    if any(word.startswith(lemma, 2) for lemma in own):
        return True
    if any(word.startswith(lemma, 3) for lemma in own):
        return False
    return word[2:] in cluster_words


def _forced_cuts(text: str) -> list[int]:
    """Indices in *text* where a syllable must start, whatever the onsets allow."""
    cuts = {match.end() for match in _NG_CODA.finditer(text)}
    prefix = _G_PREFIX.match(text)
    if prefix and not _root_opens_with_cluster(text[prefix.end() - 3 :].rstrip(_TRAILING)):
        cuts.add(prefix.end())
    return sorted(cuts)


def _has_vowel(text: str) -> bool:
    return any(ch in _CEB_VOWELS for ch in text)


def _by_phonotactics(text: str) -> list[str]:
    return _syllabify(
        text,
        _CEB_VOWELS,
        _CEB_VALID_ONSETS,
        initial_only_onsets=_CEB_INITIAL_ONLY_ONSETS,
    )


def syllabify_cebuano_word(word: str) -> list[str]:
    """Split a Cebuano word into syllables using Cebuano phonotactics."""
    text = word.lower().strip()
    pieces: list[str] = []
    start = 0
    for cut in _forced_cuts(text):
        # A cut needs a syllable on each side of it (``bangs`` has no second).
        if _has_vowel(text[start:cut]) and _has_vowel(text[cut:]):
            pieces += _by_phonotactics(text[start:cut])
            start = cut
    return pieces + _by_phonotactics(text[start:])
