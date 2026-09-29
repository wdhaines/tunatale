"""Cebuano closed-class words in the lemma table (tunatale-u8nz.21).

The builder's rule (``apply_closed_class``) against tiny inputs, then the
committed table against the committed hand-curated list.
"""

from __future__ import annotations

import pytest

from app.languages import get_lemma_table_path
from app.srs.lemma_table import TableLemmatizer
from scripts.build_cebuano_lemma_table import CLOSED_CLASS, apply_closed_class, load_closed_class

# ── the rule ──────────────────────────────────────────────────────────────────


def test_a_listed_surface_gets_the_listed_reading_as_its_only_default():
    rows = {("apan", "NOUN", "apan", 1)}
    out = apply_closed_class(rows, [("apan", "CCONJ", "apan")])
    assert out == {("apan", "NOUN", "apan", 0), ("apan", "CCONJ", "apan", 1)}


def test_a_listed_reading_wiktionary_already_has_is_promoted_not_duplicated():
    rows = {("kini", "NOUN", "kini", 0), ("kini", "ADV", "kini", 1), ("kini", "PRON", "kini", 0)}
    out = apply_closed_class(rows, [("kini", "PRON", "kini")])
    assert out == {("kini", "NOUN", "kini", 0), ("kini", "ADV", "kini", 0), ("kini", "PRON", "kini", 1)}


def test_a_surface_wiktionary_lacks_is_added():
    assert apply_closed_class(set(), [("mga", "DET", "mga")]) == {("mga", "DET", "mga", 1)}


def test_unlisted_surfaces_are_untouched():
    rows = {("tawo", "NOUN", "tawo", 1), ("ang", "PROPN", "ang", 1)}
    out = apply_closed_class(rows, [("ang", "DET", "ang")])
    assert ("tawo", "NOUN", "tawo", 1) in out


def test_load_refuses_an_unknown_upos(tmp_path):
    """A typo must fail the build, not ship a reading nothing recognises."""
    path = tmp_path / "cc.tsv"
    path.write_text("surface\tupos\tlemma\tnote\nang\tDETT\tang\tx\n", encoding="utf-8")
    with pytest.raises(ValueError, match="DETT"):
        load_closed_class(path)


def test_load_refuses_a_duplicate_surface(tmp_path):
    path = tmp_path / "cc.tsv"
    path.write_text("surface\tupos\tlemma\tnote\nang\tDET\tang\t\nang\tPRON\tang\t\n", encoding="utf-8")
    with pytest.raises(ValueError, match="ang"):
        load_closed_class(path)


# ── the committed table ───────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def table():
    lemmatizer = TableLemmatizer("ceb", get_lemma_table_path("ceb"))
    yield lemmatizer
    lemmatizer.close()


@pytest.mark.parametrize(("surface", "upos", "lemma"), load_closed_class(CLOSED_CLASS))
def test_every_listed_word_is_its_surfaces_default(table, surface, upos, lemma):
    defaults = [(r.upos, r.lemma) for r in table.readings(surface) if r.is_default]
    assert defaults == [(upos, lemma)]


@pytest.mark.parametrize("surface", ["wala", "kini", "ka"])
def test_words_that_are_vocab_cards_keep_a_content_upos(table, surface):
    """Both learners' decks hold wala and kini as vocab cards; a function-word
    UPOS would route them to clozes and orphan the cards. ``ka`` keeps PRON too:
    it IS a pronoun, and it reaches the cloze route through function_words.json's
    ``include`` list (the user, 2026-09-28), not through a mislabelled UPOS."""
    default = next(r for r in table.readings(surface) if r.is_default)
    assert default.upos not in {"ADP", "DET", "CCONJ", "SCONJ", "PART"}


def test_wiktionarys_own_readings_survive_as_non_defaults(table):
    readings = {(r.upos, r.lemma, r.is_default) for r in table.readings("apan")}
    assert ("NOUN", "apan", False) in readings
    assert ("CCONJ", "apan", True) in readings
