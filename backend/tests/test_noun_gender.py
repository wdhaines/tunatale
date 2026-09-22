"""Norwegian noun gender without a tagger (tunatale-vvb6).

Prod lemmatizes with the lookup-table lemmatizer, which returns no gender, so
every card minted there had a blank article (`kake`, not `en kake`). This lookup
fills the gap from the committed NST-derived table, then suffix rules, then a
compound's head noun — and returns "" rather than guess.

Oracles are the user's own deck conventions, measured 2026-09-22: -ing ei/en
109/112, -het ei/en 30/30, -else en 30/31, -sjon en 41/41, -ment et 7/7,
-dom en 6/6; feminine renders as ei/en (314 deck cards).
"""

import pytest

from app.plugins.languages.no.noun_gender import noun_gender


@pytest.mark.parametrize(
    ("lemma", "gender"),
    [("kake", "Fem"), ("jente", "Fem"), ("penn", "Masc"), ("skuff", "Masc"), ("hus", "Neut"), ("mann", "Masc")],
)
def test_the_nst_table_answers_known_nouns(lemma, gender):
    assert noun_gender(lemma) == gender


def test_case_is_ignored():
    assert noun_gender("Kake") == "Fem"


@pytest.mark.parametrize(
    ("lemma", "gender"),
    [
        # These -ing nouns are in NST with NO gender field, so only the rule can answer.
        ("åpning", "Fem"),
        ("anbefaling", "Fem"),
        ("fortelling", "Fem"),
        ("zzzkjærlighet", "Fem"),
        ("zzzforelse", "Masc"),
        ("zzzstasjon", "Masc"),
        ("zzzmoment", "Neut"),
        ("zzzfrihetsdom", "Masc"),
    ],
)
def test_suffix_rules_cover_what_nst_leaves_ungendered(lemma, gender):
    assert noun_gender(lemma) == gender


def test_a_compound_takes_its_head_nouns_gender():
    """finanskrise / fotballbane are absent from NST; the head noun decides."""
    assert noun_gender("finanskrise") == noun_gender("krise") != ""
    assert noun_gender("fotballbane") == noun_gender("bane") != ""
    assert noun_gender("zzzkaffehus") == "Neut"


def test_the_head_must_be_a_real_noun_of_at_least_three_letters():
    """A short tail is not a head: 'zzzqøy' must not match on 'øy' or 'y'."""
    assert noun_gender("zzzqøy") == ""


@pytest.mark.parametrize("word", ["oslo", "lofoten"])
def test_short_words_do_not_borrow_a_gender_from_a_noun_hiding_at_their_end(word):
    """Measured before the bound: oslo -> Fem, lofoten -> Masc, from tiny tails."""
    assert noun_gender(word) == ""


def test_unknown_or_ambiguous_is_blank_not_a_guess():
    assert noun_gender("zzzqqq") == ""
    assert noun_gender("") == ""
    # bråk is listed as both NEU and MAS-FEM: a wrong et/en is worse than none.
    assert noun_gender("bråk") == ""


def test_an_ambiguous_noun_stays_blank_even_when_a_suffix_rule_would_fire():
    """NST lists 'ting' as both MAS and NEU. It must NOT fall through to the -ing
    rule and come out Fem: ambiguity is an answer, not an absence."""
    assert noun_gender("ting") == ""


def test_the_table_wins_over_a_suffix_rule():
    """'ring' is Masc in NST; the -ing rule (Fem) must not override it."""
    assert noun_gender("ring") == "Masc"
