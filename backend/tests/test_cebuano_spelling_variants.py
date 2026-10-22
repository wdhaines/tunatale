"""Cebuano spelling variants in the lemma table (the user, 2026-09-28).

``pwede``/``puwede`` and ``lamesa``/``mesa`` are one word each, spelled two
ways, and the learner's deck held a card for every spelling. The builder's rule
(``apply_spelling_variants``) sends every reading of a variant to its main
spelling, so a lesson using either spelling matches the one card. Tested the
way ``test_cebuano_closed_class.py`` tests its sibling rule: against tiny inputs,
then the committed table against the committed hand-kept list.
"""

from __future__ import annotations

import pytest

from app.languages import get_lemma_table_path
from app.srs.lemma_table import TableLemmatizer
from scripts.build_cebuano_lemma_table import (
    SPELLING_VARIANTS,
    apply_spelling_variants,
    load_spelling_variants,
)

# ── the rule ──────────────────────────────────────────────────────────────────


def test_every_reading_of_a_variant_lemma_moves_to_the_main_spelling():
    """Affixed and linker forms follow too: ``puwedeng`` must match the card."""
    rows = {
        ("puwede", "ADV", "puwede", 1),
        ("puwede", "ADJ", "puwede", 0),
        ("puwedeng", "ADV", "puwede", 1),
        ("pwede", "ADV", "pwede", 1),
    }
    out = apply_spelling_variants(rows, [("puwede", "pwede")])
    assert out == {
        ("puwede", "ADV", "pwede", 1),
        ("puwede", "ADJ", "pwede", 0),
        ("puwedeng", "ADV", "pwede", 1),
        ("pwede", "ADV", "pwede", 1),
    }


def test_a_variant_wiktionary_lacks_gets_the_main_spellings_default_reading():
    rows = {("lamesa", "NOUN", "lamesa", 1)}
    out = apply_spelling_variants(rows, [("mesa", "lamesa")])
    assert out == {("lamesa", "NOUN", "lamesa", 1), ("mesa", "NOUN", "lamesa", 1)}


def test_a_variants_reading_as_another_word_is_dropped():
    """``nong`` is short for manong, but the linker rule had also read it as no + ng.
    A listed variant IS its main word, so a reading of a third word goes (the
    owner, 2026-10-22: a "no" card minted from "Nong, sa baybayon")."""
    rows = {("nong", "INTJ", "no", 1), ("manong", "NOUN", "manong", 1), ("no", "INTJ", "no", 1)}
    out = apply_spelling_variants(rows, [("nong", "manong")])
    assert out == {("nong", "NOUN", "manong", 1), ("manong", "NOUN", "manong", 1), ("no", "INTJ", "no", 1)}


def test_readings_of_other_lemmas_are_untouched():
    rows = {("tawo", "NOUN", "tawo", 1), ("puwede", "ADV", "puwede", 1), ("pwede", "ADV", "pwede", 1)}
    assert ("tawo", "NOUN", "tawo", 1) in apply_spelling_variants(rows, [("puwede", "pwede")])


def test_a_main_spelling_the_table_lacks_fails_the_build():
    """A typo in the main spelling would otherwise map real words onto nothing."""
    with pytest.raises(ValueError, match="pwedee"):
        apply_spelling_variants({("puwede", "ADV", "puwede", 1)}, [("puwede", "pwedee")])


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("variant\tmain\tnote\npuwede\tpwede\t\npuwede\tpwede\t\n", "listed twice"),
        ("variant\tmain\tnote\npwede\tpwede\t\n", "itself"),
        ("variant\tmain\tnote\npuwede\tpwede\t\npwede\tpwde\t\n", "chain"),
    ],
)
def test_load_refuses_a_malformed_list(tmp_path, body, match):
    path = tmp_path / "variants.tsv"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        load_spelling_variants(path)


# ── the committed table ───────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def table():
    lemmatizer = TableLemmatizer("ceb", get_lemma_table_path("ceb"))
    yield lemmatizer
    lemmatizer.close()


@pytest.mark.parametrize(("variant", "main"), load_spelling_variants(SPELLING_VARIANTS))
def test_every_listed_variant_reads_as_its_main_spelling(table, variant, main):
    readings = table.readings(variant)
    assert readings, f"{variant} has no reading at all"
    assert {r.lemma for r in readings} == {main}
    assert sum(r.is_default for r in readings) == 1


@pytest.mark.parametrize(("surface", "lemma"), [("puwede", "pwede"), ("puwedeng", "pwede"), ("mesa", "lamesa")])
def test_the_pairs_the_user_asked_about(table, surface, lemma):
    default = next(r for r in table.readings(surface) if r.is_default)
    assert default.lemma == lemma


@pytest.mark.parametrize("surface", ["sayal", "saya"])
def test_sayal_and_saya_stay_two_words(table, surface):
    """``sayal`` (skirt) is not a spelling of ``saya`` (the user, 2026-10-01).

    It was listed as one for a few hours on Wiktionary's choice of headword;
    Binisaya heads the entry the other way, and ``saya`` is also "merry", so the
    merge both taught the rarer word and read a skirt into every ``saya``.
    """
    assert {r.lemma for r in table.readings(surface)} == {surface}
    assert surface not in dict(load_spelling_variants(SPELLING_VARIANTS))


@pytest.mark.parametrize("surface", ["sais", "sayis"])
def test_six_reads_as_sais(table, surface):
    """Wiktionary heads "six" at ``sayis``, so the cognate seed minted a ``sayis``
    card, and the number table and style.md say ``sais``: the card got no clock
    face (the user, 2026-10-03). Cebuano writers use ``sais`` 93 to 9
    (cebuano_frequency.tsv.gz), so ``sais`` is the main spelling."""
    default = next(r for r in table.readings(surface) if r.is_default)
    assert (default.upos, default.lemma) == ("NUM", "sais")


@pytest.mark.parametrize(
    ("surface", "lemma"),
    [
        ("di", "dili"),
        ("jud", "gyud"),
        ("gayud", "gyud"),
        ("nya", "unya"),
        ("sab", "usab"),
        ("sad", "usab"),
        ("pod", "pud"),
        # Terms of address (the owner, 2026-10-22: "We really need to handle short
        # forms. I have day and inday."): Wolff manung "Short form: nung", manang
        # "short form: nang", inday "short form: day".
        ("nong", "manong"),
        ("nang", "manang"),
        ("day", "inday"),
    ],
)
def test_short_forms_read_as_the_word_they_shorten(table, surface, lemma):
    """One word, two spellings (tunatale-7jl1 §1, the owner 2026-10-16), so a
    lesson's ``di`` reaches the ``dili`` card instead of reading as unknown.
    Wolff's Dictionary: dílì "short form: dì"; gayud "short forms: dyud, giyud"
    (``jud`` is today's spelling of dyud); unyà "short forms: unyà, nyà"; usab
    "short forms: sab and sad". ``pud`` heads its own pair because it is not short
    usab: Wolff ``pud = upud``, "as a predicate modifier: = usab"."""
    readings = table.readings(surface)
    assert {r.lemma for r in readings} == {lemma}
    assert sum(r.is_default for r in readings) == 1


@pytest.mark.parametrize("surface", ["pud", "gyud", "usab", "dili", "unya", "manong", "manang", "inday"])
def test_the_main_spellings_keep_themselves(table, surface):
    default = next(r for r in table.readings(surface) if r.is_default)
    assert default.lemma == surface


@pytest.mark.parametrize(("surface", "full"), [("nong", "manong"), ("nang", "manang")])
def test_an_address_term_reads_as_its_full_form_not_a_linker_form(table, surface, full):
    """Nong and Nang are the terms of address (Wolff: manung, manang, "Short form:
    nung", "short form: nang"). Wiktionary heads neither, so the linker rule read
    them as no + ng and na + ng, and the reader minted the lesson's "Nong" as a card
    for "no", glossed "elder", with kunu's dictionary entry ("it is said") on it
    (the owner, 2026-10-22: "The card for no seems wrong"). Every nong / nang in
    the owner's lessons is the address term. As a listed short form it reads as
    its full form, so the lesson reaches the one 'manong, nong' card."""
    default = next(r for r in table.readings(surface) if r.is_default)
    assert (default.upos, default.lemma) == ("NOUN", full)


def test_the_lesson_line_the_no_card_came_from_reads_manong(table):
    from app.srs.lemmatizer import lemmatize_surfaces_in_context
    from app.srs.tokenizer import tokenize

    line = "Nong, sa baybayon, palihug."
    surfaces = tokenize(line)
    assert (
        dict(zip(surfaces, lemmatize_surfaces_in_context(surfaces, line, table, "ceb"), strict=True))["Nong"]
        == "manong"
    )
