#!/usr/bin/env python3
"""AST-based checker that flags internal-mock violations in the test suite.

Scans ``backend/tests/**/*.py`` for `patch("app.…")` and
`monkeypatch.setattr("app.…", …)` calls not covered by the allowlist or
grandfather file.

Allowlist (``tests/mock_allowlist.txt``)
  Permanent glob patterns for true process/network boundaries, settings
  pins, and path-constant pins — approved entries that never need review.

Grandfather (``tests/mock_grandfather.txt``)
  Tab-separated ``file<TAB>target<TAB>count`` lines for internal seams
  flagged by the checker on its initial run.  The grandfather can only
  shrink: unknown (file, target) → FAIL; count above recorded → FAIL
  ("new violation of grandfathered seam"); count below → FAIL
  ("edit the line to N").

Blind spots (not policed, by design):
  - ``patch.object(obj, "name")`` — predominantly settings/object pins.
  - ``monkeypatch.setattr(obj, "name", …)`` (2-arg object form) — same reason.

Usage::

    # Check against allowlist + grandfather (exit 0 = clean)
    uv run python scripts/check_mock_boundaries.py

    # Generate grandfather output to stdout
    uv run python scripts/check_mock_boundaries.py --write-grandfather

CLI flags:
  --no-location       Omit file:line from violation output (for CI).
  --write-grandfather Scan all files, output grandfather-format lines.
  --check             Default mode: check against allowlist + grandfather.
"""

from __future__ import annotations

import ast
import fnmatch
import sys
from collections import Counter
from pathlib import Path

ALLOWLIST_PATH = Path("tests/mock_allowlist.txt")
GRANDFATHER_PATH = Path("tests/mock_grandfather.txt")
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
    """Return non-empty, non-comment lines from the allowlist file."""
    if not path.exists():
        return []
    lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    return lines


def matches_allowlist(target: str, patterns: list[str]) -> bool:
    """Return True if *target* matches any allowlist glob."""
    return any(fnmatch.fnmatch(target, pat) for pat in patterns)


# ── Grandfather ──────────────────────────────────────────────────────────────


def load_grandfather(path: Path = GRANDFATHER_PATH) -> dict[tuple[str, str], int]:
    """Parse the grandfather file into ``{(file, target): count}``."""
    result: dict[tuple[str, str], int] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("\t")
        if len(parts) == 3:
            fname, target, count_str = parts
            try:
                result[(fname, target)] = int(count_str)
            except ValueError:
                print(f"  [WARN] Bad grandfather line: {line}", file=sys.stderr)
    return result


def format_grandfather_line(filepath: str, target: str, count: int) -> str:
    """Tab-separated grandfather entry."""
    return f"{filepath}\t{target}\t{count}"


# ── Main ─────────────────────────────────────────────────────────────────────


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


def do_check(
    tests_dir: Path = TESTS_DIR,
    show_location: bool = True,
) -> int:
    """Check all test files against allowlist + grandfather.  Returns exit code."""
    allowlist_patterns = load_allowlist()
    grandfather = load_grandfather()
    by_file = collect_all_hits(tests_dir)
    exit_code = 0

    for rel_path, counter in sorted(by_file.items()):
        for target, count in sorted(counter.items()):
            # Allowlisted?
            if matches_allowlist(target, allowlist_patterns):
                continue
            # Grandfathered?
            gf_key = (rel_path, target)
            if gf_key in grandfather:
                gf_count = grandfather[gf_key]
                if count == gf_count:
                    continue
                if count > gf_count:
                    print(
                        f"FAIL: {rel_path}:{count}x `{target}` exceeds "
                        f"grandfathered count {gf_count} "
                        "(new violation of grandfathered seam)",
                    )
                    exit_code = 1
                else:
                    print(
                        f"FAIL: {rel_path}:{count}x `{target}` is below "
                        f"grandfathered count {gf_count} "
                        "(edit the line to match)",
                    )
                    exit_code = 1
            else:
                print(
                    f"FAIL: {rel_path}:{count}x `{target}` not in allowlist or grandfather",
                )
                exit_code = 1

    return exit_code


def do_write_grandfather(tests_dir: Path = TESTS_DIR) -> None:
    """Print grandfather-format lines to stdout."""
    by_file = collect_all_hits(tests_dir)
    for rel_path in sorted(by_file):
        counter = by_file[rel_path]
        for target in sorted(counter):
            print(format_grandfather_line(rel_path, target, counter[target]))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Check test-file mock boundaries against allowlist + grandfather.",
    )
    parser.add_argument(
        "--no-location",
        action="store_true",
        help="Omit file:line from violation output (for CI).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--write-grandfather",
        action="store_true",
        help="Scan all files, output grandfather-format lines to stdout.",
    )
    group.add_argument(
        "--check",
        action="store_true",
        default=True,
        help="Default mode: check against allowlist + grandfather.",
    )

    args, _unknown = parser.parse_known_intermixed_args()

    if args.write_grandfather:
        # The --no-location flag is irrelevant for grandfather generation
        do_write_grandfather()
        return 0

    return do_check(show_location=not args.no_location)


if __name__ == "__main__":
    sys.exit(main())
