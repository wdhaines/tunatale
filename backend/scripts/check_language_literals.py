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
  is skipped, and it never enters the grandfather ledger.

Grandfather (``tests/language_literals_grandfather.txt``)
  Tab-separated ``file<TAB>literal<TAB>count`` lines for pre-existing
  seams flagged by the checker's initial run. The grandfather can only
  shrink: unknown (file, literal) -> FAIL; count above recorded -> FAIL
  ("new violation of grandfathered seam"); count below -> FAIL
  ("edit the line to match").

Docstrings are excluded — only literals used as real values (assignments,
comparisons, dict keys, function arguments, etc.) count as hits.

Usage::

    # Check against allowlist + grandfather (exit 0 = clean)
    uv run python scripts/check_language_literals.py

    # Generate grandfather output to stdout
    uv run python scripts/check_language_literals.py --write-grandfather

CLI flags:
  --no-location       Omit file:line from violation output (for CI).
  --write-grandfather Scan all files, output grandfather-format lines.
  --check             Default mode: check against allowlist + grandfather.
"""

from __future__ import annotations

import ast
import fnmatch
import re
import sys
from collections import Counter
from pathlib import Path

ALLOWLIST_PATH = Path("tests/language_literals_allowlist.txt")
GRANDFATHER_PATH = Path("tests/language_literals_grandfather.txt")
APP_DIR = Path("app")

_BARE_CODES = {"sl", "no", "nb"}
_NAME_SUBSTRINGS = ("slovene", "slovenian", "norwegian")
_ENGINE_SUBSTRINGS = ("classla", "stanza")
_VOICE_RE = re.compile(r"\b[a-z]{2}-[A-Z]{2}-[A-Za-z]+Neural\b")


# ── Matching ─────────────────────────────────────────────────────────────────


def _matches_language_literal(value: str) -> bool:
    """True if *value* looks like a hardcoded language literal.

    Matches (any of):
    1. Exact bare code: ``"sl"``, ``"no"``, ``"nb"`` (after ``.strip()``).
    2. Name substring (case-insensitive): ``slovene``, ``slovenian``,
       ``norwegian``.
    3. Engine substring (case-insensitive): ``classla``, ``stanza``.
    4. TTS voice id regex: e.g. ``sl-SI-PetraNeural``.
    """
    if value.strip() in _BARE_CODES:
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


def _relative_path(filepath: Path) -> str:
    """Convert an absolute path to one relative to the backend/ root."""
    try:
        return str(filepath.relative_to(Path.cwd()))
    except ValueError:
        return str(filepath)


# ── Allowlist ─────────────────────────────────────────────────────────────────


def load_allowlist(path: Path = ALLOWLIST_PATH) -> list[str]:
    """Return non-empty, non-comment lines from the allowlist file.

    Inline comments (``app/foo.py  # why``) are stripped so the remaining
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


def matches_allowlist(rel_path: str, patterns: list[str]) -> bool:
    """Return True if *rel_path* matches any allowlist glob."""
    return any(fnmatch.fnmatch(rel_path, pat) for pat in patterns)


# ── Grandfather ──────────────────────────────────────────────────────────────


def _escape_literal(value: str) -> str:
    """Escape backslash/tab/newline/carriage-return in *value*.

    Some flagged literals (e.g. multi-line LLM prompt templates) contain
    raw tabs or newlines, which would otherwise split a single grandfather
    record across multiple physical lines and corrupt the file's
    one-record-per-line format. Escaping keeps the round trip intact.
    """
    return value.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n").replace("\r", "\\r")


def _unescape_literal(value: str) -> str:
    """Inverse of :func:`_escape_literal`."""
    mapping = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    result: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value) and value[i + 1] in mapping:
            result.append(mapping[value[i + 1]])
            i += 2
            continue
        result.append(ch)
        i += 1
    return "".join(result)


def _preview(value: str, limit: int = 60) -> str:
    """Single-line, length-capped preview of *value* for terminal output."""
    collapsed = " ".join(value.split())
    if len(collapsed) > limit:
        return collapsed[: limit - 1] + "…"
    return collapsed


def load_grandfather(path: Path = GRANDFATHER_PATH) -> dict[tuple[str, str], int]:
    """Parse the grandfather file into ``{(file, literal): count}``."""
    result: dict[tuple[str, str], int] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split("\t")
        if len(parts) == 3:
            fname, literal, count_str = parts
            try:
                result[(fname, _unescape_literal(literal))] = int(count_str)
            except ValueError:
                print(f"  [WARN] Bad grandfather line: {line}", file=sys.stderr)
    return result


def format_grandfather_line(filepath: str, literal: str, count: int) -> str:
    """Tab-separated grandfather entry (literal escaped for a safe round trip)."""
    return f"{filepath}\t{_escape_literal(literal)}\t{count}"


# ── Main ─────────────────────────────────────────────────────────────────────


def collect_all_hits(
    app_dir: Path = APP_DIR,
    allowlist_path: Path | None = None,
) -> dict[str, Counter]:
    """Scan all ``*.py`` files under *app_dir*, returning
    ``{relative_path: Counter{literal: count}}``.

    Allowlisted files are skipped entirely here, so they never enter the
    grandfather ledger.
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


def do_check(
    app_dir: Path = APP_DIR,
    show_location: bool = True,
) -> int:
    """Check all app files against allowlist + grandfather. Returns exit code."""
    grandfather = load_grandfather()
    by_file = collect_all_hits(app_dir)
    exit_code = 0

    for rel_path, counter in sorted(by_file.items()):
        for literal, count in sorted(counter.items()):
            gf_key = (rel_path, literal)
            preview = _preview(literal)
            if gf_key in grandfather:
                gf_count = grandfather[gf_key]
                if count == gf_count:
                    continue
                if count > gf_count:
                    print(
                        f"FAIL: {rel_path}:{count}x `{preview}` exceeds "
                        f"grandfathered count {gf_count} "
                        "(new violation of grandfathered seam)",
                    )
                    exit_code = 1
                else:
                    print(
                        f"FAIL: {rel_path}:{count}x `{preview}` is below "
                        f"grandfathered count {gf_count} "
                        "(edit the line to match)",
                    )
                    exit_code = 1
            else:
                print(
                    f"FAIL: {rel_path}:{count}x `{preview}` not in allowlist or grandfather",
                )
                exit_code = 1

    return exit_code


def do_write_grandfather(app_dir: Path = APP_DIR, allowlist_path: Path | None = None) -> None:
    """Print grandfather-format lines to stdout, skipping allowlisted files."""
    by_file = collect_all_hits(app_dir, allowlist_path=allowlist_path)
    for rel_path in sorted(by_file):
        counter = by_file[rel_path]
        for literal in sorted(counter):
            print(format_grandfather_line(rel_path, literal, counter[literal]))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Check app-file language literals against allowlist + grandfather.",
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
