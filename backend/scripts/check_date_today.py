#!/usr/bin/env python3
"""Detect `date.today()` used where the ANKI day is meant.

Tier 1: AST-detect two unambiguous composites:
  * ``due_at_rollover_utc(date.today())``
  * ``datetime.combine(date.today(), time(4, …))``

Tier 2 (bare ``date.today()`` in the Anki-day domain) was scoped and
**NOT shipped** — 18 hits in ``app/`` plus ~302 in tests is too broad for a
ledger that would never drain (per the brief's thesis that a checker with a
huge ledger is theatre).

ZERO TOLERANCE. This checker had a shrink-only grandfather ledger while its 7
seeded sites were being fixed; all 7 landed in ``b684d82`` and the ledger has
been empty ever since. An empty shrink-only ledger and "no additions, period"
are behaviourally identical, so the ledger, its ratchet and its stale-entry
enforcement were removed (2026-07-30) rather than carried unexercised — any hit
is now a failure. There is deliberately no escape hatch: tier 1 has no known
false positives, and this composite is always wrong in the Anki-day domain.

Brief: bd ``tunatale-my0`` (closed).
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

from _checker_lib import (
    _call_fn_name,
    collect_all_hits,
)

# Stable construct identifiers (imported by drills, so values must not change).
SHAPE_ROLLOVER = "due_at_rollover_utc(date.today())"
SHAPE_COMBINE = "datetime.combine(date.today(), time(4, ...))"

APP_DIR = Path("app")


# ── AST helpers ───────────────────────────────────────────────────────────────


def _is_date_today(node: ast.AST) -> bool:
    """True if *node* is ``date.today()`` — a zero-arg call via attribute."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "date"
        and node.func.attr == "today"
        and not node.args
        and not node.keywords
    )


def _is_time_4(node: ast.AST) -> bool:
    """True if *node* is ``time(4, …)`` — first positional arg is ``4``."""
    if not isinstance(node, ast.Call):
        return False
    name = _call_fn_name(node)
    if name != "time":
        return False
    return len(node.args) >= 1 and isinstance(node.args[0], ast.Constant) and node.args[0].value == 4


def _match_shape(node: ast.Call) -> str | None:
    """Return the shape constant if *node* matches a tier-1 pattern."""
    name = _call_fn_name(node)

    if name == "due_at_rollover_utc" and len(node.args) == 1 and _is_date_today(node.args[0]):
        return SHAPE_ROLLOVER

    if name == "combine" and len(node.args) >= 2 and _is_date_today(node.args[0]) and _is_time_4(node.args[1]):
        return SHAPE_COMBINE

    return None


# ── Scanning ──────────────────────────────────────────────────────────────────


def scan_source(source: str) -> list[tuple[str, int]]:
    """Return ``[(construct, lineno), …]`` for every tier-1 shape in *source*.

    Docstrings and comments are inherently invisible to AST-based scanning, so no
    filtering is needed or wanted — see the comment below.
    """
    # NO docstring/comment filtering is needed, and adding it is actively harmful.
    # A docstring is a single string Constant: its text contains ZERO Call nodes, so
    # `ast.walk` can never yield a hit from inside one. Comments are absent from the
    # AST entirely. Being AST-based IS the docstring/comment skip.
    #
    # The original implementation carried a `_get_docstring_ranges()` line-range
    # filter. It was unreachable for its stated purpose AND produced a FALSE
    # NEGATIVE: a genuine violation sharing a line with a docstring fell inside the
    # docstring's range and was silently dropped —
    #     def f():
    #         \"\"\"d\"\"\"; return due_at_rollover_utc(date.today())
    # scanned as clean. Removed 2026-07-29; drill 4c pins the same-line case.
    tree = ast.parse(source)
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        shape = _match_shape(node)
        if shape is None:
            continue
        hits.append((shape, node.lineno))

    return hits


def scan_file(filepath: Path) -> list[tuple[str, int]]:
    """Read and scan a single ``.py`` file. Returns empty on parse error."""
    source = filepath.read_text(encoding="utf-8")
    try:
        return scan_source(source)
    except SyntaxError:
        print(f"  [WARN] Skipping {filepath}: parse error", file=sys.stderr)
        return []


# ── Grandfather ledger ────────────────────────────────────────────────────────


def evaluate(by_file: dict[str, Counter]) -> tuple[int, list[str]]:
    """Zero tolerance: any hit fails.  Pure — no I/O.  Returns ``(exit_code, messages)``."""
    messages = [
        f"FAIL: {rel_path}:{count}x `{construct}` — use anki_today(), not date.today()"
        for rel_path, counter in sorted(by_file.items())
        for construct, count in sorted(counter.items())
    ]
    return (1 if messages else 0), messages


def do_check(app_dir: Path = APP_DIR) -> int:
    """Scan, evaluate, print.  Returns exit code."""
    exit_code, messages = evaluate(collect_all_hits(app_dir, scan_file))
    for msg in messages:
        print(msg)
    return exit_code


# ── CLI entry point ───────────────────────────────────────────────────────────


if __name__ == "__main__":
    sys.exit(do_check())
