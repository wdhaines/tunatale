"""Unit tests for the day-domain checker (scripts/check_anki_day_index.py).

Uses parsed-from-string sources so the checker's own scan never flags these
samples. The shapes flagged below are the real ones that reached ``app/``: the
grade-time elapsed of tunatale-46js (2026-10-03) and the queue-sort elapsed and
studied-today stamp fixed on 2026-09-03.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from check_anki_day_index import (  # noqa: E402
    SHAPE_DEFAULT_NOW,
    SHAPE_NOW_ARG,
    do_check,
    evaluate,
    scan_file,
    scan_source,
)


class TestFlagsTodayFromTheIndexDomain:
    @pytest.mark.parametrize(
        "source",
        [
            # tunatale-46js: the no-lrt branch of _grade_elapsed_days.
            "today_col_day = compute_anki_day_index(col_crt, rollover_hour, ref_now)",
            # 2026-09-03: the same branch of _elapsed_days_for_fsrs.
            "t = compute_anki_day_index(col_crt, rollover_hour, now)",
            "t = compute_anki_day_index(col_crt, 4, now=ref_now)",
            "t = compute_anki_day_index(col_crt, 4, datetime.now(UTC))",
            "t = compute_anki_day_index(col_crt, 4, now=datetime.datetime.now(tz=UTC))",
            "t = compute_anki_day_index(col_crt, 4, self._now)",
            "t = pw.compute_anki_day_index(col_crt, 4, utc_now)",
        ],
    )
    def test_a_now_argument_is_flagged(self, source):
        assert scan_source(source) == [(SHAPE_NOW_ARG, 1)]

    @pytest.mark.parametrize(
        "source",
        [
            # 2026-09-03: the studied-today stamp, which let the default supply now.
            "day_index = compute_anki_day_index(self._anki_col_crt)",
            "t = compute_anki_day_index(col_crt, 4)",
            "t = compute_anki_day_index(col_crt, rollover_hour=4)",
        ],
    )
    def test_an_omitted_day_defaults_to_now_and_is_flagged(self, source):
        assert scan_source(source) == [(SHAPE_DEFAULT_NOW, 1)]


class TestLeavesMarkerDecodingAlone:
    @pytest.mark.parametrize(
        "source",
        [
            "review_col_day = compute_anki_day_index(col_crt, rollover_hour, last_review)",
            "review_col_day = compute_anki_day_index(col_crt, 4, lr)",
            "d = compute_anki_day_index(col_crt, 4, now=marker)",
            "x = anki_today_col_day(col_crt, now)",
            "x = compute_anki_day_index",  # a reference, not a call
        ],
    )
    def test_not_flagged(self, source):
        assert scan_source(source) == []

    def test_docstrings_and_comments_are_invisible(self):
        source = (
            'def f():\n    """compute_anki_day_index(col_crt, 4, now) is wrong."""\n'
            "    # compute_anki_day_index(col_crt)\n    return 1\n"
        )
        assert scan_source(source) == []

    def test_line_numbers_are_reported(self):
        source = "a = 1\nb = compute_anki_day_index(c, 4, now)\n"
        assert scan_source(source) == [(SHAPE_NOW_ARG, 2)]


class TestEvaluate:
    def test_clean_tree_passes(self):
        assert evaluate({}) == (0, [])

    def test_any_hit_fails_and_names_the_fix(self):
        code, messages = evaluate({"app/srs/fsrs.py": Counter({SHAPE_NOW_ARG: 1})})
        assert code == 1
        assert len(messages) == 1
        assert "app/srs/fsrs.py" in messages[0]
        assert "anki_today_col_day" in messages[0]


class TestScanning:
    def test_scan_file_skips_a_file_that_does_not_parse(self, tmp_path, capsys):
        bad = tmp_path / "bad.py"
        bad.write_text("def (:\n")
        assert scan_file(bad) == []
        assert "parse error" in capsys.readouterr().err

    def test_do_check_reports_a_hit_in_a_tree(self, tmp_path, capsys):
        (tmp_path / "m.py").write_text("t = compute_anki_day_index(c, 4, ref_now)\n")
        assert do_check(tmp_path) == 1
        assert "m.py" in capsys.readouterr().out

    def test_the_real_app_tree_is_clean(self):
        """The control on the shipped code: every remaining call decodes a marker."""
        assert do_check(Path(__file__).resolve().parent.parent / "app") == 0
