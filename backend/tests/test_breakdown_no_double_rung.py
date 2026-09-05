"""A buildup closes on the whole phrase ONCE.

Pimsleur's backward buildup is *whole thing → take it apart → whole thing
again*. Both builders wrote that closing rung explicitly, as a bookend — but
the inner sequence already ended with the whole phrase, so the learner heard it
twice, back to back, with a ~2.1 s gap and nothing in between.

Measured on the live `no` deck 2026-09-05 (cues 10 and 11 of day 1, two separate
renders of ``spor i snøen``): 5-8 doubled rungs in every one of nine stored
lessons.

Two shapes, one cause:

* **multi-word** — the ``word_index == 0`` iteration appends the phrase, and the
  loop's own trailing ``append`` adds it again;
* **single simplex word** — ``_build_syllable_inner_spans``'s ``i == 0``
  iteration emits ``syllables[0]`` and then the join of ``syllables[0:]``, which
  IS the whole word, and the bookend then repeats it.

**The discriminator that says this is a bug and not the design** is the compound
branch, which has no trailing bookend and closes on exactly one whole-word rung.
That is the path pinned by ``test_breakdown_compound_full_golden_sequence``,
whose docstring reads "human-confirmed line-for-line".

⚠️ The single-SYLLABLE case is deliberately excluded and stays ``[w, w]``: there
is no buildup to bracket, so saying it twice is the whole drill.

⚠️ Related, and the reason to land this first: ``tunatale-9yd0`` reports that
``segment_compound`` over-splits, naming ``dess+uten`` among its bogus
compounds. ``dessuten`` is the one key phrase in the 2026-09-02 review session
that showed NO double — precisely because that bogus split routed it down the
compound branch. Fixing the segmentation would move it (and every word like it)
onto the simplex branch, where it would ACQUIRE the double. This closes that
door first.
"""

from __future__ import annotations

import pytest

from app.generation.section_builder import build_word_breakdown
from app.plugins.languages.no.norwegian_breakdown import build_norwegian_breakdown

# Chosen to cover every branch of the Norwegian builder: simplex two- and
# three-syllable, a true compound, a compound of monosyllables, a multi-word
# phrase whose first word is monosyllabic, one whose first word is a compound,
# and a phrase carrying sentence punctuation.
NORWEGIAN_CASES = [
    "derimot",
    "dessuten",
    "finne",
    "etter",
    "etterforskningsteamet",
    "snømann",
    "ta hensyn",
    "uansett hva",
    "gjennomføre en handling",
    "spor i snøen",
    "hva skjedde?",
]

SLOVENE_CASES = [
    "dobrodošli",
    "prosim",
    "dober dan",
    "kako se imenujete",
]


def _assert_single_closing_rung(sequence: list[str], phrase: str) -> None:
    """The buildup ends with the whole phrase exactly once.

    Asserted as "the last rung is the phrase AND the one before it is not"
    rather than "no adjacent duplicates anywhere": a legitimate buildup may
    repeat a chunk with other chunks in between (``føre`` bookends its own
    sub-buildup), and a rule against that would be a different, wrong claim.
    """
    assert sequence[-1] == phrase, f"{phrase!r}: buildup must close on the whole phrase, got {sequence[-1]!r}"
    assert sequence[-2] != phrase, f"{phrase!r}: whole phrase spoken twice back to back — {sequence[-3:]}"


class TestNorwegian:
    @pytest.mark.parametrize("phrase", NORWEGIAN_CASES)
    def test_closes_on_the_whole_phrase_once(self, phrase):
        sequence = build_norwegian_breakdown(phrase)
        assert len(sequence) > 2, f"{phrase!r} produced no buildup at all: {sequence}"
        _assert_single_closing_rung(sequence, phrase)

    def test_a_monosyllable_is_still_said_twice(self):
        """THE CARVE-OUT. Nothing to take apart, so "say it, say it again" IS
        the drill. A fix that flattens this to one rung has over-reached."""
        assert build_norwegian_breakdown("ja") == ["ja", "ja"]

    def test_the_compound_branch_is_unchanged(self):
        """THE CONTROL. The compound path was already correct — it never had a
        trailing bookend — so this fix must not touch it. Its human-confirmed
        golden lives in test_norwegian_breakdown.py; this pins the shape."""
        sequence = build_norwegian_breakdown("etterforskningsteamet")
        assert sequence[0] == "etterforskningsteamet"
        assert sequence.count("etterforskningsteamet") == 2, (
            "opening and closing rung, and nothing in between should repeat the whole compound"
        )


class TestLanguageAgnosticFallback:
    """`section_builder._generic_breakdown_spans` carries the identical double,
    so every language without a registered breakdown function — Slovene, the
    one with a real deck — had it too. Fixed in the same pass; a fix to only
    the Norwegian plugin would have left the older path wrong."""

    @pytest.mark.parametrize("phrase", SLOVENE_CASES)
    def test_closes_on_the_whole_phrase_once(self, phrase):
        sequence = build_word_breakdown(phrase, "sl")
        assert len(sequence) > 2, f"{phrase!r} produced no buildup at all: {sequence}"
        _assert_single_closing_rung(sequence, phrase)

    def test_a_monosyllable_is_still_said_twice(self):
        assert build_word_breakdown("pa", "sl") == ["pa", "pa"]
