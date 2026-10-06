"""Where a long Tagalog word is cut in an Enunciated line (tunatale-tyfk).

The user, 2026-10-06, after hearing Tagalog one word at a time: a really long
word like *Nakakalungkot* may warrant splitting the way Norwegian compounds
are. They then heard four words cut between the affixes and the root and chose
that shape, and later the short pause at the cut over a comma. The rule here is
the one proposed to them: five syllables or more, cut after a long prefix, else
in front of the root the lemma table names.

``", "`` is the registry's notation for a cut (``LanguageConfig.slow_word_fn``,
the same one Norwegian's compounds use); the renderer turns it into the pause.
"""

from __future__ import annotations

import pytest

from app.audio.enunciation import plan_line
from app.languages import get_slow_word
from app.plugins.languages.tl.slow_word import slow_tagalog_word


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        # The user's own example, and the word their lesson's line was heard
        # with. The lemma table maps both to themselves, so only the prefix
        # list can cut them.
        ("Nakakalungkot", "Nakaka, lungkot"),
        ("Nakikiramay", "Nakiki, ramay"),
        ("Napakaganda", "Napaka, ganda"),
        ("pinakamaganda", "pinaka, maganda"),
        ("pagkakataon", "pagkaka, taon"),
        ("makikipagkita", "makikipag, kita"),
        # No listed prefix: the cut goes in front of the lemma table's root, so
        # the reduplicated syllable stays with the affix it belongs to.
        ("Magtatrabaho", "Magta, trabaho"),
        ("nagsasalita", "nagsa, salita"),
        ("makapagpahinga", "makapagpa, hinga"),
    ],
)
def test_a_long_word_is_cut_between_its_affixes_and_its_root(word, expected):
    assert slow_tagalog_word(word) == expected


@pytest.mark.parametrize(
    "word",
    [
        # Four syllables: the root rule WOULD cut these (magda + dala, pu +
        # punta), which is too eager, and is why the rule starts at five.
        "Magdadala",
        "pupunta",
        "kumakain",
        # Counted as four, because "kuwen" is said as one syllable.
        "nagkukuwento",
        # Long, with no listed prefix and no root the table can place at the
        # end of the word: left whole, as every word was before.
        "pakikiramay",
        "kinabukasan",
        "pinagdaanan",
        "nagpapasalamat",
        # Long, and the table does name a root it ends in (paliwanag), but the
        # cut would leave one syllable in front: "nag" is not a run-up, and a
        # pause after it would only make the word sound broken.
        "nagpaliwanag",
        "po",
        "",
    ],
)
def test_a_word_that_is_short_or_has_no_known_cut_is_left_whole(word):
    assert slow_tagalog_word(word) == word


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("Nakakalungkot!", "Nakaka, lungkot!"),
        ("«Nakikiramay»,", "«Nakiki, ramay»,"),
        ("?", "?"),
    ],
)
def test_punctuation_stays_on_the_edges_of_the_word(word, expected):
    assert slow_tagalog_word(word) == expected


def test_the_registry_serves_it_and_a_line_is_cut_with_it():
    """Registered, and in the shape the line planner reads: the heard line."""
    slow_word = get_slow_word("tl")

    assert slow_word is slow_tagalog_word
    assert plan_line("Magandang gabi po. Nakikiramay po ako.", slow_word).words == (
        ("Magandang",),
        ("gabi",),
        ("po.",),
        ("Nakiki", "ramay"),
        ("po",),
        ("ako.",),
    )
