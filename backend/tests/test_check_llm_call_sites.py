"""Unit tests for the LLM call-site checker (scripts/check_llm_call_sites.py).

The property it enforces: every ``.complete(`` call reachable from product code
(``backend/app/**``) carries an explicit ``call_site=`` keyword, so the Groq
ledger's sixth field is never silently a ``-``. Tests are NOT scanned — theirs
is the 25-site sweep in test_cloze_quality.py, which drives the helpers from
outside.

Uses parsed-from-string ASTs so the checker's own scan doesn't flag samples,
mirroring test_check_mock_boundaries.py.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Allow importing from scripts/ one level up.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from check_llm_call_sites import (  # noqa: E402
    _is_complete_call,
    _has_call_site_kwarg,
    do_check,
    scan_file,
)


def _parse_call(source: str):
    """Parse *source* and return the first Call node."""
    tree = ast.parse(source, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            return node
    msg = f"No Call node found in: {source}"
    raise ValueError(msg)


# ── _is_complete_call — the attribute-name discriminator ─────────────────────


class TestIsCompleteCall:
    def test_plain_attribute_complete(self):
        node = _parse_call("client.complete(prompt)")
        assert _is_complete_call(node) is True

    def test_subscript_receiver_is_still_attribute_complete(self):
        """``clients[0].complete(...)`` — the receiver shape must not matter."""
        node = _parse_call("clients[0].complete(prompt)")
        assert _is_complete_call(node) is True

    def test_is_complete_name_call_is_not(self):
        node = _parse_call("is_complete(prompt)")
        assert _is_complete_call(node) is False

    def test_mark_complete_attribute_is_not(self):
        """A LONGER identifier ending in `complete` is a different method."""
        node = _parse_call("row.mark_complete(prompt)")
        assert _is_complete_call(node) is False

    def test_obj_dot_is_complete_attribute_is_not(self):
        node = _parse_call("obj.is_complete(prompt)")
        assert _is_complete_call(node) is False

    def test_decorator_complete_has_the_same_ast_shape(self):
        """A ``@receiver.complete(...)`` decorator is an Attribute Call too."""
        node = _parse_call("@client.complete(prompt)\ndef f():\n    pass")
        assert _is_complete_call(node) is True


# ── _has_call_site_kwarg — the label discriminator ────────────────────────────


class TestHasCallSiteKwarg:
    def test_explicit_call_site_is_found(self):
        node = _parse_call('client.complete(prompt, call_site="story")')
        assert _has_call_site_kwarg(node) is True

    def test_no_call_site_is_not_found(self):
        node = _parse_call("client.complete(prompt)")
        assert _has_call_site_kwarg(node) is False

    def test_call_site_several_lines_below_is_found(self):
        """The normal repo formatting — a line-scoped regex would false-positive."""
        node = _parse_call(
            "client.complete(\n"
            "    prompt,\n"
            "    system_prompt=system_prompt,\n"
            "    temperature=0.7,\n"
            "    max_tokens=max_tokens,\n"
            "    call_site=CallSite.STORY,\n"
            ")\n"
        )
        assert _has_call_site_kwarg(node) is True

    def test_call_site_id_is_not_call_site(self):
        """A SUBSTRING must not satisfy — ``call_site_id=`` is a different argument."""
        node = _parse_call("client.complete(prompt, call_site_id=1)")
        assert _has_call_site_kwarg(node) is False

    def test_call_site_after_a_star_splat_is_still_found(self):
        """``*args`` unpacking does not hide a later ``call_site=`` keyword."""
        node = _parse_call('client.complete(prompt, *args, call_site="story")')
        assert _has_call_site_kwarg(node) is True

    def test_kwargs_splat_does_not_satisfy(self):
        """``**kwargs`` cannot prove the label — only an explicit keyword does."""
        node = _parse_call("client.complete(prompt, **kwargs)")
        assert _has_call_site_kwarg(node) is False


# ── scan_file — text-level probes (comments, strings, near-misses) ────────────


class TestScanFile:
    def test_empty_file_returns_empty_list(self, tmp_path):
        f = tmp_path / "empty.py"
        f.write_text("# just a comment\n")
        assert scan_file(f) == []

    def test_complete_inside_a_comment_is_no_hit(self, tmp_path):
        """Comment text is not code — the AST never sees it."""
        f = tmp_path / "c.py"
        f.write_text('# client.complete(prompt, call_site="story")\n')
        assert scan_file(f) == []

    def test_complete_inside_a_docstring_is_no_hit(self, tmp_path):
        f = tmp_path / "d.py"
        f.write_text('"""client.complete(prompt) appears only here."""\nx = 1\n')
        assert scan_file(f) == []

    def test_complete_inside_a_string_literal_is_no_hit(self, tmp_path):
        f = tmp_path / "s.py"
        f.write_text('text = "client.complete(prompt)"\nx = 1\n')
        assert scan_file(f) == []

    def test_call_inside_a_comprehension_is_seen(self, tmp_path):
        """Nested nodes are visited by the walk; nothing is a top-level await."""
        f = tmp_path / "comp.py"
        f.write_text("async def f():\n    return [await c.complete(p) for c in clients]\n")
        hits = scan_file(f)
        assert len(hits) == 1

    def test_subscript_receiver_call_is_seen(self, tmp_path):
        f = tmp_path / "sub.py"
        f.write_text("async def f():\n    await clients[0].complete(prompt)\n")
        assert len(scan_file(f)) == 1

    def test_two_calls_one_labelled_flags_the_other(self, tmp_path):
        f = tmp_path / "two.py"
        f.write_text('client.complete(p, call_site="story")\nclient.complete(q)\n')
        hits = scan_file(f)
        assert len(hits) == 1
        _, lineno = hits[0]
        assert lineno == 2

    def test_syntax_error_file_is_skipped_without_crashing(self, tmp_path):
        f = tmp_path / "bad.py"
        f.write_text("This is not valid python {{{{\n")
        assert scan_file(f) == []


# ── do_check — the gate over a fake app/ tree ─────────────────────────────────


class TestDoCheck:
    def _tree(self, tmp_path, files: dict[str, str]) -> Path:
        app_dir = tmp_path / "app"
        for rel, source in files.items():
            p = app_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(source)
        return app_dir

    def test_clean_tree_passes(self, tmp_path, monkeypatch, capsys):
        app_dir = self._tree(tmp_path, {"llm/x.py": 'client.complete(p, call_site="story")\n'})
        monkeypatch.chdir(tmp_path)
        assert do_check(app_dir=app_dir) == 0
        assert capsys.readouterr().out == ""

    def test_any_unlabelled_complete_fails(self, tmp_path, monkeypatch, capsys):
        app_dir = self._tree(tmp_path, {"llm/x.py": "client.complete(p)\n"})
        monkeypatch.chdir(tmp_path)
        assert do_check(app_dir=app_dir) == 1
        out = capsys.readouterr().out
        assert "call_site" in out
        assert "app/llm/x.py" in out

    def test_failure_message_points_at_the_fix(self, tmp_path, monkeypatch, capsys):
        app_dir = self._tree(tmp_path, {"llm/x.py": "client.complete(p)\n"})
        monkeypatch.chdir(tmp_path)
        do_check(app_dir=app_dir)
        out = capsys.readouterr().out
        assert "call_site=" in out

    def test_kwargs_splat_does_not_pass_the_gate(self, tmp_path, monkeypatch):
        """cassette.py:189's old **kwargs shape would be flagged — hence the fix."""
        app_dir = self._tree(
            tmp_path,
            {"llm/cassette.py": "response = await self._real_client.complete(prompt, **kwargs)\n"},
        )
        monkeypatch.chdir(tmp_path)
        assert do_check(app_dir=app_dir) == 1

    def test_tests_dir_is_not_scanned(self, tmp_path, monkeypatch):
        """Tests deliberately omit the label; only backend/app** is checked."""
        app_dir = self._tree(tmp_path, {"llm/x.py": 'client.complete(p, call_site="story")\n'})
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "t.py").write_text("client.complete(p)\n")
        monkeypatch.chdir(tmp_path)
        assert do_check(app_dir=app_dir) == 0

    def test_symlinked_py_file_is_scanned(self, tmp_path, monkeypatch):
        """A symlink in the walked tree must be read, not skipped or crashed on."""
        real = tmp_path / "real"
        real.mkdir()
        (real / "real_target.py").write_text("client.complete(p)\n")
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "link.py").symlink_to(real / "real_target.py")
        monkeypatch.chdir(tmp_path)
        assert do_check(app_dir=app_dir) == 1

    def test_py_under_pycache_is_skipped_by_the_walk(self, tmp_path, monkeypatch):
        """__pycache__ copies are build artifacts — they must not trip the gate."""
        app_dir = self._tree(tmp_path, {"llm/x.py": 'client.complete(p, call_site="story")\n'})
        cache = app_dir / "llm" / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "bad.py").write_text("client.complete(p)\n")
        monkeypatch.chdir(tmp_path)
        assert do_check(app_dir=app_dir) == 0
