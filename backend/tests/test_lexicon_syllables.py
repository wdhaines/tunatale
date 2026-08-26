"""Tests for lexicon syllable boundary alignment (tunatale-aoeu).

The orthographic aligner cuts a word at NST-lexicon syllable boundaries,
preserving the invariant that boundaries and phonemes come from the SAME
source. All oracles below were measured against the real built lexicon
(44 MB SQLite); contradicting them is a FINDING.
"""

from __future__ import annotations

from pathlib import Path

from app.plugins.languages.no.lexicon_syllables import (
    REFUSE_EMPTY,
    REFUSE_NO_PATH,
    REFUSE_SILENT_AT_CUT,
    _pieces_from_cuts,
    lexicon_syllable_split,
    orthographic_syllables,
)

# NO skipif here, deliberately. The NST database is a build artifact, and both
# ./test.sh and every backend CI job now build it (~1s) before running pytest.
# Skipping on its absence would mean this whole feature is silently untested in
# CI — green for the wrong reason. If the build step ever disappears these fail
# loudly, which is the point.


class TestOrthographicSyllables:
    """Adopted words: lexicon boundaries differ from spelling.

    Transcriptions are the real NST X-SAMPA rows.
    """

    def test_skygge(self):
        """skyg|ge -> sky|gge"""
        result, reason = orthographic_syllables("skygge", '""SY$g@')
        assert result == ["sky", "gge"]
        assert reason == ""

    def test_hadde(self):
        """had|de -> ha|dde"""
        result, reason = orthographic_syllables("hadde", '""hA$d@')
        assert result == ["ha", "dde"]
        assert reason == ""

    def test_etter(self):
        """et|ter -> e|tter"""
        result, reason = orthographic_syllables("etter", '""E$t@r')
        assert result == ["e", "tter"]
        assert reason == ""

    def test_mannen(self):
        """man|nen -> ma|nnen"""
        result, reason = orthographic_syllables("mannen", '"mA$nn=')
        assert result == ["ma", "nnen"]
        assert reason == ""

    def test_ringe(self):
        """rin|ge -> ring|e (no phantom /g/ — tunatale-96rn)"""
        result, reason = orthographic_syllables("ringe", '""rIN$@')
        assert result == ["ring", "e"]
        assert reason == ""

    def test_penger(self):
        """pen|ger -> peng|er"""
        result, reason = orthographic_syllables("penger", '""pEN$@r')
        assert result == ["peng", "er"]
        assert reason == ""

    def test_person(self):
        """per|son -> pe|rson"""
        result, reason = orthographic_syllables("person", 'p@$"s`u:n')
        assert result == ["pe", "rson"]
        assert reason == ""

    def test_kjokken(self):
        """kjøk|ken -> kjø|kken"""
        result, reason = orthographic_syllables("kjøkken", '"C9$k@n')
        assert result == ["kjø", "kken"]
        assert reason == ""

    def test_undersoke(self):
        """un|der|sø|ke -> u|nde|rsø|ke (the xk1p word)"""
        result, reason = orthographic_syllables("undersøke", '""u0$n@$%s`2:$k@')
        assert result == ["u", "nde", "rsø", "ke"]
        assert reason == ""

    def test_opprinnelig(self):
        """oppr|inne|lig -> o|ppri|nne|lig (count changes 3 -> 4)"""
        result, reason = orthographic_syllables("opprinnelig", 'O$"prI$n@$lI')
        assert result is not None
        assert len(result) == 4
        assert "".join(result) == "opprinnelig"
        assert reason == ""


class TestUnchangedAdoption:
    """Adopted words where lexicon agrees with the repo split."""

    def test_bilder(self):
        result, reason = orthographic_syllables("bilder", '""bIl$d@r')
        assert result == ["bil", "der"]
        assert reason == ""

    def test_flaske(self):
        result, reason = orthographic_syllables("flaske", '""flA$sk@')
        assert result == ["fla", "ske"]
        assert reason == ""

    def test_vinduet(self):
        result, reason = orthographic_syllables("vinduet", '""vIn$du0$@')
        assert result == ["vin", "du", "et"]
        assert reason == ""

    def test_sjekk(self):
        """Monosyllable."""
        result, reason = orthographic_syllables("sjekk", '"SEk')
        assert result == ["sjekk"]
        assert reason == ""


class TestRefusals:
    """Refused words: each pins one guard."""

    def test_sporene_silent_letter_merges_right(self):
        """sporene: lexicon spuː.ɳ̩.ə — r merged into the retroflex."""
        result, reason = orthographic_syllables("sporene", '""spu:$n`=$@')
        assert result is None
        assert reason == REFUSE_SILENT_AT_CUT

    def test_morgen_silent_letter_merges_right(self):
        """morgen: lexicon moː.ɳ̩"""
        result, reason = orthographic_syllables("morgen", '""mo:$n`=')
        assert result is None
        assert reason == REFUSE_SILENT_AT_CUT


class TestUnchangedControlWords:
    """Words that refuse or adopt identically — must not change."""

    def test_mulighet_unchanged(self):
        result, reason = orthographic_syllables("mulighet", '""m}:$lI$%he:t')
        assert result is not None
        assert "".join(result) == "mulighet"

    def test_handler_unchanged(self):
        result, reason = orthographic_syllables("handler", '""hAn$l@r')
        assert result is not None
        assert "".join(result) == "handler"

    def test_tidligere_unchanged(self):
        result, reason = orthographic_syllables("tidligere", '""ti:d$lI$@$r@')
        assert result is not None
        assert "".join(result) == "tidligere"

    def test_selvfolgelig_unchanged(self):
        result, reason = orthographic_syllables("selvfølgelig", 's@l$"f9l$g@$li:')
        assert result is not None
        assert "".join(result) == "selvfølgelig"


# ---------------------------------------------------------------------------
# lexicon_syllable_split tests
# ---------------------------------------------------------------------------


class TestLexiconSyllableSplit:
    """The whole-word split all candidate readings agree on."""

    def test_skygge(self):
        assert lexicon_syllable_split("skygge") == ["sky", "gge"]

    def test_hadde(self):
        assert lexicon_syllable_split("hadde") == ["ha", "dde"]

    def test_etter(self):
        assert lexicon_syllable_split("etter") == ["e", "tter"]

    def test_mannen(self):
        assert lexicon_syllable_split("mannen") == ["ma", "nnen"]

    def test_undersoke(self):
        assert lexicon_syllable_split("undersøke") == ["u", "nde", "rsø", "ke"]

    def test_absent_word_returns_none(self):
        assert lexicon_syllable_split("zzqqxx") is None

    def test_sporene_refuses(self):
        """sporene: r merged into retroflex — must be refused."""
        assert lexicon_syllable_split("sporene") is None

    def test_morgen_refuses(self):
        """morgen: r merged into retroflex — must be refused."""
        assert lexicon_syllable_split("morgen") is None


class TestRefusalGuards:
    """Each guard refuses for its own reason, and none of them is decorative."""

    def test_transcription_with_no_syllables_refuses(self) -> None:
        """A transcription that is only a separator has nothing to align."""
        assert orthographic_syllables("ord", "$") == (None, REFUSE_NO_PATH)

    def test_transcription_of_only_markers_refuses(self) -> None:
        """Stress marks are suprasegmental; stripped, they leave no phones."""
        assert orthographic_syllables("ord", '"$"') == (None, REFUSE_NO_PATH)

    def test_non_increasing_cut_refuses_rather_than_emitting_an_empty_piece(self) -> None:
        """Two boundaries at one offset would caption a chunk with no letters."""
        assert _pieces_from_cuts("hage", [2, 2]) == (None, REFUSE_EMPTY)

    def test_cut_at_the_end_of_the_word_refuses(self) -> None:
        """A final cut at len(word) would make the last piece empty."""
        assert _pieces_from_cuts("hage", [4]) == (None, REFUSE_EMPTY)

    def test_well_formed_cuts_slice_the_word(self) -> None:
        """The control: the same helper does its job on a sane cut list."""
        assert _pieces_from_cuts("hage", [2]) == (["ha", "ge"], "")


class TestMissingDatabaseDegrades:
    """A gitignored build artifact is absent on a fresh clone; that is not a crash."""

    def test_unbuilt_lexicon_returns_none(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.sqlite3"
        assert lexicon_syllable_split.__wrapped__("skygge", missing) is None
