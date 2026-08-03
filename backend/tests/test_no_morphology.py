"""Norwegian definiteness + lemma-plausibility helpers (plugin-local morphology).

Both exist to stop a generated vocab card from contradicting itself. TT builds a
card's front from the lemmatizer's lemma and its back from the LLM's gloss, and
nothing checked that the two agreed: `morder` (indefinite) shipped glossed "the
murderer" (definite = `morderen`), and `set` shipped as "the seat".

Kept in the `no` plugin because it is language morphology — core must reach it
through the registry (`get_definite_form_checker`), per
the no-hardcoded-language-logic rule.
"""

from __future__ import annotations

import pytest

from app.plugins.languages.no.morphology import is_definite_form


class TestIsDefiniteForm:
    @pytest.mark.parametrize(
        "word",
        [
            "setet",  # neuter definite sg (sete + t)
            "huset",  # neuter definite sg (hus + et)
            "snøen",  # masc definite sg
            "bilen",
            "vinduet",
            "bilene",  # definite plural
            "barna",  # definite plural neuter / fem definite sg
        ],
    )
    def test_definite_forms(self, word):
        assert is_definite_form(word) is True

    @pytest.mark.parametrize(
        "word",
        [
            "morder",  # the actual bug: indefinite, was glossed "the murderer"
            "sete",
            "hus",
            "jakke",
            "kopp",
            "avhør",
            "eik",
            "biler",  # indefinite plural
            "vinduer",
        ],
    )
    def test_indefinite_forms(self, word):
        assert is_definite_form(word) is False

    def test_bare_t_needs_an_e_stem(self):
        """`setet` = sete+t is definite; `student` merely ends in t and is not.

        Without the e-stem condition every -t word reads as definite, and the
        gloss aligner would stop stripping "the " where it should.
        """
        assert is_definite_form("setet") is True
        assert is_definite_form("student") is False

    def test_two_letter_e_stem_is_not_definite(self):
        """`set` must read indefinite, or the gloss fix it needs never fires.

        Parsed as `se` + `t` it would look like a definite; but a Norwegian
        e-final noun stem is at least three letters (`sete`, `hage`).
        """
        assert is_definite_form("set") is False
        assert is_definite_form("sete") is False
        assert is_definite_form("setet") is True

    def test_is_case_insensitive(self):
        assert is_definite_form("Setet") is True
        assert is_definite_form("Morder") is False

    def test_short_words_are_never_definite(self):
        """Guards against reading the whole word as its own suffix."""
        for word in ("en", "et", "a", "på", ""):
            assert is_definite_form(word) is False


class TestAlignGlossDefiniteness:
    """The reported bug: gloss says "the" but the headword carries no -en/-et."""

    def _align(self, headword, gloss):
        from app.srs.gloss_definiteness import align_gloss_definiteness

        return align_gloss_definiteness(headword, gloss, "no")

    def test_strips_the_when_headword_is_indefinite(self):
        assert self._align("morder", "the murderer") == "murderer"
        assert self._align("set", "the seat") == "seat"

    def test_keeps_the_when_headword_is_definite(self):
        assert self._align("setet", "the seat") == "the seat"
        assert self._align("huset", "the house") == "the house"

    def test_leaves_glosses_without_an_article_alone(self):
        assert self._align("morder", "murderer") == "murderer"
        assert self._align("jakke", "jacket") == "jacket"

    def test_handles_each_slash_separated_alternative(self):
        assert self._align("morder", "the murderer / the killer") == "murderer / killer"

    def test_is_case_insensitive_on_the_article(self):
        assert self._align("morder", "The murderer") == "murderer"

    def test_does_not_strip_the_inside_a_gloss(self):
        assert self._align("gjennom", "through the wall") == "through the wall"

    def test_empty_gloss_is_untouched(self):
        assert self._align("morder", "") == ""

    def test_unknown_language_is_a_no_op(self):
        from app.srs.gloss_definiteness import align_gloss_definiteness

        assert align_gloss_definiteness("morder", "the murderer", "en") == "the murderer"
