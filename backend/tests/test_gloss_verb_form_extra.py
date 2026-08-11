"""Branch coverage for ``app/srs/gloss_verb_form.py`` and its wiring.

``tests/test_gloss_verb_form.py`` (orchestrator-locked) pins the reported-bug
oracles; this file covers the structural branches those cases do not reach:
case transfer, the unconfirmed-base pass-throughs, the ``-ies``/``-es``/plain
``-ing`` rule family, and the UPOS-lookup edge cases in
``app.api.srs._resolve_gloss_translation`` (lemma-hit and no-analyzer).
"""

from __future__ import annotations


class TestAlignGlossVerbFormBranches:
    def _align(self, gloss: str) -> str:
        from app.srs.gloss_verb_form import align_gloss_verb_form

        return align_gloss_verb_form(gloss)

    # -- Case transfer -------------------------------------------------------

    def test_upper_case_input_transfers_case_to_the_base(self):
        assert self._align("RUNNING") == "RUN"

    def test_title_case_input_transfers_case_to_the_base(self):
        assert self._align("Is Running") == "Run"

    # -- -ing rule family ----------------------------------------------------

    def test_ing_stem_shorter_than_two_letters_passes_through(self):
        assert self._align("king") == "king"

    def test_doubled_consonant_with_unconfirmed_base_passes_through(self):
        """`swim` sits below the wordfreq floor, so the doubling rule cannot
        confirm it — degrade to a pass-through rather than guess."""
        assert self._align("is swimming") == "swimming"

    def test_silent_e_with_unconfirmed_base_passes_through(self):
        assert self._align("is faxing") == "faxing"

    def test_plain_ing_with_word_stem_reduces(self):
        assert self._align("is going") == "go"

    def test_plain_ing_with_non_word_stem_passes_through(self):
        assert self._align("spring") == "spring"

    # -- -s rule family ------------------------------------------------------

    def test_ies_third_person_reduces_to_y(self):
        assert self._align("she carries") == "carry"

    def test_ies_candidate_not_a_word_falls_through_to_plain_s(self):
        """`dies` fails the -ies → -y rule ("dy" is not a word) and reduces
        via the plain -s rule instead ("die")."""
        assert self._align("he dies") == "die"

    def test_es_third_person_reduces(self):
        assert self._align("he watches") == "watch"

    def test_s_with_no_confirmable_base_passes_through(self):
        assert self._align("this") == "this"

    def test_two_letter_s_word_skips_the_s_rules(self):
        """`us` ends in -s but is not a third-person form; the length guard
        keeps it intact."""
        assert self._align("us") == "us"

    # -- Framing -------------------------------------------------------------

    def test_strips_pronoun_then_auxiliary(self):
        assert self._align("he is running") == "run"

    def test_irregular_finite_forms_reduce(self):
        assert self._align("is") == "be"
        assert self._align("was") == "be"
        assert self._align("has") == "have"
        assert self._align("does") == "do"

    # -- Slash-separated alternatives ----------------------------------------

    def test_empty_alternative_is_untouched(self):
        assert self._align("show/") == "show/"

    def test_whitespace_only_alternative_is_untouched(self):
        assert self._align("lie / ") == "lie / "

    def test_pure_framing_alternative_is_untouched(self):
        assert self._align("he /") == "he /"


class TestHeadwordUposDerivation:
    """The `_resolve_gloss_translation` verb gate, beyond the locked tests."""

    def _resolve(self, lemma, token_glosses, surfaces, first_surface, surface_upos):
        from app.api.srs import _resolve_gloss_translation

        return _resolve_gloss_translation(
            lemma,
            token_glosses,
            surfaces,
            first_surface,
            language_code="no",
            surface_upos=surface_upos,
            warn_on_missing=False,
        )

    def test_lemma_present_in_surface_upos_wins(self):
        """The lemma can itself carry a UPOS entry (the dictionary form is
        often glossed alongside the surface) — it must resolve before the
        surfaces."""
        assert (
            self._resolve(
                "lyve",
                {"lyve": "to lie"},
                {"lyver"},
                "lyver",
                {"lyve": "VERB", "lyver": "VERB"},
            )
            == "lie"
        )

    def test_no_upos_for_the_headword_leaves_the_gloss_alone(self):
        """An analyzer that produced no entry for this headword (or an empty
        map) means the verb branch has nothing to act on — never guess."""
        assert (
            self._resolve(
                "lyve",
                {"lyver": "is lying"},
                {"lyver"},
                "lyver",
                {},
            )
            == "is lying"
        )
