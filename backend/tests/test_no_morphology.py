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


class TestIsLemmaPlausible:
    """The `trø` bug: stanza stripped `-tt` off the adjective `trøtt` ("tired")
    and returned `trø`, an unrelated verb ("to tread"), so the card taught
    `trø` = "tired". The gloss was right; the *headword* was a different word.

    The guard only fires on the truncation signature (a full trailing
    doubled-consonant drop) and then only when the lemma is not a common word —
    conservative, because a false accept teaches the wrong word outright while
    a false reject just keeps the inflected surface (mildly worse, still a real
    word from the sentence).
    """

    @pytest.mark.parametrize(
        ("surface", "lemma"),
        [
            ("morderen", "morder"),
            ("nabolaget", "nabolag"),
            ("åpnet", "åpne"),
            # The neuter -t doubles the final consonant, so a full pair drop is
            # legitimate when the lemma is a common word — this is exactly the
            # shape of the `trø` bug, and the common-word gate is what separates
            # them (ny=195, blå=1180 vs trø=49800).
            ("nytt", "ny"),
            ("blått", "blå"),
            # Suppletive: the lemma is not a substring of the surface at all.
            # These are the discriminating rows — a naive "lemma must be a
            # prefix of the surface" rule accepts trøtt→trø and rejects both of
            # these, i.e. exactly backwards.
            ("gikk", "gå"),
            ("eldre", "gammel"),
        ],
    )
    def test_accepts_genuine_lemmatization(self, surface, lemma):
        from app.plugins.languages.no.morphology import is_lemma_plausible

        assert is_lemma_plausible(surface, lemma) is True

    def test_rejects_the_tro_truncation(self):
        """The reported bug: `trø` sits at rank 49800 in the bundled wordlist —
        the noise tail — so the doubled-pair drop fails the common-word gate."""
        from app.plugins.languages.no.morphology import is_lemma_plausible

        assert is_lemma_plausible("trøtt", "trø") is False

    def test_rejects_a_fragment_absent_from_the_wordlist(self):
        """A doubled-pair drop whose lemma the bundled wordlist does not know
        at all (rank is None) is equally untrustworthy — reject it."""
        from app.plugins.languages.no.morphology import is_lemma_plausible

        assert is_lemma_plausible("zqtt", "zq") is False

    def test_identical_surface_and_lemma_is_always_plausible(self):
        """An uninflected word must never be rejected — there is no truncation
        to suspect, whatever the corpus says."""
        from app.plugins.languages.no.morphology import is_lemma_plausible

        assert is_lemma_plausible("morder", "morder") is True

    def test_known_limitation_setet_is_not_caught(self):
        """`setet` → `set` is OUT of scope, and this pins that it stays out.

        `setet→set` and `nabolaget→nabolag` strip the same valid `-et`, so no
        string rule separates them, and the bundled wordlist does not either:
        `set` ranks 6184 — inside the accept band — because the frequency corpus
        it is built from is English-contaminated (the borrowed noun `set` shows
        up constantly in subtitles), not because it is a genuine Norwegian word.
        Catching it needs a validated cross-language guard; the naive form
        false-rejects 8 of 14 common loanwords (`film`, `data`, `service`…).

        This test documents the boundary. If a future change makes it pass,
        that is good news — update the test and the issue rather than reverting.
        """
        from app.plugins.languages.no.morphology import is_lemma_plausible

        assert is_lemma_plausible("setet", "set") is True


class TestLemmaPlausibleRegistry:
    def test_norwegian_exposes_a_checker(self):
        from app.languages import get_lemma_plausible

        checker = get_lemma_plausible("no")
        assert checker is not None
        assert checker("trøtt", "trø") is False

    @pytest.mark.parametrize("code", ["sl", "en", "zz"])
    def test_languages_without_a_checker_are_a_no_op(self, code):
        """Slovene registers none, so callers must degrade to today's behaviour.

        Core reaches this only through the registry — it must never branch on
        the language itself, per the no-hardcoded-language-logic rule.
        """
        from app.languages import get_lemma_plausible

        assert get_lemma_plausible(code) is None


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
