#!/usr/bin/env python3
"""AST-based checker that flags internal-mock violations in the test suite.

Scans ``backend/tests/**/*.py`` for `patch("app.…")` and
`monkeypatch.setattr("app.…", …)` calls not covered by the allowlist.

ZERO TOLERANCE. The shrink-only grandfather ledger drained to empty on
2026-07-30 (22 entries → 0; the last 11 were the Anki envelope) and was removed
along with its ratchet — an empty shrink-only ledger and "no additions, period"
are the same rule, and the machinery was ~90 lines whose only evidence was its
own unit tests, since ``scripts/`` is not coverage-measured.

Allowlist (``tests/mock_allowlist.txt``) — the ONLY escape hatch
  Permanent glob patterns for true process/network boundaries, settings
  pins, and path-constant pins. Adding one asserts that the target IS a
  boundary, which is an architectural claim and needs user sign-off. It is not
  a place to record debt; the fix for a failing check is to test through the
  seam (canonical pattern: ``TestSociableSync``).

Blind spots (not policed, by design):
  - ``patch.object(obj, "name")`` — predominantly settings/object pins.
  - ``monkeypatch.setattr(obj, "name", …)`` (2-arg object form) — same reason.

Usage::

    # exit 0 = clean
    uv run python scripts/check_mock_boundaries.py

CLI flags:
  --no-location       Omit file:line from violation output (for CI).
"""

from __future__ import annotations

import ast
import fnmatch
import sys
from collections import Counter
from pathlib import Path

ALLOWLIST_PATH = Path("tests/mock_allowlist.txt")
TESTS_DIR = Path("tests")


# ── AST helpers ──────────────────────────────────────────────────────────────


def _is_patch(call_node: ast.Call) -> bool:
    """True if *call_node* is any form of ``patch("app.…", …)``.

    Handles:
    -  ``patch("app.xxx", …)``            — bare ``ast.Name``
    -  ``mock.patch("app.xxx", …)``       — ``ast.Attribute`` (``mock.patch``)
    -  ``mocker.patch("app.xxx", …)``     — ``ast.Attribute`` (``mocker.patch``)
    -  ``@patch("app.xxx")``              — decorator (same AST shape)
    """
    name = _call_fn_name(call_node)
    if name is None or name != "patch":
        return False
    if not call_node.args:
        return False
    first_arg = call_node.args[0]
    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
        return first_arg.value.startswith("app.")
    return False


def _is_monkeypatch_setattr(call_node: ast.Call) -> bool:
    """True if *call_node* is ``monkeypatch.setattr("app.…", …)`` with a
    string literal as the first argument."""
    name = _call_fn_name(call_node)
    if name != "setattr":
        return False
    # Ensure the receiver (value) is named "monkeypatch"
    if not isinstance(call_node.func, ast.Attribute):
        return False
    if not isinstance(call_node.func.value, ast.Name):
        return False
    if call_node.func.value.id != "monkeypatch":
        return False
    if len(call_node.args) < 1:
        return False
    first_arg = call_node.args[0]
    if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
        return first_arg.value.startswith("app.")
    return False


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


# ── Scanning ─────────────────────────────────────────────────────────────────


def scan_file(filepath: Path) -> list[tuple[str, int]]:
    """Return ``[(target, lineno), …]`` for every mock violation found in
    *filepath*.

    Each hit records the dotted target string (e.g. ``"app.anki.sync.main"``)
    and the line number where it appears.
    """
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        print(f"  [WARN] Skipping {filepath}: parse error", file=sys.stderr)
        return []

    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = None
        if _is_patch(node) or _is_monkeypatch_setattr(node):
            target = node.args[0].value  # type: ignore[union-attr]
        if target is not None:
            hits.append((target, node.lineno))
    return hits


def _relative_path(filepath: Path) -> str:
    """Convert an absolute path to one relative to the backend/ root."""
    try:
        return str(filepath.relative_to(Path.cwd()))
    except ValueError:
        return str(filepath)


# ── Allowlist ─────────────────────────────────────────────────────────────────


def load_allowlist(path: Path = ALLOWLIST_PATH) -> list[str]:
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


def matches_allowlist(target: str, patterns: list[str]) -> bool:
    """Return True if *target* matches any allowlist glob."""
    return any(fnmatch.fnmatch(target, pat) for pat in patterns)


# ── Grandfather ──────────────────────────────────────────────────────────────


def collect_all_hits(tests_dir: Path = TESTS_DIR) -> dict[str, Counter]:
    """Scan all ``*.py`` files under *tests_dir*, returning
    ``{relative_path: Counter{target: count}}``.
    """
    by_file: dict[str, Counter] = {}
    for pyfile in sorted(tests_dir.rglob("*.py")):
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


def do_check(tests_dir: Path = TESTS_DIR, show_location: bool = True) -> int:
    """Check all test files against the allowlist.  Returns exit code.

    Zero tolerance: any ``patch("app.…")`` / ``monkeypatch.setattr("app.…", …)``
    that is not allowlisted fails. The grandfather ledger and its shrink-only
    ratchet were removed on 2026-07-30, when the last of its 22 entries drained —
    an empty shrink-only ledger and "no additions, period" behave identically, and
    the ledger machinery was ~90 lines whose only evidence was its own unit tests
    (``scripts/`` is not coverage-measured).

    The allowlist remains the sole escape hatch, and deliberately so: it is a
    claim that something IS a real process/network boundary, which is an
    architectural statement needing sign-off — not a note that debt exists.
    """
    allowlist_patterns = load_allowlist()
    by_file = collect_all_hits(tests_dir)
    exit_code = 0

    for rel_path, counter in sorted(by_file.items()):
        for target, count in sorted(counter.items()):
            if matches_allowlist(target, allowlist_patterns):
                continue
            print(
                f"FAIL: {rel_path}:{count}x `{target}` is not a process/network boundary.\n"
                "  Fix: test THROUGH the seam (see TestSociableSync in "
                "tests/test_anki_sync_orchestrator.py), or — only if it is genuinely a "
                f"boundary — add it to {ALLOWLIST_PATH} with user sign-off.",
            )
            exit_code = 1

    return exit_code


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Check test-file mock boundaries against the allowlist.",
    )
    parser.add_argument(
        "--no-location",
        action="store_true",
        help="Omit file:line from violation output (for CI).",
    )
    args, _unknown = parser.parse_known_intermixed_args()
    return do_check(show_location=not args.no_location)


if __name__ == "__main__":
    sys.exit(main())
