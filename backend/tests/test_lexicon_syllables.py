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

    def test_gården_silent_letter_merges_right(self):
        """gården: a silent letter still sits immediately before the cut.

        Subject changed from 'sporene'/'morgen' (tunatale-4rj5): those now
        ALIGN, because r+vowel+coronal graphemes let the r carry the retroflex
        it causes instead of aligning to nothing. See
        TestRetroflexAcrossAWrittenVowel. This guard is NOT decorative — 488
        words in the first 20000 still refuse for this reason — so it keeps a
        live subject rather than being deleted.
        """
        result, reason = orthographic_syllables("gården", '"go:$n`=')
        assert result is None
        assert reason == REFUSE_SILENT_AT_CUT

    def test_ordene_silent_letter_merges_right(self):
        """A second live subject, so one lexicon edit cannot empty this class."""
        result, reason = orthographic_syllables("ordene", '"u:$n`=$@')
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

    def test_gården_refuses(self):
        """Still refused after tunatale-4rj5 (sporene/morgen no longer are)."""
        assert lexicon_syllable_split("gården") is None
        assert lexicon_syllable_split("standarden") is None


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


class TestRetroflexAcrossAWrittenVowel:
    """tunatale-4rj5: an r fuses with a following coronal across a written letter.

    'sporene' is /'spu:n`=@/. The r produces the retroflex but is separated from
    its n by an e that is not pronounced, so the adjacent-only "rn" grapheme
    cannot match, the r aligns to nothing, and a silent letter immediately
    before a cut is what REFUSE_SILENT_AT_CUT rejects. The word was refused
    entirely and got no IPA.
    """

    def test_sporene_now_cuts_before_the_r(self):
        assert lexicon_syllable_split("sporene") == ["spo", "ren", "e"]

    def test_two_letters_may_intervene(self):
        """morgen /'mo:n`=/ and hjernen /'j{:n`=/ need a 4-letter grapheme.

        _MAXG is derived from TABLE, so these only work when added at module
        level -- adding them at runtime leaves _MAXG stale and silently does
        nothing, which is how they were missed on the first pass.
        """
        assert lexicon_syllable_split("morgen") == ["mo", "rgen"]
        assert lexicon_syllable_split("hjernen") == ["hje", "rnen"]

    def test_adjacent_rn_is_unchanged(self):
        """The control that separates this class from ordinary retroflexes.

        barnet already aligned because its r and n are adjacent; bilene has no
        r at all and takes the plain syllabic n̩. Neither may move.
        """
        assert lexicon_syllable_split("barnet") == ["ba", "rnet"]
        assert lexicon_syllable_split("gjerne") == ["gje", "rne"]
        assert lexicon_syllable_split("bilene") == ["bi", "len", "e"]

    def test_the_r_goes_with_the_sound_it_causes(self):
        """The property, stated once: no piece may end in a silent r.

        Cutting 'spor|ene' would show a chunk whose r makes no sound followed
        by a chunk opening with a retroflex containing no visible r.
        """
        for word in ("sporene", "faren", "morgen", "hjernen", "dørene"):
            pieces = lexicon_syllable_split(word)
            assert pieces is not None, word
            assert "".join(pieces) == word
            assert not any(p.endswith("r") for p in pieces[:-1]), (word, pieces)
