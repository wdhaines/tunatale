"""Cebuano demonstrative short forms draw their long form's picture (tunatale-tofg.19).

Design: .beads-tasks/briefs/design-short-forms-and-linkers-2026-11.md § 3 R6/R8, oracle
rows O3-O6. The owner's 'to' card (short for kádto) showed a stock photo, because
spatial.json had no 'to'. Of Wolff's 1A chart (Beginning Cebuano, the 14 demonstrative
short forms) exactly three drew nothing: to, ni, na. ni and na are also function
words (the name marker, 'already'), so they get a picture only as a second sense
keyed ``demonstrative``; their clozes keep the function-word route.
"""

from __future__ import annotations

import pytest

from app.cards.drawn_picture import drawn_picture
from app.plugins.languages.ceb.short_forms import short_form_line
from app.srs.function_words import is_function_word

#: Wolff 1A's demonstrative short forms: (short, its card key, long form, a gloss).
#: The walk is the whole chart, not a sample (R8).
_CHART_1A = [
    ("ri", "", "kiri", "this (near me)"),
    ("ni", "demonstrative", "kini", "this (near you and me)"),
    ("na", "demonstrative", "kana", "that (near you)"),
    ("to", "", "kadto", "that (far from both of us)"),
    ("iri", "", "niiri", "of this (near me)"),
    ("ari", "", "niiri", "of this (near me)"),
    ("ini", "", "niini", "of this (near you and me)"),
    ("ani", "", "niini", "of this (near you and me)"),
    ("ana", "", "niana", "of that (near you)"),
    ("adto", "", "niadto", "of that (far from both of us)"),
    ("dia", "", "adia", "is here (near me)"),
    ("nia", "", "ania", "is here (near you and me)"),
    ("naa", "", "anaa", "is there (near you)"),
    ("tua", "", "atua", "is there (far from both of us)"),
]


def _concept(word: str, gloss: str, key: str = "") -> str | None:
    picture = drawn_picture(word, "ceb", gloss, key)
    return picture.filename.rsplit("_", 1)[0] if picture else None


@pytest.mark.parametrize(("short", "key", "long", "gloss"), _CHART_1A)
def test_every_1a_short_form_draws_its_long_forms_concept(short, key, long, gloss):
    """O4: 14 of 14 (11 before)."""
    assert _concept(long, gloss) is not None
    assert _concept(short, gloss, key) == _concept(long, gloss)


def test_to_is_drawn_as_kadto():
    """O3 + its control O3c."""
    assert _concept("kadto", "that (far from both of us)") == "spatial_that_far"
    assert _concept("to", "that (far from both of us)") == "spatial_that_far"


@pytest.mark.parametrize("word", ["ni", "na"])
def test_ni_and_na_stay_function_words(word):
    """O5: the demonstrative is a second sense; the bare token keeps its cloze route."""
    assert is_function_word(word, "ceb") is True


@pytest.mark.parametrize(("word", "gloss"), [("ni", "this (near you and me)"), ("na", "that (near you)")])
def test_ni_and_na_draw_only_as_their_demonstrative_sense(word, gloss):
    assert drawn_picture(word, "ceb", gloss) is None
    assert drawn_picture(word, "ceb", gloss, "demonstrative") is not None


@pytest.mark.parametrize(
    ("word", "key", "gloss", "line"),
    [
        # O6: each long nominative demonstrative names its short form (R6: own cards, 3 B 4).
        ("kini", "", "this (near you and me)", "short form: ni"),
        ("kiri", "", "this (near me)", "short form: ri"),
        ("kana", "", "that (near you)", "short form: nà"),
        ("kadto", "", "that (far from both of us)", "short form: to"),
        # ...and each short one names it back.
        ("ri", "", "this (near me)", "short for kirí"),
        ("to", "", "that (far from both of us)", "short for kádto"),
        ("ni", "demonstrative", "this (near you and me)", "short for kiní"),
        ("na", "demonstrative", "that (near you)", "short for kanâ"),
    ],
)
def test_nominative_demonstratives_name_their_partner(word, key, gloss, line):
    assert short_form_line(word, gloss, key) == line


@pytest.mark.parametrize(("word", "gloss"), [("ni", "name marker"), ("na", "already")])
def test_function_word_senses_say_nothing_about_a_demonstrative(word, gloss):
    """The name marker ni is not short for kiní: only the demonstrative sense is."""
    assert short_form_line(word, gloss) is None


def test_a_language_without_a_spatial_file_has_no_second_senses_or_pairs():
    from app.cards.spatial_picture import load_spatial_second_senses, load_spatial_short_forms

    assert load_spatial_second_senses("en") == {}
    assert load_spatial_short_forms("en") == ()
