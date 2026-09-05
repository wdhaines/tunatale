"""Tests for breakdown provenance (Stage 2: syllable-span tracking).

Asserts:
- flat_syllables returns correct flat syllable lists (or None for unjoinable).
- build_norwegian_breakdown_spans produces text-identical output to the
  existing build_norwegian_breakdown over the entire existing test corpus.
- Span correctness: for every chunk with a non-None span, the raw syllables
  rejoin to the chunk's source text.
- Phrase round-trip (to_json / from_json) preserves source_word and
  syllable_span, and old JSON without those fields still loads.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.generation.section_builder import build_word_breakdown_spans
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.plugins.languages.no.lexicon_syllables import lexicon_syllable_split
from app.plugins.languages.no.norwegian_breakdown import (
    _INFLECTIONS,
    _NORWEGIAN_VOWELS,
    _compound_buildup_units,
    build_norwegian_breakdown,
    build_norwegian_breakdown_spans,
    flat_syllables,
    load_no_lexicon,
    segment_compound,
)

# ---- Every phrase that build_norwegian_breakdown is called with in the
#      existing test corpus. Kept in sync mechanically, not by good intentions:
#      test_corpus_covers_every_phrase_in_the_oracle_file parses
#      test_norwegian_breakdown.py and fails naming any phrase missing here.

_CORPUS_PHRASES: list[str] = [
    "jeg hadde",
    "etterforskningsteamet",
    "etter",
    "finne",
    "snømann",
    "forskning",
    "kjærlighet",
    "politiet",
    "jeg",
    "",
    "på flyplassen",
    "på plassen",
    "jeg er her",
    "busstasjon",
    "bokklubb",
    "hadde",
    "de lyver",
    "oppklart",
]


# ---- flat_syllables -----------------------------------------------------


@pytest.fixture(scope="session")
def repo_syllabified_wordlist():
    """One pass over the ~50k lexicon, shared by the three invariant sweeps.

    Each of the three whole-wordlist invariants below used to walk the lexicon
    itself, recomputing ``flat_syllables`` (and ``segment_compound``) from
    scratch — three identical passes costing ~40s of a 55s suite
    (``--durations`` profile, 2026-08-31; tunatale-a5p2). They ask DIFFERENT
    questions of the SAME data, so the data is computed once here and each test
    keeps its own assertion and its own failure message.

    Yields ``(word, pieces, morphemes)`` for every entry that is in scope for
    the folds: at least 3 characters, and NOT lexicon-adopted as a whole word
    (the lexicon's boundaries are authoritative, so the folds are not applied
    to it — see the individual docstrings).

    ``flat_syllables`` returning None means the pieces would not rejoin to the
    word. That is a precondition of all three invariants rather than one of
    them, so it is asserted here; a failure legitimately stops all three.
    """
    out: list[tuple[str, list[str], list[str]]] = []
    for word in load_no_lexicon():
        if len(word) < 3:
            continue
        if lexicon_syllable_split(word) is not None:
            continue
        pieces = flat_syllables(word)
        assert pieces is not None, f"flat_syllables({word!r}) does not rejoin"
        out.append((word, pieces, segment_compound(word)))
    return out


class TestFlatSyllables:
    def test_simple_stem(self):
        assert flat_syllables("forskning") == ["fors", "kning"]

    def test_compound(self):
        pieces = flat_syllables("etterforskningsteamet")
        assert pieces is not None
        assert pieces == ["e", "tter", "fors", "knings", "tea", "met"]

    def test_overlap_compound_rejoins(self):
        """s-overlap busstasjon: pieces rejoin despite truncated morpheme."""
        pieces = flat_syllables("busstasjon")
        assert pieces is not None
        assert "".join(pieces) == "busstasjon"

    def test_empty(self):
        assert flat_syllables("") == []

    def test_single_syllable(self):
        assert flat_syllables("jeg") == ["jeg"]

    def test_inflected_stem(self):
        pieces = flat_syllables("plassen")
        assert pieces is not None
        assert pieces == ["pla", "ssen"]
        assert "".join(pieces) == "plassen"

    def test_vowelless_inflection_rides_its_stem(self):
        """A compound whose inflection is a bare consonant yields no bare piece.

        segment_compound("oppklart") is ["opp", "klar", "t"]; the buildup used to
        flatten that verbatim, so span (2,3) was a lone "t" — a chunk sliced out
        of the whole-word render at a CTC-peaky stop. Pieces stay speakable.
        """
        assert flat_syllables("oppklart") == ["opp", "klart"]
        assert flat_syllables("velkommen") == ["vel", "ko", "mmen"]

    def test_no_wordlist_entry_yields_an_unspeakable_chunk(self, repo_syllabified_wordlist):
        """The invariant, over all 50k wordlist entries: every piece has a nucleus.

        A multi-piece split whose pieces don't all contain a vowel puts a bare
        consonant in front of the learner — as text, and as a CTC-sliced burst
        of audio. A *single*-piece result is exempt: a vowel-less acronym (nrk,
        sms, http) is a whole word, not a fragment of one.

        Lexicon-adopted words are exempt: the brief forbids applying
        ``_fold_vowelless_pieces`` to lexicon pieces, so the lexicon's
        boundaries are authoritative even when a piece has no vowel.
        """
        offenders = []
        for word, pieces, _morphemes in repo_syllabified_wordlist:
            if len(pieces) > 1 and any(not set(p) & _NORWEGIAN_VOWELS for p in pieces):
                offenders.append((word, pieces))
        assert offenders == [], f"{len(offenders)} unspeakable chunks, e.g. {offenders[:5]}"

    def test_vowel_only_inflection_never_stranded_inside_a_morpheme(self, repo_syllabified_wordlist):
        """The invariant, over all 50k entries: a vowel-only inflection is never
        stranded behind a consonant-final piece **of its own morpheme**.

        for·klar·e would put a bare-nucleus chunk (``e``) in front of the
        learner — text, and audio sliced out of the whole-word render with no
        onset to cut on, bleeding into a "short re". The stem's final consonant
        rides onto it instead (for·kla·re).

        Scoped to *within a morpheme* deliberately. The fold runs per buildup
        unit, and it must: ``_compound_buildup_units`` yields ``(surface,
        pieces)`` pairs whose pieces rejoin to their surface, and both span
        builders rely on that. Moving a character across a seam would break the
        pairing and desync the text from the audio spans.

        What survives at a seam is not the same defect. In ``genetiske`` the
        spurious split ``gen|etisk|e`` makes the bare ``e`` the *first syllable
        of a middle part*, not an inflection — it only looks like one because
        ``"e" in _INFLECTIONS`` is a string test. Folding there would corrupt the
        morpheme boundary. The real fix for those is in ``segment_compound``,
        which should not have split the word; that is out of scope here, so this
        test pins them as seam-only rather than pretending they are gone.

        Lexicon-adopted words are exempt: the brief forbids applying
        ``_fold_vowel_only_inflections`` to lexicon pieces, so the lexicon's
        boundaries are authoritative even when a vowel-only inflection is
        stranded.
        """
        stranded = []
        for word, pieces, morphemes in repo_syllabified_wordlist:
            # Skip any word whose pieces came from the LEXICON rather than the
            # repo syllabifier — the lexicon's boundaries are authoritative even
            # when they strand a vowel-only inflection (see the docstring above).
            # The lexicon is consulted per WHOLE word for a simplex and per PART
            # for a compound, so both must be checked; the fixture applies the
            # whole-word half, and this is the per-PART half. Testing only the
            # whole word let per-part adoption through and reported 17 false
            # stranders.
            if any(lexicon_syllable_split(surface) is not None for surface, _ in _compound_buildup_units(morphemes)):
                continue
            # Piece indices that begin a buildup unit — the seams the fold cannot
            # and must not cross.
            seam_starts = set()
            if len(morphemes) >= 2:
                idx = 0
                for _, unit_pieces in _compound_buildup_units(morphemes):
                    seam_starts.add(idx)
                    idx += len(unit_pieces)
            for i in range(1, len(pieces)):
                if (
                    set(pieces[i]) <= _NORWEGIAN_VOWELS
                    and pieces[i] in _INFLECTIONS
                    and pieces[i - 1][-1] not in _NORWEGIAN_VOWELS
                    and i not in seam_starts
                ):
                    stranded.append((word, pieces))
        assert stranded == [], f"{len(stranded)} vowel-only inflections stranded inside a morpheme, e.g. {stranded[:5]}"

    def test_non_inflection_all_vowel_pieces_untouched(self, repo_syllabified_wordlist):
        """All-vowel pieces that are NOT inflections keep their slots.

        arbeids·u·ke, and·øy·a, alle·manns·ei·e: a vowel-only piece at a
        compound seam has a consonant to its right only because a morpheme
        boundary between two content stems sits there — moving it would cross
        that boundary. The fold must leave all such pieces exactly alone, so an
        over-broad fix goes red here.

        Lexicon-adopted words are excluded: the fold is not applied to them.
        """
        offenders = []
        words = set()
        for word, pieces, _morphemes in repo_syllabified_wordlist:
            for i in range(1, len(pieces)):
                piece = pieces[i]
                if (
                    set(piece) <= _NORWEGIAN_VOWELS
                    and piece not in _INFLECTIONS
                    and pieces[i - 1][-1] not in _NORWEGIAN_VOWELS
                ):
                    offenders.append((word, pieces))
                    words.add(word)
        assert len(offenders) > 0, "expected some non-inflection all-vowel pieces"
        assert len(words) > 0, "expected some distinct words"

    def test_corpus_words_all_rejoin(self):
        """Every phrase in the test corpus must produce rejoining syllables."""
        for phrase in _CORPUS_PHRASES:
            if not phrase:
                continue
            for word in phrase.split():
                pieces = flat_syllables(word)
                assert pieces is not None, (
                    f"flat_syllables({word!r}) returned None — pieces would not rejoin to '{word}'"
                )
                assert "".join(pieces) == word.lower(), (
                    f"flat_syllables({word!r}) pieces {'+'.join(pieces)} rejoin to {''.join(pieces)!r}, not {word!r}"
                )

    def test_none_on_unjoinable(self, monkeypatch):
        """flat_syllables returns None when pieces do not rejoin."""
        from app.plugins.languages.no import norwegian_breakdown as nb

        orig = nb.syllabify_morpheme

        def broken(word: str) -> list[str]:
            return orig(word) + ["x"]

        monkeypatch.setattr(nb, "syllabify_morpheme", broken)
        from app.plugins.languages.no import lexicon_syllables as ls

        monkeypatch.setattr(ls, "lexicon_syllable_split", lambda _w: None)
        assert flat_syllables("jeg") is None

    def test_compound_per_part_lexicon_adopted_busstasjon(self):
        """busstasjon: both parts match lexicon → adopted syllables."""
        pieces = flat_syllables("busstasjon")
        assert pieces is not None
        assert pieces == ["bus", "sta", "sjon"]

    def test_compound_per_part_lexicon_adopted_skisporet(self):
        """skisporet: both parts match lexicon → adopted syllables.

        The repo syllabifies 'sporet' as ['spo', 'ret'], which happens to
        match the lexicon split.  The lexicon's whole-word split is
        ['ski', 'spo', 'ret'] — the adopted pieces must match it.
        """
        pieces = flat_syllables("skisporet")
        assert pieces is not None
        assert pieces == ["ski", "spo", "ret"]

    def test_compound_per_part_lexicon_adopted_stortinget(self):
        """stortinget: 'tinget' lexicon split matches repo syllables → adopted.

        _compound_buildup_units splits off inflection 'et', syllabifies stem
        'ting' → ['ting'], appends 'et' → ['ting', 'et'].  The lexicon also
        gives ['ting', 'et'] for 'tinget', so the part adopts.
        """
        pieces = flat_syllables("stortinget")
        assert pieces is not None
        assert pieces == ["stor", "ting", "et"]

    def test_compound_single_syllable_parts_keep_going(self):
        """Monosyllabic parts must remain whole-word spans for TTS.

        tunatale-9yd0: forstår is no longer a compound (known word, no ``%``),
        so its syllables come from the whole-word transcription — the retroflex
        cut fo|rstår surfaces (second-order effect of the merge).
        """
        pieces = flat_syllables("forstår")
        assert pieces is not None
        assert pieces == ["fo", "rstår"]

    def test_compound_partial_adoption_undersøke(self):
        """undersøke: 'under' has no lexicon split, 'søke' adopts.

        Partial adoption within one word MUST be allowed.
        """
        pieces = flat_syllables("undersøke")
        assert pieces is not None
        # 'under' lexicon=None → repo ['un', 'der']; 'søke' matches → ['sø', 'ke']
        assert pieces == ["un", "der", "sø", "ke"]

    # ---- seam-guarded whole-word fallback (tunatale-nlhh) -------

    def test_seam_guarded_fallback_boundaries_that_MOVE(self):
        """ORACLE 2.1: whole-word cuts that meet (not cross) a seam are adopted.

        Each of these has a part the lexicon cannot resolve ('sident' in
        pre+sident). The whole word HAS a lexicon split, and every morpheme seam
        falls on one of its cuts — so the split is redistributed back across the
        parts at the seams, fixing the stuck per-part guess. In each row:
        current flat_syllables -> authoritative target.

        ⚠️ 'underkant' USED to be in this list (u|nder|kant) and has GRADUATED
        out of it (tunatale-k318.1). It was only ever here because the part
        'under' could not resolve; now that the most-enunciated tiebreak gives
        it un|der, per-part resolution fires and the fallback is never reached.
        Its new value is asserted below — losing that assertion would let the
        word slide back without anything noticing.
        """
        targets = {
            "deltaker": ["del", "ta", "ker"],
            "profeten": ["pro", "fe", "ten"],
            "lanserer": ["lan", "se", "rer"],
            "plassering": ["pla", "sse", "ring"],
            "etterkommere": ["e", "tter", "ko", "mme", "re"],
        }
        for word, expected in targets.items():
            pieces = flat_syllables(word)
            assert pieces == expected, f"{word}: got {pieces!r}, want {expected!r}"

    def test_underkant_graduated_from_the_fallback_to_per_part(self):
        """ORACLE 2.1 addendum: per-part resolution outranks the seam fallback.

        'under' resolves to un|der since tunatale-k318.1, so under+kant is
        settled by per-part resolution and never consults the whole word's
        u|nde|rkant cut. This is the designed precedence, not a regression: a
        buildup drill speaks the parts separately, so each part should be
        resolved as the word it is.
        """
        assert flat_syllables("underkant") == ["un", "der", "kant"]

    def test_seam_guarded_fallback_boundaries_that_do_NOT_move(self):
        """ORACLE 2.2: words that must NOT move.

        undersøke is the load-bearing one: its whole-word cut u|nde|rsø|ke
        (cuts at 1,4,7) CROSSES the under|søke seam (at 5), so adopting it
        would put nde/rsø rungs that span no morpheme. A change that "fixes"
        undersøke has broken the guard.
        """
        stays = {
            "undersøke": ["un", "der", "sø", "ke"],
            "samarbeid": ["sam", "ar", "beid"],
            "timevis": ["ti", "me", "vis"],
            "president": ["pre", "si", "dent"],
            "hverken": ["hver", "ken"],
            "torsdag": ["tors", "dag"],
            "skygge": ["sky", "gge"],
        }
        for word, expected in stays.items():
            assert flat_syllables(word) == expected, f"{word} must not move"

    # ---- build_norwegian_breakdown_spans text equality oracle ----------------

    def test_per_part_adoption_takes_boundaries_that_DIFFER(self):
        """The discriminating case: adoption must not require agreement.

        Every other per-part test above passes under BOTH a correct
        implementation and one that adopts only when the lexicon agrees with
        the repo syllabifier — because for those words the two coincide.
        'etterforskerens' is the case that separates them: both its parts have
        a lexicon split that DIFFERS from the repo's, which is exactly when
        adopting is worth doing.

            etter       repo et|ter      lexicon e|tter
            forskerens  repo for|sker|ens  lexicon fo|rskerens

        An equality guard here adopts only when adopting changes nothing.
        """
        assert flat_syllables("etterforskerens") == ["e", "tter", "fo", "rskerens"]

    def test_per_part_never_uses_the_whole_word_transcription(self):
        """Morphological units survive; the compound's own split is not consulted.

        The whole-word NST transcription of these encodes connected-speech
        assimilation ACROSS the compound seam (hverandre -> hve|ran|dre,
        forstår -> fo|rstår), which never happens in a drill where the parts
        are spoken separately. Adopting it would be the superseded design.

        tunatale-9yd0: hverandre and forstår are no longer compounds (known
        words, no ``%``), so their whole-word transcription now applies — the
        seam assimilation surfaces, second-order effect of the merge.
        """
        assert flat_syllables("hverandre") == ["hve", "ran", "dre"]
        assert flat_syllables("forstår") == ["fo", "rstår"]
        assert flat_syllables("tyskland") == ["tysk", "land"]


class TestBreakdownSpansTextEquality:
    def test_corpus_covers_every_phrase_in_the_oracle_file(self):
        """``_CORPUS_PHRASES`` must be the WHOLE oracle corpus, not a sample.

        The equality oracle below is only as strong as this list. A comment
        asking the next author to keep it in sync is not a mechanism, so read
        the literals back out of ``test_norwegian_breakdown.py`` and compare.
        Adding a ``build_norwegian_breakdown("…")`` call there without adding
        the phrase here fails HERE, naming the missing phrase.
        """
        oracle_file = Path(__file__).with_name("test_norwegian_breakdown.py")
        tree = ast.parse(oracle_file.read_text(encoding="utf-8"))
        called: set[str] = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "build_norwegian_breakdown"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        assert called, f"no build_norwegian_breakdown string literals found in {oracle_file}"
        assert called - set(_CORPUS_PHRASES) == set(), (
            "phrases exercised in test_norwegian_breakdown.py but missing from "
            f"_CORPUS_PHRASES: {sorted(called - set(_CORPUS_PHRASES))}"
        )

    @pytest.mark.parametrize("phrase", _CORPUS_PHRASES)
    def test_oracle(self, phrase):
        """Text identity guard — must not diverge from the plain breakdown.

        After the inversion, ``build_norwegian_breakdown`` delegates to the
        spans variant by construction, so this property is tautological — and
        that is exactly the point: anyone who re-implements the plain path
        independently will break here.
        """
        texts = [c.text for c in build_norwegian_breakdown_spans(phrase)]
        expected = build_norwegian_breakdown(phrase)
        assert texts == expected, (
            f"build_norwegian_breakdown_spans({phrase!r}).text differs from "
            f"build_norwegian_breakdown({phrase!r}):\n"
            f"  spans:  {texts}\n"
            f"  expected: {expected}"
        )


# ---- Span correctness ----------------------------------------------------


class TestBreakdownSpansCorrectness:
    @pytest.mark.parametrize("phrase", [p for p in _CORPUS_PHRASES if p])
    def test_span_reproduces_chunk_text_from_source_word(self, phrase):
        """``flat_syllables(source_word)[a:b]`` must rejoin to the chunk's text.

        This is the whole contract Stage 3 consumes: it renders ``source_word``
        once and cuts ``[a:b]`` out of that render. The join may differ from
        ``chunk.text`` only by the geminate/overlap doubling of
        :func:`_spoken_syllable` / :func:`_spoken_part` (``et``->``ett``,
        ``bus``->``buss``) — a real orthographic doubling, not a phonetic hint.
        Anything else means the span points somewhere the text did not come from.

        This used to also permit an invented ``de``->``deh`` respelling. Slicing
        removed the need for it and it was the one divergence a learner could
        actually see, since the caption is ``Phrase.text``.
        """
        for chunk in build_norwegian_breakdown_spans(phrase):
            if chunk.span is None:
                continue
            assert chunk.source_word is not None
            flat = flat_syllables(chunk.source_word)
            assert flat is not None, f"span {chunk.span} on unsliceable source_word {chunk.source_word!r}"
            a, b = chunk.span
            assert 0 <= a < b <= len(flat), (
                f"span {chunk.span} out of range for "
                f"flat_syllables({chunk.source_word!r}) = {flat} "
                f"(chunk text {chunk.text!r})"
            )
            raw = "".join(flat[a:b])
            doubled = raw + raw[-1:]
            assert chunk.text in (raw, doubled), (
                f"span {chunk.span} of flat_syllables({chunk.source_word!r}) = {flat} "
                f"rejoins to {raw!r}, which is not {chunk.text!r} nor its geminate doubling"
            )

    @pytest.mark.parametrize("phrase", [p for p in _CORPUS_PHRASES if p])
    def test_source_word_is_a_word_of_the_phrase(self, phrase):
        """``source_word`` must be a word the caller can actually render.

        Regression: compound chunks used to carry the compound *part*
        (``forsknings``, ``teamet``, ``plassen``) with part-local indices.
        Stage 3 renders ``source_word`` and slices it, so a bare morpheme
        there means synthesizing an isolated fragment — reintroducing the
        word-level-G2P bug this workstream exists to fix — and it costs one
        TTS call per part instead of one per word.
        """
        words = phrase.split()
        for chunk in build_norwegian_breakdown_spans(phrase):
            if chunk.span is None:
                continue
            assert chunk.source_word in words, (
                f"chunk {chunk.text!r} of {phrase!r} names source_word "
                f"{chunk.source_word!r}, which is not one of {words}"
            )

    def test_compound_chunks_index_the_whole_word(self):
        """A compound's syllables are spans of the whole compound's render."""
        chunks = build_norwegian_breakdown_spans("etterforskningsteamet")
        assert flat_syllables("etterforskningsteamet") == [
            "e",
            "tter",
            "fors",
            "knings",
            "tea",
            "met",
        ]
        by_text = {(c.text, c.span) for c in chunks if c.span is not None}
        assert ("tea", (4, 5)) in by_text
        assert ("met", (5, 6)) in by_text
        assert ("knings", (3, 4)) in by_text
        assert ("tter", (1, 2)) in by_text
        assert {c.source_word for c in chunks if c.span is not None} == {"etterforskningsteamet"}

    def test_compound_of_monosyllables_is_still_sliceable(self):
        """``snø``/``mann`` are cut from one ``snømann`` render, not resynthesized.

        Each part is monosyllabic, so part-local provenance made both chunks
        whole-word spans of a bare morpheme — which Stage 3 skips, leaving the
        compound entirely unsliced.
        """
        chunks = build_norwegian_breakdown_spans("snømann")
        spans = {(c.text, c.source_word, c.span) for c in chunks if c.span is not None}
        assert ("snø", "snømann", (0, 1)) in spans
        assert ("mann", "snømann", (1, 2)) in spans

    def test_busstasjon_buss_chunk_adopted_span(self):
        """busstasjon: 'buss' chunk must be present (geminate restoration) and
        its span must index the adopted syllables, not the repo ones.

        The acceptance test for per-part lexicon adoption: _spoken_part
        restores the geminate so the learner hears 'buss', and the span
        (0, 1) maps to the adopted syllable 'bus' in flat_syllables.
        """
        chunks = build_norwegian_breakdown_spans("busstasjon")
        adopted = flat_syllables("busstasjon")
        assert adopted == ["bus", "sta", "sjon"]
        spans = {(c.text, c.span) for c in chunks if c.span is not None}
        # 'buss' chunk (geminate-doubled from 'bus') at span (0, 1)
        assert ("buss", (0, 1)) in spans, f"'buss' chunk missing from spans {spans} — geminate restoration broken?"
        # Part chunks at correct adopted-syllable offsets
        assert ("stasjon", (1, 3)) in spans
        assert ("sjon", (2, 3)) in spans
        assert ("sta", (1, 2)) in spans
        assert ("ski", (0, 1)) not in spans  # 'ski' is for skisporet, not busstasjon

    def test_skisporet_adopted_spans(self):
        """skisporet: spans index adopted syllables ['ski', 'spo', 'ret'].

        The repo syllabifies 'sporet' as ['spo', 'ret'], matching the lexicon,
        so the adopted flat_syllables is ['ski', 'spo', 'ret'] — not
        ['ski', 'spor', 'et'].
        """
        chunks = build_norwegian_breakdown_spans("skisporet")
        adopted = flat_syllables("skisporet")
        assert adopted == ["ski", "spo", "ret"]
        spans = {(c.text, c.span) for c in chunks if c.span is not None}
        assert ("ski", (0, 1)) in spans
        assert ("sporet", (1, 3)) in spans
        assert ("ret", (2, 3)) in spans
        assert ("spo", (1, 2)) in spans

    def test_compound_inside_multi_word_phrase_indexes_its_word(self):
        chunks = build_norwegian_breakdown_spans("på flyplassen")
        # Per-part resolution (tunatale-oqxz) resolves the 'plassen' unit
        # against the lexicon as the word it is: /ˈplɑ.sn̩/, so pla|ssen. The
        # standalone word and the unit inside the compound now agree, which the
        # old comment here noted they did NOT — that divergence is gone.
        assert flat_syllables("flyplassen") == ["fly", "pla", "ssen"]
        spans = {(c.text, c.source_word, c.span) for c in chunks if c.span is not None}
        assert ("fly", "flyplassen", (0, 1)) in spans
        assert ("plassen", "flyplassen", (1, 3)) in spans
        assert ("ssen", "flyplassen", (2, 3)) in spans

    def test_source_word_for_non_compound_stem(self):
        """Single-stem word: non-bookend chunks carry source_word."""
        chunks = build_norwegian_breakdown_spans("forskning")
        for c in chunks:
            if c.span is not None:
                assert c.source_word == "forskning"

    def test_multi_word_partials_have_no_source(self):
        """Multi-word chunks (partials) have source_word=None, span=None."""
        chunks = build_norwegian_breakdown_spans("jeg er her")
        for c in chunks:
            if " " in c.text and c.text != "jeg er her":
                assert c.source_word is None
                assert c.span is None

    def test_monosyllabic_word_spans_none(self):
        """Monosyllabic words have span=None on all chunks."""
        chunks = build_norwegian_breakdown_spans("jeg")
        for c in chunks:
            assert c.span is None


# ---- Phrase round-trip ---------------------------------------------------


class TestPhraseProvenanceRoundTrip:
    def test_round_trip_preserves_provenance(self):
        phrase = Phrase(
            text="test",
            voice_id="nb-NO-PernilleNeural",
            language_code="no",
            source_word="test",
            syllable_span=(0, 1),
        )
        lesson = Lesson(
            title="Test",
            language_code="no",
            sections=[],
            key_phrases=[],
        )
        section = Section(
            section_type=SectionType.KEY_PHRASES,
            phrases=[phrase],
        )
        lesson.sections = [section]

        json_str = lesson.to_json()
        restored = Lesson.from_json(json_str)
        restored_p = restored.sections[0].phrases[0]
        assert restored_p.source_word == "test"
        assert restored_p.syllable_span == (0, 1)

    def test_back_compat_without_provenance(self):
        """Old stored JSON without source_word / syllable_span still loads."""
        old_json = json.dumps(
            {
                "title": "Test",
                "language_code": "no",
                "narrator_voice": "en-US-JennyNeural",
                "key_phrases": [],
                "sections": [
                    {
                        "section_type": "key_phrases",
                        "phrases": [
                            {
                                "text": "hei",
                                "voice_id": "nb-NO-PernilleNeural",
                                "language_code": "no",
                                "rate": "+0%",
                                "pitch": "+0Hz",
                                "volume": "+0%",
                                "role": "",
                            },
                        ],
                    },
                ],
                "generation_metadata": {},
            }
        )
        lesson = Lesson.from_json(old_json)
        phrase = lesson.sections[0].phrases[0]
        assert phrase.text == "hei"
        assert phrase.source_word is None
        assert phrase.syllable_span is None

    def test_syllable_span_normalized_from_list(self):
        """syllable_span round-trips as list in JSON but normalizes to tuple."""
        json_str = json.dumps(
            {
                "title": "Test",
                "language_code": "no",
                "narrator_voice": "en-US-JennyNeural",
                "key_phrases": [],
                "sections": [
                    {
                        "section_type": "key_phrases",
                        "phrases": [
                            {
                                "text": "test",
                                "voice_id": "nb-NO-PernilleNeural",
                                "language_code": "no",
                                "rate": "+0%",
                                "pitch": "+0Hz",
                                "volume": "+0%",
                                "role": "",
                                "source_word": "test",
                                "syllable_span": [0, 1],
                            },
                        ],
                    },
                ],
                "generation_metadata": {},
            }
        )
        lesson = Lesson.from_json(json_str)
        phrase = lesson.sections[0].phrases[0]
        assert phrase.syllable_span == (0, 1)
        assert isinstance(phrase.syllable_span, tuple)

    def test_to_json_serializes_provenance(self):
        phrase = Phrase(
            text="ett",
            voice_id="nb-NO-PernilleNeural",
            language_code="no",
            source_word="etter",
            syllable_span=(0, 1),
        )
        data = json.loads(
            Lesson(
                title="T",
                language_code="no",
                sections=[
                    Section(
                        section_type=SectionType.KEY_PHRASES,
                        phrases=[phrase],
                    )
                ],
            ).to_json()
        )
        p = data["sections"][0]["phrases"][0]
        assert p["source_word"] == "etter"
        assert p["syllable_span"] == [0, 1]


class TestWordAbsentFromTheLexicon:
    """A word NST has never heard of keeps its repo syllabification.

    ~7.7% of words are genuinely absent (measured), so this is the ordinary
    path, not an edge case. It also pins the other half of the invariant: with
    no lexicon split there is no lexicon IPA either, so boundaries and phonemes
    stay on the spelling side together rather than crossing.
    """

    def test_absent_simplex_word_uses_repo_boundaries(self) -> None:
        assert lexicon_syllable_split("kvasimuk") is None
        assert flat_syllables("kvasimuk") == ["kva", "si", "muk"]

    def test_absent_simplex_word_still_gets_a_breakdown(self) -> None:
        chunks = build_norwegian_breakdown_spans("kvasimuk")
        assert [c.text for c in chunks] == [
            "kvasimuk",
            "muk",
            "si",
            "simuk",
            "kva",
            "kvasimuk",
        ]
        assert all(c.source_word in (None, "kvasimuk") for c in chunks)


# ---- Trailing punctuation stripping --------------------------------------


class TestTrailingPunctuationStripping:
    """Sentence-final punctuation (., ? !) must be stripped from individual
    words before they enter segment_compound / lexicon_syllable_split /
    syllabify_morpheme, so the lexicon can look them up and syllabification
    is not corrupted.

    Whole-phrase bookend chunks must KEEP their punctuation.
    """

    def test_source_word_strips_trailing_question_mark(self):
        """tunatale-7vxv: 'personen?' -> source_word='personen'."""
        chunks = build_word_breakdown_spans("Hvem er personen?", "no")
        word_chunks = [c for c in chunks if c.source_word is not None]
        assert len(word_chunks) > 0
        for c in word_chunks:
            assert c.source_word == "personen", (
                f"expected source_word='personen', got {c.source_word!r} for chunk text={c.text!r}"
            )

    def test_no_question_mark_in_any_chunk_text(self):
        """The '?' must not appear inside any per-word chunk text."""
        chunks = build_word_breakdown_spans("Hvem er personen?", "no")
        for c in chunks:
            if c.source_word is not None:
                assert "?" not in c.text, f"chunk text {c.text!r} contains '?'"

    def test_whole_phrase_keeps_punctuation(self):
        """Bookend chunks preserve the original punctuation."""
        chunks = build_word_breakdown_spans("Hvem er personen?", "no")
        bookends = [c for c in chunks if c.source_word is None]
        assert any(c.text == "Hvem er personen?" for c in bookends)

    def test_source_word_strips_trailing_period(self):
        """'personen.' -> source_word='personen'."""
        chunks = build_word_breakdown_spans("Jeg fant personen.", "no")
        word_chunks = [c for c in chunks if c.source_word is not None]
        for c in word_chunks:
            assert c.source_word == "personen", f"expected source_word='personen', got {c.source_word!r}"

    def test_source_word_strips_trailing_exclamation(self):
        """'personen!' -> source_word='personen'."""
        chunks = build_word_breakdown_spans("Se, personen!", "no")
        word_chunks = [c for c in chunks if c.source_word is not None]
        for c in word_chunks:
            assert c.source_word == "personen", f"expected source_word='personen', got {c.source_word!r}"

    def test_whole_phrase_strips_trailing_question_mark(self):
        """Single-word phrase: bookend text keeps punctuation."""
        chunks = build_word_breakdown_spans("personen?", "no")
        assert chunks[0].text == "personen?"
        assert chunks[-1].text == "personen?"
        # The inner chunks should have no '?' in source_word or text
        for c in chunks[1:-1]:
            if c.source_word is not None:
                assert c.source_word == "personen"

    def test_internal_apostrophe_or_hyphen_unaffected(self):
        """Words with internal apostrophes or hyphens are unchanged."""
        chunks = build_word_breakdown_spans("kom, jeg", "no")
        # 'kom' has no trailing punctuation here (comma is a separate token after split)
        # but verify the comma-separated word is processed correctly
        for c in chunks:
            if c.source_word is not None:
                assert c.source_word == "kom"

    def test_a_single_word_compound_strips_punctuation_from_source_word_too(self):
        """The single-word branch had its own copy of the tunatale-7vxv bug.

        'flyplassen.' alone in a phrase kept the period in source_word, while
        'pa flyplassen.' stripped it — so the SAME word got different provenance
        depending on whether anything else shared the phrase, and the punctuated
        form silently lost its IPA (lexicon_syllable_split returns None for it).
        The multi-word branch discards the compound bookends, which is why only
        the single-word path showed it.
        """
        alone = build_word_breakdown_spans("flyplassen.", "no")
        in_phrase = build_word_breakdown_spans("på flyplassen.", "no")
        assert {c.source_word for c in alone if c.source_word} == {"flyplassen"}
        assert {c.source_word for c in in_phrase if c.source_word} == {"flyplassen"}
        # ...and the whole-phrase rung still shows the punctuation.
        assert "flyplassen." in {c.text for c in alone if c.source_word is None}

    def test_partial_phrase_rungs_keep_internal_punctuation(self):
        """A partial rung is a PHRASE, so its punctuation survives.

        The first implementation built partials from the punctuation-stripped
        words, which deleted internal sentence breaks: "Ja. I dag har vi en ny
        sak." produced the rung "Ja I dag har vi en ny sak" — a run-on the TTS
        gives one falling contour instead of two. Only chunks BELOW the phrase
        level are stripped (tunatale-7vxv, and tunatale-3q0u's rule that
        nothing above the syllable moves).
        """
        chunks = build_word_breakdown_spans("Ja. I dag har vi en ny sak.", "no")
        texts = [c.text for c in chunks]
        assert "Ja I dag har vi en ny sak" not in texts
        assert "I dag har vi en ny sak." in texts
        assert "ny sak." in texts

    def test_partial_phrase_rungs_keep_internal_comma(self):
        """Same rule for a comma pause, which is a prosodic instruction."""
        chunks = build_word_breakdown_spans("Han kom, og gikk.", "no")
        texts = [c.text for c in chunks]
        assert "Han kom og gikk" not in texts
        assert "kom, og gikk." in texts

    def test_word_rungs_are_stripped_but_phrase_bookends_are_not(self):
        """The two halves of the rule, asserted together so neither drifts."""
        chunks = build_word_breakdown_spans("Hvem er personen?", "no")
        word_rungs = {c.text for c in chunks if c.source_word is not None}
        assert not any("?" in t for t in word_rungs)
        # personen now cuts at the LEXICON's boundaries (pe|rso|nen); with the
        # "?" attached it was syllabified per|so|nen? and never reached the
        # lexicon at all.
        assert {"pe", "rso", "nen"} <= word_rungs
        assert "Hvem er personen?" in {c.text for c in chunks if c.source_word is None}
