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
    """Both learners' decks hold wala and kini as vocab cards. Cebuano's cloze
    route no longer reads UPOS at all (function_words.json's ``pos`` is empty
    since 2026-10-14), but a closed-class default would still misreport them to
    everything else that reads the tag. ``ka`` keeps PRON too: it IS a pronoun,
    and it reaches the cloze route through the ``include`` list (the user,
    2026-09-28)."""
    default = next(r for r in table.readings(surface) if r.is_default)
    assert default.upos not in {"ADP", "DET", "CCONJ", "SCONJ", "PART"}


def test_wiktionarys_own_readings_survive_as_non_defaults(table):
    readings = {(r.upos, r.lemma, r.is_default) for r in table.readings("apan")}
    assert ("NOUN", "apan", False) in readings
    assert ("CCONJ", "apan", True) in readings


@pytest.mark.parametrize(
    ("surface", "root"),
    [("nagdala", "dala"), ("naghikog", "hikog"), ("nagbuhi", "buhi"), ("gibuhi", "buhi"), ("gipangandoy", "pangandoy")],
)
def test_an_affixed_verb_wiktionary_also_heads_as_a_noun_defaults_to_its_root(table, surface, root):
    """Wiktionary heads ``nagdala`` as a noun ("the protagonist"), and a noun
    headword keeps its own reading by default, so "Nagdala siya og bag" taught
    nothing about ``dala`` (the user, 2026-10-03). The verb is the default now;
    the noun survives for the context resolver to pick."""
    readings = {(r.upos, r.lemma, r.is_default) for r in table.readings(surface)}
    assert ("VERB", root, True) in readings
    assert ("NOUN", surface, False) in readings


@pytest.mark.parametrize(
    ("surface", "host"),
    [("unsay", "unsa"), ("kinsay", "kinsa"), ("koy", "ko"), ("naay", "naa"), ("walay", "wala"), ("siyay", "siya")],
)
def test_a_word_carrying_the_y_particle_reads_as_its_host(table, surface, host):
    """``-y`` is its own particle, not part of the word (Wolff's Dictionary,
    headword y: the subject marker after a predicate, "Unsay ímung gibúhat?", and
    "there is (isn't) any" after náa/walà, "Walay táwu"; Wolff's course 3 B 5).
    So ``unsay`` is unsa + y and must reach the unsa card: on the live deck it had
    become a card of its own, "what is" (the owner, 2026-10-16: "Why do I still
    have unsay?"). Same UPOS as the host's default, so it behaves like the host."""
    host_default = next(r for r in table.readings(host) if r.is_default)
    defaults = [(r.upos, r.lemma) for r in table.readings(surface) if r.is_default]
    assert defaults == [(host_default.upos, host)]


@pytest.mark.parametrize(("surface", "linked"), [("nong", "no"), ("nang", "na")])
def test_an_address_term_is_its_own_word_not_a_linker_form(table, surface, linked):
    """Nong and Nang are the terms of address (Wolff: manung, manang, "Short form:
    nung", "short form: nang"). Wiktionary heads neither, so the linker rule read
    them as no + ng and na + ng, and the reader minted the lesson's "Nong" as a card
    for "no", glossed "elder", with kunu's dictionary entry ("it is said") on it
    (the owner, 2026-10-22: "The card for no seems wrong"). Every nong / nang in
    the owner's lessons is the address term. The linker reading stays, for the
    context resolver to pick."""
    readings = {(r.upos, r.lemma, r.is_default) for r in table.readings(surface)}
    assert ("NOUN", surface, True) in readings
    assert any(lemma == linked and not default for _upos, lemma, default in readings)


def test_the_lesson_line_the_no_card_came_from_reads_nong(table):
    from app.srs.lemmatizer import lemmatize_surfaces_in_context
    from app.srs.tokenizer import tokenize

    line = "Nong, sa baybayon, palihug."
    surfaces = tokenize(line)
    assert (
        dict(zip(surfaces, lemmatize_surfaces_in_context(surfaces, line, table, "ceb"), strict=True))["Nong"] == "nong"
    )
