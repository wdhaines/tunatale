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
    do_check,
    _matches_language_literal,
    _preview,
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

    @pytest.mark.parametrize("value", ["NO", "No", "SL", "Sl", "NB", "Nb"])
    def test_bare_code_matches_case_variants(self, value):
        # The name/engine rules are case-insensitive; the bare-code rule must be
        # too, or `LANG = "NO"` walks straight through the gate (found by the
        # 2026-07-10 review — empirically confirmed bypass, zero legitimate
        # uppercase bare-code literals exist in backend/app).
        assert _matches_language_literal(value) is True

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


# ── Grandfather ───────────────────────────────────────────────────────────────


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


# ── The stale-entry ratchet ──────────────────────────────────────────────────


class TestZeroTolerance:
    """``do_check`` after the grandfather ledger was removed (2026-07-30).

    Replaces the ledger test classes. Without these ``do_check`` had NO test at
    all, and ``scripts/`` is not coverage-measured, so nothing would have said so.
    """

    def _tree(self, tmp_path, source: str, allowlist: str = ""):
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "thing.py").write_text(source)
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "language_literals_allowlist.txt").write_text(allowlist)
        return app_dir

    def test_clean_tree_passes(self, tmp_path, monkeypatch, capsys):
        app_dir = self._tree(tmp_path, 'CODE = "fr"\n')
        monkeypatch.chdir(tmp_path)
        assert do_check(app_dir=app_dir) == 0
        assert capsys.readouterr().out == ""

    def test_any_language_literal_fails(self, tmp_path, monkeypatch, capsys):
        app_dir = self._tree(tmp_path, 'CODE = "sl"\n')
        monkeypatch.chdir(tmp_path)
        assert do_check(app_dir=app_dir) == 1
        assert "sl" in capsys.readouterr().out

    def test_failure_message_points_at_the_registry_not_a_ledger(self, tmp_path, monkeypatch, capsys):
        app_dir = self._tree(tmp_path, 'CODE = "sl"\n')
        monkeypatch.chdir(tmp_path)
        do_check(app_dir=app_dir)
        out = capsys.readouterr().out
        assert "app/languages.py" in out
        assert "grandfather" not in out.lower()

    def test_allowlisted_file_still_passes(self, tmp_path, monkeypatch):
        """The file-glob allowlist is the surviving escape hatch."""
        app_dir = self._tree(tmp_path, 'CODE = "sl"\n', allowlist="app/thing.py\n")
        monkeypatch.chdir(tmp_path)
        assert do_check(app_dir=app_dir) == 0
