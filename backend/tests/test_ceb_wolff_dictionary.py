"""Parsing Wolff's dictionary entries (tunatale-h0q3.2).

The fixtures in ``tests/data/wolff_entries.json`` are entry paragraphs copied
verbatim from Project Gutenberg #40074 (Wolff, *A Dictionary of Cebuano
Visayan*, 1972, public domain). The oracle is the entry as printed: each
expected value below can be read off the paragraph by eye.

The verb-class decoding follows the dictionary's intro §7.11-7.231: capitals
are the active classes (A action, B stative, C mutual), minuscules the passive
ones (a direct, b local, c instrumental), and a digit removes an affix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.plugins.languages.ceb.wolff_dictionary import (
    Example,
    compose,
    decode_classes,
    is_affix_label,
    parse_entry,
    shifted,
    split_examples,
)

ENTRIES = json.loads((Path(__file__).parent / "data" / "wolff_entries.json").read_text(encoding="utf-8"))


def _entry(key):
    return parse_entry(ENTRIES[key])


# --- the head of an entry ---------------------------------------------------------------------


def test_heads_homograph_number_and_variants():
    e = _entry("tuu, túu_1")
    assert e.heads == ["tuu", "túu"]
    assert e.number == "1"
    assert e.id == "tuu"
    assert _entry("kini_1").id == "kini_1"


def test_short_forms_and_cross_references():
    anaa = _entry("anáa")
    assert anaa.root.short_forms == ["náa"]
    assert anaa.root.senses[0].gloss == "be there (near you)"
    naa = _entry("náa")
    assert naa.root.xref == ("=", "anáa")
    assert naa.root.senses == []
    assert _entry("maáyu").root.xref == ("see", "áyu")
    assert _entry("kini_1").root.short_forms == ["ni", "king"]
    assert _entry("dílì").root.short_forms == ["dì"]


# --- senses -----------------------------------------------------------------------------------


def test_senses_carry_part_of_speech_number_classes_and_gloss():
    e = _entry("tan-aw")
    first = e.root.senses[0]
    assert (first.pos, first.number, first.classes, first.gloss) == ("v", "1", "A; ab2", "see, look at")
    assert [s.number for s in e.root.senses] == ["1", "2", "2a", "3", "4", None]
    assert e.root.senses[2].classes == "b8"
    noun = e.root.senses[-1]
    assert (noun.pos, noun.gloss) == ("n", "way one regards s.t.")


def test_a_sense_inherits_the_class_code_its_verb_heading_gave():
    # 'v [A; ab2] {1} see...' : the code sits before the first number and holds for every
    # numbered sense until one gives its own
    senses = _entry("tan-aw").root.senses
    assert senses[0].classes == senses[1].classes == "A; ab2"
    assert senses[4].classes == "a12"
    assert senses[5].classes is None  # the noun


def test_examples_pair_cebuano_with_english():
    s = _entry("tan-aw").root.senses[0]
    assert s.examples[0] == Example("Mutan-aw kug sini karung gabíi", "I’ll go see a movie tonight.")
    assert s.examples[1] == Example("Ispisyalista ang nagtan-aw níya", "A specialist is examining her.")
    assert len(s.examples) == 3
    q = _entry("anáa").root.senses[0]
    assert q.examples[0] == Example("Náa ba dihà ang yáwi?", "Do you have (is there) the key?")


def test_numeral_and_noun_heads():
    tulu = _entry("tulu")
    assert tulu.root.senses[0].pos == "numeral"
    assert tulu.root.senses[0].gloss == "three"
    bata = _entry("bátà")
    assert [s.gloss for s in bata.root.senses[:4]] == [
        "child",
        "son or daughter",
        "mistress, concubine",
        "bodyguard, protege of s.o. of high rank",
    ]
    assert bata.root.senses[4].phrase == "nga lintì"  # '— nga lintì tough, ruffian'
    assert [(s.pos, s.gloss) for s in bata.root.senses if s.pos == "a"][0] == (
        "a",
        "young, in the early period of life or growth",
    )


# --- sub-entries ------------------------------------------------------------------------------


def test_affix_sub_entries_and_their_words():
    subs = {s.label: s for s in _entry("tan-aw").subs}
    assert [s.label for s in _entry("tan-aw").subs] == [
        "hi-/ha-",
        "hiN-",
        "-in-ay",
        "ig-r-/l-",
        "-in-an(→)",
        "pag-",
        "paN-",
        "paN-un",
        "-um-r-",
        "-l-un",
    ]
    assert subs["paN-"].form == "panan-aw"
    assert subs["paN-"].senses[0].gloss == "sense of sight"
    assert subs["pag-"].form == "pagtan-aw"
    assert subs["-l-un"].form == "talan-áwun"
    assert subs["-in-an(→)"].shift == "→"
    assert subs["ig-r-/l-"].form is None  # no example to read it from, and no rule for it


def test_word_sub_entries_and_label_only_ones():
    labels = [s.label for s in _entry("tulu").subs]
    assert labels[:5] == ["r-", "ika-", "ka-, maka-", "-in-", "katluan"]
    katluan = _entry("tulu").subs[4]
    assert katluan.form == "katluan"
    assert katluan.senses[0].gloss == "thirty"
    assert _entry("tulu").subs[1].senses[0].gloss == "third"


def test_a_stress_shift_of_the_root_is_its_own_form():
    taas = _entry("taas")
    shift = [s for s in taas.subs if s.label == "(←)"][0]
    assert shift.form == "táas"
    assert shift.senses[0].gloss == "be stretched long, tall"
    upstairs = [s for s in taas.subs if s.label == "i-, i-(←)"][0]
    assert upstairs.senses[0].gloss == "upstairs"
    duktur = [s for s in _entry("duktur").subs if s.label == "(→)"][0]
    assert duktur.form == "duktǔr"
    assert duktur.xref == ("=", "duktur, n 1, v")


def test_phrases_on_the_root_stay_in_its_senses():
    # '— ang buut v [B4] be in a good mood' is the root in a phrase, not a sub-entry
    root = _entry("áyu").root
    phrase = [s for s in root.senses if s.phrase][0]
    assert (phrase.phrase, phrase.pos, phrase.classes, phrase.gloss) == ("ang buut", "v", "B4", "be in a good mood")
    labels = [s.label for s in _entry("áyu").subs]
    assert "— ang buut" not in labels
    assert labels[:2] == ["ma-", "mina-(→)"]
    ma = _entry("áyu").subs[0]
    assert ma.short_forms == ["maay", "máyu"]
    assert ma.form == "maáyu"
    assert ma.senses[0].gloss == "nice, good, well"


def test_the_paragraph_is_kept_verbatim():
    assert _entry("tan-aw").text == ENTRIES["tan-aw"]


# --- helpers ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "affix"),
    [
        ("ma-", True),
        ("paN-un", True),
        ("-in-an(→)", True),
        ("ig-r-/l-", True),
        ("ka-, kama-", True),
        ("hi-/ha-", True),
        ("tag-(←)", True),
        ("tan-aw", False),
        ("mag-una", False),
        ("ka-an", True),
    ],
)
def test_affix_labels_are_told_from_hyphenated_words(label, affix):
    assert is_affix_label(label) is affix


def test_shifting_stress_marks_the_dictionary_spelling():
    assert shifted("taas", "←") == "táas"
    assert shifted("duktur", "→") == "duktǔr"
    assert shifted("tulu", "→") == "tulu"  # an open penult already stresses the final syllable
    assert shifted("búnal", "→") == "bunal"
    assert shifted("inúm", "←") == "ínum"


def test_split_examples_handles_questions_and_multi_sentence_translations():
    assert split_examples("Naáyu na ku. Wà na ku hilanti, I am well now. I don’t have a fever any more.") == [
        Example("Naáyu na ku. Wà na ku hilanti", "I am well now. I don’t have a fever any more.")
    ]
    assert split_examples("Unsay ímung natan-awan ni Piyuks? What do you see in Piux?") == [
        Example("Unsay ímung natan-awan ni Piyuks?", "What do you see in Piux?")
    ]


# --- verb classes -----------------------------------------------------------------------------


def test_decode_an_action_verb_with_direct_and_accidental_passives():
    d = decode_classes("A; ab2")
    assert [a for a, _ in d] == ["mo-/mi-", "mag-/nag-", "maka-/naka-", "-on/gi-", "-an/gi-an", "i-/gi-", "hi-an"]
    assert dict(d)["mo-/mi-"] == "the one doing it"
    assert dict(d)["-on/gi-"] == "what it is done to"


def test_digits_remove_affixes():
    assert [a for a, _ in decode_classes("A13; a12")] == ["mag-/nag-", "-on/gi-"]
    assert [a for a, _ in decode_classes("A2")] == ["mo-/mi-", "maka-/naka-"]


def test_decode_stative_and_mutual_classes():
    assert [a for a, _ in decode_classes("B16")] == ["mag-/nag-", "ma-/na-", "maka-/naka-"]
    assert dict(decode_classes("B16"))["ma-/na-"] == "the one it happens to"
    assert [a for a, _ in decode_classes("C; c6")] == ["mag-/nag-", "magka-/nagka-", "makig-/nakig-", "i-/gi-"]


def test_n_adds_man_to_a_stative_verb_as_to_an_action_verb():
    """§7.121: 'A symbol BN indicates that paN- may be added to the base with the
    volitional affix (mu-)', as AN does for class A (§7.11). Wolff's example is pula."""
    assert ("maN-/naN-", "the one it happens to") in decode_classes("BN")
    assert ("maN-/naN-", "the one it happens to") in decode_classes("BN; a4")
    assert "maN-/naN-" not in dict(decode_classes("B16"))


def test_decode_local_and_instrumental_subclasses():
    assert decode_classes("b6") == [("-an/gi-an", "where or for whom it is done")]
    assert decode_classes("b8") == [("ma-an/na-an", "who it is done to")]
    # a3: only the potential passives
    assert decode_classes("a3") == [("ma-/na-", "what it is done to"), ("ma-an/na-an", "where or for whom it is done")]
    assert ("i-/gi-", "what is moved or conveyed") in decode_classes("c")
    assert decode_classes("b(1)") == [("-an/gi-an", "who or what it is done to")]


def test_unreadable_codes_decode_to_nothing():
    assert decode_classes("") == []
    assert decode_classes("dat.") == []


# --- affixed forms the dictionary leaves to rule (§5.0: only unpredictable ones are written out) -----


@pytest.mark.parametrize(
    ("label", "root", "word"),
    [
        ("ma-", "áyu", "maáyu"),
        ("ika-", "tulu", "ikatulu"),
        ("ka-, maka-", "tulu", "katulu"),
        ("-in-", "tulu", "tinulu"),
        ("hi-/ha-", "tan-aw", "hitan-aw"),
        # paN- and hiN-: the nasal takes the place of p b / t d s / k, before a vowel it is ng
        ("paN-", "tan-aw", "panan-aw"),
        ("hiN-", "tan-aw", "hinan-aw"),
        ("paN-", "kúhà", "pangúhà"),
        ("paN-", "bása", "pamása"),
        ("paN-", "ábut", "pangábut"),
        ("paN-", "lakaw", "panglakaw"),
        ("ka-(←)", "yabag", "kayábag"),
        # a suffix moves the stress in ways the entry writes out when it matters: not composed
        ("-an", "tulu", None),
        ("paN-un", "tan-aw", None),
        ("-in-an(→)", "tan-aw", None),
        ("r-", "tulu", None),
    ],
)
def test_compose_prefixed_and_infixed_forms(label, root, word):
    assert compose(label, root) == word


def test_a_composed_word_wins_over_an_inflected_example():
    subs = {s.label: s for s in _entry("tan-aw").subs}
    assert subs["hi-/ha-"].form == "hitan-aw"  # its example has nahitan-aw
    assert subs["-in-ay"].form == "nagkatinan-áway"  # a suffix: read off the example


# --- rarer shapes -----------------------------------------------------------------------------


def test_rarer_heads_source_and_root_shift():
    lawa = parse_entry("láwà_2 (from walà) not have (slang). Láwà nu siyay takwal, He says he’s out of dough.")
    assert (lawa.source, lawa.number, lawa.root.senses[0].gloss) == ("from walà", "2", "not have (slang)")
    dalaga = parse_entry("†dalága (→) v [A13] for a widow to primp herself up.")
    assert dalaga.root.shift == "→"
    assert dalaga.root.senses[0].classes == "A13"
    assert parse_entry("*talinghug pa- v [A; b] listen, heed.").not_alone is True


def test_degenerate_pointers_and_short_forms():
    assert parse_entry("ri short for").root.xref is None
    assert parse_entry("bay short form: {1} friend.").root.short_forms == []
    assert parse_entry("bay short form: {1} friend.").root.senses[0].gloss == "friend"
    assert parse_entry("bay short forms: ba,").root.short_forms == ["ba"]  # the paragraph ends in the list
    assert parse_entry("zz = qq").root.xref is None  # a pointer needs its full stop


def test_a_phrase_ends_at_a_sentence_end():
    e = parse_entry("bátà n child. — nga. Next thing.")
    assert [s.phrase for s in e.root.senses if s.phrase] == ["nga."]


def test_suffixed_sub_entries_read_their_word_off_an_example():
    e = parse_entry("ábut v come. -an n meeting place. Abútan ta sa iskuylahan, We’ll meet at school.")
    assert e.subs[0].form == "abútan"
    e = parse_entry("ábut v come. -un n thing. Káun ta, Let’s eat.")
    assert e.subs[0].form is None


def test_compose_refuses_words_and_cluster_initial_roots():
    assert compose("tan-aw", "tulu") is None
    assert compose("-in-", "tsinílas") is None  # the infix would split ts
    assert is_affix_label(", ") is False
    assert shifted("bir", "←") == "bir"


@pytest.mark.parametrize(
    ("code", "affixes"),
    [
        ("AN", ["mo-/mi-", "mag-/nag-", "maka-/naka-", "maN-/naN-"]),
        ("B", ["mo-/mi-", "mag-/nag-", "ma-/na-", "maka-/naka-", "magka-/nagka-"]),
        ("a13", ["ma-/na-"]),
        ("b12", ["-an/gi-an", "hi-an", "i-/gi-"]),
        ("c3", ["ika-/gika-"]),
        ("C2", ["mag-/nag-", "makig-/nakig-"]),
        ("c1", ["i-/gi-", "-on/gi-", "-an/gi-an"]),
        ("c5", ["i-/gi-", "-an/gi-an"]),
    ],
)
def test_more_class_codes(code, affixes):
    assert [a for a, _ in decode_classes(code)] == affixes


def test_the_reason_subjects():
    assert dict(decode_classes("c5"))["i-/gi-"] == "the reason for it"
    assert dict(decode_classes("b3"))["-an/gi-an"] == "the reason for it"


def test_a_sub_entry_head_may_carry_wolffs_accents():
    # The owner, 2026-10-18: ato's card showed kita's nominative 'we, us'. Wolff
    # prints the possessives as run-in heads inside the pronoun's paragraph
    # ('átù {1} gen.' inside kita), and a head with an accent was not a head.
    e = parse_entry("kita {1} we, us. Kita ra, Just us. átù {1} gen. Ang átung balay, Our house. {2} ours.")
    subs = {s.label: s for s in e.subs}
    assert list(subs) == ["átù"]
    assert subs["átù"].form == "átù"
    assert [s.gloss for s in subs["átù"].senses] == ["gen.", "ours"]
    assert [s.gloss for s in e.root.senses] == ["we, us"]


def test_a_capitalised_accented_word_is_not_a_head():
    # An example sentence starts with a capital; only a lower-case word heads a sub-entry.
    e = parse_entry("aku I, me. Ákù {1} nà, That is mine.")
    assert e.subs == []


def test_preposed_starts_a_sub_entry():
    # 'íla, iláha preposed gen.: their' inside sila carries no {n} and no part of speech.
    e = parse_entry("sila they. Muadtu sila. íla, iláha preposed gen.: their, by them. Ang ílang amahan, Their father.")
    assert [(f.label, f.form) for f in e.subs] == [("íla, iláha", "íla")]
    assert e.subs[0].senses[0].gloss == "preposed gen.: their, by them"
