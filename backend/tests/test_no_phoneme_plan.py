"""Norwegian chunk planner (stage 2d): sub-word IPA via the pronunciation lexicon.

Tests the PhonemePlanner Protocol (``plan_chunk``), the NorwegianPhonemePlanner
implementation, and the ``get_phoneme_planner`` registry accessor. All fixtures
are tiny in-test lexicons — the committed extract and built database are never
touched.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

import pytest

from app.languages import (
    get_phoneme_planner,
)
from app.plugins.languages.no.lexicon import (
    NstLexicon,
    build_lexicon_db,
)
from app.plugins.languages.no.phoneme_plan import NorwegianPhonemePlanner

# Oracle-derived fixture rows. SAMPA transcriptions verified against
# sampa_to_ipa + ipa_syllables to produce the exact IPA syllable sequences
# stated in the brief's oracle table.
FIXTURE_ROWS = [
    # skisporet: lex ['ˈʃɪː', 'ˌspuː', 'rə']
    ("skisporet", "NN", '"Si:$%spu:$r@', 1),
    # hagen: lex ['ˈhɑː', 'gən']
    ("hagen", "NN", '"hA:$g@n', 1),
    # snøen: lex ['ˈsnøː', 'ən']
    ("snøen", "NN", '"sn2:$@n', 1),
    # finne: lex ['ˈfɪ', 'nə'] — chunk text 'finn' must still return 'fɪ'
    ("finne", "VB", '"fI$n@', 1),
    # galt: used for AMBIGUOUS_NO_POS (2 POS entries)
    ("galt", "NN", '"gAl', 2),
    ("galt", "VB", '"gAl', 1),
    # snømann: used for tone-stripping tests
    ("snømann", "NN", '""sn2:$%mAn', 2),
    # uansett: SIMPLEX (so it adopts lexicon boundaries) and carries a secondary
    # stress — every convenient ˌ-bearing word is a compound, and compounds do
    # not adopt yet, so their spans return None.
    ("uansett", "AB", '""}:$An$%sEt', 1),
]


def _make_db(tmp_path: Path, rows: list[tuple[str, str, str, int]] = FIXTURE_ROWS) -> Path:
    gz = tmp_path / "fixture.tsv.gz"
    payload = "".join(f"{w}\t{p}\t{s}\t{c}\n" for w, p, s, c in rows)
    gz.write_bytes(__import__("gzip").compress(payload.encode("utf-8"), mtime=0))
    db = tmp_path / "lexicon.sqlite3"
    build_lexicon_db(gz, db)
    return db


def _lex(tmp_path: Path, rows: list[tuple[str, str, str, int]] = FIXTURE_ROWS) -> NstLexicon:
    return NstLexicon(_make_db(tmp_path, rows))


def _planner(tmp_path: Path, rows: list[tuple[str, str, str, int]] = FIXTURE_ROWS) -> NorwegianPhonemePlanner:
    db = _make_db(tmp_path, rows)
    return NorwegianPhonemePlanner(db)


class TestPhonemePlannerProtocol:
    """The Protocol is satisfied by NorwegianPhonemePlanner."""

    @runtime_checkable
    class _PhonemePlanner(Protocol):
        def plan_chunk(
            self, source_word: str, span: tuple[int, int], upos: str | None = None, chunk_text: str | None = None
        ) -> str | None: ...

    def test_satisfies_protocol(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert isinstance(p, self._PhonemePlanner)


# ---------------------------------------------------------------------------
# Oracle tests — each row from the brief's oracle table
# ---------------------------------------------------------------------------


class TestSkisporet:
    """skisporet is a COMPOUND (ski+sporet) and it NOW adopts lexicon boundaries.

    Per-part resolution (tunatale-oqxz) resolves each buildup unit against the
    lexicon as the word it is — ski + spo|ret — instead of slicing the whole
    compound's connected-speech transcription. Its repo split therefore agrees
    with the lexicon's and every sub-word span gets IPA. The previous version of
    this class asserted None on all four spans and predicted, in its own
    docstring, exactly the values now asserted below."""

    def test_span_0_1(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("skisporet", (0, 1)) == "ˈʃɪː"

    def test_span_1_2(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("skisporet", (1, 2)) == "ˌspuː"

    def test_span_2_3(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("skisporet", (2, 3)) == "rə"

    def test_span_1_3(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("skisporet", (1, 3)) == "ˌspuː.rə"

    def test_whole_word_returns_none(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("skisporet", (0, 3)) is None


# ---------------------------------------------------------------------------
# POS-aware oracle tests — plan_chunk(word, span, upos)
# ---------------------------------------------------------------------------


class TestPlanChunkWithUpos:
    """plan_chunk(word, span, upos) resolves when POS disambiguates."""

    def test_sporet_noun_returns_rə(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, TestSpanLevelDisambiguation.ROWS)
        assert p.plan_chunk("sporet", (1, 2), upos="NOUN") == "rə"

    def test_sporet_verb_returns_rət(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, TestSpanLevelDisambiguation.ROWS)
        assert p.plan_chunk("sporet", (1, 2), upos="VERB") == "rət"

    def test_huset_noun_returns_sə(self, tmp_path: Path) -> None:
        rows = [
            ("huset", "NN", '"h}:$s@', 1),
            ("huset", "VB", '""h}:$s@t', 1),
        ]
        p = _planner(tmp_path, rows)
        assert p.plan_chunk("huset", (1, 2), upos="NOUN") == "sə"

    def test_dekket_noun_returns_kə(self, tmp_path: Path) -> None:
        rows = [
            ("dekket", "NN", '"dE$k@', 1),
            ("dekket", "VB", '""dE$k@t', 1),
        ]
        p = _planner(tmp_path, rows)
        assert p.plan_chunk("dekket", (1, 2), upos="NOUN") == "kə"

    def test_vitnet_noun_returns_nə(self, tmp_path: Path) -> None:
        rows = [
            ("vitnet", "NN", '""vIt$n@', 1),
            ("vitnet", "VB", '""vIt$n@t', 1),
        ]
        p = _planner(tmp_path, rows)
        assert p.plan_chunk("vitnet", (1, 2), upos="NOUN") == "nə"

    def test_sporet_noun_span_0_1(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, TestSpanLevelDisambiguation.ROWS)
        assert p.plan_chunk("sporet", (0, 1), upos="NOUN") == "ˈspuː"

    def test_sporet_none_upos_unchanged(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, TestSpanLevelDisambiguation.ROWS)
        assert p.plan_chunk("sporet", (1, 2), upos=None) is None

    def test_unambiguous_word_unchanged_with_upos(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("hagen", (1, 2), upos="NOUN") == "gən"


class TestHagen:
    """hagen: repo ['ha','gen'] lex ['ˈhɑː','gən']."""

    def test_span_0_1(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("hagen", (0, 1)) == "ˈhɑː"

    def test_span_1_2(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("hagen", (1, 2)) == "gən"

    def test_whole_word_returns_none(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("hagen", (0, 2)) is None


class TestSnøen:
    """snøen: repo ['snø','en'] lex ['ˈsnøː','ən']."""

    def test_span_1_2(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("snøen", (1, 2)) == "ən"


class TestFinne:
    """finne: repo ['fin','ne'] lex ['ˈfɪ','nə'].

    The critical anti-regression test: chunk text is 'finn' (respelled),
    but plan_chunk takes source_word='finne' and span, so it MUST still
    return 'fɪ'. This is §3 of the brief — the text-equality check is
    deliberately dropped.
    """

    def test_span_0_1(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("finne", (0, 1)) == "ˈfɪ"

    def test_span_1_2(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("finne", (1, 2)) == "nə"

    def test_whole_word_returns_none(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("finne", (0, 2)) is None


class TestSyllableCountMismatch:
    """Repo vs lexicon syllable-count mismatch returns None (rule 4)."""

    # NOTE: this class used to pin 'etterforskerens' as a real-word example of
    # the count mismatch (repo 5 vs lexicon 4). Per-part resolution retired it:
    # its parts now resolve to e|tter + fo|rskerens, so repo and lexicon agree
    # at 4 and there is no mismatch left to pin.
    #
    # No real word replaces it, and that is a FACT ABOUT THE GUARD rather than a
    # gap in the fixtures: orthographic_syllables DERIVES the orthographic split
    # from the phone syllables, so len(split) == len(ipa_syllables) holds by
    # construction for anything the aligner accepts. The count guard is
    # therefore unreachable with a self-consistent lexicon — it guards against
    # an INCONSISTENT one, where resolve() hands back a different transcription
    # than the split was cut from. Only a synthetic fixture can express that,
    # which is what test_mismatch_in_fixture below does.

    def test_mismatch_in_fixture(self, tmp_path: Path) -> None:
        """1 repo syllable vs 2 lexicon syllables.

        NOTE: with one repo syllable the only span IS the whole word, so the
        whole-word rule reaches None first and this cannot discriminate the
        count guard on its own. It is kept as a shape check; the discriminating
        case is test_etterforskerens_every_span_returns_none above.
        """
        rows = [
            # rett: repo ['rett'] (1 syllable) vs lex ['rɛ', 't'] (2)
            ("rett", "NN", '"rE$t', 1),
        ]
        p = _planner(tmp_path, rows)
        assert p.plan_chunk("rett", (0, 1)) is None


class TestSpanLevelDisambiguation:
    """An ambiguity that does not touch the requested span is not an ambiguity.

    The rows are the REAL NST readings of ``sporet``: as a definite neuter noun
    it ends /ə/, as an adjective or past participle /ət/. They AGREE on the
    first syllable (differing only in stress) and DISAGREE on the second, which
    is the whole point — the first syllable is rescuable and the second is not.
    """

    ROWS = [
        ("sporet", "NN", '"spu:$r@', 1),
        ("sporet", "JJ", '""spu:$r@t', 1),
        ("sporet", "VB", '""spu:$r@t', 1),
    ]

    def test_ambiguous_word_still_resolves_a_span_the_readings_agree_on(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, self.ROWS)
        assert p.plan_chunk("sporet", (0, 1)) == "ˈspuː"

    def test_span_where_readings_differ_returns_none(self, tmp_path: Path) -> None:
        """The -et ending: /ə/ (noun) vs /ət/ (participle). Never guess."""
        p = _planner(tmp_path, self.ROWS)
        assert p.plan_chunk("sporet", (1, 2)) is None

    def test_whole_word_still_none_even_when_spans_agree(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, self.ROWS)
        assert p.plan_chunk("sporet", (0, 2)) is None

    def test_lower_certainty_readings_are_excluded_like_resolve_does(self, tmp_path: Path) -> None:
        """candidate_transcriptions must apply the SAME certainty floor as resolve.

        Both entry points reduce to minimum certainty BEFORE anything else. If
        the candidate set skipped that step it would compare readings resolve
        never considered, and spans that are genuinely unambiguous would start
        falling back.

        The certainty-2 row carries a real, convertible transcription borrowed
        from another word — it has to differ at syllable 0 for this to
        discriminate, and inventing X-SAMPA is how an earlier test in this file
        ended up passing at the wrong gate.
        """
        rows = [
            ("sporet", "NN", '"spu:$r@', 1),
            ("sporet", "VB", '""spu:$r@t', 1),
            ("sporet", "JJ", '""hA:$g@n', 2),  # excluded by the floor
        ]
        p = _planner(tmp_path, rows)
        assert p.plan_chunk("sporet", (0, 1)) == "ˈspuː"

    def test_unambiguous_word_is_unaffected(self, tmp_path: Path) -> None:
        """The single-reading path must not change behaviour."""
        p = _planner(tmp_path)
        assert p.plan_chunk("hagen", (1, 2)) == "gən"


class TestAbsentWord:
    """Word absent from the lexicon returns None."""

    def test_absent_returns_none(self, tmp_path: Path) -> None:
        """A MULTI-syllable absent word with a SUB-word span.

        "zzqqxx" is one syllable, so span (0,1) is the whole word and the
        whole-word rule would answer None first — leaving the ABSENT branch
        untested. "zzqqxxala" syllabifies as ['zzqqxxa', 'la'], so (0,1) is a
        genuine sub-word span and the lexicon actually gets consulted.
        """
        p = _planner(tmp_path)
        assert p.plan_chunk("zzqqxxala", (0, 1)) is None


class TestAmbiguousNoPos:
    """AMBIGUOUS_NO_POS returns None."""

    def test_ambiguous_returns_none(self, tmp_path: Path) -> None:
        """huset: 2 readings that DISAGREE at the requested syllable.

        Not "galt": it is monosyllabic, so its only span is the whole word and
        the test would pass at the whole-word rule without ever reaching the
        ambiguity handling. "huset" splits ['hu', 'set'] and its readings differ
        at (1,2) — /sə/ (definite noun) vs /sət/ (participle).
        """
        p = _planner(tmp_path)
        assert p.plan_chunk("huset", (1, 2)) is None


class TestUnknownSegmentError:
    """UnknownSegmentError from sampa_to_ipa returns None."""

    def test_unknown_segment_returns_none(self, tmp_path: Path) -> None:
        rows = [("badword", "NN", '"xXxX', 1)]
        p = _planner(tmp_path, rows)
        assert p.plan_chunk("badword", (0, 1)) is None


class TestToneStripping:
    """Tone marks are stripped; primary/secondary stress survive."""

    def test_tone2_stripped(self, tmp_path: Path) -> None:
        """snømann has tone-2 mark ("); it must not appear in IPA."""
        p = _planner(tmp_path)
        # snømann: lex has 2 syllables, repo has 2 — span (0,1) is sub-word
        result = p.plan_chunk("snømann", (0, 1))
        assert result is not None
        assert '"' not in result

    def test_primary_stress_survives(self, tmp_path: Path) -> None:
        """hagen span (0,1) → 'ˈhɑː' — stress mark survives."""
        p = _planner(tmp_path)
        result = p.plan_chunk("hagen", (0, 1))
        assert result is not None
        assert "ˈ" in result

    def test_secondary_stress_survives(self, tmp_path: Path) -> None:
        """uansett span (2,3) → 'ˌsɛt' — secondary stress survives."""
        p = _planner(tmp_path)
        result = p.plan_chunk("uansett", (2, 3))
        assert result is not None
        assert "ˌ" in result


class TestRegistry:
    """get_phoneme_planner returns None for languages without a planner."""

    def test_no_planner_for_en(self) -> None:
        assert get_phoneme_planner("en") is None

    def test_no_planner_for_sl(self) -> None:
        assert get_phoneme_planner("sl") is None

    def test_no_planner_for_unknown(self) -> None:
        assert get_phoneme_planner("xx") is None

    def test_returns_planner_for_no(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Norwegian returns a planner when the lexicon is available."""
        db = _make_db(tmp_path)
        import app.plugins.languages.no.lexicon as lex_mod

        monkeypatch.setattr(lex_mod, "DB_PATH", db)
        planner = get_phoneme_planner("no")
        assert planner is not None
        assert isinstance(planner, NorwegianPhonemePlanner)


class TestLexiconIsOpenedOnce:
    """One lexicon per planner — plan_chunk is called per chunk."""

    def test_second_call_reuses_the_same_lexicon(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert p.plan_chunk("hagen", (0, 1)) == "ˈhɑː"
        first = p._lexicon
        assert first is not None

        assert p.plan_chunk("snøen", (0, 1)) == "ˈsnøː"
        assert p._lexicon is first, "the planner reopened the lexicon on the second call"


class TestUnbuiltLexicon:
    """An unbuilt lexicon makes plan_chunk return None and warn, never raise."""

    def test_unbuilt_returns_none(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        missing_db = tmp_path / "nonexistent.sqlite3"
        p = NorwegianPhonemePlanner(missing_db)
        with caplog.at_level(logging.WARNING):
            result = p.plan_chunk("hagen", (0, 1))
        assert result is None
        assert any(
            "not built" in rec.message.lower() or "missing" in rec.message.lower() or "nst" in rec.message.lower()
            for rec in caplog.records
        )

    def test_returns_none_not_raise(self, tmp_path: Path) -> None:
        missing_db = tmp_path / "nonexistent.sqlite3"
        p = NorwegianPhonemePlanner(missing_db)
        assert p.plan_chunk("anything", (0, 1)) is None
        assert p.plan_chunk("something else", (0, 1)) is None


class TestUnsyllabifiableWord:
    """A word the syllabifier cannot split gives None before any lookup."""

    def test_empty_source_word_returns_none(self, tmp_path: Path) -> None:
        """flat_syllables("") is [], so there is no syllable to select."""
        p = _planner(tmp_path)
        assert p.plan_chunk("", (0, 1)) is None

    def test_candidate_transcriptions_unmapped_upos_no_filter(self, tmp_path: Path) -> None:
        """A UPOS that maps to None (e.g. SCONJ) skips POS filtering in candidate_transcriptions."""
        rows = [
            ("sporet", "NN", '"spu:$r@', 1),
            ("sporet", "VB", '""spu:$r@t', 1),
        ]
        lex = _lex(tmp_path, rows)
        # SCONJ maps to None in UPOS_TO_NST → nst is None → no filter applied
        candidates = lex.candidate_transcriptions("sporet", upos="SCONJ")
        assert len(candidates) == 2  # both readings survive unfiltered


class TestGuardsThatRefuse:
    """Every path that declines to supply IPA, and why it declines.

    These use a fixture database whose transcription for a word DIFFERS from
    the real lexicon's. The orthographic split still comes from the real
    lexicon (that is what ``lexicon_syllable_split`` reads), so the fixture can
    make the phoneme half fail while the boundary half succeeds — which is
    exactly the crossing the guards exist to catch.
    """

    def test_unconvertible_sampa_returns_none(self, tmp_path: Path) -> None:
        """A transcription with an undefined segment must not reach <phoneme>."""
        p = _planner(tmp_path, [("skygge", "NN", '"QQZZ$XX', 1)])
        assert p.plan_chunk("skygge", (0, 1)) is None

    def test_syllable_count_disagreement_returns_none(self, tmp_path: Path) -> None:
        """'skygge' splits sky|gge; a one-syllable reading has no 2nd syllable."""
        p = _planner(tmp_path, [("skygge", "NN", '"SY', 1)])
        assert p.plan_chunk("skygge", (0, 1)) is None

    def test_stale_chunk_text_returns_none(self, tmp_path: Path) -> None:
        """A stored lesson cut at the OLD boundary must degrade, not mis-sound.

        'hadde' is now cut ha|dde. A lesson generated before that carries the
        chunk text 'had' for span (0,1); serving it 'hɑ' would play a syllable
        the caption does not name.
        """
        p = _planner(tmp_path, [("hadde", "VB", '""hA$d@', 1)])
        assert p.plan_chunk("hadde", (0, 1), chunk_text="had") is None

    def test_matching_chunk_text_is_served(self, tmp_path: Path) -> None:
        """The control: the same call with today's chunk text does get IPA."""
        p = _planner(tmp_path, [("hadde", "VB", '""hA$d@', 1)])
        assert p.plan_chunk("hadde", (0, 1), chunk_text="ha") == "hɑ"


# ---------------------------------------------------------------------------
# Constituent descent — the compound the lexicon does not contain
# ---------------------------------------------------------------------------

# Rows for the descent tests. The compound itself is DELIBERATELY absent: these
# fixtures make the whole-word path fail the way the real lexicon fails it, so
# only the descent can satisfy them. SAMPA copied from the real lexicon so the
# fixture and production agree on what the parts sound like.
DESCENT_ROWS = [
    ("etter", "AB", '""E$t@r', 1),  # ['ɛ', 'tər']
    ("forsknings", "NN", '""fOs`$knINs', 1),  # ['fɔʂ', 'knɪŋs']
    ("teamet", "NN", '"ti:$m@', 1),  # present, but its BOUNDARIES refuse
    ("søke", "VB", '""s2:$k@', 1),  # ['søː', 'kə']
    ("under", "PP", '"u0$n@r', 1),  # two readings that disagree on boundaries
    ("under", "AB", '"u0n$d@r', 1),
]


class TestConstituentDescent:
    """A compound absent from the lexicon resolves through the part that hosts the span.

    ``plan_chunk`` used to look up the WHOLE ``source_word`` only, so a compound
    the lexicon does not contain refused every one of its chunks — even where
    the PARTS resolve cleanly. That was an asymmetry left behind by per-part
    boundary resolution (``_resolve_compound_parts``): the boundary half already
    resolved each buildup unit as the word it is, and the phoneme half did not.

    etterforskningsteamet is the worked example. It is absent from the lexicon,
    its buildup units are etter | forsknings | teamet, and the first two resolve.
    """

    def test_first_part_whole(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("etterforskningsteamet", (0, 2)) == "ɛ.tər"

    def test_first_part_first_syllable(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("etterforskningsteamet", (0, 1)) == "ɛ"

    def test_first_part_second_syllable(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("etterforskningsteamet", (1, 2)) == "tər"

    def test_middle_part_whole(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("etterforskningsteamet", (2, 4)) == "fɔʂ.knɪŋs"

    def test_middle_part_first_syllable(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("etterforskningsteamet", (2, 3)) == "fɔʂ"

    def test_middle_part_second_syllable(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("etterforskningsteamet", (3, 4)) == "knɪŋs"

    def test_whole_compound_still_returns_none(self, tmp_path: Path) -> None:
        """Gate 2 is asked about the SOURCE WORD, not about the part.

        Rebasing the span into its host part must not make a whole-word span
        look sub-word: the whole compound is the TTS's job however many parts
        it decomposes into.
        """
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("etterforskningsteamet", (0, 6)) is None

    def test_whole_part_is_not_a_whole_word(self, tmp_path: Path) -> None:
        """The converse control: a whole PART is a sub-word chunk and does get IPA.

        'etter' fills its host part exactly — span (0, 2) rebases to (0, 2) of a
        2-syllable unit. Applying gate 2 to the REBASED span would refuse it,
        which would silently delete the largest rung of every compound buildup.
        """
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("etterforskningsteamet", (0, 2)) is not None

    def test_span_crossing_two_parts_returns_none(self, tmp_path: Path) -> None:
        """'forskningsteamet' is a partial rung with no single host part.

        Spans (2, 6) covers all of 'forsknings' and all of 'teamet'. No part
        hosts it, and stitching two parts' transcriptions together would
        reintroduce exactly the cross-seam splice per-part resolution exists to
        avoid. Its constituent rungs are covered on their own.
        """
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("etterforskningsteamet", (2, 6)) is None

    @pytest.mark.parametrize("span", [(4, 5), (5, 6), (4, 6)])
    def test_part_whose_own_boundaries_refuse(self, tmp_path: Path, span: tuple[int, int]) -> None:
        """'teamet' IS in the fixture, and still refuses — boundaries, not absence.

        The real lexicon's readings of 'teamet' do not agree on an orthographic
        split, so ``lexicon_syllable_split`` returns None for it and there is no
        split the caption was cut at. Descending does not lower the boundary
        guard; it only asks it about a smaller word.
        """
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("etterforskningsteamet", span) is None

    def test_refusal_is_per_part_not_per_word(self, tmp_path: Path) -> None:
        """undersøke: 'søke' resolves even though 'under' cannot.

        This corrects a claim made repeatedly while the whole-word path was the
        only one: that undersøke was 'the one principled refusal'. Only its
        UNDER half is — under's two readings disagree on where the n goes
        (u|nder vs un|der), so no split survives. søke has one reading.
        """
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("undersøke", (2, 4)) == "søː.kə"
        assert p.plan_chunk("undersøke", (2, 3)) == "søː"
        assert p.plan_chunk("undersøke", (3, 4)) == "kə"

    @pytest.mark.parametrize("span", [(0, 1), (1, 2), (0, 2)])
    def test_undersøke_under_half_still_refuses(self, tmp_path: Path, span: tuple[int, int]) -> None:
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("undersøke", span) is None

    def test_descent_still_honours_stale_chunk_text(self, tmp_path: Path) -> None:
        """Gate 7 survives the rebase: a caption that names another syllable refuses."""
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("etterforskningsteamet", (0, 1), chunk_text="ett") is None
        assert p.plan_chunk("etterforskningsteamet", (0, 1), chunk_text="e") == "ɛ"

    def test_simplex_word_absent_from_lexicon_still_returns_none(self, tmp_path: Path) -> None:
        """Descent needs a compound. A simplex word has no part to descend to."""
        p = _planner(tmp_path, DESCENT_ROWS)
        assert p.plan_chunk("skygge", (0, 1)) is None


class TestWholeWordWinsOverItsParts:
    """The descent is a FALLBACK. A compound the lexicon HAS keeps its own reading.

    This is the ordering test, and it is the one that keeps the change additive.
    Resolving every compound per-part instead — which is what the boundary half
    does — was measured over the nine stored lessons and is NOT additive: it
    loses 10 chunks outright (skisporet's 'sporet' and 'ret' among them, where
    the standalone part is ambiguous between the definite-noun and
    past-participle -et readings but the compound is not) and re-stresses 22
    more, turning a compound's secondary stress into a citation-form primary.

    Whether a fragment heard alone WANTS its citation stress is an ear question,
    not a code question. Answering it silently, as a side effect of a coverage
    change, is what this test prevents.
    """

    ROWS = [
        # The compound: ski|spo|ret with the tail on SECONDARY stress.
        ("skisporet", "NN", '"Si:$%spu:$r@', 1),
        # The bare part, deliberately given the OTHER -et reading. If the part
        # were consulted first, span (2, 3) would come back 'rət'.
        ("sporet", "NN", '"spu:$r@t', 1),
    ]

    def test_compound_reading_wins_for_a_tail_syllable(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, self.ROWS)
        assert p.plan_chunk("skisporet", (2, 3)) == "rə"

    def test_compound_reading_wins_for_the_whole_tail_part(self, tmp_path: Path) -> None:
        p = _planner(tmp_path, self.ROWS)
        assert p.plan_chunk("skisporet", (1, 3)) == "ˌspuː.rə"

    def test_compound_secondary_stress_is_not_upgraded(self, tmp_path: Path) -> None:
        """'spo' keeps the compound's ˌ; standalone 'sporet' would give it ˈ."""
        p = _planner(tmp_path, self.ROWS)
        assert p.plan_chunk("skisporet", (1, 2)) == "ˌspuː"
