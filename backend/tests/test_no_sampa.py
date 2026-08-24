"""X-SAMPA -> IPA conversion for the NST lexicon (stage 2a).

Wired to nothing yet: these pin the converter's contract so the ``<phoneme>``
seam (2b) and the phrase planner (2c) can consume it without re-deriving the
mapping. Every case here is a literal; the whole-extract sweep that proves
649,591 transcriptions convert with zero failures is a one-off probe under
``scripts/local/``, deliberately NOT a test — the gate must not scan a 4.6 MB
build artifact.
"""

from __future__ import annotations

import pytest

from app.plugins.languages.no.sampa import (
    UnknownSegmentError,
    ipa_syllables,
    sampa_to_ipa,
    strip_tone,
)


class TestLiteralConversions:
    """The six oracles measured against the real lexicon before this landed."""

    @pytest.mark.parametrize(
        ("sampa", "ipa"),
        [
            ('""sn2:$%mAn', '"snøː.ˌmɑn'),
            ('""hA:$g@n', '"hɑː.gən'),
            ('"s{*I', "ˈsæ͡ɪ"),
            ('"gA:lt', "ˈgɑːlt"),
            ('"h}:$s@', "ˈhʉː.sə"),
            ('""h}:$s@t', '"hʉː.sət'),
        ],
    )
    def test_oracle(self, sampa: str, ipa: str) -> None:
        assert sampa_to_ipa(sampa) == ipa


class TestSegmentGroups:
    def test_consonants(self) -> None:
        # C -> ç and N -> ŋ are the two that a naive ASCII reading gets wrong.
        assert sampa_to_ipa("C") == "ç"
        assert sampa_to_ipa("N") == "ŋ"

    def test_vowels(self) -> None:
        assert sampa_to_ipa("A:") == "ɑː"
        assert sampa_to_ipa("}:") == "ʉː"
        assert sampa_to_ipa("@") == "ə"

    def test_diphthongs(self) -> None:
        assert sampa_to_ipa("{*I") == "æ͡ɪ"
        assert sampa_to_ipa("E*u0") == "æ͡ʉ"
        assert sampa_to_ipa("@U") == "ɔ͡ʊ"

    def test_retroflexes_use_the_backtick_forms(self) -> None:
        # Never write these inline in a shell command: a backtick is command
        # substitution, and it has already cost a session.
        assert sampa_to_ipa("d`") == "ɖ"
        assert sampa_to_ipa("l`") == "ɭ"
        assert sampa_to_ipa("n`") == "ɳ"
        assert sampa_to_ipa("s`") == "ʂ"
        assert sampa_to_ipa("t`") == "ʈ"

    def test_syllabic_consonants(self) -> None:
        assert sampa_to_ipa("n=") == "n̩"
        assert sampa_to_ipa("l`=") == "ɭ̩"


class TestBoundariesAndStress:
    def test_syllable_boundary_becomes_a_dot(self) -> None:
        assert sampa_to_ipa("hA:$g@n") == "hɑː.gən"

    def test_word_boundary_is_preserved(self) -> None:
        # Real multiword entry ("A-B_Klinikken"). An earlier version of this
        # test used invented X-SAMPA and failed — the UPSTREAM converter
        # rejected it identically, which is what proved the fixture wrong
        # rather than the code.
        assert sampa_to_ipa('"A:$%b@_¤klI$"nI$k@n') == "ˈɑː.ˌbə_¤klɪ.ˈnɪ.kən"

    def test_phrasal_stress_marker_survives(self) -> None:
        # ¤ marks the main phrasal stress in a multiword expression and is
        # passed through unchanged.
        assert sampa_to_ipa("¤A:") == "¤ɑː"

    def test_tone1_becomes_a_stress_mark(self) -> None:
        assert sampa_to_ipa('"gA:lt').startswith("ˈ")

    def test_tone2_becomes_a_literal_quote(self) -> None:
        # `""` and `"""` both mean tone 2 and both map to a bare `"`, which is
        # what strip_tone later removes. A tone-1 `"` maps to `ˈ` and stays.
        assert sampa_to_ipa('""hA:').startswith('"')
        assert sampa_to_ipa('"""hA:').startswith('"')

    def test_secondary_stress(self) -> None:
        assert sampa_to_ipa("%mAn") == "ˌmɑn"


class TestStripTone:
    def test_removes_the_tone2_marker(self) -> None:
        assert strip_tone('"hɑː.gən') == "hɑː.gən"

    def test_leaves_primary_stress_alone(self) -> None:
        assert strip_tone("ˈsæ͡ɪ") == "ˈsæ͡ɪ"

    def test_leaves_secondary_stress_alone(self) -> None:
        assert strip_tone("ˌmɑn") == "ˌmɑn"

    def test_removes_every_occurrence(self) -> None:
        assert strip_tone('"hɑː."gən') == "hɑː.gən"

    def test_is_not_baked_into_the_conversion(self) -> None:
        # The raw conversion stays lossless: captions and stage 3 want the tone
        # mark, only <phoneme> cannot carry it.
        assert '"' in sampa_to_ipa('""hA:$g@n')


class TestIpaSyllables:
    def test_multisyllable(self) -> None:
        assert ipa_syllables('"hɑː.gən') == ('"hɑː', "gən")

    def test_single_syllable(self) -> None:
        assert ipa_syllables("ˈsæ͡ɪ") == ("ˈsæ͡ɪ",)

    def test_word_boundary_also_splits(self) -> None:
        # `_` is a word boundary, and a word boundary is necessarily a syllable
        # boundary too. Matches lexicon.py::_syllables, which splits on both so
        # "boundary" means one thing across the two modules.
        assert ipa_syllables("ˈɑː.ˌbə_¤klɪ.ˈnɪ.kən") == ("ˈɑː", "ˌbə", "¤klɪ", "ˈnɪ", "kən")

    def test_empty_segments_are_dropped(self) -> None:
        assert ipa_syllables("..hɑː..gən..") == ("hɑː", "gən")

    def test_empty_input(self) -> None:
        assert ipa_syllables("") == ()


class TestUnknownSegment:
    def test_raises_rather_than_exiting_the_process(self) -> None:
        # The upstream script calls sys.exit() here, which would kill the API
        # process. A library must raise.
        with pytest.raises(UnknownSegmentError, match="Q"):
            sampa_to_ipa("Q")

    def test_error_names_the_whole_input_too(self) -> None:
        with pytest.raises(UnknownSegmentError, match="hA:Q"):
            sampa_to_ipa("hA:Q")

    def test_is_a_value_error(self) -> None:
        assert issubclass(UnknownSegmentError, ValueError)


class TestNorwegianLetters:
    def test_round_trips_ae_oe_aa(self) -> None:
        assert sampa_to_ipa('""sn2:$%mAn') == '"snøː.ˌmɑn'
        assert sampa_to_ipa("{:") == "æː"
        assert sampa_to_ipa("O") == "ɔ"
