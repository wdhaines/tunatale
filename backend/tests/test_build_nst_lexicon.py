"""Tests for scripts/build_nst_lexicon.py (NST lexicon extract/build CLI).

Every fixture is a tiny in-test file built in the real source format — never
the 170 MB ``.pron``, the committed 4.6 MB gz, or the 44 MB database. All
calls go through ``main([...])`` so the argparse wiring stays tested.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest

from app.plugins.languages.no.lexicon import NstLexicon

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import build_nst_lexicon  # noqa: E402
from build_nst_lexicon import main  # noqa: E402


def _pron_line(word: str, pos: str, transcription: str, certainty: str) -> str:
    """A 13-field source row in the real semicolon format (fields 0/1/11/12 used)."""
    fillers = ";".join(["x"] * 9)
    return f"{word};{pos};{fillers};{transcription};{certainty}\n"


def _run_extract(tmp_path: Path, content: str) -> Path:
    src = tmp_path / "source.pron"
    src.write_text(content, encoding="latin-1")
    out = tmp_path / "extract.tsv.gz"
    main(["extract", "--input", str(src), "--output", str(out)])
    return out


def _read_rows(gz_path: Path) -> list[list[str]]:
    with gzip.open(gz_path, "rt", encoding="utf-8") as fh:
        return [line.rstrip("\n").split("\t") for line in fh]


class TestExtract:
    def test_latin1_round_trips_norwegian_letters(self, tmp_path: Path) -> None:
        src = tmp_path / "source.pron"
        src.write_text(_pron_line("snømann", "NN", '""sn2:$%mAn', "1"), encoding="latin-1")
        assert b"\xf8" in src.read_bytes()  # ø is a bare latin-1 byte, not UTF-8
        out = tmp_path / "extract.tsv.gz"
        main(["extract", "--input", str(src), "--output", str(out)])
        assert _read_rows(out) == [["snømann", "NN", '""sn2:$%mAn', "1"]]

    def test_empty_certainty_normalises_to_nine(self, tmp_path: Path) -> None:
        out = _run_extract(tmp_path, _pron_line("aftenposten", "", '"A:$ft@npost@n', ""))
        assert _read_rows(out) == [["aftenposten", "", '"A:$ft@npost@n', "9"]]

    def test_short_row_is_a_hard_error(self, tmp_path: Path) -> None:
        src = tmp_path / "source.pron"
        src.write_text("word;pos;x\n", encoding="latin-1")
        with pytest.raises(ValueError, match="Malformed line"):
            main(["extract", "--input", str(src), "--output", str(tmp_path / "out.tsv.gz")])

    def test_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        content = "\n" + _pron_line("galt", "NN", '"gAlt', "1") + "\n" + _pron_line("huset", "NN", '"h}:$s@', "1")
        out = _run_extract(tmp_path, content)
        words = [row[0] for row in _read_rows(out)]
        assert words == ["galt", "huset"]

    def test_deduplicates_identical_four_tuples(self, tmp_path: Path) -> None:
        line = _pron_line("seg", "PN", '"s{*I', "1")
        out = _run_extract(tmp_path, line + line)
        assert _read_rows(out) == [["seg", "PN", '"s{*I', "1"]]

    def test_sort_order_is_word_pos_transcription_certainty(self, tmp_path: Path) -> None:
        content = (
            _pron_line("snømann", "NN", '""sn2:$%mAn', "2")
            + _pron_line("galt", "VB", '"gA:lt', "2")
            + _pron_line("galt", "NN", '"gAlt', "1")
            + _pron_line("galt", "JJ", '"gA:lt', "1")
        )
        rows = _read_rows(_run_extract(tmp_path, content))
        assert rows == [
            ["galt", "JJ", '"gA:lt', "1"],
            ["galt", "NN", '"gAlt', "1"],
            ["galt", "VB", '"gA:lt', "2"],
            ["snømann", "NN", '""sn2:$%mAn', "2"],
        ]
        assert rows == sorted(rows)

    def test_gzip_header_mtime_is_zero(self, tmp_path: Path) -> None:
        # Asserted on the HEADER, not by diffing two runs: two extracts in the
        # same wall-clock second are byte-identical even WITHOUT mtime=0, so a
        # two-run comparison only discriminates if it straddles a second
        # boundary. Verified by sabotage drill — dropping mtime=0 left that
        # comparison green. The header's MTIME field is bytes 4:8, LE uint32.
        src = tmp_path / "source.pron"
        src.write_text(_pron_line("galt", "NN", '"gAlt', "1"), encoding="latin-1")
        out = tmp_path / "a.tsv.gz"
        main(["extract", "--input", str(src), "--output", str(out)])
        header = out.read_bytes()[:8]
        assert header[:2] == b"\x1f\x8b"  # gzip magic, so the offset means what we think
        assert int.from_bytes(header[4:8], "little") == 0

    def test_extract_is_byte_reproducible_across_runs(self, tmp_path: Path) -> None:
        src = tmp_path / "source.pron"
        src.write_text(_pron_line("galt", "NN", '"gAlt', "1"), encoding="latin-1")
        out_a = tmp_path / "a.tsv.gz"
        out_b = tmp_path / "b.tsv.gz"
        main(["extract", "--input", str(src), "--output", str(out_a)])
        main(["extract", "--input", str(src), "--output", str(out_b)])
        assert out_a.read_bytes() == out_b.read_bytes()


class TestBuild:
    def test_delegates_to_build_lexicon_db_and_produces_queryable_db(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[Path, Path]] = []
        real = build_nst_lexicon.build_lexicon_db

        def spy(extract_path: Path, db_path: Path) -> None:
            calls.append((extract_path, db_path))
            real(extract_path, db_path)

        monkeypatch.setattr(build_nst_lexicon, "build_lexicon_db", spy)
        gz = _run_extract(tmp_path, _pron_line("snømann", "NN", '""sn2:$%mAn', "1"))
        db = tmp_path / "lexicon.sqlite3"
        main(["build", "--input", str(gz), "--output", str(db)])
        assert calls == [(gz, db)]
        assert NstLexicon(db).resolve("snømann").transcription == '""sn2:$%mAn'
