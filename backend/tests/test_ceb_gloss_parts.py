"""A Cebuano word glossed by its parts (tunatale-h0q3.26).

The owner's design (https://claude.ai/artifact/WYdxSNf99uuvyzDyojEWza) and wording
(https://claude.ai/artifact/WNCxM9XxVTpje6ANtoyiRm, version 2, approved 2026-10-28):
the line reads "form label + root", each affix with ONE short label chosen by what kind
of root it is on (Wolff's class codes, or the root's part of speech when it has no verb
senses), and the root's own meaning first, then the verb sense that licensed the affix.

Silent rather than guessing: no analysis, a form the wording table has no label for, or
two readings that differ. Two stress forms of one root are two words (nadalá / nadála),
so readings that come from different forms make the line silent; within one form,
Wolff's class B rule decides (a verb of state's ma-/na- is its own active, §7.12).

Every expected line below was written from Wolff's entries before the builder existed.
"""

from __future__ import annotations

import pytest

from app.plugins.languages.ceb.wolff_cards import load_lexicon


@pytest.fixture(scope="module")
def lexicon():
    return load_lexicon()


@pytest.mark.parametrize(
    ("word", "line"),
    [
        # mu- on adtu [A2]: the plain 'will'; the root is a place word first, then the verb
        ("moadto", "mo- will + ádto there (far away, future) · go, bring to a place"),
        ("muadtu", "mo- will + ádto there (far away, future) · go, bring to a place"),
        ("miabot", "mi- did + abót arrive, reach a place"),  # abut v {1} [A; a12]
        # maka- lists its three readings, never picks one
        ("makaadto", "maka- can / get to / happen to + ádto there (far away, future) · go, bring to a place"),
        # -a: the patient's command; kúhà's own first sense covers its verb sense 'get'
        ("kuhaa", "kúhà get and bring, get and take away + -a do it to it! (or: didn't)"),
        # gi- on káun [a]: the patient; the general instrument row does not compete
        ("gikaon", "gi- did, to it + kâon eat"),
        # pa- under the patient command, on káun's pa- form [A; ac]
        ("pakan-a", "pa- have someone + kâon eat + -a do it to it! (or: didn't)"),
        # túlog [B123S6; a2]: one form, so Wolff's class B rule makes na- the state
        ("natulog", "na- became + túlog go, put to sleep"),
        # kítà takes mag- only as a mutual verb ({2} [C; a4c] meet with)
        ("magkita", "mag- will, each other + kítà meet with"),
        # suwat '= sulat' (write) is followed; the homograph súwat 'pull up' ranks below it
        ("mosuwat", "mo- will + suwát write"),
        # ághop is an adjective with no verb sense: Wolff's mu- {3a}, 'become [adj.]'
        ("moaghop", "mo- will become + ághop tender in feelings or treatment"),
        # árat's first sense and its verb sense say the same thing ('the' aside): said once
        ("paarata", "pa- have someone + árat for throat to be sore + -a do it to it! (or: didn't)"),
        # dakù is an adjective first ('big, great'), and its verb sense is a verb of state
        ("modako", "mo- will become + dakô big, great · become, make big"),
    ],
)
def test_a_word_reads_as_its_parts(lexicon, word, line):
    assert lexicon.parts_line(word) == line


@pytest.mark.parametrize(
    "word",
    [
        "nadala",  # dalá's na- (got brought) and dála's (carried along): two stress words
        "nagpuyo",  # puyô [A] 'is living' and púyò [C] 'live together': two stress words
        "misor",  # a form of address: mi- + sor (a mahjong wind) is the decoder's, and a noun reads nothing
        "giabwag",  # ábwag {1} [AP; c1]: one sense makes gi- both the patient and the thing moved
        "padakbayana",  # dakbayan 'city' is a noun: pa- has no label on a noun
        "pasusoa",  # the decoder's best root, susu 'breast', has no verb; sûsò 'pound' is a guess
        "padaplina",  # dáplin 'side' is a noun, and pa- on a noun ('go to the side') has no label
        "gipaabangan",  # three affixes the decoder does not analyse
        "adto",  # a bare root has no parts
        "asdfgh",
    ],
)
def test_silent_when_the_reading_is_not_one(lexicon, word):
    assert lexicon.parts_line(word) is None


def test_the_parts_are_in_the_words_order_and_say_which_is_the_affix(lexicon):
    parts = lexicon.parts("pakan-a")
    assert [(p.text, p.affix) for p in parts] == [("pa-", True), ("kâon", False), ("-a", True)]
    assert [p.gloss for p in parts] == ["have someone", "eat", "do it to it! (or: didn't)"]


def test_the_english_it_came_with_does_not_override_a_settled_reading(lexicon):
    # the meaning ranks the decoder's candidates; one reading stays one reading
    assert lexicon.parts_line("gikaon", "they ate the rice") == "gi- did, to it + kâon eat"
