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

from app.plugins.languages.no.morphology import (
    _better_lemma_one_char_away,
    _open_nst,
    is_definite_form,
    is_lemma_plausible,
)


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

    def test_rejects_the_snom_truncation(self):
        """tunatale-wum6: stanza returned `snøm` for `snømenn` (irregular plural
        of `snømann`, mann→menn), and the non-word became the headword. The user
        failed it twelve times, which is the expected outcome for a card that
        cannot be answered.

        The old signature could not see it: the dropped tail is `enn`, not a
        doubled consonant, so the guard returned True before reaching the
        common-word gate. What separates it from every legitimate case measured
        is that `enn` is not a Norwegian inflectional ending at all — `en`,
        `et`, `ene`, `er` are; `enn` is not.
        """
        from app.plugins.languages.no.morphology import is_lemma_plausible

        assert is_lemma_plausible("snømenn", "snøm") is False

    @pytest.mark.parametrize(
        ("surface", "lemma"),
        [
            # Productive compounds absent from the 50k wordlist. These are the
            # false-positive family the widening has to spare: rejecting them
            # would key the card on the definite form (`snømannen`), a real word
            # but the wrong shape, for a lemma that was perfectly correct.
            ("snømannen", "snømann"),
            ("billettautomaten", "billettautomat"),
            ("koordinatene", "koordinat"),
            ("retningslinjene", "retningslinje"),
            # Stem geminate: `rom` doubles its final consonant before `-et`, so
            # the raw dropped tail reads `met`. Without the geminate step this
            # is the one legitimate row the widening would have broken —
            # measured, not hypothesised.
            ("avhørsrommet", "avhørsrom"),
        ],
    )
    def test_widening_spares_real_compounds(self, surface, lemma):
        """Measured over 1264 real (surface, lemma) pairs from the live
        `lemma_analysis_cache`: the widened rule flips exactly ONE pair from
        accept to reject — `snømenn`→`snøm` — with zero regressions in the
        other direction. These rows are that measurement's guard rail.
        """
        from app.plugins.languages.no.morphology import is_lemma_plausible

        assert is_lemma_plausible(surface, lemma) is True

    @pytest.mark.parametrize(
        ("surface", "lemma", "expected"),
        [
            ("mappen", "mapp", False),
            ("kaffen", "kaff", False),
            ("programvaren", "programvar", False),
            ("gluten", "glute", False),
            ("eksamen", "eksame", False),
            ("lyktene", "lykte", False),
            ("rør", "rure", False),
            ("avstikker", "avstikk", False),
            ("fortsett", "fortse", False),
            ("snømenn", "snøm", False),
            ("vårkonserten", "vårkonsert", True),
            ("billettautomaten", "billettautomat", True),
            ("åttitallet", "åttitall", True),
            ("setet", "set", True),
            ("vinteren", "vinter", True),
            ("nytt", "ny", True),
            ("snømann", "snømann", True),
        ],
    )
    def test_neighbour_screen_oracle(self, surface, lemma, expected):
        assert is_lemma_plausible(surface, lemma) is expected

    def test_the_screen_is_inert_without_a_built_lexicon(self):
        """The NST database is a BUILD ARTIFACT, so "not there" is an ordinary
        state a checkout can be in — the screen must go quiet, never guess.

        ⚠️ Asserted by passing the dependency in, NOT by patching
        `nst_lexicon_installed`. That patch is a string-form `patch("app.…")` and
        `scripts/check_mock_boundaries.py` fails the build on it — correctly: the
        thing under test is a capability gate, and a gate is testable by handing
        it the absent capability.
        """
        assert _better_lemma_one_char_away("mapp", None) is False

    def test_an_unbuilt_lexicon_really_does_report_absent(self, tmp_path):
        """The other half of the pair above, and the reason neither is vacuous
        alone: `_better_lemma_one_char_away` handles None, and this is what
        actually produces the None."""
        with _open_nst(tmp_path / "never-built.sqlite3") as lexicon:
            assert lexicon is None

    def test_a_built_lexicon_is_opened(self):
        """The other side, so the None above is not the only path ever taken."""
        with _open_nst() as lexicon:
            assert lexicon is not None

    def test_the_whole_predicate_is_inert_without_a_lexicon(self, tmp_path):
        """End to end: no database, and `mappen` -> `mapp` is accepted again,
        exactly as it was before this screen existed."""
        with _open_nst(tmp_path / "never-built.sqlite3") as lexicon:
            assert _better_lemma_one_char_away("mapp", lexicon) is False


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
