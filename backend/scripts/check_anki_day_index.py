#!/usr/bin/env python3
"""Detect ``compute_anki_day_index`` asked "what day is it now?".

Anki answers "which study day is it?" by counting LOCAL calendar dates and
subtracting one until today's 04:00 rollover has passed; in TunaTale that is
``anki_today_col_day``. ``compute_anki_day_index`` is a different domain: it rolls
over at the UTC instant ``col_crt - rollover_hour``, which for a real collection
(crt at 04:00 local) is local MIDNIGHT. Inside ``[local midnight, 04:00)`` it is a
day ahead of Anki. Its one legitimate job is decoding the day-level
``last_review`` marker ``_compute_last_review`` writes, which it inverts exactly
(see the "Three day rules" section of ``.claude/rules/anki-queue-parity.md``).

So in ``app/`` a call is a failure when its day argument is "now":

* the argument is missing — the parameter defaults to the current time;
* the argument is a name or attribute with ``now`` in it (``now``, ``ref_now``,
  ``self._now``), or a call to ``now`` / ``utcnow``.

The shape is the one that keeps recurring, and it hides because the two domains
agree for twenty hours a day. It reached ``app/`` three times: the studied-today
stamp and the queue-sort elapsed (fixed 2026-09-03, after ``anki-gates`` caught
one at the rollover), and the grade-time elapsed twin (tunatale-46js, 2026-10-03,
after main's CI went red at 00:54 UTC). A name check is a heuristic: a "now"
smuggled in under another name (``t = now; f(crt, 4, t)``) passes it. It is meant
to catch the honest mistake, which is how all three happened.

ZERO TOLERANCE, no ledger and no allowlist: on the day it landed every call left
in ``app/`` decodes a marker, so there is nothing to grandfather.
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

from _checker_lib import _call_fn_name, collect_all_hits

SHAPE_NOW_ARG = "compute_anki_day_index(..., now)"
SHAPE_DEFAULT_NOW = "compute_anki_day_index(col_crt[, hour]) — day defaults to now"

APP_DIR = Path("app")

_TARGET = "compute_anki_day_index"
_NOW_CALLS = {"now", "utcnow"}
_DAY_POSITION = 2  # (col_crt, rollover_hour, now)


def _day_argument(call: ast.Call) -> ast.expr | None:
    """The expression passed as the day to decode, or ``None`` when it is omitted."""
    if len(call.args) > _DAY_POSITION:
        return call.args[_DAY_POSITION]
    return next((kw.value for kw in call.keywords if kw.arg == "now"), None)


def _is_now(node: ast.expr) -> bool:
    """True when *node* reads as the current time."""
    if isinstance(node, ast.Call):
        return _call_fn_name(node) in _NOW_CALLS
    if isinstance(node, ast.Name):
        return "now" in node.id.lower()
    if isinstance(node, ast.Attribute):
        return "now" in node.attr.lower()
    return False


def scan_source(source: str) -> list[tuple[str, int]]:
    """Return ``[(shape, lineno), …]`` for every call that asks for today.

    AST-based, so docstrings and comments are invisible by construction — the
    reasoning is in ``check_date_today.scan_source``.
    """
    hits: list[tuple[str, int]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or _call_fn_name(node) != _TARGET:
            continue
        day = _day_argument(node)
        if day is None:
            hits.append((SHAPE_DEFAULT_NOW, node.lineno))
        elif _is_now(day):
            hits.append((SHAPE_NOW_ARG, node.lineno))
    return hits


def scan_file(filepath: Path) -> list[tuple[str, int]]:
    """Read and scan a single ``.py`` file. Returns empty on parse error."""
    try:
        return scan_source(filepath.read_text(encoding="utf-8"))
    except SyntaxError:
        print(f"  [WARN] Skipping {filepath}: parse error", file=sys.stderr)
        return []


def evaluate(by_file: dict[str, Counter]) -> tuple[int, list[str]]:
    """Zero tolerance: any hit fails. Pure — no I/O. Returns ``(exit_code, messages)``."""
    messages = [
        f"FAIL: {rel_path}:{count}x `{shape}` — Anki's today is anki_today_col_day(col_crt, now); "
        f"compute_anki_day_index only decodes a stored last-review marker"
        for rel_path, counter in sorted(by_file.items())
        for shape, count in sorted(counter.items())
    ]
    return (1 if messages else 0), messages


def do_check(app_dir: Path = APP_DIR) -> int:
    """Scan, evaluate, print. Returns exit code."""
    exit_code, messages = evaluate(collect_all_hits(app_dir, scan_file))
    for msg in messages:
        print(msg)
    return exit_code


if __name__ == "__main__":
    sys.exit(do_check())
