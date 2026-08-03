#!/usr/bin/env python3
"""Ban direct reads of the SINGULAR ``settings.database_url``.

``database_url`` is the single-language default. On a multi-language install it
names ONE fixed language — Slovene, per the repo's dev ``.env`` — regardless of
the language a caller is actually working with;
``resolve_language_context`` consults it only as the fallback for a code with no
``database_urls`` entry.

**The failure is silent, which is why it needs a checker rather than care.** A
caller that opens the singular db and then filters by ``language_code`` simply
matches nothing, and reports success:

  * ``grave_ignored_lemma_cards --language no`` opened the Slovene db and
    printed "Nothing to grave." every run for a month, while the
    ignored-but-carded cards it exists to remove sat in the Norwegian db.
  * ``schedule()`` graded a Norwegian card on Slovene learning steps (Layer 82).
    ``tests/test_queue_stats_language_isolation.py`` could only cover that "by
    INSPECTION during audit", with a grep recipe in its docstring — inspection
    being exactly what failed above.

The fix at a call site is ``app.languages.resolve_db_path(code, settings)``, or
``resolve_language_context(code, settings)`` when more than the path is needed.

Detection is AST-based, so prose is safe: these modules explain the trap at
length, and a text search would flag every explanation. It is also exact on the
singular/plural split — a substring match on ``database_url`` would flag every
CORRECT caller, since they all read ``database_urls``.

ZERO TOLERANCE, and there is no grandfather ledger — the eight non-sanctioned
readers were fixed in the same effort that added this (044bc10 and its
follow-up), so the checker shipped already-green. Per the house policy that an
empty shrink-only ledger and "no additions, period" are the same rule, the
ledger machinery is deliberately absent.

Allowlist (``tests/singular_database_url_allowlist.txt``) — file globs, three
entries, each of which genuinely owns the singular setting:

  * ``app/config.py``   — defines the field.
  * ``app/languages.py``— the registry fallback that gives it its meaning.
  * ``app/main.py``     — the single-language ``{target_language: database_url}``
                          map, which is what "single-language mode" IS.

Adding a fourth is an architectural claim (this code legitimately has no
language in hand) and needs sign-off, same as ``mock_allowlist.txt``.

Usage::

    uv run python scripts/check_singular_database_url.py
"""

from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

from _checker_lib import (
    collect_all_hits,
    load_allowlist,
    matches_allowlist,
)

ALLOWLIST_PATH = Path("tests/singular_database_url_allowlist.txt")

# Roots to scan. `scripts/` is included deliberately: the incident that motivated
# this checker was in a one-off maintenance script, not in app code.
SCAN_ROOTS = (Path("app"), Path("scripts"))

_TARGET = "settings.database_url"


def _is_settings_object(node: ast.AST) -> bool:
    """True if *node* denotes the settings singleton.

    Matches a bare ``settings`` name and any dotted path ending in ``.settings``
    (``app.config.settings``, ``queue_stats.settings``, …). Anything else — a
    local variable, some other config object — is out of scope: the trap is
    specific to this module-level singleton.
    """
    if isinstance(node, ast.Name):
        return node.id == "settings"
    if isinstance(node, ast.Attribute):
        return node.attr == "settings"
    return False


def scan_source(source: str) -> list[tuple[str, int]]:
    """Return ``(target, lineno)`` for every ``settings.database_url`` access.

    Covers reads and writes alike — assigning the setting is how a script
    reroutes itself at runtime, which is the same trap wearing a hat.
    ``settings.database_urls`` (plural) is a DIFFERENT attribute and never
    matches, which is the whole point.
    """
    tree = ast.parse(source)
    return [
        (_TARGET, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "database_url" and _is_settings_object(node.value)
    ]


def scan_file(filepath: Path) -> list[tuple[str, int]]:
    """Read and scan a single ``.py`` file.

    A parse error PROPAGATES — deliberately unlike the sibling checkers, which
    warn and skip. Nothing else in the gate reads ``scripts/`` (ruff is scoped to
    ``app`` and ``tests``), so a skip here means an unparseable file is reported
    clean. That is not hypothetical: a bad bulk rewrite while writing this
    checker left an ``IndentationError`` in an archive script, and the
    warn-and-skip version passed the tree.
    """
    return scan_source(filepath.read_text(encoding="utf-8"))


def evaluate(by_file: dict[str, Counter], allowlist_path: Path | None = None) -> tuple[int, list[str]]:
    """Zero tolerance outside the allowlist. Pure — no I/O beyond the allowlist."""
    patterns = load_allowlist(allowlist_path or ALLOWLIST_PATH)
    messages = [
        f"FAIL: {rel_path}:{count}x `{_TARGET}` — singular setting, names one fixed language;"
        " use app.languages.resolve_db_path(code, settings)"
        for rel_path, counter in sorted(by_file.items())
        if not matches_allowlist(rel_path, patterns)
        for count in (sum(counter.values()),)
    ]
    return (1 if messages else 0), messages


def do_check(roots: tuple[Path, ...] = SCAN_ROOTS) -> int:
    """Scan, evaluate, print. Returns exit code."""
    by_file: dict[str, Counter] = {}
    for root in roots:
        if root.exists():
            by_file.update(collect_all_hits(root, scan_file))
    exit_code, messages = evaluate(by_file)
    for msg in messages:
        print(msg)
    return exit_code


if __name__ == "__main__":  # pragma: no cover - CLI guard
    sys.exit(do_check())
