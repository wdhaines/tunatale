"""Gloss by parts on an irregular English past (tunatale-h0q3.37).

A lesson glosses a completed-aspect verb in the English past, and an irregular past is
not a word WordNet's base map leads back to its verb: ``morphy`` returns the noun or the
proper name first (Ate the goddess, a saw, SAT), so gikaon 'ate' and nakakita 'saw' read
as nothing while gikaon 'eaten' reads. Worse, 'ate' is not unmatched: the definition of
Ate gives it a weak hit on a shuttlecock sense of káun, so "no sense agrees" never fires.

The rule (measured 2026-11-25 over the 1676 one-word build-up rows of the Wolff book):
only when no sense agrees with the English as written by the same word or a related one,
try the English with each irregular past replaced by its verb, and keep only the senses
whose gloss says that verb itself. Applying a base-verb table everywhere fixed one book
row and broke others (the guards below), so these tests pin both directions.
Card matching does not take the table: its scores are pinned unchanged.

Every expected line was taken from the line its regular form already reads.
"""

from __future__ import annotations

import pytest

from app.plugins.languages.ceb.wolff_cards import load_lexicon


@pytest.fixture(scope="module")
def lexicon():
    return load_lexicon()


@pytest.mark.parametrize(
    ("word", "english", "line"),
    [
        ("gikaon", "ate", "gi- did, to it + kâon eat"),  # = gikaon 'eaten'
        ("nakakita", "saw", "naka- got to / happened to + kítà see"),  # = nakakita 'seen'
        ("napíldi", "lost", "na- got + píldi lose in a contest"),  # a book row, silent before
    ],
)
def test_an_irregular_past_reads_as_its_verb(lexicon, word, english, line):
    assert lexicon.parts_line(word, english) == line


@pytest.mark.parametrize(
    ("word", "english", "line"),
    [
        ("gikaon", "eaten", "gi- did, to it + kâon eat"),
        ("nakakita", "seen", "naka- got to / happened to + kítà see"),
    ],
)
def test_the_participle_reads_as_before(lexicon, word, english, line):
    assert lexicon.parts_line(word, english) == line


@pytest.mark.parametrize(
    ("word", "english", "line"),
    [
        # a base-verb table everywhere made this 'be able to carry, lift, or move something heavy'
        ("nakadàóg", "won", "naka- got to / happened to + dàóg win money, prize"),
        # ... and this 'for something to be going on'
        ("milakaw", "left", "mi- did + lakáw go away, depart"),
    ],
)
def test_a_past_that_already_reads_keeps_its_sense(lexicon, word, english, line):
    assert lexicon.parts_line(word, english) == line


def test_silent_rather_than_a_loose_sense(lexicon):
    # 'sit' does not settle língkod today; a base-verb table read it as 'assume, hold office or position'
    assert lexicon.parts_line("milingkod", "sat") is None


def test_card_matching_scores_do_not_take_the_past_table(lexicon):
    relations = lexicon.relations
    assert relations.score("ate", {"ate"}, "eat") == (0, 0, 0)
    assert relations.score("saw", {"saw"}, "see") == (0, 0, 0)
