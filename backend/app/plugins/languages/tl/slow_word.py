"""Where a long Tagalog word is cut when a line is enunciated.

An Enunciated line is said one word at a time, and most Tagalog words are fine
said whole. A long affixed one is not: the user, on hearing *Nakakalungkot*
that way (2026-10-06, tunatale-tyfk), asked for it to be split the way
Norwegian splits its compounds, then heard four words cut between the affixes
and the root and chose that. This is the rule proposed to them afterwards.

A word of FIVE syllables or more is cut once:

1. after a long prefix from the list below, when it has one; else
2. in front of its root, when the lemma table names a root the word ends in.

Either cut must leave two syllables on each side, and a word with neither is
left whole. Five is where the rule starts because the root cut alone is too
eager below it: it would split *Magdadala* (magda + dala) and *pupunta*
(pu + punta), which nobody found hard to follow.

The prefix list exists because the lemma table does not know the words the rule
was asked for: *nakakalungkot*, *napakaganda*, *pinakamaganda* and
*pagkakataon* each map to themselves there.
"""

from __future__ import annotations

import re
from contextlib import closing
from functools import cache
from pathlib import Path

from app.plugins.languages.tl.syllabify import syllabify_tagalog_word
from app.srs.lemma_table import LemmaTable, ensure_lemma_table_db

_DATA = Path(__file__).parent / "data"

_LONG_SYLLABLES = 5
_SIDE_SYLLABLES = 2

# Longest first: "nakikipag" must be tried before "nakiki". Each is an affix
# stack a learner hears as one run-up to the root. Extend it with the same
# evidence as the rest: a long word the lemma table cannot cut.
_PREFIXES = ("nakikipag", "makikipag", "pagkaka", "nakaka", "napaka", "pinaka", "nakiki", "makiki")

# Punctuation on the word's edges stays there (``Nakakalungkot!``), around the
# cut word. The same split the syllabifier makes of its own input.
_EDGE = re.compile(r"^(\W*)(.*?)(\W*)$", re.DOTALL)


def _syllables(text: str) -> int:
    return len(syllabify_tagalog_word(text)) if text else 0


@cache
def _lemma_db() -> Path:
    """The built lemma table, located once: finding it hashes the whole extract."""
    return ensure_lemma_table_db(_DATA / "tagalog_lemmas.tsv.gz")


def _root(word: str) -> str | None:
    """The lemma table's root for *word*, or ``None`` when it lists none.

    Read from the plugin's own table rather than through ``get_lemmatizer``:
    that one is switched to plain lowercasing by a global setting (the test and
    CI default), and a pause that moved with a deployment's lemmatizer setting
    would make one lesson sound two ways. Opened per lookup and closed again —
    only a long word with no listed prefix gets this far, and a connection held
    for the life of the process is a handle nobody closes.
    """
    with closing(LemmaTable(_lemma_db())) as table:
        readings = table.readings(word)
    return next((r.lemma for r in readings if r.is_default), None)


def _cut(word: str) -> int | None:
    """Index in lowercase *word* where it is cut, or ``None`` to leave it whole."""
    if _syllables(word) < _LONG_SYLLABLES:
        return None
    for prefix in _PREFIXES:
        if word.startswith(prefix) and _syllables(word[len(prefix) :]) >= _SIDE_SYLLABLES:
            return len(prefix)
    root = _root(word)
    if root is None or root == word or not word.endswith(root):
        return None
    cut = len(word) - len(root)
    if _syllables(root) >= _SIDE_SYLLABLES and _syllables(word[:cut]) >= _SIDE_SYLLABLES:
        return cut
    return None


def slow_tagalog_word(word: str) -> str:
    """*word* with ``", "`` where it is cut, or unchanged.

    ``Nakakalungkot`` -> ``Nakaka, lungkot``. The comma is the registry's
    notation for a cut (``LanguageConfig.slow_word_fn``); the renderer turns it
    into a short pause and it is never spoken as a comma.
    """
    lead, core, trail = _EDGE.match(word).groups()  # type: ignore[union-attr]  # the pattern matches any string
    cut = _cut(core.lower()) if core else None
    if cut is None:
        return word
    return f"{lead}{core[:cut]}, {core[cut:]}{trail}"
