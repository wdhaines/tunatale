"""Tests for lexicon syllable boundary alignment (tunatale-aoeu).

The orthographic aligner cuts a word at NST-lexicon syllable boundaries,
preserving the invariant that boundaries and phonemes come from the SAME
source. All oracles below were measured against the real built lexicon
(44 MB SQLite); contradicting them is a FINDING.
"""

from __future__ import annotations

import gzip
from pathlib import Path

from app.plugins.languages.no.lexicon import build_lexicon_db
from app.plugins.languages.no.lexicon_syllables import (
    REFUSE_EMPTY,
    REFUSE_NO_PATH,
    REFUSE_SILENT_AT_CUT,
    _pieces_from_cuts,
    lexicon_reading,
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


class TestEaDigraph:
    """tunatale-d4td: the English digraph ``ea`` may spell /ɪː/.

    Without a rule saying ``ea`` can spell /ɪː/, ``teamet`` (ONE lexicon reading,
    /ˈtɪː.mə/) refused with ``no-path`` and got no boundaries at all. The
    grapheme follows the lexicon, not a hardcoded cut: ``teamleder`` has the same
    ``ea`` but the /m/ really sits in syllable 1 (/ˈtɪːm.ˌleː.dər/), so it cuts
    differently.
    """

    def test_teamet_cuts_after_the_digraph(self) -> None:
        result, reason = orthographic_syllables("teamet", '"ti:$m@')
        assert result == ["tea", "met"]
        assert reason == ""

    def test_teamleder_keeps_the_m_in_syllable_one(self) -> None:
        """The discriminating control: same rule, opposite result."""
        result, reason = orthographic_syllables("teamleder", '"ti:m$%le:$d@r')
        assert result == ["team", "le", "der"]
        assert reason == ""

    def test_sporet_unchanged_control(self) -> None:
        result, reason = orthographic_syllables("sporet", '"spu:$r@')
        assert result == ["spo", "ret"]
        assert reason == ""

    def test_teamet_lexicon_split(self) -> None:
        assert lexicon_syllable_split("teamet") == ["tea", "met"]


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


class TestMostEnunciatedTiebreak:
    """tunatale-k318: when readings cut differently, adopt the most-enunciated.

    The old rule (agree-or-refuse) returned None on disagreement and the word
    fell to the audio slicer. The user decided instead to pick the reading that
    elides least. Rule, in brief:
    1. any reading that fails to align -> None, unchanged;
    2. all aligned splits agree -> that split, unchanged;
    3. drop readings whose split has a vowelless piece (fall back to the full
       set if that empties it);
    4. among what remains, take the reading with the most phonemic segments;
    5. one distinct split at that maximum -> return it; otherwise break the tie
       toward the FINER split (same phones, so no caption can lie about the
       audio), and refuse only when that ties too.

    ⚠ 'segments' means PHONES. Stress marks and syllable dots are excluded:
    counting them decided gylden/tanger/grunder on the stress mark alone.
    """

    def test_under_keeps_the_d(self):
        """under -> ["un","der"], never None.

        The decisive case that motivated the rule: /'ʉ.nər/ elides the d (4
        segments) while /'ʉn.dər/ keeps it (5), so the enunciated reading must
        win. Failing here is the whole tiebreak misbehaving (steps 4-5).
        """
        assert lexicon_syllable_split("under") == ["un", "der"]

    def test_flat_does_not_strand_a_vowelless_piece(self):
        """flat -> ["flat"], not ["fla","t"].

        Pins step 3: without discarding readings whose split contains a
        vowelless piece, the most-segments rule would caption a chunk with no
        vowel ("t"). The measured naive-winner flat -> ['fla','t'] must not
        survive the discard step.
        """
        assert lexicon_syllable_split("flat") == ["flat"]

    def test_studerte_does_not_strand_a_vowelless_piece(self):
        """studerte -> ["stu","de","rte"], not ["stu","de","rt","e"].

        Same step-3 guard as test_flat, multi-syllable: pieces without a vowel
        identify the candidate to discard before the count decides.
        """
        assert lexicon_syllable_split("studerte") == ["stu", "de", "rte"]

    def test_videre_wins_by_phonemic_count(self):
        """videre -> ["vi","de","re"].

        Pins step 4 with no vowelless complication: every candidate split is
        clean, so the win rests purely on which reading has the most phonemic
        segments.
        """
        assert lexicon_syllable_split("videre") == ["vi", "de", "re"]

    def test_beskjeden_still_refuses_on_a_tie(self):
        """beskjeden -> None.

        Pins step 5: when two or more distinct splits tie at the most-segments
        maximum the rule refuses, exactly as agree-or-refuse did. Resolving any
        Tied word would mean the tie guard is broken.
        """
        assert lexicon_syllable_split("beskjeden") is None

    def test_gylden_refuses_when_only_the_stress_mark_differs(self):
        """gylden -> None.

        The second tie guard, and the one that pins WHAT COUNTS as a segment.
        gylden's readings say the same phones in the same number of syllables
        and differ only in stress marking. Counting 'ˈ' or '.' as segments would
        resolve it on the stress mark alone — the one signal this project
        explicitly does not caption on. Resolving this word means _segments has
        started counting marks again.
        """
        assert lexicon_syllable_split("gylden") is None

    def test_mønstre_resolves_on_the_phone_count(self):
        """mønstre -> ["møns","tre"].

        The companion to test_gylden: here the readings genuinely differ in how
        many phones they contain, so level 1 decides and refusing would be
        wrong. Pins that stripping the marks did not turn the rule into a
        blanket refusal.
        """
        assert lexicon_syllable_split("mønstre") == ["møns", "tre"]

    def test_tittelen_resolves_on_the_finer_split(self):
        """tittelen -> ["ti","tte","len"], not ["ti","ttelen"].

        Pins LEVEL 2. Both readings carry the same phones — the sound is
        identical — so neither caption could lie about the audio, and the tie is
        broken toward the finer split because a buildup drill wants the smaller
        rungs. Returning None here would mean level 2 was dropped.
        """
        assert lexicon_syllable_split("tittelen") == ["ti", "tte", "len"]

    def test_sporet_unchanged_control(self):
        """sporet -> ["spo","ret"], still.

        Control from the unchanged mass: sporet's two readings differ
        phonemically (rə vs rət) but cut the spelling identically, so the
        tiebreak must not move it (step 2, agree-on-split, unchanged).
        """
        assert lexicon_syllable_split("sporet") == ["spo", "ret"]

    def test_every_reading_vowelless_falls_back_to_the_full_set(self, tmp_path: Path) -> None:
        """The discard must not empty the pool it then takes a maximum over.

        No word in the real 50006-word lexicon has EVERY reading strand a
        vowelless piece, so this shape needs a fixture. 'att' gets two readings
        that both do — a|tt and at|t. Without the fall-back-to-the-full-set
        line, `remaining` is empty and `max()` raises ValueError instead of
        returning; the two readings then tie at every level, so the correct
        answer is a refusal.
        """
        gz = tmp_path / "fixture.tsv.gz"
        payload = 'att\tNN\t"A$tt\t1\natt\tNN\t"At$t\t1\n'
        gz.write_bytes(gzip.compress(payload.encode("utf-8"), mtime=0))
        db = tmp_path / "lexicon.sqlite3"
        build_lexicon_db(gz, db)

        assert lexicon_syllable_split("att", db) is None


class TestLexiconReading:
    """tunatale-k318.5: the three-way contract that lets phonemes reuse the split's reading.

    lexicon_syllable_split and the phoneme planner (phoneme_plan.py) must consume
    the SAME decision. lexicon_reading is that single fact: it returns the split
    AND, when the most-enunciated tiebreak chose one reading over others, that
    winner's transcription — so boundaries and sound cannot cross (the module's
    invariant). The three-way distinction is the whole point:
    ``None`` (no adoptable split) vs ``(split, None)`` (readings agreed on the
    split, may still differ in sound) vs ``(split, transcription)`` (a reading
    was chosen). Collapsing the middle and empty cases is the exact drift this
    bead exists to prevent.
    """

    def test_tiebreak_chosen_word_returns_transcription(self) -> None:
        """under -> (['un','der'], <X-SAMPA·of·/ʉn.dər/>).

        under's two readings cut differently (u|nder vs un|der) and the tiebreak
        picks the more enunciated /ˈʉn.dər/ — the reading that keeps the d. The
        transcription half must be that winner's, so a phoneme consumer can play
        the SAME sound the caption's cut was built on. Failing here —
        transcription None — would send under back to the phoneme gate that
        refuses the ʉndər-vs-ʉnər disagreement, undoing the bead's one payoff.
        """
        result = lexicon_reading("under")
        assert result is not None
        split, transcription = result
        assert split == ["un", "der"]
        assert transcription is not None
        assert "d" in transcription

    def test_agree_on_split_word_returns_none_transcription(self) -> None:
        """sporet -> (['spo','ret'], None).

        sporet's two readings cut the spelling identically but sound differently
        at the -et syllable (/rə/ vs /rət/). No single reading was chosen, so
        the phoneme consumer MUST keep the all-candidates-agree gate. Returning
        a transcription here would pin one sound and lower the tunatale-d4td gate
        this bead must not touch.
        """
        assert lexicon_reading("sporet") == (["spo", "ret"], None)

    def test_no_adoptable_split_returns_none(self) -> None:
        """gylden -> None, and so does lexicon_reading.

        A word the tiebreak cannot settle (readings tie, or an alignment fails)
        has no adoptable split at all. Returning the empty case as anything but
        None would be confused with the agree-on-split ``(split, None)`` case the
        phoneme gate treats completely differently.
        """
        assert lexicon_reading("gylden") is None

    def test_disagreeing_readings_share_the_winning_split_returns_none_transcription(self, tmp_path: Path) -> None:
        """mata -> (['ma','ta'], None) when two equal readings tie for the split.

        Three readings: two cut ma|ta with the SAME phone count but a different
        vowel (/mɑ.tɑ/ vs /mæ.tɑ/ — both ɑ and æ align to the letter ``a``), one
        cuts the whole word mata as a single syllable. The tiebreak picks the
        winning SPLIT (ma|ta, the finer one) but two equally-enunciated readings
        own it and differ in SOUND, so no single reading was chosen — the
        transcription half must be None and the phoneme gate keeps the right to
        refuse. Failing here means lexicon_reading pinned an arbitrary sound,
        crossing the invariant.

        No real word in the 50006-word list has two max-segments readings sharing
        the winning split (measured 0), so this shape needs a fixture.
        """
        gz = tmp_path / "fixture.tsv.gz"
        payload = 'mata\tNN\t"mA$tA\t1\nmata\tNN\t"m{$tA\t1\nmata\tNN\t"mAtA\t1\n'
        gz.write_bytes(gzip.compress(payload.encode("utf-8"), mtime=0))
        db = tmp_path / "lexicon.sqlite3"
        build_lexicon_db(gz, db)

        assert lexicon_reading("mata", db) == (["ma", "ta"], None)
        # lexicon_syllable_split still returns the decided split (LOST must be 0).
        assert lexicon_syllable_split("mata", db) == ["ma", "ta"]
