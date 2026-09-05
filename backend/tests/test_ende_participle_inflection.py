"""``-ende`` is an inflection, not a compound constituent.

The present participle was being peeled as its own drill unit:
``tilsvarende`` segmented ``til+svar+end+e``, and ``_compound_buildup_units``
folds only ONE trailing inflection, so ``e`` merged onto ``end`` and the learner
heard **til | svar | ende**. ``ende`` is a real Norwegian word (an end, a duck),
which is why it passes the content-stem gate — the same accident that put
``ens`` through it before ``tunatale-95zt``.

This is that fix's exact shape, and the measurement is its exact shape too:
adding the ending to ``_INFLECTIONS`` peels it whole (longest-first) and the
buildup merge folds it onto the stem.

MEASURED over the first 20000 lines of ``no_wordlist.txt`` before the change:

    segment_compound changed : 47
    buildup units changed    : 47
    of those, NOT ending in -ende (collateral) : 0

All 47 are present participles; there is no word in that corpus where ``ende``
is a genuine final element. Zero collateral is the same result ``95zt`` got for
``ens`` (60 changed, 60 of them genitives).

⚠️ The COMPOUND boundary must survive — this merges the participle onto its
stem, it does not flatten the word. ``grunn+legg+ende`` becomes
``grunn | leggende``, never ``grunnleggende``.

NOT FIXED HERE, and measured rather than assumed: compounds cut in the WRONG
PLACE (``deltakere`` is ``delt+aker``, should be ``del+taker``). See
``test_the_boundary_shift_rule_was_measured_and_declined``.
"""

from __future__ import annotations

import pytest

from app.plugins.languages.no.norwegian_breakdown import (
    _compound_buildup_units,
    segment_compound,
)

# (word, parts, buildup units) — the participle rides its stem, the compound
# boundary in front of it survives.
CASES = [
    ("tilsvarende", ["til", "svar", "ende"], ["til", "svarende"]),
    ("grunnleggende", ["grunn", "legg", "ende"], ["grunn", "leggende"]),
    ("overraskende", ["over", "rask", "ende"], ["over", "raskende"]),
    ("tilhørende", ["til", "hør", "ende"], ["til", "hørende"]),
    ("underholdende", ["under", "hold", "ende"], ["under", "holdende"]),
    ("oppsiktsvekkende", ["opp", "sikts", "vekk", "ende"], ["opp", "sikts", "vekkende"]),
    # A single-stem participle collapses to ONE part, not stem + inflection:
    # peeling "ende" leaves "sammenheng", which _is_lexicalized_whole then keeps
    # whole. Nothing to build up to, and the buildup merge never runs.
    ("sammenhengende", ["sammenhengende"], ["sammenhengende"]),
]


class TestParticipleRidesItsStem:
    @pytest.mark.parametrize(("word", "parts", "units"), CASES)
    def test_segments_ende_as_one_inflection(self, word, parts, units):
        assert segment_compound(word) == parts

    @pytest.mark.parametrize(("word", "parts", "units"), CASES)
    def test_buildup_folds_it_onto_the_stem(self, word, parts, units):
        assert [surface for surface, _pieces in _compound_buildup_units(segment_compound(word))] == units

    def test_the_compound_boundary_in_front_survives(self):
        """THE CONTROL against over-reaching. A fix that flattened the whole
        word would satisfy "no bare ende unit" and destroy the drill."""
        units = [s for s, _ in _compound_buildup_units(segment_compound("grunnleggende"))]
        assert units == ["grunn", "leggende"]
        assert len(units) == 2, "the grunn+legge compound boundary is real and must not merge away"


class TestZeroCollateral:
    """THE CONTROL that pins the measurement. Every word the change touches ends
    in -ende; a rule that reached further would break one of these."""

    @pytest.mark.parametrize("word", ["stortinget", "samarbeidet", "nettverk", "tyskland", "spørsmålet", "flertallet"])
    def test_a_word_not_ending_in_ende_is_untouched(self, word):
        assert "ende" not in segment_compound(word)

    def test_the_bare_word_ende_is_not_a_compound(self):
        """``ende`` on its own is a word, not an inflection of anything."""
        assert segment_compound("ende") == ["ende"]


class TestOutOfScope:
    def test_the_boundary_shift_rule_was_measured_and_declined(self):
        """``deltakere`` stays wrong ON PURPOSE, and this pins why.

        The obvious fix for a misplaced boundary is to shift it onto the nearest
        lexicon syllable boundary when both halves remain content stems. Probed
        2026-09-05 over all 49 residual stem disputes: it fires on ONE word
        (``landslaget``) and is BLOCKED on ``deltakere`` itself, because
        ``takere`` is not a content stem. A rule that misses the case that
        motivated it, on a population of two in four thousand, is not worth the
        risk to the 43 correct segmentations beside it.

        If this test fails, someone built that rule — re-run the probe before
        believing it is an improvement.
        """
        assert segment_compound("deltakere") == ["delt", "aker", "e"]
