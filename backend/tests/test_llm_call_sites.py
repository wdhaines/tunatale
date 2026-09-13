"""Tests for the declared LLM call-site label set (bead 6zzu2 Stage 2).

The labels are literals the brief pins: the 9 flat labels, the 3 cloze
operation suffixes, and the 2 callers. A test asserting the FULL set means
an added label cannot slip in unnoticed — the checker's completeness is only
as good as this enumeration's honesty.
"""

import re

from app.llm.call_sites import CallSite

#: A label is a field of a whitespace-split ledger line — spaces, hyphens and
#: uppercase would corrupt the column count (Oracle 5).
_FIELD_SAFE = re.compile(r"^[a-z0-9_.]+$")

#: Exactly the 9 single-route sites from the label table (Oracle 4/table).
_EXPECTED_FLAT = {
    "story",
    "planner",
    "glossing",
    "translate_term",
    "word_gloss",
    "media_query",
    "media_choose",
    "regloss",
    "srs_translate",
}

#: Exactly the 3 threaded-helper operation suffixes from the label table.
_EXPECTED_SUFFIXES = {"cloze_generate", "cloze_judge", "cloze_translate"}

#: Exactly the two callers that thread through those helpers.
_EXPECTED_CALLERS = {"prestage", "api"}


class TestCallSitePattern:
    def test_every_label_matches_the_field_safe_pattern(self):
        """Whitespace would split the ledger line; the pattern forbids it."""
        for label in CallSite.FLAT_LABELS | CallSite.OPERATION_SUFFIXES | CallSite.CALLERS:
            assert _FIELD_SAFE.match(label), f"{label!r} is not field-safe"

    def test_composed_labels_stay_field_safe(self):
        for caller in CallSite.CALLERS:
            for operation in CallSite.OPERATION_SUFFIXES:
                assert _FIELD_SAFE.match(CallSite.compose(caller, operation))


class TestCallSiteEnumeration:
    """The full set is asserted, so an added label cannot slip in unnoticed."""

    def test_flat_labels_are_exactly_the_nine_single_route_sites(self):
        assert CallSite.FLAT_LABELS == _EXPECTED_FLAT
        assert len(CallSite.FLAT_LABELS) == 9

    def test_operation_suffixes_are_exactly_the_three_helpers(self):
        assert CallSite.OPERATION_SUFFIXES == _EXPECTED_SUFFIXES
        assert len(CallSite.OPERATION_SUFFIXES) == 3

    def test_callers_are_exactly_prestage_and_api(self):
        assert CallSite.CALLERS == _EXPECTED_CALLERS
        assert len(CallSite.CALLERS) == 2

    def test_no_member_overlaps_another_set(self):
        """Flat labels and operation suffixes are disjoint namespaces."""
        assert CallSite.FLAT_LABELS.isdisjoint(CallSite.OPERATION_SUFFIXES)
        assert CallSite.FLAT_LABELS.isdisjoint(CallSite.CALLERS)
        assert CallSite.OPERATION_SUFFIXES.isdisjoint(CallSite.CALLERS)


class TestCallSiteCompose:
    def test_compose_joins_caller_and_operation_with_a_dot(self):
        assert CallSite.compose("prestage", "cloze_generate") == "prestage.cloze_generate"
        assert CallSite.compose("api", "cloze_judge") == "api.cloze_judge"
        assert CallSite.compose("api", "cloze_generate") == "api.cloze_generate"
        assert CallSite.compose("prestage", "cloze_translate") == "prestage.cloze_translate"
