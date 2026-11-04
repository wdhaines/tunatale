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
    # (EnglishRelations' base map lacks 'ate' and 'saw'; 'eaten' is in it)
    assert lexicon.parts_line("gikaon", "it was eaten") == "gi- did, to it + kâon eat"


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


# ── precision on Wolff's own lessons (measured 2026-10-28: 59 build-up rows of Lessons 1-4,
# about 12 wrong) ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("word", "english"),
    [
        # -a on a noun is Wolff's 'that particular', not the patient's command
        ("baláya", "house"),
        ("kwartóha", "particular room"),
        ("dyípa", "particular jeep"),
        # the English the word came with agrees with none of the senses that take the affix:
        # kítà's maka- is 'earn', hádlok's ma- 'can be scared'
        ("nakakitâ", "see"),
        ("mahádlok", "am afraid (of something in the future)"),
    ],
)
def test_silent_where_the_lesson_shows_the_reading_is_wrong(lexicon, word, english):
    assert lexicon.parts_line(word, english) is None


@pytest.mark.parametrize(
    ("word", "english", "line"),
    [
        ("mòábang", "will rent", "mo- will + ábang rent"),
        ("subáya", "follow", "súbay follow, go along a certain way + -a do it to it! (or: didn't)"),
        ("mohúnong", "stop", "mo- will + húnong stop, cease forward motion, cause something to do so"),
    ],
)
def test_a_reading_the_lesson_agrees_with_still_shows(lexicon, word, english, line):
    assert lexicon.parts_line(word, english) == line


def test_ma_on_an_adjective_root_is_the_adjective_with_no_english_to_go_by(lexicon):
    # the reader's word may come with no gloss: mainit is still 'hot', not 'will become hot'
    assert lexicon.parts_line("mainit") is None


# ── the build-up rows the Wolff EPUB withholds (tunatale-30t0) ────────────────────────────────
# The owner's review of the book (2026-10-28) found these lines wrong; the book hides them until
# TT reads them right or says nothing. Written from Wolff's entries before any fix.


@pytest.mark.parametrize(
    ("word", "english", "line"),
    [
        # bílin 'leave s.t. behind' [A; c] is native: Belén (bilin, the nativity scene) is a
        # different entry, and the Handbook spelling of bílin keeps its i
        ("ibílin", "will leave", "i- will bring it + bílin leave something behind"),
        # hángyò {1} [A; b5c] 'ask for s.t., request as a favor' is a verb sense with its class
        # code, and it comes first: 'haggle the price down' is {2}
        ("mohángyò", "propose, ask a favor", "mo- will + hángyò ask for, request as a favor"),
    ],
)
def test_a_withheld_row_reads_right(lexicon, word, english, line):
    assert lexicon.parts_line(word, english) == line


@pytest.mark.parametrize(
    ("word", "english", "right", "wrong"),
    [
        # the word's stress is sakít 'painful, sick'; sákit 'torture' is the other stress word
        ("nagsakít", "aches", "nag- is, or was + sakít ", ("sákit", "torture")),
        # báyad {1} 'pay for s.t.' is the row; {2} 'pay a moral debt' licenses mag- but is not it
        ("nagbayád", "were paying", "nag- is, or was + báyad pay for", ("moral debt",)),
        ("makakàón", "can eat", "maka- can / get to / happen to + kâon eat", ("destroy",)),
        ("napíldi", "lost", "na- got + píldi lose in a contest", ("die of",)),
        # buhî here is the → stress form of búhì 'set free' (its own entry), not of búhì 'alive'
        ("buhîi", "to be let go", "buhî set free", ("raise", "family")),
        # ingón {1} 'say, tell' takes -an and i- [b5c]; only {1a} 'said to be' takes -a
        ("íngna", "tell him", "ingón say, tell", ("said to be",)),
        # sóod is sulúd 'go in' with its l dropped (Wolff §5.2), not sùód 'close in space'
        ("mosóod", "will enter", "mo- will + sulód go in, into", ("sùód", "close in space")),
        # guwà is gulà 'go out from' with its l dropped; gúwa = gúla is raw food
        ("moguwâ", "get out", "mo- will + gulâ go out from", ("cooked",)),
        # maN- + ádto (Wolff's mangádtu), never ma- + ngádto
        ("mangádto", "will go (plural)", "maN- ", ("ngádto", "can be")),
        ("mangádto", "go", "maN- ", ("ngádto", "can be")),
    ],
)
def test_a_withheld_row_reads_right_or_says_nothing(lexicon, word, english, right, wrong):
    line = lexicon.parts_line(word, english)
    assert line is None or line.startswith(right), line
    assert line is None or not any(w in line for w in wrong), line


# Known wrong, still withheld from the book: each row's English reaches a verb sense only through
# one loose word ('pay' in súhul's 'hire for wages, pay a certain wage'; 'do' in unay's 'do some
# harm …'), and every rule tried that silences them also silenced right lines that agree the same
# way (molakáw 'want to go out' is lakáw 'walk'). Strict: fixing them turns this red.
_LOOSE_ENGLISH = pytest.mark.xfail(
    strict=True, reason="tunatale-30t0: no rule yet separates these from rows that agree as loosely"
)


@pytest.mark.parametrize(
    ("word", "english"),
    [
        # a noun: the row is 'money to pay a salary', which no inflection of súhul says
        pytest.param("isúhol", "money to pay a salary", marks=_LOOSE_ENGLISH),
        # no sense of unay is 'do by oneself'; 'mainland' is a noun
        pytest.param("nangunáy", "do by oneself", marks=_LOOSE_ENGLISH),
    ],
)
def test_a_withheld_row_with_no_reading_says_nothing(lexicon, word, english):
    assert lexicon.parts_line(word, english) is None


# --- the helpers the 30t0 readings stand on ----------------------------------------------------


def test_a_card_word_gives_back_the_letters_that_spell_the_root_with_their_marks():
    from app.plugins.languages.ceb.wolff_cards import _card_letters

    assert _card_letters("ibílin", "bílin", "i-") == "bílin"
    # a mark with no letter before it is dropped, not an error
    assert _card_letters("́ibílin", "bílin", "i-") == "bílin"
    # a word §5 does not derive from that root has no root letters
    assert _card_letters("zzz", "bílin", "i-") is None


def test_marked_letters_ignores_a_mark_that_is_no_stress_or_glottal_mark():
    from app.plugins.languages.ceb.wolff_cards import _marked_letters

    assert _marked_letters("́ä") == [("a", frozenset())]
    assert _marked_letters("sakít") == [
        ("s", frozenset()),
        ("a", frozenset()),
        ("k", frozenset()),
        ("i", frozenset({"́"})),
        ("t", frozenset()),
    ]
