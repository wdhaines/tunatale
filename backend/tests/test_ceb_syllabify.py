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

Two later tables pin what onset maximization alone got wrong, both found from
one lesson's ``maglakaw``, cut ``ma|gla|kaw`` (2026-10-05):

* A loan cluster may not reach back across a prefix. ``mag-``, ``nag-`` and
  ``pag-`` end in the ``g`` that ``gl``/``gr``/``gw`` would claim, so
  ``maglakaw`` is ``mag|la|kaw``. The lemma table tells that apart from a root
  that really opens with the cluster (``ma`` + ``gwapa``).
* ``ng`` is one consonant before a consonant too, so ``nanglakaw`` is
  ``nang|la|kaw``. The cluster rule used to take its ``g``: ``nan|gla|kaw``.
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

# mag-/nag-/pag- + a root in l, r or w: the prefix keeps its g.
PREFIX_KEEPS_ITS_G = [
    ("maglakaw", ["mag", "la", "kaw"]),
    ("naglakaw", ["nag", "la", "kaw"]),
    ("paglakaw", ["pag", "la", "kaw"]),
    ("magluto", ["mag", "lu", "to"]),
    ("magwala", ["mag", "wa", "la"]),
    # Not in the lemma table, so the prefix rule alone decides. The loan
    # cluster INSIDE the root still opens its syllable.
    ("nagreklamo", ["nag", "re", "kla", "mo"]),
    ("paglaum", ["pag", "la", "um"]),
]

# The same letters where the root itself opens with the cluster, and loan
# clusters nowhere near a prefix. These must not move.
CLUSTER_STAYS_AN_ONSET = [
    # The table's lemma starts at the cluster: ma + gwapa, ma + glu.
    ("magwapa", ["ma", "gwa", "pa"]),
    ("maglu", ["ma", "glu"]),
    # No reading of its own, but what follows "pa" is a word the table knows.
    ("pagwapa", ["pa", "gwa", "pa"]),
    ("nagtrabaho", ["nag", "tra", "ba", "ho"]),
    ("tigre", ["ti", "gre"]),
    ("milagro", ["mi", "la", "gro"]),
    ("libro", ["li", "bro"]),
]

# ng closes a syllable whole when a consonant follows it.
NG_STAYS_WHOLE = [
    ("nanglakaw", ["nang", "la", "kaw"]),
    ("panglaba", ["pang", "la", "ba"]),
    ("banglay", ["bang", "lay"]),
    ("gihangwat", ["gi", "hang", "wat"]),
    ("mangga", ["mang", "ga"]),
    # No vowel after the ng, so there is no second syllable to cut off.
    ("bangs", ["bangs"]),
]

ALL_TABLES = GOLDEN + PREFIX_KEEPS_ITS_G + CLUSTER_STAYS_AN_ONSET + NG_STAYS_WHOLE


@pytest.mark.parametrize(("word", "expected"), PREFIX_KEEPS_ITS_G)
def test_a_loan_cluster_does_not_reach_back_across_a_prefix(word, expected):
    assert syllabify_cebuano_word(word) == expected


@pytest.mark.parametrize(("word", "expected"), CLUSTER_STAYS_AN_ONSET)
def test_a_root_that_opens_with_the_cluster_keeps_it(word, expected):
    assert syllabify_cebuano_word(word) == expected


@pytest.mark.parametrize(("word", "expected"), NG_STAYS_WHOLE)
def test_ng_is_not_cut_to_feed_a_cluster(word, expected):
    assert syllabify_cebuano_word(word) == expected


def test_the_prefix_cut_survives_case_and_punctuation():
    """A key phrase reaches the syllabifier as written: capitalised, and with
    the sentence's punctuation still on its edge words."""
    assert syllabify_cebuano_word("Maglakaw") == ["mag", "la", "kaw"]
    assert syllabify_cebuano_word("maglakaw.") == ["mag", "la", "kaw."]
    assert syllabify_cebuano_word("«Maglakaw") == ["«mag", "la", "kaw"]


@pytest.mark.parametrize(("word", "expected"), GOLDEN)
def test_golden_table(word, expected):
    assert syllabify_cebuano_word(word) == expected


@pytest.mark.parametrize(("word", "expected"), ALL_TABLES)
def test_the_breakdown_uses_it(word, expected):
    """The registry is what the key-phrase breakdown asks, so pin THAT path,
    not just the function: an unregistered syllabifier would pass the table
    above and change nothing in a lesson."""
    assert syllabify_word(word, "ceb") == expected


def test_pieces_rejoin_the_word():
    """The breakdown only trusts a split whose pieces rejoin the lowercased word."""
    for word, _ in ALL_TABLES:
        assert "".join(syllabify_cebuano_word(word)) == word.lower()


def test_sentence_punctuation_stays_on_the_edge_pieces():
    assert syllabify_cebuano_word("Pangalan?") == ["pa", "nga", "lan?"]
