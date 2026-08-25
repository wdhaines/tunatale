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
        def plan_chunk(self, source_word: str, span: tuple[int, int], upos: str | None = None) -> str | None: ...

    def test_satisfies_protocol(self, tmp_path: Path) -> None:
        p = _planner(tmp_path)
        assert isinstance(p, self._PhonemePlanner)


# ---------------------------------------------------------------------------
# Oracle tests — each row from the brief's oracle table
# ---------------------------------------------------------------------------


class TestSkisporet:
    """skisporet: repo ['ski','spor','et'] lex ['ˈʃɪː','ˌspuː','rə']."""

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

    def test_etterforskerens_every_span_returns_none(self, tmp_path: Path) -> None:
        """etterforskerens: repo 5 syllables vs lexicon 4 -> every span is None.

        The transcription is the REAL NST row, not an invented one. An earlier
        version of this test used made-up X-SAMPA that raised
        UnknownSegmentError, so it passed at the conversion gate and never
        reached the syllable-count guard it was written to pin — the guard
        survived sabotage untouched. Pull real rows from the extract.
        """
        rows = [("etterforskerens", "NN", '""E$t@r$%fO$s`k@rn`s`', 1)]
        p = _planner(tmp_path, rows)
        # SUB-WORD spans, so the whole-word rule cannot mask the count guard.
        assert p.plan_chunk("etterforskerens", (0, 1)) is None
        assert p.plan_chunk("etterforskerens", (1, 3)) is None
        assert p.plan_chunk("etterforskerens", (2, 4)) is None
        assert p.plan_chunk("etterforskerens", (0, 5)) is None

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
        """skisporet span (1,2) → 'ˌspuː' — secondary stress survives."""
        p = _planner(tmp_path)
        result = p.plan_chunk("skisporet", (1, 2))
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

        assert p.plan_chunk("skisporet", (0, 1)) == "ˈʃɪː"
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
