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
    do_check,
    _is_monkeypatch_setattr,
    _is_patch,
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


# ── Grandfather ───────────────────────────────────────────────────────────────


# ── Integration-style ─────────────────────────────────────────────────────────


def test_scan_does_not_crash_on_syntax_error(tmp_path):
    f = tmp_path / "bad_syntax.py"
    f.write_text("This is not valid python {{{{\n")
    # Should not raise; should return empty list
    assert scan_file(f) == []


# ── The stale-entry ratchet ──────────────────────────────────────────────────


class TestZeroTolerance:
    """``do_check`` after the grandfather ledger was removed (2026-07-30).

    Replaces TestGrandfather / TestGrandfatherOutput / TestStaleLedgerEntries,
    which tested a ledger that no longer exists. Without these ``do_check`` had
    NO test at all — worth knowing, since ``scripts/`` is not coverage-measured,
    so the 100% gate would not have said a word.
    """

    def _tree(self, tmp_path, source: str):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_sample.py").write_text(source)
        (tmp_path / "tests" / "mock_allowlist.txt").write_text("app.plugins.anki_sync.sync_orchestrator._run_driver\n")
        return tests_dir

    def test_clean_tree_passes(self, tmp_path, monkeypatch, capsys):
        tests_dir = self._tree(tmp_path, "def test_x():\n    assert True\n")
        monkeypatch.chdir(tmp_path)
        assert do_check(tests_dir=tests_dir) == 0
        assert capsys.readouterr().out == ""

    def test_any_internal_mock_fails(self, tmp_path, monkeypatch, capsys):
        """No ledger means no free pass — the whole point of the change."""
        tests_dir = self._tree(
            tmp_path,
            'from unittest.mock import patch\n\n\ndef test_x():\n    with patch("app.srs.database.SRSDatabase"):\n        pass\n',
        )
        monkeypatch.chdir(tmp_path)
        assert do_check(tests_dir=tests_dir) == 1
        assert "app.srs.database.SRSDatabase" in capsys.readouterr().out

    def test_failure_message_points_at_the_fix_not_a_ledger(self, tmp_path, monkeypatch, capsys):
        """A checker with no escape hatch must say what to do, or it gets disabled."""
        tests_dir = self._tree(
            tmp_path,
            'from unittest.mock import patch\n\n\ndef test_x():\n    with patch("app.srs.database.SRSDatabase"):\n        pass\n',
        )
        monkeypatch.chdir(tmp_path)
        do_check(tests_dir=tests_dir)
        out = capsys.readouterr().out
        assert "test THROUGH the seam" in out
        assert "grandfather" not in out.lower()

    def test_allowlisted_boundary_still_passes(self, tmp_path, monkeypatch):
        """The allowlist is the surviving escape hatch and must still work."""
        tests_dir = self._tree(
            tmp_path,
            "from unittest.mock import patch\n\n\ndef test_x():\n"
            '    with patch("app.plugins.anki_sync.sync_orchestrator._run_driver"):\n        pass\n',
        )
        monkeypatch.chdir(tmp_path)
        assert do_check(tests_dir=tests_dir) == 0
