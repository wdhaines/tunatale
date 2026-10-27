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

from app.plugins.languages.ceb.wolff_cards import CardFields, Miss, load_lexicon


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


# ── only a verb's inflection reads (measured 2026-10-28 on the owner's 815 one-word cards) ───
# Wolff's derivational affixes make nouns with the same letters: ka- (kalinaw 'peace'), -an
# (tunghaan 'school'), -on (baybayon 'beach'), a bare pa- (paagi 'way'). Read as the verb's
# inflection, 30 of the 36 cards that got a line were wrong. A line needs a verb's inflection.


@pytest.mark.parametrize(
    ("word", "english"),
    [
        ("kalinaw", "peace"),
        ("kaopisina", "colleague"),
        ("kaayo", "very"),
        ("tunghaan", "school"),
        ("orasan", "clock"),
        ("atubangan", "front"),
        ("selpon", "cell phone"),
        ("baybayon", "beach"),
        ("pabuto", "fireworks"),
        ("paagi", "way, means"),
        ("pangalan", "name"),
        # Wolff's ma-_2 makes an adjective of an adjective root: mainit is 'hot', not 'will become hot'
        ("mainit", "warm"),
        # ma-...-on makes adjectives too: a verb carries one inflection, never ma- and -on
        ("malipayon", "happy"),
    ],
)
def test_a_derived_noun_is_not_read_as_a_verb(lexicon, word, english):
    assert lexicon.parts_line(word, english) is None


@pytest.mark.parametrize(
    ("word", "english", "line"),
    [
        ("moinom", "will drink", "mo- will + inóm drink"),
        # ma- on a verb of state still reads: túlog opens with its verb sense, not an adjective
        ("matulog", "will sleep", "ma- will become + túlog go, put to sleep"),
        # Wolff's 's.o. together with s.o. else': a placeholder mid-phrase becomes a word, at the end it goes
        ("miuban", "went along", "mi- did + ubán companion, someone together with someone else · go with, accompany"),
    ],
)
def test_a_verb_card_still_reads(lexicon, word, english, line):
    assert lexicon.parts_line(word, english) == line


# ── on the card: the line leads the Meaning field (owner, 2026-10-28: "Into meaning is fine") ──
# A field change is data-only (no new notetype field, no full upload). Wolff's sense numbers
# go to Details, as the approved wording says.


def test_an_inflected_card_wolff_does_not_list_gets_the_line_as_its_meaning(lexicon):
    f = lexicon.card_fields("moinom", "will drink")
    assert isinstance(f, CardFields)
    assert f.meaning == '<div class="wolff-parts">mo- will + inóm drink</div>'
    assert f.details == '<div class="wolff-parts-cite">Wolff\u2019s senses: mo- = mu- 1, 2</div>'
    assert (f.spelling, f.verb, f.dictionary_entry) == ("", "", "")


def test_the_line_leads_a_meaning_wolff_gives(lexicon):
    f = lexicon.card_fields("milakaw", "walked")
    assert f.meaning.startswith('<div class="wolff-parts">mi- did + lakáw walk</div>')
    assert '<div class="wolff-parts-cite">Wolff\u2019s senses: mi- = mu- 1, 2</div>' in f.details


def test_a_stacked_word_cites_each_affix(lexicon):
    f = lexicon.card_fields("pakan-a", "feed (them)!")
    assert f.details.endswith(
        '<div class="wolff-parts-cite">Wolff\u2019s senses: pa- = pa- 1; -a = -a = -un, dependent</div>'
    )


def test_a_card_with_no_line_is_unchanged(lexicon):
    # a root, and a word the decoder reads as a derived noun
    assert "wolff-parts" not in lexicon.card_fields("tan-aw", "watch; look").meaning
    tung = lexicon.card_fields("tunghaan", "school")
    assert not isinstance(tung, CardFields) or "wolff-parts" not in tung.meaning


def test_a_card_wolff_cannot_settle_stays_unsettled(lexicon):
    # Wolff prints motágò and motagô: the card's lookup is ambiguous, so the card gets no
    # fields, though the parts line alone would read (it is for the reader)
    assert lexicon.parts_line("motago", "will hide") is not None
    assert lexicon.card_fields("motago", "will hide") == Miss("ambiguous", ("motágò", "motagô"))


def test_a_two_letter_root_is_the_decoders_not_cebuanos(lexicon):
    # nawa 'may it be' is a particle, not na- + wa 'left hand'
    assert lexicon.parts_line("nawa") is None
