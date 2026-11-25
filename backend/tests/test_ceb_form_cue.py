"""A 3 B 4 card's front shows which form it asks for (tunatale-avcl).

Design: .beads-tasks/briefs/design-short-forms-and-linkers-2026-11.md § 3 R6, owner D3
(2026-11-25): a short form's one blank is short (``__``), a long form's is split
by a syllable dot (``___·___``), and a shared card keeps today's plain blank.
Wolff 3 B 4: a pronoun as predicate must be long, and so must a demonstrative
unless màó is in the sentence; elsewhere short and long substitute freely. So
only the nominative pairs, which keep two cards each, carry the cue. The cue is
in the SVG, so TT and Anki show the same front, and it is language-free, so the
front stays free of English. Oracle row O7.
"""

from __future__ import annotations

import re

import pytest

from app.cards.drawn_picture import drawn_picture

_TEXT = re.compile(r"<text[^>]*>([^<]*)</text>")


def _band(word: str, gloss: str, key: str = "") -> str | None:
    picture = drawn_picture(word, "ceb", gloss, key)
    assert picture is not None, word
    found = _TEXT.findall(picture.svg.decode("utf-8"))
    return found[0] if found else None


#: The live 3 B 4 cards (laptop ceb DB, 2026-11-25): word, key, gloss, expected band.
@pytest.mark.parametrize(
    ("word", "key", "gloss", "band"),
    [
        ("ko", "", "I", "Moadto __ sa merkado."),
        ("ka", "", "you (one person)", "Moadto __ sa merkado."),
        ("mi", "", "we (not including you)", "Moadto __ sa merkado."),
        ("mo", "", "you (plural)", "Moadto __ sa merkado."),
        ("ta", "", "we (including you)", "Moadto __ sa merkado."),
        ("ako", "", "I", "___·___ ra."),
        ("ikaw", "", "you (one person)", "___·___ ra."),
        ("kami", "", "we (not including you)", "___·___ ra."),
        ("kamo", "", "you (plural)", "___·___ ra."),
        ("kita", "pronoun", "we (including you)", "___·___ ra."),
    ],
)
def test_a_nominative_pronoun_card_shows_its_length(word, key, gloss, band):
    assert _band(word, gloss, key) == band


@pytest.mark.parametrize(
    ("word", "key", "gloss"),
    [
        # Interchangeable genitives: one card each after slice 4, either form right.
        ("ko", "genitive", "my; by me"),
        ("mo", "genitive", "your; by you (one person)"),
        ("ta", "genitive", "our (including you); by us"),
        # Control: a card in no pair at all.
        ("ato", "", "our, ours (including you); by us"),
    ],
)
def test_a_card_that_asks_for_no_particular_length_keeps_the_plain_blank(word, key, gloss):
    band = _band(word, gloss, key)
    assert band is not None and "____" in band and "·" not in band


@pytest.mark.parametrize(
    ("word", "key", "gloss", "band"),
    [
        ("to", "", "that (far from both of us)", "Mao __ ang bola."),
        ("ri", "", "this (near me)", "Mao __ ang bola."),
        ("ni", "demonstrative", "this (near you and me)", "Mao __ ang bola."),
        ("na", "demonstrative", "that (near you)", "Mao __ ang bola."),
        ("kadto", "", "that (far from both of us)", "Mao ___·___ ang bola."),
        ("kiri", "", "this (near me)", "Mao ___·___ ang bola."),
        ("kini", "", "this (near you and me)", "Mao ___·___ ang bola."),
        ("kana", "", "that (near you)", "Mao ___·___ ang bola."),
    ],
)
def test_a_nominative_demonstrative_card_gets_a_band_with_its_length(word, key, gloss, band):
    assert _band(word, gloss, key) == band


@pytest.mark.parametrize(
    ("word", "gloss"),
    [("sulod", "inside"), ("kari", "this (near me)"), ("nia", "is here (near you and me)")],
)
def test_other_spatial_words_have_no_band(word, gloss):
    """kari shares kiri's picture but is in no pair; nia's pair is not a 3 B 4 one."""
    assert _band(word, gloss) is None


def test_the_band_makes_the_picture_taller():
    plain = drawn_picture("kari", "ceb", "this (near me)").svg.decode()
    banded = drawn_picture("kiri", "ceb", "this (near me)").svg.decode()
    assert 'height="220"' in plain
    assert 'height="256"' in banded
