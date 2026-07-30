#!/usr/bin/env python3
"""Ledger touch-rule: modifying a grandfathered violation's scope must drain it.

⚠️ **SCAFFOLD ONLY — every function raises NotImplementedError.** This file
exists so the locked guardrails in `tests/test_check_ledger_touch.py` fail
individually and specifically rather than collapsing at import. The executor
implements these bodies until those tests pass, **without editing the tests**.

Design and rationale: `docs/briefs/stage4b-touch-rule-proposal.md`.

THE RULE, in one line: if a commit modifies any line inside the *enclosing scope*
(function / method / class body, else module scope) of a grandfathered violation,
that violation's ledger entry must be gone in the same commit.

Granularity is deliberate and is the whole design:
  * NOT file-level  — over-fires on every unrelated edit to a large module, which
                      is what forced the original design to need a powerful and
                      therefore abusable escape hatch.
  * NOT construct/count-level — `check_mock_boundaries.do_check` ALREADY fails on
                      any count mismatch and on a renamed target, so that keying
                      adds nothing, and it is silent in the one case this rule
                      exists for: a test body refactored with the ledgered mock
                      left byte-identical. See guardrail G1b.
  * Enclosing scope — fires on engagement, not proximity.

Model this module on `check_mock_boundaries.py`: same exit-code style, same
shrink-only spirit, wired into `test.sh`'s backend group and the CI backend job.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from check_mock_boundaries import _is_monkeypatch_setattr, _is_patch

GRANDFATHER_PATH = Path("tests/mock_grandfather.txt")
LITERAL_GRANDFATHER_PATH = Path("tests/language_literals_grandfather.txt")


@dataclass(frozen=True)
class LedgerEntry:
    """One parsed grandfather-ledger entry."""

    file: str
    construct: str
    count: int
    permanent: bool = False
    reason: str = ""


_PERMANENT_RE = re.compile(r"\s*(?:reason:\s*)?PERMANENT\b")


def parse_ledger_line(line: str) -> LedgerEntry | None:
    """Parse `file<TAB>construct<TAB>count  # reason` into a LedgerEntry.

    Returns None for blank lines and whole-line comments. `permanent` is True when
    the trailing reason marks the entry PERMANENT (a checker false positive that
    no correct action can drain).

    ⚠️ The PERMANENT marker rides **inside** the canonical `# reason:` field
    (6d1c455), so the parsed reason begins `reason: `. An earlier
    `reason.startswith("PERMANENT:")` could therefore never match a committed
    line, and production recognised zero permanent entries while the unit tests —
    which fed a bare `# PERMANENT:` that no real ledger uses — stayed green.
    Hence `_PERMANENT_RE`: optional `reason:` prefix, then PERMANENT on a word
    boundary so `PERMANENT:` and `PERMANENT —` both count.

    ⚠️ Split on TAB **first**, then strip a trailing comment from the LAST field
    only — a ledgered construct can itself contain a `#` (the prompts.py entry is
    an entire multi-line planner prompt). Stripping from the first `#` silently
    truncates it.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    parts = stripped.split("\t")
    if len(parts) < 3:
        return None
    file = parts[0]
    construct = parts[1]
    count_str = parts[2]
    comment_pos = count_str.find(" #")
    reason = ""
    if comment_pos != -1:
        reason = count_str[comment_pos + 2 :].lstrip()
        count_str = count_str[:comment_pos].rstrip()
    try:
        count = int(count_str)
    except ValueError:
        return None
    permanent = _PERMANENT_RE.match(reason) is not None
    return LedgerEntry(file=file, construct=construct, count=count, permanent=permanent, reason=reason)


def _all_func_class_ranges(tree: ast.AST) -> list[tuple[int, int]]:
    """Return sorted (start, end) for every FunctionDef/AsyncFunctionDef/ClassDef."""
    ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = node.end_lineno if node.end_lineno is not None else node.lineno
            ranges.append((node.lineno, end))
    return sorted(ranges, key=lambda r: r[0])


def all_scopes(source: str) -> list[tuple[int, int]]:
    """Every scope range in *source* as inclusive 1-based `(start, end)` pairs.

    Functions, async functions, methods and class bodies. Used to distinguish a
    genuine whole-file mechanical change from a targeted edit (G11).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return _all_func_class_ranges(tree)


def _module_gap(lineno: int, func_ranges: list[tuple[int, int]], total_lines: int) -> tuple[int, int]:
    """Return the continuous module-level gap containing *lineno*."""
    prev_end = 0
    for s, e in func_ranges:
        if s > lineno:
            return (prev_end + 1, s - 1)
        prev_end = e
    return (prev_end + 1, total_lines)


def enclosing_scope(source: str, lineno: int) -> tuple[int, int]:
    """Innermost scope range containing *lineno*, else the module range."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return (1, len(source.splitlines()))
    func_ranges = _all_func_class_ranges(tree)
    contained = [(s, e) for s, e in func_ranges if s <= lineno <= e]
    if contained:
        contained.sort(key=lambda r: r[1] - r[0])
        return contained[0]
    return _module_gap(lineno, func_ranges, len(source.splitlines()))


def violation_lines(source: str, construct: str, kind: str) -> list[int]:
    """1-based line numbers where *construct* appears as a violation.

    `kind="mock"`: AST-detected `patch("…")` / `monkeypatch.setattr("…", …)`
    string targets, matching `check_mock_boundaries.py`'s detection — which means
    inheriting its documented blind spot: `patch.object(obj, "name")` and the
    2-arg setattr form are NOT violations here either (G3 pins that limitation
    deliberately; the mock-count ceiling is the guard for that fake).

    `kind="literal"`: source occurrences of the literal.
    """
    if kind == "literal":
        return _literal_violation_lines(source, construct)
    return _mock_violation_lines(source, construct)


def _mock_violation_lines(source: str, construct: str) -> list[int]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (_is_patch(node) or _is_monkeypatch_setattr(node)):
            continue
        target = node.args[0].value
        if target == construct:
            lines.append(node.lineno)
    return lines


def _literal_violation_lines(source: str, construct: str) -> list[int]:
    result: list[int] = []
    for i, line in enumerate(source.splitlines(), 1):
        if construct in line:
            result.append(i)
    return result


def drainable_count(entries: list[LedgerEntry]) -> int:
    """Entries that represent real, drainable debt (excludes PERMANENT)."""
    return sum(1 for e in entries if not e.permanent)


def permanent_count(entries: list[LedgerEntry]) -> int:
    """Entries classed PERMANENT. Printed every run so growth stays visible."""
    return sum(1 for e in entries if e.permanent)


def _infer_kind(file: str) -> str:
    """Infer whether an entry is a mock or literal from its file path."""
    if file.startswith("tests/"):
        return "mock"
    return "literal"


def check_touch(
    entries: list[LedgerEntry],
    changed_lines: dict[str, set[int]],
    sources: dict[str, str],
) -> list[str]:
    """Return one failure message per violated entry; empty list means pass.

    **Must be pure** — no git, no filesystem, no environment reads. That purity is
    what makes the guardrails deterministic (G6), and it is why no environment
    variable can suppress the rule (G4).

    Each message must name the file and the construct, state that there is **no
    bypass**, and must not mention pragmas or any flag: an agent does what the
    failure text says, so a message hinting at an escape gets the escape used
    (G9).
    """
    messages: list[str] = []
    for entry in entries:
        if entry.permanent:
            continue
        if entry.file not in changed_lines:
            continue
        source = sources.get(entry.file)
        if source is None:
            continue
        kind = _infer_kind(entry.file)
        violation_linenos = violation_lines(source, entry.construct, kind)
        scopes = all_scopes(source)
        file_changed = changed_lines[entry.file]

        scopes_touched = sum(1 for s, e in scopes if any(s <= cl <= e for cl in file_changed))

        if len(scopes) > 1 and scopes_touched == len(scopes):
            if violation_linenos and len(violation_linenos) == entry.count:
                continue
            messages.append(
                f"LEDGER TOUCH-RULE: you modified a grandfathered violation.\n\n"
                f"  entry:     {entry.file}\n"
                f"  construct: {entry.construct}\n\n"
                f"You changed this violation while working on it, so the context is already loaded. "
                f"Required: remove the construct and delete the ledger line in this same commit.\n\n"
                f"There is no bypass. If this violation cannot be drained, it does not "
                f"belong in this ledger \u2014 report it to the orchestrator."
            )
            continue

        if not violation_linenos:
            continue
        # EVERY occurrence's scope counts, not just the first: an entry with
        # count > 1 routinely spreads across several test functions (e.g.
        # `sync_push 6`), and checking only violation_linenos[0] let an edit to
        # any later occurrence's scope pass silently. Guardrail G13 pins this.
        violation_scopes = [enclosing_scope(source, vln) for vln in violation_linenos]
        if any(s <= cl <= e for s, e in violation_scopes for cl in file_changed):
            messages.append(
                f"LEDGER TOUCH-RULE: you modified a grandfathered violation.\n\n"
                f"  entry:     {entry.file}\n"
                f"  construct: {entry.construct}\n\n"
                f"You changed this violation while working on it, so the context is already loaded. "
                f"Required: remove the construct and delete the ledger line in this same commit.\n\n"
                f"There is no bypass. If this violation cannot be drained, it does not "
                f"belong in this ledger \u2014 report it to the orchestrator."
            )
    return messages


def _parse_diff(diff_output: str) -> dict[str, set[int]]:
    """Parse `git diff --unified=0` output into {path: set of changed lines}."""
    result: dict[str, set[int]] = {}
    current_file: str | None = None
    for line in diff_output.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
            if current_file not in result:
                result[current_file] = set()
        elif line.startswith("@@"):
            if current_file is None:
                continue
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                for i in range(start, start + count):
                    result[current_file].add(i)
    return result


def resolve_changed(
    git: Callable[[list[str]], str] | None = None,
) -> tuple[str, dict[str, set[int]]]:
    """Return `(mode, {path: changed line numbers})`.

    *git* is an injectable command runner so tests never shell out; the default
    is git-backed. Modes:
      * `merge-base(main)` — CI: diff against the merge-base with main.
      * `worktree`         — local: `git diff HEAD` plus `--cached`.
      * `skipped(<why>)`   — detached HEAD, shallow clone, or no reachable main.

    Degradation must **print a notice and skip** — never a silent pass (which
    makes the rule a no-op nobody notices) and never a hard fail (which makes it
    the first thing removed). See G5.
    """
    if git is None:

        def _git(args: list[str]) -> str:
            return subprocess.check_output(["git", *args], text=True)
    else:
        _git = git

    # UNION both sources; never choose one. Choosing merge-base first and falling
    # back to worktree only on *exception* made the rule a SILENT NO-OP locally:
    # on branch `main`, merge-base(HEAD, main) == HEAD, so the diff is legitimately
    # empty, no exception is raised, and the working tree is never examined. Found
    # by live drill 2026-07-29 — an edit inside a real ledgered violation's scope
    # exited 0 both unstaged and staged. All 27 injected-changed-set guardrails
    # passed while the wired gate was inert, which is exactly the failure the brief
    # warned about ("silently no-ops locally and only fires in CI is worse than no
    # rule"). Union is also env-independent: keying the mode off $CI would hand an
    # agent a one-variable bypass. G14 pins this.
    diffs: list[str] = []
    mode_parts: list[str] = []
    try:
        merge_base = _git(["merge-base", "HEAD", "main"]).strip()
        head = _git(["rev-parse", "HEAD"]).strip()
        if merge_base and merge_base != head:
            diffs.append(_git(["diff", "--unified=0", merge_base, "HEAD"]))
            mode_parts.append("merge-base(main)")
    except Exception:  # noqa: BLE001 — any git failure just drops this source
        pass
    try:
        diffs.append(_git(["diff", "--unified=0", "HEAD"]))
        diffs.append(_git(["diff", "--unified=0", "--cached"]))
        mode_parts.append("worktree")
    except Exception:  # noqa: BLE001 — any git failure just drops this source
        pass

    if not mode_parts:
        print("touch-rule: mode=skipped(no reachable main nor working tree)")
        return ("skipped(no reachable main)", {})

    # PATH DOMAIN: git reports paths relative to the REPO ROOT
    # (`backend/tests/test_x.py`), while both ledgers key on paths relative to
    # `backend/` (`tests/test_x.py`) — the domain the sibling checkers use via
    # `_relative_path`. Without this translation the two key sets NEVER intersect
    # and the rule is inert no matter which mode was selected. Found by live drill
    # 2026-07-29, after the mode fix: `mode=worktree` with a real ledgered
    # violation edited, still exit 0. `rev-parse --show-prefix` gives this
    # directory's path relative to the repo root (e.g. `backend/`), so it stays
    # correct if the layout moves — do not hardcode "backend/". Paths outside the
    # prefix are dropped: they cannot carry a ledger entry. G15 pins this.
    prefix = ""
    try:
        prefix = _git(["rev-parse", "--show-prefix"]).strip()
    except Exception:  # noqa: BLE001 — no prefix available; assume repo root
        prefix = ""

    raw = _parse_diff("\n".join(diffs))
    if not prefix:
        return ("+".join(mode_parts), raw)

    changed: dict[str, set[int]] = {}
    for path, lines in raw.items():
        if path.startswith(prefix):
            changed[path[len(prefix) :]] = lines
    return ("+".join(mode_parts), changed)


def _load_entries() -> list[LedgerEntry]:
    """Load entries from both grandfather files."""
    entries: list[LedgerEntry] = []
    for gf_path in (GRANDFATHER_PATH, LITERAL_GRANDFATHER_PATH):
        if not gf_path.exists():
            continue
        for line in gf_path.read_text(encoding="utf-8").splitlines():
            entry = parse_ledger_line(line)
            if entry is not None:
                entries.append(entry)
    return entries


def main() -> int:
    """Print exactly one `touch-rule: mode=…` line (G10), then check.

    Exposes **no** skip/disable/bypass flag — G4b greps this file for one.
    """
    mode, changed = resolve_changed()
    print(f"touch-rule: mode={mode}")

    # Printed on EVERY run, including skips: an exemption class nobody counts is
    # how PERMANENT quietly becomes a dumping ground. Real debt is the first
    # number; the second must only ever shrink or be argued for.
    entries = _load_entries()
    print(f"ledger: {drainable_count(entries)} drainable, {permanent_count(entries)} permanent")

    if mode.startswith("skip"):
        return 0

    sources: dict[str, str] = {}
    for filepath in changed:
        p = Path(filepath)
        if p.exists():
            sources[filepath] = p.read_text(encoding="utf-8")

    messages = check_touch(entries, changed, sources)
    if messages:
        for msg in messages:
            print(msg)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(main())
