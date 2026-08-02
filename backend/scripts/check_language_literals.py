#!/usr/bin/env python3
"""AST-based checker that flags hardcoded language literals in ``app/``.

Scans ``backend/app/**/*.py`` for string literals that look like hardcoded
language-specific values (bare language codes, language names, NLP engine
names, or TTS voice ids) living outside the sanctioned plugin/registry
modules. No-hardcoded-language-logic is a house convention (see root
``CLAUDE.md`` "Key Conventions") — language-specific behavior should route
through a language plugin (``TextPreprocessor``, voice maps, etc.), not a
literal string scattered through general-purpose code.

Allowlist (``tests/language_literals_allowlist.txt``)
  Permanent glob patterns for the sanctioned plugin/registry homes where
  language literals legitimately live (``app/languages.py``, the audio
  preprocessing plugins, the vocab notetype modules, the lemmatizer, and
  ``app/config.py``). An allowlisted FILE is fully exempt — every hit in it
  is skipped. It is the ONLY escape hatch, and deliberately coarse, so adding
  one needs sign-off.

ZERO TOLERANCE. The shrink-only grandfather ledger drained to empty on
2026-07-30 (13 entries -> 0) and was removed with its ratchet.

Docstrings are excluded — only literals used as real values (assignments,
comparisons, dict keys, function arguments, etc.) count as hits.

Known limitations
  String concatenation (``"n" + "o"``) produces two separate ``ast.Constant``
  nodes joined by ``ast.BinOp`` — neither constant alone matches a language
  literal, so the checker misses it. An AST constant-folding pass could resolve
  this but is likely overkill: the case-variant bypass (``"No"`` → ``"no"``) was
  fixed 2026-07-10, and concatenation to form language codes is not observed in
  the codebase today. If a violation of this shape appears, it must be caught by
  code review.

Usage::

    # exit 0 = clean
    uv run python scripts/check_language_literals.py
"""

from __future__ import annotations

import ast
import re
import sys
from collections import Counter
from pathlib import Path

from _checker_lib import (
    _relative_path,
    load_allowlist,
    matches_allowlist,
)

ALLOWLIST_PATH = Path("tests/language_literals_allowlist.txt")
APP_DIR = Path("app")

_BARE_CODES = {"sl", "no", "nb"}
_NAME_SUBSTRINGS = ("slovene", "slovenian", "norwegian")
_ENGINE_SUBSTRINGS = ("classla", "stanza")
_VOICE_RE = re.compile(r"\b[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural\b")


# ── Matching ─────────────────────────────────────────────────────────────────


def _matches_language_literal(value: str) -> bool:
    """True if *value* looks like a hardcoded language literal.

    Matches (any of):
    1. Exact bare code, case-insensitive: ``"sl"``, ``"no"``, ``"NB"``
       (after ``.strip()``).
    2. Name substring (case-insensitive): ``slovene``, ``slovenian``,
       ``norwegian``.
    3. Engine substring (case-insensitive): ``classla``, ``stanza``.
    4. TTS voice id regex: e.g. ``sl-SI-PetraNeural``.
    """
    if value.strip().lower() in _BARE_CODES:
        return True
    lowered = value.lower()
    if any(substr in lowered for substr in _NAME_SUBSTRINGS):
        return True
    if any(substr in lowered for substr in _ENGINE_SUBSTRINGS):
        return True
    return bool(_VOICE_RE.search(value))


# ── AST helpers ──────────────────────────────────────────────────────────────


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Return ``id()`` of every docstring ``Constant`` node in *tree*.

    A docstring is the first statement of a Module/ClassDef/FunctionDef/
    AsyncFunctionDef body, when that statement is a bare string expression.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = node.body
        if not body or not isinstance(body[0], ast.Expr):
            continue
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            ids.add(id(value))
    return ids


# ── Scanning ─────────────────────────────────────────────────────────────────


def scan_file(filepath: Path) -> list[tuple[str, int]]:
    """Return ``[(literal, lineno), …]`` for every language-literal hit in
    *filepath*, excluding docstrings.
    """
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        print(f"  [WARN] Skipping {filepath}: parse error", file=sys.stderr)
        return []

    docstring_ids = _docstring_ids(tree)
    hits: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        if not isinstance(node.value, str):
            continue
        if id(node) in docstring_ids:
            continue
        if _matches_language_literal(node.value):
            hits.append((node.value, node.lineno))
    return hits


# ── Grandfather ──────────────────────────────────────────────────────────────


def _preview(value: str, limit: int = 60) -> str:
    """Single-line, length-capped preview of *value* for terminal output."""
    collapsed = " ".join(value.split())
    if len(collapsed) > limit:
        return collapsed[: limit - 1] + "…"
    return collapsed


def collect_all_hits(
    app_dir: Path = APP_DIR,
    allowlist_path: Path | None = None,
) -> dict[str, Counter]:
    """Scan all ``*.py`` files under *app_dir*, returning
    ``{relative_path: Counter{literal: count}}``.

    Allowlisted files are skipped entirely here.
    """
    allowlist_patterns = load_allowlist(allowlist_path or ALLOWLIST_PATH)
    by_file: dict[str, Counter] = {}
    for pyfile in sorted(app_dir.rglob("*.py")):
        if pyfile.name == "__init__.py":
            continue
        # Skip __pycache__
        if "__pycache__" in pyfile.parts:
            continue
        rel = _relative_path(pyfile)
        if matches_allowlist(rel, allowlist_patterns):
            continue
        hits = scan_file(pyfile)
        if not hits:
            continue
        counter: Counter = Counter()
        for literal, _lineno in hits:
            counter[literal] += 1
        if counter:
            by_file[rel] = counter
    return by_file


def do_check(app_dir: Path = APP_DIR) -> int:
    """Check all app files against the allowlist. Returns exit code.

    Zero tolerance: any hardcoded language literal outside an allowlisted
    plugin/registry module fails. The shrink-only grandfather ledger drained to
    empty on 2026-07-30 (13 entries → 0) and was removed with its ratchet.

    The last entry was instructive: ``pixabay.py``'s dict key ``"no"`` (the
    English word) had been classed PERMANENT as "not drainable by any correct
    action". It was drained anyway, by moving the 353-row query table into a JSON
    data file — the checker's trigger is "a bare string literal in
    ``app/**/*.py``", not "this string is a language code", and the trigger was
    removable. Third time that reframing worked. **Before concluding a hit here is
    a false positive you must exempt, check whether the trigger can be removed.**

    The allowlist (file globs for sanctioned plugin/registry homes) is the only
    escape hatch and is deliberately coarse — it exempts a whole file forever, so
    adding one needs sign-off.
    """
    by_file = collect_all_hits(app_dir)
    exit_code = 0

    for rel_path, counter in sorted(by_file.items()):
        for literal, count in sorted(counter.items()):
            print(
                f"FAIL: {rel_path}:{count}x `{_preview(literal)}` — resolve it through "
                "app/languages.py (get_language / get_deck_name / get_l2_css_class / …) "
                "or move the data out of app/**/*.py.",
            )
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(do_check())
