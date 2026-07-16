#!/usr/bin/env python3
"""AST-based checker that forbids core modules from importing ``app.plugins.*``.

The plugin architecture requires that ``app/`` (outside ``app/plugins/``) never
imports a concrete plugin module — language-specific behaviour is accessed
through the registry accessors in ``app/languages.py``.  A module-level plugin
import from core creates a hard coupling: deleting the plugin folder crashes the
app.

Sanctioned exceptions (see inline comments):

1. ``app/languages.py`` imports the namespace package ``app.plugins.languages``
   for discovery — but never any subpackage of it at module level.
2. Function-level (lazy) imports of ``app.plugins.anki_sync.*`` are allowed
   only in ``app/api/anki.py`` and ``app/api/admin.py``.  Module-level imports
   of ``anki_sync`` from core are violations everywhere.

Usage::

    uv run python scripts/check_plugin_imports.py

Exit 0 = clean, exit 1 = violation found.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

APP_DIR = Path("app")

# ── Sanctioned exceptions ──────────────────────────────────────────────────

# Core files allowed to function-level (lazy) import ``app.plugins.anki_sync.*``.
# Module-level imports from these files are still violations.
ANKI_SYNC_LAZY_ALLOW: frozenset[str] = frozenset({
    "app/api/anki.py",
    "app/api/admin.py",
})


def _is_under(path: str, prefix: str) -> bool:
    """True when *path* starts with *prefix* (component-level match)."""
    return path == prefix or path.startswith(prefix + "/")


def _is_in_plugins(rel_path: str) -> bool:
    """True when *rel_path* lives inside ``app/plugins/``."""
    return _is_under(rel_path, "app/plugins")


def _is_languages_discovery(rel_path: str, module: str) -> bool:
    """True when the import is the ``app.plugins.languages`` namespace (discovery)
    and the importer is ``app/languages.py``."""
    return rel_path == "app/languages.py" and module == "app.plugins.languages"


def _is_anki_sync_lazy_allow(rel_path: str) -> bool:
    """True when *rel_path* is in the lazy-import allowlist for anki_sync."""
    return rel_path in ANKI_SYNC_LAZY_ALLOW


# ── AST scanning ───────────────────────────────────────────────────────────


def _check_file(filepath: Path, app_dir: Path = APP_DIR) -> list[tuple[int, str]]:
    """Return ``[(lineno, message), ...]`` for plugin-import violations."""
    source = filepath.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError:
        return []

    rel = str(filepath.relative_to(app_dir.parent))
    hits: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        # --- import app.plugins.X  ---
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not alias.name.startswith("app.plugins"):
                    continue
                hits.append((node.lineno, alias.name))
        # --- from app.plugins.X import ... ---
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app.plugins"):
            hits.append((node.lineno, node.module))

    if not hits:
        return []

    violations: list[tuple[int, str]] = []
    for lineno, module in hits:
        # Plugin-internal imports always pass.
        if _is_in_plugins(rel):
            continue

        # Exception 1: languages.py importing the namespace package.
        if _is_languages_discovery(rel, module):
            continue

        # Exception 2: function-level anki_sync imports in allowlisted files.
        # We only skip when the import is NOT at module level.  Detecting
        # function-level requires walking the AST more carefully — we already
        # have the node, so check if it's directly inside a FunctionDef body.
        if module.startswith("app.plugins.anki_sync") and _is_anki_sync_lazy_allow(rel):
            # Check if this node is at module level (inside Module body) — if so,
            # it's a violation.  If it's inside a FunctionDef/AsyncFunctionDef,
            # it's an allowed lazy import.
            if _is_inside_function(tree, lineno):
                continue

        violations.append((lineno, module))

    return violations


def _is_inside_function(tree: ast.Module, target_line: int) -> bool:
    """Return True if *target_line* is inside a FunctionDef or AsyncFunctionDef."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.lineno > target_line:
            continue
        # Walk the function body to find the target line.
        for child in ast.walk(node):
            if hasattr(child, "lineno") and child.lineno == target_line:
                return True
    return False


# ── Main ───────────────────────────────────────────────────────────────────


def do_check(app_dir: Path = APP_DIR) -> int:
    """Scan all ``app/**/*.py`` for plugin-import violations.  Returns exit code."""
    exit_code = 0
    for pyfile in sorted(app_dir.rglob("*.py")):
        if "__pycache__" in pyfile.parts:
            continue
        violations = _check_file(pyfile, app_dir)
        for lineno, module in violations:
            rel = str(pyfile.relative_to(app_dir.parent))
            print(
                f"FAIL: {rel}:{lineno} — imports {module!r} from core. "
                "Route through the registry accessors in app/languages.py instead."
            )
            exit_code = 1
    return exit_code


def main() -> int:
    return do_check()


if __name__ == "__main__":
    sys.exit(main())
