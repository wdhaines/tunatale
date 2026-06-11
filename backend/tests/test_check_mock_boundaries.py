"""Unit tests for the mock-boundary checker (scripts/check_mock_boundaries.py).

Uses parsed-from-string ASTs so the checker's own scan doesn't flag samples.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Allow importing from scripts/ one level up.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from check_mock_boundaries import (  # noqa: E402
    _is_monkeypatch_setattr,
    _is_patch,
    format_grandfather_line,
    load_allowlist,
    load_grandfather,
    matches_allowlist,
    scan_file,
)


# ── _is_patch ─────────────────────────────────────────────────────────────────


def _parse_call(source: str):
    """Parse *source* as an expression and return the first Call node."""
    tree = ast.parse(source, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            return node
    msg = f"No Call node found in: {source}"
    raise ValueError(msg)


class TestIsPatch:
    def test_bare_patch_app_string(self):
        node = _parse_call('patch("app.foo.bar", return_value=0)')
        assert _is_patch(node) is True

    def test_bare_patch_non_app(self):
        node = _parse_call('patch("sys.argv", ["prog"])')
        assert _is_patch(node) is False

    def test_bare_patch_no_args(self):
        node = _parse_call("patch()")
        assert _is_patch(node) is False

    def test_mock_dot_patch_app_string(self):
        node = _parse_call('mock.patch("app.foo.bar")')
        assert _is_patch(node) is True

    def test_mocker_dot_patch_app_string(self):
        node = _parse_call('mocker.patch("app.foo.bar")')
        assert _is_patch(node) is True

    def test_decorator_patch_app_string(self):
        node = _parse_call('@patch("app.foo.bar")\ndef f(): pass')
        assert _is_patch(node) is True

    def test_rejects_non_constant_first_arg(self):
        node = _parse_call("patch(some_var)")
        assert _is_patch(node) is False


# ── _is_monkeypatch_setattr ───────────────────────────────────────────────────


class TestIsMonkeypatchSetattr:
    def test_monkeypatch_setattr_app_string(self):
        node = _parse_call('monkeypatch.setattr("app.foo.bar", 42)')
        assert _is_monkeypatch_setattr(node) is True

    def test_monkeypatch_setattr_no_args(self):
        node = _parse_call("monkeypatch.setattr()")
        assert _is_monkeypatch_setattr(node) is False

    def test_monkeypatch_setattr_non_app(self):
        node = _parse_call('monkeypatch.setattr("sys.argv", ["prog"])')
        assert _is_monkeypatch_setattr(node) is False

    def test_monkeypatch_setattr_object_form(self):
        node = _parse_call("monkeypatch.setattr(obj, 'name', 42)")
        assert _is_monkeypatch_setattr(node) is False


# ── scan_file ─────────────────────────────────────────────────────────────────


class TestScanFile:
    def test_empty_file_returns_empty_list(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("# just a comment\n")
        assert scan_file(f) == []

    def test_rejects_non_string_first_arg(self, tmp_path):
        f = tmp_path / "var.py"
        f.write_text("target = 'app.foo'\npatch(target, return_value=0)\n")
        # Checks that scan_file doesn't crash on non-constant first arg.
        assert scan_file(f) == []

    def test_mixed_patch_and_setattr(self, tmp_path):
        f = tmp_path / "mixed.py"
        f.write_text(
            "from unittest.mock import patch\n"
            "\n"
            "def test_foo(monkeypatch):\n"
            '    patch("app.one")\n'
            '    monkeypatch.setattr("app.two", 42)\n'
            '    patch("sys.nope")\n'
            '    monkeypatch.setattr(obj, "app.three", 1)\n'
        )
        hits = scan_file(f)
        targets = {t for t, _ in hits}
        assert targets == {"app.one", "app.two"}

    def test_duplicate_targets_are_separate_hits(self, tmp_path):
        f = tmp_path / "dups.py"
        f.write_text(
            "from unittest.mock import patch\n"
            "\n"
            "def test_a():\n"
            '    patch("app.x")\n'
            '    patch("app.y")\n'
            '    patch("app.x")\n'
        )
        hits = scan_file(f)
        # Two app.x hits, one app.y hit
        assert len(hits) == 3
        xs = [t for t, _ in hits if t == "app.x"]
        assert len(xs) == 2


# ── Allowlist ─────────────────────────────────────────────────────────────────


class TestAllowlist:
    def test_load_allowlist_skips_comments_and_blanks(self, tmp_path):
        allow = tmp_path / "allow.txt"
        allow.write_text("# this is a comment\n\napp.foo.*\n  # indented comment  \napp.bar.baz\n")
        patterns = load_allowlist(allow)
        assert patterns == ["app.foo.*", "app.bar.baz"]

    def test_load_allowlist_missing_file(self, tmp_path):
        missing = tmp_path / "nope.txt"
        assert load_allowlist(missing) == []

    def test_matches_allowlist_glob(self):
        patterns = ["app.audio.edge_tts.edge_tts.*", "app.config.settings.*"]
        assert matches_allowlist("app.audio.edge_tts.edge_tts.Communicate", patterns) is True
        assert matches_allowlist("app.config.settings.anki_collection_path", patterns) is True
        assert matches_allowlist("app.anki.sync.main", patterns) is False

    def test_matches_allowlist_star_dot_star(self):
        patterns = ["app.*.settings.*"]
        assert matches_allowlist("app.config.settings.foo", patterns) is True
        assert matches_allowlist("app.srs.queue_stats.settings.bar", patterns) is True
        assert matches_allowlist("app.config.notsettings.foo", patterns) is False


# ── Grandfather ───────────────────────────────────────────────────────────────


class TestGrandfather:
    def test_load_grandfather_parses_valid_lines(self, tmp_path):
        gf = tmp_path / "gf.txt"
        gf.write_text("test_foo.py\tapp.bar\t3\ntest_baz.py\tapp.qux\t1\n")
        d = load_grandfather(gf)
        assert d == {("test_foo.py", "app.bar"): 3, ("test_baz.py", "app.qux"): 1}

    def test_load_grandfather_skips_comments_and_blanks(self, tmp_path):
        gf = tmp_path / "gf.txt"
        gf.write_text("# header comment\n\ntest_a.py\tapp.x\t5\n")
        d = load_grandfather(gf)
        assert d == {("test_a.py", "app.x"): 5}

    def test_load_grandfather_missing_file(self, tmp_path):
        assert load_grandfather(tmp_path / "nope.txt") == {}

    def test_load_grandfather_skips_bad_lines(self, tmp_path):
        gf = tmp_path / "gf.txt"
        gf.write_text("test_a.py\tapp.x\nnot-a-tab-line\n")
        d = load_grandfather(gf)
        assert d == {}  # both lines invalid

    def test_format_grandfather_line(self):
        assert format_grandfather_line("test_foo.py", "app.bar", 3) == "test_foo.py\tapp.bar\t3"


# ── Integration-style ─────────────────────────────────────────────────────────


def test_scan_does_not_crash_on_syntax_error(tmp_path):
    f = tmp_path / "bad_syntax.py"
    f.write_text("This is not valid python {{{{\n")
    # Should not raise; should return empty list
    assert scan_file(f) == []
