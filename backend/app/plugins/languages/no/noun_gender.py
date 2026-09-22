"""Norwegian noun gender without a tagger (tunatale-vvb6).

Stanza tags gender in context; the lookup-table lemmatizer that production runs
(kbb.18) does not, so every card minted on prod got a blank article. This
answers from the lemma alone, in order:

1. The committed NST-derived table (``data/noun_genders.tsv.gz``, built by
   ``scripts/build_noun_genders.py``). An ``Amb`` entry (NST lists both neuter
   and common gender, e.g. ``ting``) is an ANSWER: blank, and the rules below
   must not second-guess it.
2. Derivational suffixes, which fix gender in Bokmål. Each one is checked
   against the user's deck (2026-09-22): -ing ei/en 109/112, -het ei/en 30/30,
   -else en 30/31, -sjon en 41/41, -ment et 7/7, -dom en 6/6. (-skap is mixed
   in the deck and deliberately absent.)
3. A compound takes its head noun's gender: the longest known tail of three or
   more letters, behind a modifier of three or more (``fotballbane`` -> ``bane``).
   Without the modifier bound, tails matched real nouns hiding inside unrelated
   words (``oslo`` came out Fem, ``lofoten`` Masc).

Anything else is ``""``. A blank article is honest; a wrong ``et`` is not.
"""

from __future__ import annotations

import gzip
from functools import cache
from pathlib import Path

TABLE_PATH = Path(__file__).parent / "data" / "noun_genders.tsv.gz"
AMBIGUOUS = "Amb"

# Longest first, so a longer suffix is never shadowed by a shorter one it ends with.
_SUFFIX_RULES: tuple[tuple[str, str], ...] = (
    ("sjon", "Masc"),
    ("else", "Masc"),
    ("ment", "Neut"),
    ("het", "Fem"),
    ("ing", "Fem"),
    ("dom", "Masc"),
)
# A suffix rule needs a stem in front of it: 'ring', 'ting', 'dom' are words,
# not derivations.
_MIN_STEM = 3
_MIN_HEAD = 3
_MIN_MODIFIER = 3


@cache
def _table() -> dict[str, str]:
    with gzip.open(TABLE_PATH, "rt", encoding="utf-8") as fh:
        return dict(line.rstrip("\n").split("\t", 1) for line in fh if line.strip())


def noun_gender(lemma: str) -> str:
    """UD gender ("Masc" / "Fem" / "Neut") for a Norwegian noun lemma, or ``""``."""
    word = lemma.strip().lower()
    if not word:
        return ""
    table = _table()
    if word in table:
        found = table[word]
        return "" if found == AMBIGUOUS else found
    for suffix, gender in _SUFFIX_RULES:
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return gender
    for start in range(_MIN_MODIFIER, len(word) - _MIN_HEAD + 1):
        head = table.get(word[start:])
        if head is not None:
            return "" if head == AMBIGUOUS else head
    return ""
