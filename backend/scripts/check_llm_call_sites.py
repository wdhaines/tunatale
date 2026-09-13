#!/usr/bin/env python3
"""AST-based checker that flags unlabelled LLM ``.complete()`` calls in product code.

Scans ``backend/app/**/*.py`` for any ``<receiver>.complete(...)`` call that
does not pass an explicit ``call_site=`` keyword, and fails. The Groq ledger's
sixth field is call-site attribution (bead 6zzu2 Stage 2); a missed label
silently files the spend under ``-`` — the exact hole the bead exists to close
— because ``LLMClient.complete(call_site="")`` has a default by design.

ZERO TOLERANCE, like the sibling checkers: there is no allowlist and no
grandfather ledger. The fix for a violation is to add the label.

What counts as a label (the property):
- only an EXPLICIT ``call_site=`` keyword argument satisfies; a ``**kwargs``
  splat cannot prove the label is being forwarded — it may omit it, so
  cassette.py's ``_patch`` forwards it by name;
- ``call_site_id=`` does not satisfy (a substring is not the keyword);
- a call whose ``call_site=`` sits several lines below the ``.complete(`` is
  the normal repo formatting and DOES satisfy — the check is AST-based, so
  line scoping is a trap a regex would fall into;
- ``is_complete(...)`` / ``obj.mark_complete(...)`` are different methods —
  only the attribute ``complete`` is checked.

Not scanned:
- ``backend/tests/**`` — tests drive the helpers from outside
  (test_cloze_quality.py's 25-site sweep) and deliberately omit labels;
- ``__pycache__`` build artifacts and ``__init__.py`` (the shared walker
  skips both).

⚠️ KNOWN LATENT HOLES, measured by adversarial probe 2026-09-13 (20 cases, ZERO
false positives — including the multi-line ``call_site=``, where a line-scoped
regex would false-positive and block every commit). Three shapes are NOT
flagged. All three are latent: a grep of ``backend/app`` confirms none of them
occurs today, and every ``.complete()`` receiver in the tree is an LLM client,
so there is no foreign-method false positive either.

1. ``getattr(client, "complete")(...)`` — the call's ``func`` is a Call, not an
   Attribute, so the attribute name is never seen.
2. ``f = client.complete`` then ``f(...)`` — a bound-method alias. Resolving it
   needs dataflow, not an AST shape.
3. A call inside an ``__init__.py`` — ``_checker_lib.collect_all_hits`` skips
   every ``__init__.py``, a property SHARED with check_mock_boundaries.py and
   check_date_today.py. Do not "fix" it here alone; changing the shared walker
   changes three zero-tolerance gates at once.

If a future refactor introduces shape 1 or 2, this gate goes quiet rather than
red — so prefer a direct ``<client>.complete(...)`` call at every site.

Usage::

    # exit 0 = clean
    uv run python scripts/check_llm_call_sites.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from _checker_lib import collect_all_hits

APP_DIR = Path("app")


def _is_complete_call(call_node: ast.Call) -> bool:
    """True if *call_node* invokes any ``<receiver>.complete(...)`` method."""
    func = call_node.func
    return isinstance(func, ast.Attribute) and func.attr == "complete"


def _has_call_site_kwarg(call_node: ast.Call) -> bool:
    """True if the call passes an EXPLICIT ``call_site=`` keyword.

    Only a keyword whose *name* is exactly ``call_site`` counts. A ``**kwargs``
    splat has ``kw.arg is None``, so it can never satisfy this — that is
    deliberate (see the module docstring).
    """
    return any(kw.arg == "call_site" for kw in call_node.keywords)


def scan_file(filepath: Path) -> list[tuple[str, int]]:
    """Return ``[("func_repr@line", lineno), …]`` for every unlabelled complete call.

    The first element carries the line number inline so ``collect_all_hits``'
    Counter (which keeps only the target) can still drive an "at line <n>"
    failure message; the second element is the real lineno for direct callers.
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
        if not _is_complete_call(node):
            continue
        if _has_call_site_kwarg(node):
            continue
        func_repr = ast.unparse(node.func)
        hits.append((f"{func_repr}@{node.lineno}", node.lineno))
    return hits


def do_check(app_dir: Path = APP_DIR) -> int:
    """Check every ``.complete()`` under *app_dir* for an explicit label.

    Returns exit code. Zero tolerance: any unlabelled call is a silent ``-``
    bucket, and a silently-merged bucket is the thing this bead exists to fix.
    """
    by_file = collect_all_hits(app_dir, scan_file)
    exit_code = 0

    for rel_path, counter in sorted(by_file.items()):
        for target, count in sorted(counter.items()):
            func_name, lineno = target.rsplit("@", 1)
            print(
                f"FAIL: {rel_path}:{count}x `{func_name}(` at line {lineno} is missing call_site=.\n"
                "  Fix: pass call_site=<label> using the CallSite constants in "
                "app/llm/call_sites.py — e.g. CallSite.STORY, and "
                "CallSite.compose(caller, operation) from the cloze helpers. A "
                "**kwargs splat does not count: only an explicit call_site= "
                "keyword proves the label.",
            )
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(do_check())
