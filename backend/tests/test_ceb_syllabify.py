"""Cebuano syllabifier golden table (tunatale-u8nz.6).

The table is the oracle. Every row was checked by hand against Cebuano
phonotactics, and the generic default and these rules were run side by side on
all 28 words on 2026-09-25. They disagreed on exactly two, and both are rows
the rules below exist for:

* ``ng`` is ONE consonant (/ŋ/) and a legal onset, so ``pangalan`` is
  ``pa|nga|lan``. The generic default cut it ``pan|ga|lan``.
* ``kw`` opens a syllable in loans, so ``eskwelahan`` is ``es|kwe|la|han``. The
  generic default cut it ``esk|we|la|han``.

``y`` and ``w`` are consonants, so a glide lands in a coda or an onset by the
ordinary rule (``ba|lay``, ``a|sa|wa``, ``ma|a|yong``). The hyphen of
``kanus-a`` marks a glottal stop before the final vowel; it stays on that
vowel's piece, which is how the Cebuano phoneme planner reads it.
"""

from __future__ import annotations

import pytest

from app.generation.syllabify import syllabify_word
from app.plugins.languages.ceb.syllabify import syllabify_cebuano_word

GOLDEN = [
    ("balay", ["ba", "lay"]),
    ("maayong", ["ma", "a", "yong"]),
    ("buntag", ["bun", "tag"]),
    ("salamat", ["sa", "la", "mat"]),
    ("lubong", ["lu", "bong"]),
    ("kanus-a", ["ka", "nus", "-a"]),
    ("pangalan", ["pa", "nga", "lan"]),
    ("tubig", ["tu", "big"]),
    ("asawa", ["a", "sa", "wa"]),
    ("simbahan", ["sim", "ba", "han"]),
    ("ngano", ["nga", "no"]),
    ("gihigugma", ["gi", "hi", "gug", "ma"]),
    ("kaon", ["ka", "on"]),
    ("ambot", ["am", "bot"]),
    ("trabaho", ["tra", "ba", "ho"]),
    ("eskwelahan", ["es", "kwe", "la", "han"]),
    ("unsa", ["un", "sa"]),
    ("asa", ["a", "sa"]),
    ("diin", ["di", "in"]),
    ("kinsa", ["kin", "sa"]),
    ("kami", ["ka", "mi"]),
    ("siya", ["si", "ya"]),
    ("bayad", ["ba", "yad"]),
    ("palihug", ["pa", "li", "hug"]),
    ("nindot", ["nin", "dot"]),
    ("kalayo", ["ka", "la", "yo"]),
    ("tiil", ["ti", "il"]),
    ("baboy", ["ba", "boy"]),
]


@pytest.mark.parametrize(("word", "expected"), GOLDEN)
def test_golden_table(word, expected):
    assert syllabify_cebuano_word(word) == expected


@pytest.mark.parametrize(("word", "expected"), GOLDEN)
def test_the_breakdown_uses_it(word, expected):
    """The registry is what the key-phrase breakdown asks, so pin THAT path,
    not just the function: an unregistered syllabifier would pass the table
    above and change nothing in a lesson."""
    assert syllabify_word(word, "ceb") == expected


def test_pieces_rejoin_the_word():
    """The breakdown only trusts a split whose pieces rejoin the lowercased word."""
    for word, _ in GOLDEN:
        assert "".join(syllabify_cebuano_word(word)) == word.lower()


def test_sentence_punctuation_stays_on_the_edge_pieces():
    assert syllabify_cebuano_word("Pangalan?") == ["pa", "nga", "lan?"]
