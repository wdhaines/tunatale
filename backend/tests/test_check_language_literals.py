"""Unit tests for the language-literal checker (scripts/check_language_literals.py).

Uses parsed-from-string / written-to-tmp_path sources so the checker's own
scan (which only walks ``app/``) never sees these samples.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow importing from scripts/ one level up.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from check_language_literals import (  # noqa: E402
    _matches_language_literal,
    _preview,
    do_write_grandfather,
    format_grandfather_line,
    load_allowlist,
    load_grandfather,
    matches_allowlist,
    scan_file,
)


# ── _matches_language_literal ─────────────────────────────────────────────────


class TestMatchesLanguageLiteral:
    @pytest.mark.parametrize(
        "value",
        [
            "sl",
            "no",
            "nb",
            "Slovene",
            "a Norwegian word",
            "sl-SI-PetraNeural",
            "nb-NO-PernilleNeural",
        ],
    )
    def test_positive_matches(self, value):
        assert _matches_language_literal(value) is True

    def test_positive_matches_engine_names(self):
        # Deliberately NOT parametrized: a parametrize id of exactly "classla"
        # or "stanza" collides with conftest.py's --run-classla/--run-stanza
        # gate (pytest_collection_modifyitems skips on `"classla" in
        # item.keywords`, and pytest folds parametrize ids into item.keywords)
        # — it silently auto-skips without the flag, hiding this assertion.
        assert _matches_language_literal("classla") is True
        assert _matches_language_literal("STANZA") is True

    @pytest.mark.parametrize(
        "value",
        [
            "en",  # explicitly excluded from bare codes
            "sludge",  # contains "sl" but is not an exact bare-code match
            "nope",  # not an exact "no" match
            "annotate",  # contains "no" as a substring, but bare-code rule is exact-only
            "hello",
            "",
        ],
    )
    def test_negative_matches(self, value):
        assert _matches_language_literal(value) is False

    def test_bare_code_strips_whitespace(self):
        assert _matches_language_literal("  sl  ") is True

    def test_bare_code_is_exact_not_substring(self):
        # "slovenian" contains "sl" but is caught by the *name* rule, not the
        # bare-code rule; confirm it's still True (via the name rule).
        assert _matches_language_literal("slovenian") is True


# ── scan_file ─────────────────────────────────────────────────────────────────


class TestScanFile:
    def test_excludes_docstrings_flags_real_code(self, tmp_path):
        f = tmp_path / "sample.py"
        f.write_text(
            '"""Module docstring mentioning Norwegian, not a real hit."""\n'
            "\n"
            "def foo():\n"
            '    """Docstring with sl in it, also not a hit."""\n'
            '    x = "no"\n'
            '    voice = "sl-SI-PetraNeural"\n'
            "    return x, voice\n"
        )
        hits = scan_file(f)
        assert hits == [("no", 5), ("sl-SI-PetraNeural", 6)]

    def test_class_and_async_function_docstrings_excluded(self, tmp_path):
        f = tmp_path / "sample2.py"
        f.write_text(
            "class Foo:\n"
            '    """A Norwegian-flavored docstring, not a hit."""\n'
            "\n"
            "    async def bar(self):\n"
            '        """Another sl docstring, not a hit."""\n'
            '        return "nb"\n'
        )
        hits = scan_file(f)
        assert hits == [("nb", 6)]

    def test_empty_file_returns_empty_list(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("# just a comment\n")
        assert scan_file(f) == []

    def test_no_hits_returns_empty_list(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text('x = "hello"\ny = "world"\n')
        assert scan_file(f) == []

    def test_duplicate_literals_are_separate_hits(self, tmp_path):
        f = tmp_path / "dups.py"
        f.write_text('a = "sl"\nb = "sl"\nc = "nb"\n')
        hits = scan_file(f)
        assert hits == [("sl", 1), ("sl", 2), ("nb", 3)]

    def test_scan_does_not_crash_on_syntax_error(self, tmp_path):
        f = tmp_path / "bad_syntax.py"
        f.write_text("This is not valid python {{{{\n")
        assert scan_file(f) == []


# ── Allowlist ─────────────────────────────────────────────────────────────────


class TestAllowlist:
    def test_load_allowlist_skips_comments_and_blanks(self, tmp_path):
        allow = tmp_path / "allow.txt"
        allow.write_text("# this is a comment\n\napp/config.py\n  # indented comment  \napp/audio/preprocessing/*.py\n")
        patterns = load_allowlist(allow)
        assert patterns == ["app/config.py", "app/audio/preprocessing/*.py"]

    def test_load_allowlist_missing_file(self, tmp_path):
        missing = tmp_path / "nope.txt"
        assert load_allowlist(missing) == []

    def test_matches_allowlist_exact_file(self):
        patterns = ["app/config.py", "app/audio/preprocessing/*.py"]
        assert matches_allowlist("app/config.py", patterns) is True

    def test_matches_allowlist_glob(self):
        patterns = ["app/config.py", "app/audio/preprocessing/*.py"]
        assert matches_allowlist("app/audio/preprocessing/slovene.py", patterns) is True

    def test_matches_allowlist_no_match(self):
        patterns = ["app/config.py", "app/audio/preprocessing/*.py"]
        assert matches_allowlist("app/api/srs.py", patterns) is False


# ── Grandfather ───────────────────────────────────────────────────────────────


class TestGrandfather:
    def test_round_trip_simple_literal(self, tmp_path):
        gf = tmp_path / "gf.txt"
        gf.write_text(format_grandfather_line("app/foo.py", "sl", 3) + "\n")
        d = load_grandfather(gf)
        assert d == {("app/foo.py", "sl"): 3}

    def test_round_trip_literal_with_newline_and_tab(self, tmp_path):
        literal = 'line one\nline two\twith "tab" and \\backslash'
        gf = tmp_path / "gf.txt"
        gf.write_text(format_grandfather_line("app/foo.py", literal, 1) + "\n")
        d = load_grandfather(gf)
        assert d == {("app/foo.py", literal): 1}

    def test_load_grandfather_skips_comments_and_blanks(self, tmp_path):
        gf = tmp_path / "gf.txt"
        gf.write_text("# header comment\n\napp/foo.py\tsl\t2\n")
        d = load_grandfather(gf)
        assert d == {("app/foo.py", "sl"): 2}

    def test_load_grandfather_missing_file(self, tmp_path):
        assert load_grandfather(tmp_path / "nope.txt") == {}

    def test_load_grandfather_skips_bad_lines(self, tmp_path):
        gf = tmp_path / "gf.txt"
        gf.write_text("app/foo.py\tsl\nnot-a-tab-line\n")
        d = load_grandfather(gf)
        assert d == {}  # neither line has exactly 3 tab-separated parts

    def test_load_grandfather_warns_on_bad_count(self, tmp_path, capsys):
        gf = tmp_path / "gf.txt"
        gf.write_text("app/foo.py\tsl\tnot-an-int\n")
        d = load_grandfather(gf)
        assert d == {}
        captured = capsys.readouterr()
        assert "Bad grandfather line" in captured.err

    def test_format_grandfather_line(self):
        assert format_grandfather_line("app/foo.py", "sl", 3) == "app/foo.py\tsl\t3"

    def test_format_grandfather_line_escapes_special_chars(self):
        line = format_grandfather_line("app/foo.py", "a\tb\nc\\d", 1)
        # Exactly one physical line — no raw tab/newline leaked into the record.
        assert "\n" not in line
        assert line.count("\t") == 2  # only the two field-separator tabs


# ── _preview ──────────────────────────────────────────────────────────────────


class TestPreview:
    def test_short_value_unchanged(self):
        assert _preview("sl") == "sl"

    def test_collapses_whitespace(self):
        assert _preview("line one\nline two\t\tpadded") == "line one line two padded"

    def test_truncates_long_value(self):
        long_value = "x" * 100
        result = _preview(long_value, limit=10)
        assert result == "x" * 9 + "…"
        assert len(result) == 10


# ── do_write_grandfather ──────────────────────────────────────────────────────


class TestGrandfatherOutput:
    def test_allowlisted_files_excluded_from_write_output(self, tmp_path, monkeypatch):
        """Invariant: ``--write-grandfather`` excludes files matching the allowlist.

        ``_relative_path`` computes paths relative to ``Path.cwd()``, so the
        sample tree is built under a chdir'd tmp_path with a *relative*
        ``app_dir`` — matching how the real script runs from ``backend/``.
        """
        import sys
        from io import StringIO

        monkeypatch.chdir(tmp_path)
        app_dir = Path("app")
        app_dir.mkdir()
        (app_dir / "languages.py").write_text('CODE = "sl"\n')
        (app_dir / "other.py").write_text('CODE = "nb"\n')

        allow_path = Path("allow.txt")
        allow_path.write_text("app/languages.py\n")

        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            do_write_grandfather(app_dir=app_dir, allowlist_path=allow_path)
        finally:
            sys.stdout = old_stdout

        output = captured.getvalue()
        assert "app/other.py" in output, "non-allowlisted file's hit should appear in grandfather output"
        assert "app/languages.py" not in output, "allowlisted file's hit should NOT appear in grandfather output"

    def test_init_and_pycache_files_skipped(self, tmp_path, monkeypatch):
        import sys
        from io import StringIO

        monkeypatch.chdir(tmp_path)
        app_dir = Path("app")
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text('CODE = "sl"\n')
        pycache_dir = app_dir / "__pycache__"
        pycache_dir.mkdir()
        (pycache_dir / "mod.py").write_text('CODE = "nb"\n')

        allow_path = Path("allow.txt")
        allow_path.write_text("")

        captured = StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured
        try:
            do_write_grandfather(app_dir=app_dir, allowlist_path=allow_path)
        finally:
            sys.stdout = old_stdout

        assert captured.getvalue() == ""
