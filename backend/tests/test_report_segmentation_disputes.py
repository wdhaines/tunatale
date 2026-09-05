"""Unit tests for scripts/report_segmentation_disputes.py.

The tally at ``--limit 4000`` and the pinned rows below are the measured
acceptance oracle (brief-bp-segmentation-report-and-voice-chip-2026-09-04.md
§ Task A, re-measured 2026-09-05 after tunatale-9yd0) — they were verified
against the committed wordlist and ``nst_lexicon.sqlite3``, not regenerated
from the script's own output. The skip buckets
(``parts_unalignable``, ``syll_unalignable``, ``lexicon_miss``) are
constructed with small monkeypatched inputs, not by scanning the wordlist.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import sys
import unicodedata
from collections import Counter
from pathlib import Path

# Allow importing from scripts/ one level up.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import pytest  # noqa: E402
import report_segmentation_disputes as repmod  # noqa: E402
from report_segmentation_disputes import (  # noqa: E402
    DisputeRow,
    boundaries,
    build_report,
    classify,
    main,
    print_rows,
    print_tally,
    read_words,
)

# Hand-verified stem_disputed rows: (parts, syllables, disputed stem offsets).
# tunatale-9yd0 shrank the disputed population: ekspert, forstår, hverandre
# and billetter are now single_part (known words whose readings carry no ``%``
# are not prosodic compounds), so only tyskland and spørsmålet remain.
_ORACLE_ROWS: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[int, ...]]] = {
    "tyskland": (("tysk", "land"), ("ty", "skland"), (4,)),
    "spørsmålet": (("spørs", "mål", "et"), ("spø", "rsmå", "let"), (5,)),
}

# The acceptance tally at --limit 4000, re-measured 2026-09-05 after
# tunatale-9yd0 (see the docstring on test_acceptance_tally for why it shrank).
_ORACLE_TALLY = {
    "single_part": 3783,
    "parts_unalignable": 0,
    "lexicon_miss": 18,
    "syll_unalignable": 0,
    "compared": 199,
    "fully_agree": 93,
    "only_infl_disputed": 60,
    "stem_disputed": 46,
}


class TestBoundaries:
    def test_cumulative_sums_with_final_offset_removed(self):
        assert boundaries(["eks", "per", "t"]) == [3, 6]
        assert boundaries(["snø", "mann"]) == [3]

    def test_single_piece_has_no_boundary(self):
        assert boundaries(["hagen"]) == []


class TestClassify:
    @pytest.mark.parametrize("word", sorted(_ORACLE_ROWS))
    def test_pinned_stem_disputed_rows(self, word: str) -> None:
        expected = _ORACLE_ROWS[word]
        bucket, row = classify(word)
        assert bucket == "stem_disputed"
        assert row is not None
        assert (row.parts, row.syllables, row.disputed_offsets) == expected

    def test_hagen_is_a_single_part_and_never_compared(self) -> None:
        assert classify("hagen") == ("single_part", None)

    def test_oppdaget_disputes_only_at_the_inflectional_cut(self) -> None:
        bucket, row = classify("oppdaget")
        assert bucket == "only_infl_disputed"
        assert row is not None
        assert row.parts == ("opp", "dag", "et")
        assert row.syllables == ("opp", "da", "get")
        assert row.disputed_offsets == ()

    def test_snomann_part_boundaries_match_syllable_boundaries(self) -> None:
        bucket, row = classify("snømann")
        assert bucket == "fully_agree"
        assert row is not None
        assert row.disputed_offsets == ()
        assert row.parts == ("snø", "mann")
        assert row.syllables == ("snø", "mann")

    def test_parts_unalignable_when_parts_do_not_join_back(self, monkeypatch) -> None:
        monkeypatch.setattr(repmod, "segment_compound", lambda word: [word, "x"])
        assert classify("ekspert") == ("parts_unalignable", None)

    def test_lexicon_miss_when_word_absent_from_lexicon(self, monkeypatch) -> None:
        # tunatale-9yd0: ekspert is single_part now, so a split word is needed
        # to reach the lexicon consult — tyskland still splits.
        monkeypatch.setattr(repmod, "lexicon_syllable_split", lambda word: None)
        assert classify("tyskland") == ("lexicon_miss", None)

    def test_syll_unalignable_when_syllables_do_not_join_back(self, monkeypatch) -> None:
        # parts join to the word; the patched syllables lose a letter.
        monkeypatch.setattr(repmod, "segment_compound", lambda word: ["ek", "spert"])
        monkeypatch.setattr(repmod, "lexicon_syllable_split", lambda word: ["ek", "spertx"])
        assert classify("ekspert") == ("syll_unalignable", None)

    def test_stem_dispute_even_when_no_inflection_was_peeled(self) -> None:
        # tunatale-9yd0: forstår is single_part now; tyskland has the same
        # no-peeled-inflection shape (its last part "land" is not an ending).
        bucket, row = classify("tyskland")
        assert bucket == "stem_disputed"
        assert row is not None
        assert row.disputed_offsets == (4,)


class TestBuildReport:
    def test_counts_each_bucket_and_keeps_stem_disputed_rows(self) -> None:
        # tunatale-9yd0: ekspert/forstå are single_part now; tyskland and
        # spørsmålet are the surviving pinned stem-disputed rows.
        words = ["tyskland", "hagen", "snømann", "oppdaget", "spørsmålet"]
        counts, rows = build_report(words)
        assert counts["stem_disputed"] == 2  # tyskland, spørsmålet
        assert counts["single_part"] == 1  # hagen
        assert counts["fully_agree"] == 1  # snømann
        assert counts["only_infl_disputed"] == 1  # oppdaget
        assert [r.word for r in rows] == ["tyskland", "spørsmålet"]

    def test_skip_buckets_never_leave_a_row(self, monkeypatch) -> None:
        monkeypatch.setattr(repmod, "segment_compound", lambda word: [word, "x"])
        counts, rows = build_report(["ekspert"])
        assert counts["parts_unalignable"] == 1
        assert rows == []


class TestPrintTally:
    def test_compared_is_derived_not_double_counted(self, capsys) -> None:
        counts = Counter({"fully_agree": 2, "only_infl_disputed": 1, "stem_disputed": 1, "single_part": 5})
        print_tally(counts)
        table = dict(
            line.split("\t")
            for line in capsys.readouterr().out.splitlines()
            if "\t" in line and line.split("\t")[0] != "word"
        )
        assert int(table["compared"]) == 4
        assert int(table["fully_agree"]) + int(table["only_infl_disputed"]) + int(table["stem_disputed"]) == 4
        assert int(table["single_part"]) == 5


class TestPrintRows:
    def test_tsv_shape_with_dash_and_plus_joins(self, capsys) -> None:
        row = DisputeRow(word="ekspert", parts=("eks", "per", "t"), syllables=("ek", "spert"), disputed_offsets=(3,))
        print_rows([row])
        lines = capsys.readouterr().out.splitlines()
        assert lines[0] == "word\tparts\tsyllables\tdisputed_stem_offsets"
        assert lines[1] == "ekspert\teks+per+t\tek-spert\t[3]"

    def test_no_stem_disputes_is_explicit_not_silent(self, capsys) -> None:
        print_rows([])
        assert capsys.readouterr().out.strip() == "stem_disputed: none"


class TestReadWords:
    def test_skips_comments_and_blanks_and_honours_limit(self, tmp_path) -> None:
        wl = tmp_path / "wl.txt"
        wl.write_text("# header\n\nen\nog\n\net\n", encoding="utf-8")
        assert read_words(wl, 2) == ["en", "og"]
        assert read_words(wl, 1) == ["en"]
        assert read_words(wl, 100) == ["en", "og", "et"]

    def test_a_non_positive_limit_reads_nothing(self, tmp_path) -> None:
        """The bound is checked before the append, so 0 means 0 — not 1.

        Regression: the original form appended first and broke on
        ``len(words) >= limit``, so ``--limit 0`` returned one word.
        """
        wl = tmp_path / "wl.txt"
        wl.write_text("en\nog\n", encoding="utf-8")
        assert read_words(wl, 0) == []
        assert read_words(wl, -5) == []

    def test_a_decomposed_wordlist_is_normalized_to_nfc(self, tmp_path) -> None:
        """A decomposed source must not silently read as "nothing to compare".

        ``spørsmålet`` written as ``a`` + U+030A is 11 code points, not 10.
        Without normalization every offset past the accent shifts,
        ``segment_compound`` matches no stem, and the word lands in
        ``single_part`` — a clean negative that looks exactly like a corpus
        with no compounds. tunatale-9yd0 retired the previous witness
        (``forstår``): both its forms now land in ``single_part``.
        """
        decomposed = unicodedata.normalize("NFD", "spørsmålet")
        assert len(decomposed) == 11
        wl = tmp_path / "wl.txt"
        wl.write_text(decomposed + "\n", encoding="utf-8")

        (word,) = read_words(wl, 10)
        assert word == "spørsmålet"
        assert len(word) == 10
        assert classify(word)[0] == "stem_disputed"
        assert classify(decomposed)[0] == "single_part"


class TestMain:
    def test_runs_end_to_end_on_a_small_wordlist(self, tmp_path, capsys) -> None:
        wl = tmp_path / "wl.txt"
        wl.write_text("# header\ntyskland\nhagen\nsnømann\noppdaget\n", encoding="utf-8")
        assert main(["--wordlist", str(wl)]) == 0
        out = capsys.readouterr().out
        assert "stem_disputed\t1" in out
        assert "only_infl_disputed\t1" in out
        assert "fully_agree\t1" in out
        assert "tyskland\ttysk+land\tty-skland\t[4]" in out

    def test_missing_wordlist_is_an_error(self, tmp_path, capsys) -> None:
        assert main(["--wordlist", str(tmp_path / "nope.txt")]) == 1
        assert "FAIL: no such wordlist" in capsys.readouterr().err


class TestPinned4000Tally:
    def test_acceptance_tally(self, capsys) -> None:
        """The full-corpus scan the oracle was measured on (fast: less than a
        second, ~285 lexicon lookups over the committed 44 MB database).

        The tally is EXPECTED to be smaller than the 2026-09-04 measurement
        (compared 264 -> 199): tunatale-9yd0 made known words without a
        ``%`` secondary-stress reading stop being compounds, so genuine
        over-splits retired from the disputed population into single_part. The
        instrument did not break; the corpus genuinely has fewer disputes.
        """
        assert main(["--limit", "4000"]) == 0
        out = capsys.readouterr().out
        table = {}
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[0] in _ORACLE_TALLY:
                table[parts[0]] = int(parts[1])
        assert table == _ORACLE_TALLY
        # The three-way split is exhaustive over compared words.
        assert table["fully_agree"] + table["only_infl_disputed"] + table["stem_disputed"] == table["compared"]
