"""Unit tests for the date.today() checker (scripts/check_date_today.py).

Uses parsed-from-string sources so the checker's own scan never flags these
samples.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from check_date_today import (  # noqa: E402
    SHAPE_COMBINE,
    SHAPE_ROLLOVER,
    do_write_grandfather,
    evaluate,
    format_grandfather_line,
    load_grandfather,
    scan_source,
)


# ── scan_source ───────────────────────────────────────────────────────────────


class TestScanSource:
    def test_detects_due_at_rollover_utc(self):
        source = "due_at_rollover_utc(date.today())"
        hits = scan_source(source)
        assert hits == [(SHAPE_ROLLOVER, 1)]

    def test_detects_datetime_combine(self):
        source = "datetime.combine(date.today(), time(4, 0))"
        hits = scan_source(source)
        assert hits == [(SHAPE_COMBINE, 1)]

    def test_detects_combine_with_more_args(self):
        source = "datetime.combine(date.today(), time(4, 0, tzinfo=UTC))"
        hits = scan_source(source)
        assert hits == [(SHAPE_COMBINE, 1)]

    def test_ignores_datetime_combine_with_non_4_time(self):
        source = "datetime.combine(date.today(), time(1, 0))"
        assert scan_source(source) == []

    def test_ignores_datetime_combine_with_time_max(self):
        source = "datetime.combine(date.today(), time.max)"
        assert scan_source(source) == []

    def test_ignores_bare_date_today(self):
        source = "x = date.today()"
        assert scan_source(source) == []

    def test_ignores_bare_due_at_rollover_utc(self):
        source = "x = due_at_rollover_utc()"
        assert scan_source(source) == []

    def test_empty_source(self):
        assert scan_source("") == []

    def test_only_comments(self):
        source = "# due_at_rollover_utc(date.today())"
        assert scan_source(source) == []

    def test_inside_module_docstring_is_skipped(self):
        source = '"""due_at_rollover_utc(date.today()) is wrong here."""\nx = 1'
        assert scan_source(source) == []

    def test_inside_function_docstring_is_skipped(self):
        source = 'def foo():\n    """due_at_rollover_utc(date.today()) warning."""\n    pass'
        assert scan_source(source) == []

    def test_inside_class_docstring_is_skipped(self):
        source = 'class Foo:\n    """due_at_rollover_utc(date.today()) warning."""\n    pass'
        assert scan_source(source) == []

    def test_real_code_outside_docstring_is_detected(self):
        source = (
            "def foo():\n"
            '    """due_at_rollover_utc(date.today()) is wrong."""\n'
            "    return due_at_rollover_utc(date.today())\n"
        )
        hits = scan_source(source)
        assert hits == [(SHAPE_ROLLOVER, 3)]

    def test_multiple_hits(self):
        source = (
            "a = due_at_rollover_utc(date.today())\n"
            "b = datetime.combine(date.today(), time(4, 0))\n"
            "c = due_at_rollover_utc(date.today())\n"
        )
        hits = scan_source(source)
        assert len(hits) == 3
        assert hits[0] == (SHAPE_ROLLOVER, 1)
        assert hits[1] == (SHAPE_COMBINE, 2)
        assert hits[2] == (SHAPE_ROLLOVER, 3)

    def test_async_function_docstring_skipped(self):
        source = 'async def bar():\n    """docstring with date.today() pattern"""\n    pass'
        assert scan_source(source) == []

    def test_rollover_with_qualified_name(self):
        source = "rollover.due_at_rollover_utc(date.today())"
        hits = scan_source(source)
        assert hits == [(SHAPE_ROLLOVER, 1)]

    def test_rollover_deeply_qualified(self):
        source = "srs.anki_mirror.rollover.due_at_rollover_utc(date.today())"
        hits = scan_source(source)
        assert hits == [(SHAPE_ROLLOVER, 1)]

    def test_real_app_pattern_line(self):
        source = (
            "from app.srs.anki_mirror.rollover import due_at_rollover_utc\n"
            "from datetime import date\n\n"
            "today_due_at = due_at_rollover_utc(date.today()).isoformat()\n"
        )
        hits = scan_source(source)
        assert hits == [(SHAPE_ROLLOVER, 4)]


# ── Grandfather ───────────────────────────────────────────────────────────────


class TestGrandfather:
    def test_round_trip_simple(self, tmp_path):
        gf = tmp_path / "gf.txt"
        gf.write_text(format_grandfather_line("app/foo.py", SHAPE_ROLLOVER, 3) + "\n")
        d = load_grandfather(gf)
        assert d == {("app/foo.py", SHAPE_ROLLOVER): 3}

    def test_round_trip_combine(self, tmp_path):
        gf = tmp_path / "gf.txt"
        gf.write_text(format_grandfather_line("app/foo.py", SHAPE_COMBINE, 2) + "\n")
        d = load_grandfather(gf)
        assert d == {("app/foo.py", SHAPE_COMBINE): 2}

    def test_load_grandfather_skips_comments_and_blanks(self, tmp_path):
        gf = tmp_path / "gf.txt"
        gf.write_text("# header comment\n\napp/foo.py\t" + SHAPE_ROLLOVER + "\t2\n")
        d = load_grandfather(gf)
        assert d == {("app/foo.py", SHAPE_ROLLOVER): 2}

    def test_load_grandfather_missing_file(self, tmp_path):
        assert load_grandfather(tmp_path / "nope.txt") == {}

    def test_load_grandfather_skips_bad_lines(self, tmp_path):
        gf = tmp_path / "gf.txt"
        gf.write_text("app/foo.py\t" + SHAPE_ROLLOVER + "\nnot-a-tab-line\n")
        d = load_grandfather(gf)
        assert d == {}

    def test_entry_with_reason_parses_identically(self, tmp_path):
        gf = tmp_path / "gf.txt"
        gf.write_text("app/foo.py\t" + SHAPE_ROLLOVER + "\t3  # reason: LIVE BUG, do not fix\n")
        d = load_grandfather(gf)
        assert d == {("app/foo.py", SHAPE_ROLLOVER): 3}

    def test_hash_in_construct_untouched(self, tmp_path):
        gf = tmp_path / "gf.txt"
        gf.write_text("app/foo.py\t" + SHAPE_ROLLOVER + "\t1\n")
        d = load_grandfather(gf)
        assert d == {("app/foo.py", SHAPE_ROLLOVER): 1}

    def test_format_grandfather_line(self):
        assert format_grandfather_line("app/foo.py", SHAPE_ROLLOVER, 3) == ("app/foo.py\t" + SHAPE_ROLLOVER + "\t3")

    def test_combine_with_reason_parses_identically(self, tmp_path):
        gf = tmp_path / "gf.txt"
        gf.write_text("app/bar.py\t" + SHAPE_COMBINE + "\t5  # reason: test fixture\n")
        d = load_grandfather(gf)
        assert d == {("app/bar.py", SHAPE_COMBINE): 5}


# ── evaluate ──────────────────────────────────────────────────────────────────


class TestEvaluate:
    def test_no_violations_no_ledger_passes(self):
        exit_code, messages = evaluate({}, {})
        assert exit_code == 0
        assert messages == []

    def test_ledgered_exact_match_passes(self):
        by_file = {"app/srs/db_revlog.py": Counter({SHAPE_ROLLOVER: 2})}
        grandfather = {("app/srs/db_revlog.py", SHAPE_ROLLOVER): 2}
        exit_code, messages = evaluate(by_file, grandfather)
        assert exit_code == 0

    def test_new_violation_unledgered_fails(self):
        by_file = {"app/srs/db_new.py": Counter({SHAPE_ROLLOVER: 1})}
        exit_code, messages = evaluate(by_file, {})
        assert exit_code == 1
        assert any("not in grandfather ledger" in m for m in messages)

    def test_exceeds_ledgered_count_fails(self):
        by_file = {"app/srs/db_revlog.py": Counter({SHAPE_ROLLOVER: 2})}
        grandfather = {("app/srs/db_revlog.py", SHAPE_ROLLOVER): 1}
        exit_code, messages = evaluate(by_file, grandfather)
        assert exit_code == 1
        assert any("exceeds grandfathered count" in m for m in messages)

    def test_below_ledgered_count_fails(self):
        by_file = {"app/srs/db_revlog.py": Counter({SHAPE_ROLLOVER: 1})}
        grandfather = {("app/srs/db_revlog.py", SHAPE_ROLLOVER): 3}
        exit_code, messages = evaluate(by_file, grandfather)
        assert exit_code == 1
        assert any("below grandfathered count" in m for m in messages)

    def test_stale_ledger_entry_fails(self):
        exit_code, messages = evaluate({}, {("app/srs/db_revlog.py", SHAPE_ROLLOVER): 1})
        assert exit_code == 1
        assert any("stale ledger entry" in m for m in messages)

    def test_multiple_files_mixed_results(self):
        by_file = {
            "app/ok.py": Counter({SHAPE_ROLLOVER: 1}),
            "app/bad.py": Counter({SHAPE_ROLLOVER: 1}),
        }
        grandfather = {("app/ok.py", SHAPE_ROLLOVER): 1}
        exit_code, messages = evaluate(by_file, grandfather)
        assert exit_code == 1
        assert any("bad.py" in m for m in messages)
        assert not any("ok.py" in m for m in messages)

    def test_combine_shape_respected_in_ratchet(self):
        by_file = {"app/foo.py": Counter({SHAPE_COMBINE: 1})}
        grandfather = {("app/foo.py", SHAPE_COMBINE): 1}
        assert evaluate(by_file, grandfather) == (0, [])


# ── do_write_grandfather ──────────────────────────────────────────────────────


class TestGrandfatherOutput:
    def test_output_format(self, tmp_path, monkeypatch):
        import sys
        from io import StringIO

        monkeypatch.chdir(tmp_path)
        app_dir = Path("app/srs")
        app_dir.mkdir(parents=True)
        (app_dir / "db_new.py").write_text("due_at_rollover_utc(date.today())\n")

        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            do_write_grandfather(app_dir=Path("app"))
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue().strip()
        assert output
        assert SHAPE_ROLLOVER in output
        assert "app/srs/db_new.py" in output

    def test_init_and_pycache_skipped(self, tmp_path, monkeypatch):
        import sys
        from io import StringIO

        monkeypatch.chdir(tmp_path)
        app_dir = Path("app")
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text("due_at_rollover_utc(date.today())\n")
        pycache = app_dir / "__pycache__"
        pycache.mkdir()
        (pycache / "mod.py").write_text("due_at_rollover_utc(date.today())\n")

        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            do_write_grandfather(app_dir=app_dir)
        finally:
            sys.stdout = old_stdout

        assert captured.getvalue() == ""

    def test_no_hits_yields_empty_output(self, tmp_path, monkeypatch):
        import sys
        from io import StringIO

        monkeypatch.chdir(tmp_path)
        app_dir = Path("app")
        app_dir.mkdir()
        (app_dir / "clean.py").write_text("x = 1\n")

        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            do_write_grandfather(app_dir=app_dir)
        finally:
            sys.stdout = old_stdout

        assert captured.getvalue() == ""
