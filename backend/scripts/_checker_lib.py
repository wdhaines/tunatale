"""Shared helpers for the AST-based checkers in ``backend/scripts/``.

Single source for the copy-pasted helper bodies that used to live in each
checker. Used by ``check_mock_boundaries.py``, ``check_language_literals.py``,
and ``check_date_today.py``.

``collect_all_hits`` takes the per-checker ``scan_file`` as a parameter because
that is the genuinely per-checker part and stays local to each checker.
``check_language_literals.py`` keeps its OWN ``collect_all_hits``: that copy
also skips allowlisted files before scanning, which the shared version does not.
"""

from __future__ import annotations

import ast
import fnmatch
from collections import Counter
from collections.abc import Callable
from pathlib import Path


def _call_fn_name(call_node: ast.Call) -> str | None:
    """Return the function name of a call, handling both ``Name`` and
    ``Attribute`` forms.

    - ``patch(…)``        → ``"patch"``
    - ``mock.patch(…)``   → ``"patch"``
    - ``mocker.patch(…)`` → ``"patch"``
    - ``monkeypatch.setattr(…)`` → ``"setattr"``
    """
    if isinstance(call_node.func, ast.Name):
        return call_node.func.id
    if isinstance(call_node.func, ast.Attribute):
        return call_node.func.attr
    return None


def _relative_path(filepath: Path) -> str:
    """Convert an absolute path to one relative to the backend/ root."""
    try:
        return str(filepath.relative_to(Path.cwd()))
    except ValueError:
        return str(filepath)


def _find_inline_comment(s: str) -> int | None:
    """Return index of the first ``#`` that starts a comment (not inside a
    string or escaped), or None."""
    in_single = False
    in_double = False
    escape = False
    for i, ch in enumerate(s):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return i
    return None


def load_allowlist(path: Path) -> list[str]:
    """Return non-empty, non-comment lines from the allowlist file.

    Inline comments (``app.foo  # why``) are stripped so the remaining
    text is a clean fnmatch glob.
    """
    if not path.exists():
        return []
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Strip inline comment (first unquoted ``#``)
        comment_pos = _find_inline_comment(stripped)
        if comment_pos is not None:
            stripped = stripped[:comment_pos].rstrip()
        if stripped:
            lines.append(stripped)
    return lines


def matches_allowlist(target: str, patterns: list[str]) -> bool:
    """Return True if *target* matches any allowlist glob."""
    return any(fnmatch.fnmatch(target, pat) for pat in patterns)


def collect_all_hits(
    root: Path,
    scan_file: Callable[[Path], list[tuple[str, int]]],
) -> dict[str, Counter]:
    """Scan all ``*.py`` files under *root*, returning
    ``{relative_path: Counter{target: count}}``.
    """
    by_file: dict[str, Counter] = {}
    for pyfile in sorted(root.rglob("*.py")):
        if pyfile.name == "__init__.py":
            continue
        # Skip __pycache__
        if "__pycache__" in pyfile.parts:
            continue
        hits = scan_file(pyfile)
        if not hits:
            continue
        rel = _relative_path(pyfile)
        counter: Counter = Counter()
        for target, _lineno in hits:
            counter[target] += 1
        if counter:
            by_file[rel] = counter
    return by_file
