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
from pathlib import Path

import pytest

from app.plugins.languages.ceb.handbook_spelling import build_lexicon
from app.plugins.languages.ceb.wolff_cards import (
    LIGHT_WORDS,
    CardFields,
    EnglishRelations,
    Miss,
    WolffLexicon,
    english_tokens,
    entry_record,
    letters_key,
    load_lexicon,
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
    return WolffLexicon([entry_record(e, spelling) for e in entries])


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
    assert lexicon.card_fields("tulo", "three; drop") == Miss("ambiguous", ("tuló", "túlò"))


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
        ("igsoon", "sibling", "igsûon", "sa inahan"),  # a brother is a kind of sibling
        ("pako", "wing", "pakô", "wings"),  # plural
        ("ngipon", "tooth", "ngípon", "teeth"),  # irregular plural
        ("ligid", "tire", "ligíd", "wheel"),  # tire: "hoop that covers a wheel"
        ("uyab", "boyfriend, girlfriend", "uyáb", "girl or boy friend"),
        ("okay", "okay", "úkay", "O.K."),  # Wolff writes O.K.
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


def test_a_tie_no_whole_gloss_settles_stays_ambiguous(committed):
    # The pronoun's entry never says 'you'; the linker's and the letter's don't either.
    assert committed.match("ka", "you (one person)").reason == "ambiguous"


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
