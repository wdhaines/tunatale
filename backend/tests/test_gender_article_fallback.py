"""get_gender_article falls back to the language's noun-gender lookup (tunatale-vvb6).

Prod's lookup-table lemmatizer returns gender="" for every token, so the article
was always blank there. The fallback fires ONLY on a blank gender: a tagger that
read the gender in context (Stanza, on the laptop) still decides.
"""

from app.languages import get_gender_article


def test_a_blank_gender_falls_back_to_the_lemma_lookup():
    assert get_gender_article("no", "", lemma="kake") == "ei/en"
    assert get_gender_article("no", "", lemma="hus") == "et"
    assert get_gender_article("no", "", lemma="penn") == "en"


def test_the_taggers_gender_wins_over_the_lookup():
    """kake is Fem in the table; a tagger that said Masc in context is kept."""
    assert get_gender_article("no", "Masc", lemma="kake") == "en"


def test_no_lemma_means_no_fallback():
    assert get_gender_article("no", "") == ""


def test_an_unknown_noun_stays_blank():
    assert get_gender_article("no", "", lemma="zzzqqq") == ""


def test_a_language_without_a_lookup_is_unchanged():
    """Slovene registers no gender articles at all: still blank, never a guess."""
    assert get_gender_article("sl", "", lemma="kake") == ""
