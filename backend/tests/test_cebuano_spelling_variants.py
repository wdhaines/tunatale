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
