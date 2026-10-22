"""Wolff's dictionary on a Cebuano card (tunatale-h0q3.2).

Layout A with layers, as the owner chose it from the mockup (2026-10-17):
the Handbook spelling under the plain word; Meaning and the verb's affixes
always shown; Details holds the numbered senses with an example each, who the
subject is per affix, related words and homographs; the entry verbatim sits
innermost. A card picks its entry by its English; when the English cannot
settle it, the card gets nothing and the word is listed for the owner.

The entries are real paragraphs (``tests/data/wolff_entries.json``, PG #40074).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.plugins.languages.ceb.handbook_spelling import build_lexicon
from app.plugins.languages.ceb.wolff_cards import (
    LIGHT_WORDS,
    CardFields,
    Decoded,
    EnglishRelations,
    Miss,
    WolffLexicon,
    add_attested,
    english_tokens,
    entry_record,
    letters_key,
    load_decoded,
    load_lexicon,
    onto_card,
)
from app.plugins.languages.ceb.wolff_dictionary import parse_entry, strip_marks

PARAGRAPHS = json.loads((Path(__file__).parent / "data" / "wolff_entries.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def lexicon():
    entries = [parse_entry(p) for p in PARAGRAPHS.values()]
    spelling = build_lexicon(
        freq={"libro": 210, "doktor": 300},
        entries={strip_marks(h).lower(): "" for e in entries for h in e.heads},
        es_words=["libro", "doctor"],
        en_sounds=[],
        english_common=set(),
    )
    records = [entry_record(e, spelling) for e in entries]
    add_attested(entries, records, spelling)
    return WolffLexicon(records)


def test_a_record_carries_the_handbook_spelling_and_the_paragraph(lexicon):
    rec = next(r for r in lexicon.entries if r["id"] == "tan-aw")
    root = rec["forms"][0]
    assert (root["wolff"], root["hb"]) == ("tan-aw", "tán-aw")
    assert root["senses"][0]["gloss"] == "see, look at"
    assert root["senses"][0]["ex"] == ["Motán-aw kog siní karóng gabîi", "I’ll go see a movie tonight."]
    assert rec["text"] == PARAGRAPHS["tan-aw"]
    pan = next(f for f in rec["forms"] if f["label"] == "paN-")
    assert pan["hb"] == "panán-aw"


def test_the_verb_card(lexicon):
    f = lexicon.card_fields("tan-aw", "watch; look")
    assert isinstance(f, CardFields)
    assert f.spelling == "tán-aw"
    assert f.meaning.startswith("see, look at; size up, form an opinion on s.t.; see in s.o.; look after")
    assert f.verb == "mo-/mi-, mag-/nag-, maka-/naka-, -on/gi-, -an/gi-an, i-/gi-, hi-an, ma-an/na-an"
    assert "<li>mo-/mi-, mag-/nag-, maka-/naka-: the one doing it</li>" in f.details
    assert "<b>panán-aw</b> sense of sight" in f.details
    assert "<i>Motán-aw kog siní karóng gabîi</i> I’ll go see a movie tonight." in f.details
    assert "Wolff, <i>A Dictionary of Cebuano Visayan</i> (1972)" in f.dictionary_entry
    assert "Mutan-aw kug sini karung gabíi, I’ll go see a movie tonight." in f.dictionary_entry


def test_a_stress_homograph_is_picked_by_its_english(lexicon):
    three = lexicon.card_fields("tulo", "three")
    assert three.spelling == "tuló"
    assert "Not to be confused with" in three.details
    assert "<b>túlò</b> drop" in three.details
    drip = lexicon.card_fields("tulo", "to drip; leak")
    assert drip.spelling == "túlò"


def test_a_root_beats_its_own_derived_form_on_a_tie(lexicon):
    # bátà's (→) form batà shares the English 'child'; the root is the card's word
    assert lexicon.match("bata", "child").form == 0


def test_a_glottal_stop_homograph(lexicon):
    child = lexicon.card_fields("bata", "child")
    assert child.spelling == "bátà"
    assert child.meaning.startswith("child; son or daughter")
    assert child.verb == ""  # the entry's verb senses don't give the card a verb line: it is a noun card
    assert "<b>báta</b>" in child.details


def test_a_short_form_points_at_its_full_form(lexicon):
    f = lexicon.card_fields("naa", "there is; is at (a place)")
    assert f.spelling == "nâa"
    assert "Short form of <b>anâa</b>" in f.details
    assert f.meaning.startswith("be there (near you)")
    # both paragraphs, the pointer and the entry it points at
    assert "náa = anáa." in f.dictionary_entry
    assert "anáa short form: náa." in f.dictionary_entry


def test_a_sub_entry_word_shows_its_root(lexicon):
    f = lexicon.card_fields("maayo", "good")
    assert f.spelling == "màáyo"
    assert f.meaning.startswith("nice, good, well")
    assert "From the root <b>áyo</b>" in f.details
    # 'maáyu see áyu' points at a sub-entry, not at a fuller spelling: the card
    # must not call màáyo a short form of itself.
    assert "Short form" not in f.details


def test_the_card_front_may_carry_its_short_form(lexicon):
    f = lexicon.card_fields("dili, di", "no; not")
    assert f.spelling == "dílì"


def test_one_candidate_needs_no_english(lexicon):
    # the English only chooses between homographs; one word with these letters is the word
    f = lexicon.card_fields("kutsara", "eating utensil")
    assert f.spelling == "kutsára"
    assert lexicon.match("kutsara", "eating utensil").english_matched is False
    assert lexicon.match("tulo", "three").english_matched is True


def test_wolffs_cy_is_the_cards_ciy():
    # §3.24: one consonant before y or w is written Cy, Cw (syudad); cards write siyudad
    assert letters_key("siyudad") == letters_key("syudad")
    assert letters_key("Biyernes") == letters_key("Byirnis")
    assert letters_key("kuwarta") == letters_key("kwarta")
    assert letters_key("maayo") == "maayu"  # a vowel before y is untouched


def test_misses_are_named(lexicon):
    assert lexicon.card_fields("tulo", "banana") == Miss("english-unmatched", ("tuló", "túlò", "tulô"))
    assert lexicon.card_fields("kompyuter", "computer") == Miss("not-in-dictionary", ())
    assert lexicon.card_fields("maayong buntag", "good morning") == Miss("not-in-dictionary", ())


def test_html_in_the_dictionary_text_is_escaped(lexicon):
    rec = dict(next(r for r in lexicon.entries if r["id"] == "tan-aw"))
    rec["text"] = "tan-aw <script>x</script>"
    lex = WolffLexicon([rec])
    assert "&lt;script&gt;" in lex.card_fields("tan-aw", "see").dictionary_entry


def test_fields_by_anki_field_name(lexicon):
    f = lexicon.card_fields("tulo", "three")
    assert f.as_note_fields() == {
        "Spelling": "tuló",
        "Meaning": f.meaning,
        "Verb": f.verb,
        "Details": f.details,
        "Dictionary entry": f.dictionary_entry,
    }


def test_two_homographs_the_english_fits_equally_are_ambiguous(lexicon):
    # One sense naming both. Split by ';' the first sense would lead instead (see
    # test_the_first_sense_of_a_two_sense_gloss_leads).
    assert lexicon.card_fields("tulo", "three, drop") == Miss("ambiguous", ("tuló", "túlò"))
    assert lexicon.card_fields("tulo", "three; drop").spelling == "tuló"


def test_a_pointer_found_after_its_target_still_shows_both_paragraphs(lexicon):
    reversed_lex = WolffLexicon(list(reversed(lexicon.entries)))
    f = reversed_lex.card_fields("naa", "there is")
    assert "náa = anáa." in f.dictionary_entry and "anáa short form: náa." in f.dictionary_entry


def test_a_pointer_to_a_missing_entry_is_taken_as_it_stands(lexicon):
    rec = {
        "id": "zz",
        "heads": ["zz"],
        "text": "zz = qq.",
        "forms": [{"label": "zz", "wolff": "zz", "hb": "zz", "short": [], "xref": ["=", "qq"], "senses": []}],
    }
    f = WolffLexicon([rec]).card_fields("zz", "anything")
    assert f.spelling == "zz" and "Related words" not in f.details


def test_the_committed_lexicon():
    # the real build: Wolff's whole dictionary
    lex = load_lexicon()
    assert len(lex.entries) > 20000
    f = lex.card_fields("tan-aw", "watch; look")
    assert f.spelling == "tán-aw"
    assert lex.card_fields("maayo", "good").spelling == "màáyo"
    assert lex.card_fields("tulo", "three").spelling == "tuló"


# ── close meanings (the owner, 2026-10-18: "You need to allow synonyms or close meanings") ──
#
# The card's English and Wolff's gloss are matched in tiers: the same words
# first, then WordNet relations (a synonym, or a kind of it either way), then
# the card word's own definition. A whole-gloss match breaks a tie the root
# rule leaves. The relations are committed data (english_relations.json.gz);
# the control is the same lexicon with no relations, where these stay blank.


@pytest.fixture(scope="module")
def committed():
    return load_lexicon()


@pytest.fixture(scope="module")
def no_relations(committed):
    return WolffLexicon(committed.entries, relations=EnglishRelations.empty())


@pytest.mark.parametrize(
    ("word", "english", "spelling", "meaning"),
    [
        ("bagyo", "storm", "bágyo", "typhoon"),  # a typhoon is a kind of storm
        ("pusil", "gun", "pusíl", "firearm"),  # synonyms
        ("igsoon", "sibling", "igsôon", "sa inahan"),  # a brother is a kind of sibling; the card writes oo
        ("pako", "wing", "pakô", "wings"),  # plural
        ("ngipon", "tooth", "ngípon", "teeth"),  # irregular plural
        ("ligid", "tire", "ligíd", "wheel"),  # tire: "hoop that covers a wheel"
        ("uyab", "boyfriend, girlfriend", "uyáb", "girl or boy friend"),
        ("okay", "okay", "ókay", "O.K."),  # Wolff writes O.K. (and úkay; the card writes o)
    ],
)
def test_a_close_meaning_settles_the_entry(committed, no_relations, word, english, spelling, meaning):
    assert isinstance(no_relations.match(word, english), Miss)
    f = committed.card_fields(word, english)
    assert (f.spelling, f.meaning[: len(meaning)]) == (spelling, meaning)


def test_a_light_word_never_relates(committed):
    # WordNet lists "kind" with "like"; Wolff's búut is "want, would like to".
    assert committed.match("buotan", "kind; nice") == Miss("english-unmatched", ("bùútan",))


def test_a_light_word_never_relates_even_if_the_table_says_so(committed):
    # The builder keeps light words out of the table; the matcher does not
    # rely on that.
    rel = EnglishRelations(base={}, related={"like": frozenset({"kind"})}, defined_by={"nice": frozenset({"like"})})
    assert isinstance(WolffLexicon(committed.entries, relations=rel).match("buotan", "kind; nice"), Miss)


def test_the_same_words_outrank_a_relation(committed):
    # Relate 'three' to túlò's 'drip': the exact gloss still wins.
    rel = EnglishRelations(base={}, related={"drip": frozenset({"three"})}, defined_by={})
    assert WolffLexicon(committed.entries, relations=rel).card_fields("tulo", "three").spelling == "tuló"


@pytest.mark.parametrize(
    ("word", "english", "spelling"),
    [("kabayo", "horse", "kabáyò"), ("numero", "number", "numeró"), ("ihap", "count", "iháp")],
)
def test_a_whole_gloss_breaks_a_tie(committed, word, english, spelling):
    assert committed.card_fields(word, english).spelling == spelling


def test_a_tie_no_whole_gloss_settles_stays_ambiguous():
    # Two spellings whose glosses both hold 'child', neither as the whole gloss.
    def rec(head, hb, gloss):
        sense = {"pos": "n", "n": None, "cls": None, "gloss": gloss, "phrase": None, "ex": None}
        form = {"label": head, "wolff": head, "hb": hb, "short": [], "xref": None, "senses": [sense]}
        return {"id": head, "heads": [head], "text": f"{head} n {gloss}.", "forms": [form]}

    lex = WolffLexicon([rec("báta", "báta", "small child here"), rec("batá", "batá", "child over there")])
    assert lex.match("bata", "child") == Miss("ambiguous", ("báta", "batá"))


@pytest.mark.parametrize(("word", "english", "spelling"), [("tubig", "water", "túbig"), ("duol", "near", "dûol")])
def test_the_tie_break_comes_after_the_root_rule(committed, word, english, spelling):
    # Breaking ties by whole gloss before the root rule flipped these three to a
    # derived form's spelling, measured on the deck.
    assert committed.card_fields(word, english).spelling == spelling


def test_a_modern_sense_wolff_lacks_stays_blank(committed):
    # limón is 'hard candies' in Wolff; the card's lemon is not there.
    assert isinstance(committed.match("limon", "lemon"), Miss)


def test_english_tokens():
    assert english_tokens("O.K., I give agreement") == ["ok", "i", "give", "agreement"]
    assert english_tokens("the Teeth of saws") == ["teeth", "saws"]


def test_the_committed_relations():
    rel = EnglishRelations.load()
    assert "storm" in rel.related["typhoon"]
    assert {"brother", "sister"} <= rel.defined_by["sibling"]
    # One step down only: "person" -> "relative" -> "sibling" is too far.
    assert "sibling" not in rel.related["person"]
    assert rel.base["children"] == "child"
    # WordNet keeps 'teeth' as a word of its own, so it relates instead.
    assert "tooth" in rel.related["teeth"]
    assert "wheel" in rel.defined_by["tire"]
    assert not LIGHT_WORDS & set(rel.related)


# ── the course's letters, Wolff's marks (tunatale-h0q3.21) ──────────────────
#
# The card word is the course spelling, and 75 of the 662 live spelling lines
# had other letters: Wolff writes Spanish loans with i/u (Martes -> Mártis).
# The line keeps the card's letters and carries Wolff's stress, glottal and
# hyphen (a glottal stop after a consonant) onto them.


@pytest.mark.parametrize(
    ("card", "surface", "expected"),
    [
        ("lamesa", "lamísa", "lamésa"),
        ("Martes", "Mártis", "Mártes"),
        ("oras", "úras", "óras"),
        ("kauban", "kàóban", "kàúban"),  # the glottal and the stress both land
        ("siyudad", "syudád", "siyudád"),  # the card's glide spelling
        ("uyog", "úy-og", "úy-og"),  # Wolff's hyphen is a glottal stop: kept
        ("ting-init", "tinginít", "ting-inít"),  # the card's own hyphen: kept
        ("maayo", "màáyo", "màáyo"),
        ("Kumusta", "kumústa", "Kumústa"),
    ],
)
def test_the_line_keeps_the_cards_letters_and_wolffs_marks(card, surface, expected):
    assert onto_card(card, surface) == expected


def test_a_word_too_different_keeps_wolffs_spelling():
    assert onto_card("xyzzy", "bátà") == "bátà"


def test_a_stress_that_cannot_be_placed_keeps_wolffs_spelling():
    # 'bta' has no vowel for bátà's stress to sit on
    assert onto_card("bta", "bátà") == "bátà"


@pytest.mark.parametrize(
    ("word", "english", "spelling"),
    [
        ("lamesa", "table", "lamésa"),
        ("Martes", "Tuesday", "Mártes"),
        ("tulo", "three", "tuló"),
        ("bata", "child", "bátà"),
    ],
)
def test_a_cards_spelling_uses_its_own_letters(committed, word, english, spelling):
    assert committed.card_fields(word, english).spelling == spelling


def test_the_short_form_front_uses_its_first_spelling(committed):
    assert committed.card_fields("dili, di", "no; not").spelling == "dílì"


# ── words Wolff prints but does not list (tunatale-h0q3.21) ────────────────
#
# 32 of the 79 cards with no entry are words Wolff prints in full, accented,
# in his examples (mainit -> maínit under ínit). The card takes that printed
# spelling; Meaning stays empty, because the example is under the ROOT's sense.


def test_a_word_printed_in_an_example_takes_that_spelling(lexicon):
    f = lexicon.card_fields("madawat", "be accepted")
    assert f.spelling == "madáwat"
    assert (f.meaning, f.verb) == ("", "")
    assert "<b>tán-aw</b>" in f.details  # the entry it is printed in
    assert "acceptable" in f.details  # and the example itself
    assert f.dictionary_entry.startswith('<div class="wolff-cite">') and "Mutan-aw kug sini" in f.dictionary_entry


def test_a_listed_word_is_never_also_attested(lexicon):
    listed = {letters_key(f["wolff"]) for r in lexicon.entries for f in r["forms"] if f["wolff"]}
    listed |= {letters_key(h) for r in lexicon.entries for h in r["heads"]}
    attested = {letters_key(strip_marks(a[0])) for r in lexicon.entries for a in r.get("attested", [])}
    assert attested and not attested & listed


def _attested_only(*spellings: tuple[str, int]) -> WolffLexicon:
    rec = {
        "id": "x",
        "heads": ["x"],
        "text": "x n thing.",
        "forms": [{"label": "x", "wolff": "x", "hb": "x", "short": [], "xref": None, "senses": []}],
        "attested": [[hb, n, "Ex.", "Example."] for hb, n in spellings],
    }
    return WolffLexicon([rec])


def test_the_printed_spelling_needs_nine_in_ten_attestations():
    assert _attested_only(("kinabúhì", 9), ("kinabúhi", 1)).card_fields("kinabuhi", "life").spelling == "kinabúhì"
    assert _attested_only(("kinabúhì", 5), ("kinabúhi", 5)).match("kinabuhi", "life") == Miss(
        "ambiguous", ("kinabúhì", "kinabúhi")
    )


@pytest.mark.parametrize(
    ("word", "english", "spelling"),
    [
        ("mainit", "warm", "màínit"),
        ("tindahan", "store", "tindáhan"),
        ("kinabuhi", "life", "kinabúhì"),
        ("serado", "closed", "serádo"),  # Wolff's sirádu, in the card's letters
    ],
)
def test_the_committed_lexicon_has_the_printed_words(committed, word, english, spelling):
    assert committed.card_fields(word, english).spelling == spelling


# ── 'ka' (the owner, 2026-10-18: "Ka being missing from the dictionary is sus") ──
#
# Two faults left the everyday 'you' blank. The prefix ka- matched the card
# word ka, because letters_key drops hyphens. And Wolff's "ka short for ikaw"
# was not followed to ikáw, because the sub-entry carries usage notes of its
# own, so 'you (singular)' was never compared with the card's English.


def test_ka_is_ikaw_s_short_form(committed):
    f = committed.card_fields("ka", "you (one person)")
    assert f.spelling == "ka"
    assert f.meaning.startswith("you (singular)")
    assert "Short form of <b>ikáw</b>" in f.details


def test_an_affix_matches_only_a_card_word_with_the_hyphen(committed):
    assert all(not h.surface.endswith("-") for h in committed._hits("ka"))
    assert any(h.surface == "ka-" for h in committed._hits("ka-"))


def test_a_short_form_keeps_its_notes_when_the_full_form_has_none(committed):
    # 'duy short for uduy': uduy gives no senses, so following it would throw
    # away duy's own note ('or unduy') and leave a hit with nothing to match on
    assert all(committed._senses(h) for h in committed._hits("duy"))


def test_a_homograph_with_no_gloss_is_not_listed(committed):
    # 'mu see ámù' lands on an entry with no senses: an empty line told nothing apart
    for english in ("you (plural); your (one person)", "your; by you (one person)"):
        assert not re.search(r"<b>[^<]*</b> </li>", committed.card_fields("mo", english).details)


# The possessives are run-in heads inside their pronoun's paragraph ('átù {1}
# gen.' inside kita). Heads with an accent were not split off, so 'átù see kita'
# landed on kita's whole root and ato's card read "we, us (nominative)" (the
# owner, 2026-10-18).


@pytest.mark.parametrize(
    ("word", "english", "spelling", "meaning"),
    [
        ("ato", "our, ours (including you); by us", "átò", "gen.: our, by us"),
        ("imo", "your, yours (one person); by you", "ímo", "preposed gen.: your, by you"),
        ("ila", "their, theirs; by them", "íla", "preposed gen.: their, by them"),
        ("ako", "my, mine; by me", "ákò", "preposed gen.: me, my"),
    ],
)
def test_a_preposed_possessive_reads_its_own_sub_entry(committed, word, english, spelling, meaning):
    f = committed.card_fields(word, english)
    assert (f.spelling, f.meaning.split("; ")[0]) == (spelling, meaning)
    assert "nominative" not in f.meaning and not f.meaning.startswith(("we, us", "you (singular)", "they", "I, me"))


def test_the_pronoun_itself_keeps_its_own_meaning(committed):
    assert committed.card_fields("kita", "we (including you)").meaning.startswith("we, us (including person addressed")
    assert committed.card_fields("ako", "I").meaning.startswith("I, me")


# Wolff glosses a possessive with its case label alone ('átù {1} gen.') and
# leaves the English to the examples. His genitive is "my, by me" (nákù), so a
# bare label on a personal pronoun's entry reads as that pronoun's possessive
# and agent, for matching and on the card.


@pytest.mark.parametrize(
    ("word", "english", "meaning"),
    [
        ("nato", "our (including you); by us", "gen. form: our, by us"),
        # 'mu short for nímu' lands on 'nímu see ikaw', a pointer; the senses are ikaw's nímu
        ("mo", "your; by you (one person)", "gen. form: your, by you"),
        ("ko", "my; by me", "gen.: my, by me (= nákù)"),
    ],
)
def test_a_bare_case_label_reads_as_the_pronouns_possessive(committed, word, english, meaning):
    assert meaning in committed.card_fields(word, english).meaning.split("; ")


def test_a_case_label_on_a_word_that_is_not_a_pronoun_is_left_alone():
    entries = [
        {
            "id": "balay",
            "heads": ["balay"],
            "forms": [
                {
                    "label": "balay",
                    "wolff": "balay",
                    "hb": "baláy",
                    "short": [],
                    "xref": None,
                    "senses": [
                        {"pos": "n", "n": None, "cls": None, "gloss": "house", "phrase": None, "ex": None},
                        {"pos": None, "n": None, "cls": None, "gloss": "gen.", "phrase": None, "ex": None},
                    ],
                },
            ],
            "text": "balay n house. gen.",
        }
    ]
    lex = WolffLexicon(entries)
    assert lex.card_fields("balay", "house").meaning == "house; gen."


def test_the_third_person_reads_both_genders():
    lex = WolffLexicon(
        [
            {
                "id": "siya",
                "heads": ["siya"],
                "forms": [
                    {
                        "label": "siya",
                        "wolff": "siya",
                        "hb": "siyá",
                        "short": [],
                        "xref": None,
                        "senses": [
                            {"pos": None, "n": None, "cls": None, "gloss": "he, she", "phrase": None, "ex": None}
                        ],
                    },
                    {
                        "label": "íya",
                        "wolff": "íya",
                        "hb": "íya",
                        "short": [],
                        "xref": None,
                        "senses": [
                            {"pos": None, "n": "1", "cls": None, "gloss": "preposed gen.", "phrase": None, "ex": None}
                        ],
                    },
                ],
                "text": "siya he, she. íya {1} preposed gen.",
            }
        ]
    )
    assert lex.card_fields("iya", "his").meaning == "preposed gen.: his, her, by him or her"


def _form(label, hb, xref=None, glosses=()):
    senses = [{"pos": None, "n": None, "cls": None, "gloss": g, "phrase": None, "ex": None} for g in glosses]
    return {"label": label, "wolff": label, "hb": hb, "short": [], "xref": xref, "senses": senses}


def test_a_short_form_keeps_its_notes_when_the_pointer_chain_ends_empty():
    # 'duy short for uduy', and uduy is a head that gives no senses and points
    # nowhere further: duy's own note is all there is to show.
    lex = WolffLexicon(
        [
            {
                "id": "duy",
                "heads": ["duy"],
                "forms": [_form("duy", "duy", ["short for", "uduy"], ["or unduy"])],
                "text": "",
            },
            {"id": "uduy", "heads": ["uduy"], "forms": [_form("uduy", "údoy")], "text": ""},
        ]
    )
    assert lex.card_fields("duy", "or unduy").meaning == "or unduy"


def test_a_pointer_chain_stops_after_two_hops():
    # a see b, b see c, c see d: following past _POINTER_HOPS would loop on a cycle.
    lex = WolffLexicon(
        [
            {"id": w, "heads": [w], "forms": [_form(w, w, ["see", nxt])], "text": ""}
            for w, nxt in (("aa", "bb"), ("bb", "cc"), ("cc", "aa"))
        ]
    )
    fields = lex.card_fields("aa", "anything")  # returns rather than recursing forever
    assert fields.meaning == ""


# A card gloss naming two senses ('I; my, mine') leads with the card's own: the
# subject card of a pronoun with a second-sense note names both because its
# recognition side shows the bare word (R2). Matched whole, the second sense can
# outscore the first, and on 2026-10-19 the refresh would have spelled ako 'I'
# as ákò with "preposed gen." The first sense is tried alone first.


@pytest.mark.parametrize(
    ("word", "english", "spelling", "meaning"),
    [
        ("ako", "I; my, mine", "akó", "I, me"),
        ("mo", "you (plural); your (one person)", "mo", "you (plural)"),
        ("kita", "see; we (including you)", "kítà", "see"),  # the verb card is the verb
    ],
)
def test_the_first_sense_of_a_two_sense_gloss_leads(committed, word, english, spelling, meaning):
    f = committed.card_fields(word, english)
    assert (f.spelling, f.meaning.split("; ")[0]) == (spelling, meaning)


def test_a_first_sense_that_settles_nothing_falls_back_to_the_whole_gloss(committed):
    # 'gen.: my, by me' answers the second sense; 'xyzzy' alone settles nothing
    assert committed.card_fields("ko", "xyzzy; my, by me").meaning == committed.card_fields("ko", "my, by me").meaning


# ── palihug (the owner, 2026-10-19: "palihug isn't getting a good definition") ──
#
# Wolff's entry is "palíhug see *líhug". The star marks a root that is never
# said bare, and the pointer kept it, so it looked for a head '*lihug' and the
# card got no Meaning. Under the root, his senses open on the affix they belong
# to ('pa-') and point at another affix's form ('= -in-'): neither is a meaning.


def test_a_pointer_to_a_starred_root_is_followed(committed):
    f = committed.card_fields("palihug", "please")
    assert f.meaning.startswith("please; work bee")
    assert "pa-" not in f.meaning.split("; ") and "= -in-" not in f.meaning


def test_a_see_pointer_to_a_root_names_the_root_not_a_short_form(committed):
    details = committed.card_fields("palihug", "please").details
    assert "Short form" not in details
    assert "From the root <b>lihóg</b> please" in details
    assert "<li>pa-</li>" not in details and "= -in-" not in details


def test_a_starred_pointer_to_a_sub_entry_reads_as_before(committed):
    """amahan see *ama: the sub-entry amahán was found directly all along."""
    f = committed.card_fields("amahan", "father")
    assert f.meaning.startswith("father")
    assert "Short form" not in f.details
    assert "From the root <b>amá</b>" in f.details


def test_a_see_pointer_to_a_full_word_is_still_a_short_form(committed):
    """'ta' points at kita with 'see' too, but kita is a word said bare: ta is
    short for it, and only a starred root is where a word comes from."""
    details = committed.card_fields("ta", "we (including you); our (including you)").details
    assert "Short form of <b>kitá</b>" in details
    assert "From the root <b>kitá</b>" not in details


# ── decoded forms (the owner, 2026-10-23: option A of the kabalo page) ──
#
# kabalo 'can' is ka- + baló 'know', a form Wolff never writes out. His only
# kabálo is bálu's 'feeling of shame', and the lookup took it on its letters
# alone (card 841). A hand row in wolff_decoded.tsv points the card at the
# sub-entry that carries the sense: hibaló 'know s.t., how to do s.t.'.


def test_a_decoded_form_reads_the_sub_entry_its_hand_row_names(committed):
    f = committed.card_fields("kabalo", "can")
    assert f.spelling == "kabaló"
    assert f.meaning == "know s.t., how to do s.t.; take care of a problem"
    assert f.verb == "ma-/na-, maka-/naka-, -an/gi-an, i-/gi-"
    assert f.details.startswith(
        "<div>Not written out in Wolff: <b>ka-</b> + <b>baló</b>. He gives the meaning under <b>hibaló</b>."
        " His example writes the full form kahibaló.</div>"
        "<div>From the root <b>baló</b> I don’t know</div>"
    )
    assert "<i>Kahibaló ka bang molangóy?</i> Do you know how to swim?" in f.details
    # the homograph it was mistaken for is still named, as the one not to confuse it with
    assert "<li><b>kabálo</b> feeling of shame and hurt for not being given s.t</li>" in f.details
    assert "<p>balu I don’t know." in f.dictionary_entry and "bálu_2" not in f.dictionary_entry


def test_without_its_hand_row_kabalo_reads_the_shame_entry(committed):
    # the control: the same lexicon with no hand rows is what card 841 showed
    bare = WolffLexicon(committed.entries, committed.relations, decoded=())
    f = bare.card_fields("kabalo", "can")
    assert (f.spelling, f.meaning) == ("kabálo", "feeling of shame and hurt for not being given s.t")


def test_every_hand_row_names_a_sub_entry_that_exists(committed):
    rows = load_decoded()
    assert rows and all(letters_key(r.word) == letters_key(r.spelling) for r in rows)
    for r in rows:
        assert committed.match(r.word, "").decoded == r  # a Miss has no .decoded


def test_a_hand_row_naming_a_missing_sub_entry_is_refused(committed):
    row = Decoded("kabalo", "kabaló", "balu", "no-such-label", "ka-", "")
    with pytest.raises(ValueError, match="no-such-label"):
        WolffLexicon(committed.entries, committed.relations, decoded=[row])
