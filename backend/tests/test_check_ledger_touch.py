"""Guardrail tests for the ledger touch-rule (scripts/check_ledger_touch.py).

⚠️ LOCKED GUARDRAILS — authored before the implementation exists. The executor
implements `scripts/check_ledger_touch.py` until these pass, and **must not edit
this file**. If a test here looks wrong, stop and report it rather than changing
it: each one pins a specific wrong-but-obvious implementation.

Design rationale lives in `docs/briefs/stage4b-touch-rule-proposal.md`.

THE RULE: a commit that modifies the *enclosing scope* of a grandfathered
violation must drain that violation's ledger entry in the same commit.
Scope-level — not file-level (over-fires on any unrelated edit) and not
construct/count-level (which the existing ratchet already covers, and which is
SILENT in the one case this rule exists for; see G1b).

API CONTRACT this file pins:

    @dataclass(frozen=True)
    class LedgerEntry:
        file: str; construct: str; count: int; permanent: bool; reason: str

    parse_ledger_line(line: str) -> LedgerEntry | None
    all_scopes(source: str) -> list[tuple[int, int]]        # every scope range
    enclosing_scope(source: str, lineno: int) -> tuple[int, int]
    violation_lines(source: str, construct: str, kind: str) -> list[int]
    drainable_count(entries) -> int                          # excludes PERMANENT
    permanent_count(entries) -> int
    check_touch(entries, changed_lines, sources) -> list[str]   # PURE. [] == pass
    resolve_changed(git=None) -> tuple[str, dict[str, set[int]]]  # (mode, changed)
    main() -> int

`check_touch` must be pure: no git, no filesystem, no environment reads. That is
what makes these tests deterministic (G6).
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow importing from scripts/ one level up.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from check_ledger_touch import (  # noqa: E402
    GRANDFATHER_PATH,
    LITERAL_GRANDFATHER_PATH,
    LedgerEntry,
    _load_entries,
    all_scopes,
    check_touch,
    drainable_count,
    enclosing_scope,
    main,
    parse_ledger_line,
    permanent_count,
    resolve_changed,
    violation_lines,
)


# ── Fixtures: synthetic sources, never real repo files ────────────────────────

# Two functions. The ledgered violation is in `test_with_mock` (lines 6-9);
# `test_unrelated` (lines 2-4) has nothing to do with it.
TWO_FUNCS = """\
def test_unrelated():
    value = 1
    assert value == 1


def test_with_mock(monkeypatch):
    monkeypatch.setattr("app.srs.queue_stats.resolve_learning_steps", lambda db=None: ([1.0], "x"))
    result = 2
    assert result == 2
"""

MOCK_TARGET = "app.srs.queue_stats.resolve_learning_steps"


def _entry(
    file: str = "tests/test_sample.py",
    construct: str = MOCK_TARGET,
    count: int = 1,
    *,
    permanent: bool = False,
    reason: str = "",
) -> LedgerEntry:
    return LedgerEntry(file=file, construct=construct, count=count, permanent=permanent, reason=reason)


def _scope_of_violation(source: str, construct: str, kind: str = "mock") -> tuple[int, int]:
    lines = violation_lines(source, construct, kind)
    assert lines, "fixture is broken: no violation found in source"
    return enclosing_scope(source, lines[0])


# ── G1: must NOT be file-level ────────────────────────────────────────────────


def test_g1_unrelated_scope_in_ledgered_file_does_not_fire():
    """A changed line in a DIFFERENT function of a ledgered file must not fire.

    Guards the easy wrong implementation: file-level touching. That over-fires on
    every unrelated edit, which is what made the original design need a powerful
    escape hatch.
    """
    entries = [_entry()]
    changed = {"tests/test_sample.py": {2, 3}}  # inside test_unrelated only
    msgs = check_touch(entries, changed, {"tests/test_sample.py": TWO_FUNCS})
    assert msgs == [], f"fired on an unrelated scope: {msgs}"


# ── G1b: must NOT be construct/count-level — THE central case ─────────────────


def test_g1b_edit_inside_violation_scope_fires_even_when_construct_identical():
    """Changed line inside the violation's OWN function, construct and count
    byte-identical → MUST fire.

    This is the case the whole rule exists for, and the one a construct/count
    keyed implementation silently misses (the existing ratchet already covers
    count changes, so such an implementation adds nothing). An earlier draft of
    the proposal specified exactly that wrong design — this test is the trap.
    """
    entries = [_entry()]
    start, end = _scope_of_violation(TWO_FUNCS, MOCK_TARGET)
    # Change a line inside the violation's function that is NOT the mock line.
    changed_line = end  # the trailing `assert result == 2`
    assert start < changed_line, "fixture should have a line after the mock"
    msgs = check_touch(
        entries,
        {"tests/test_sample.py": {changed_line}},
        {"tests/test_sample.py": TWO_FUNCS},
    )
    assert msgs, "did NOT fire on an edit inside the violation's own scope"
    assert MOCK_TARGET in msgs[0]


# ── G2: scope-boundary off-by-one ─────────────────────────────────────────────


@pytest.mark.parametrize("boundary", ["start", "end"])
def test_g2_changed_line_on_scope_boundary_fires(boundary: str):
    """A changed line on the first or last line of the enclosing scope fires.

    Off-by-one in the range intersection is the likely bug; test both ends.
    """
    entries = [_entry()]
    start, end = _scope_of_violation(TWO_FUNCS, MOCK_TARGET)
    line = start if boundary == "start" else end
    msgs = check_touch(entries, {"tests/test_sample.py": {line}}, {"tests/test_sample.py": TWO_FUNCS})
    assert msgs, f"did not fire on the {boundary} boundary (line {line})"


# ── G3: the unpoliced-rewrite blind spot is INHERITED, not covered ────────────


def test_g3_object_form_mock_is_not_detected_documenting_the_blind_spot():
    """`patch.object(obj, "name")` is NOT detected as a violation.

    This pins a known LIMITATION so it stays conscious. The touch-rule inherits
    check_mock_boundaries.py's documented blind spot: rewriting a string-target
    mock into the object form deletes the ledger line while leaving the mock in
    place, and NEITHER checker sees it. The guard for that fake is the
    mock-count ceiling in the audit protocol, not this rule. Do not "fix" this
    test by teaching the detector object-form mocks without also updating the
    ledgers, or the ratchet will report a flood of new violations.
    """
    object_form = """\
def test_thing():
    with patch.object(SRSDatabase, "__init__", _fail):
        pass
"""
    assert violation_lines(object_form, "app.srs.database.SRSDatabase.__init__", "mock") == []


# ── G4: no bypass may exist, now or later ─────────────────────────────────────


@pytest.mark.parametrize(
    "var",
    ["LEDGER_SKIP", "SKIP_CHECKS", "NO_TOUCH_RULE", "LEDGER_TOUCH_DISABLE", "CI"],
)
def test_g4_no_environment_variable_suppresses_a_firing_rule(monkeypatch, var: str):
    """No env var may turn a firing rule off. check_touch is pure."""
    monkeypatch.setenv(var, "1")
    entries = [_entry()]
    start, end = _scope_of_violation(TWO_FUNCS, MOCK_TARGET)
    msgs = check_touch(entries, {"tests/test_sample.py": {end}}, {"tests/test_sample.py": TWO_FUNCS})
    assert msgs, f"{var}=1 suppressed the rule"


def test_g4b_no_bypass_flag_in_the_cli():
    """The CLI must expose no skip/disable/bypass flag.

    Guards against one being added later "just for CI". An agent will use any
    bypass that exists, so the affordance must be absent rather than discouraged.
    """
    source = (_SCRIPTS / "check_ledger_touch.py").read_text(encoding="utf-8")
    for forbidden in ("--no-touch-rule", "--skip", "--disable", "--bypass", "--force"):
        assert forbidden not in source, f"CLI exposes a bypass flag: {forbidden}"


# ── G5: degradation must be loud, never silent, never fatal ───────────────────


def test_g5_missing_main_degrades_to_a_skip_with_a_printed_notice(capsys):
    """No reachable `main` → skip mode AND a printed notice.

    A silent pass and a hard fail are both failures of this stage.
    """

    def fake_git(args: list[str]) -> str:
        msg = "fatal: bad revision 'main'"
        raise RuntimeError(msg)

    mode, changed = resolve_changed(git=fake_git)
    assert "skip" in mode.lower(), f"expected a skip mode, got {mode!r}"
    assert changed == {}
    printed = capsys.readouterr().out
    assert printed.strip(), "degraded silently — no notice printed"
    assert "main" in printed.lower() or "skip" in printed.lower()


# ── G6: determinism — no git, no filesystem in the pure path ──────────────────


def test_g6_check_touch_is_pure_and_never_shells_out(monkeypatch):
    """check_touch must work with subprocess entirely unavailable."""
    import subprocess

    def explode(*a, **k):  # noqa: ANN002, ANN003
        msg = "check_touch must not shell out"
        raise AssertionError(msg)

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "check_output", explode)
    entries = [_entry()]
    _start, end = _scope_of_violation(TWO_FUNCS, MOCK_TARGET)
    msgs = check_touch(entries, {"tests/test_sample.py": {end}}, {"tests/test_sample.py": TWO_FUNCS})
    assert msgs


# ── G7 / G8: the PERMANENT class ──────────────────────────────────────────────


def test_g7_permanent_entry_never_fires_even_when_its_scope_is_touched():
    """PERMANENT entries are exempt from the touch-rule, unconditionally.

    A rule that demands the impossible gets disabled. pixabay.py's ledgered "no"
    is the English word (a dict key), not a language code — no correct action can
    drain it.
    """
    entries = [_entry(permanent=True, reason="PERMANENT: English word, dict key")]
    _start, end = _scope_of_violation(TWO_FUNCS, MOCK_TARGET)
    msgs = check_touch(entries, {"tests/test_sample.py": {end}}, {"tests/test_sample.py": TWO_FUNCS})
    assert msgs == [], f"PERMANENT entry fired: {msgs}"


def test_g8_permanent_is_excluded_from_the_drainable_count_but_still_counted():
    """PERMANENT must not inflate the drainable-debt number, and must stay visible.

    Exempt from the touch-rule is NOT exempt from the ratchet or from reporting;
    anonymous non-debt inflating the headline is the burn-down's own thesis of
    hopelessness.
    """
    entries = [
        _entry(construct="real.debt.one"),
        _entry(construct="real.debt.two"),
        _entry(construct="no", permanent=True, reason="PERMANENT: English word"),
    ]
    assert drainable_count(entries) == 2
    assert permanent_count(entries) == 1


def test_g8b_permanent_marker_is_parsed_from_the_ledger_line_not_guessed():
    """PERMANENT is a parsed class, keyed off the reason field written in 4a."""
    permanent = parse_ledger_line('app/cards/media/pixabay.py\tno\t1  # PERMANENT: English word "no", dict key')
    assert permanent is not None
    assert permanent.permanent is True
    assert permanent.count == 1
    assert permanent.construct == "no"

    ordinary = parse_ledger_line("tests/test_queue_stats.py\tapp.srs.database.SRSDatabase.__init__\t5  # reason: DEFER")
    assert ordinary is not None
    assert ordinary.permanent is False
    assert ordinary.count == 5


# ── G9: the error message is the instruction set ──────────────────────────────


def test_g9_failure_message_is_actionable_and_advertises_no_escape():
    """An agent does what the failure text says, so pin what it says.

    Must name the file and construct, must state there is no bypass, and must not
    mention pragmas or any flag — a message that hints at an escape gets the
    escape used.
    """
    entries = [_entry()]
    _start, end = _scope_of_violation(TWO_FUNCS, MOCK_TARGET)
    msgs = check_touch(entries, {"tests/test_sample.py": {end}}, {"tests/test_sample.py": TWO_FUNCS})
    assert msgs
    blob = "\n".join(msgs).lower()
    assert "tests/test_sample.py" in blob
    assert MOCK_TARGET.lower() in blob
    assert "no bypass" in blob
    assert "pragma" not in blob
    assert "--skip" not in blob


# ── G10: the mode line must always be printed ─────────────────────────────────


def test_g10_main_prints_exactly_one_mode_line(capsys):
    """Every run declares which changed-set mode it chose."""
    main()
    out = capsys.readouterr().out
    mode_lines = [ln for ln in out.splitlines() if "touch-rule: mode=" in ln]
    assert len(mode_lines) == 1, f"expected exactly one mode line, got {mode_lines}"


# ── G11: the mechanical-churn allowance must not become a general bypass ──────

# Three scopes; the violation is in the middle one.
THREE_FUNCS = """\
def test_a():
    assert 1 == 1


def test_b(monkeypatch):
    monkeypatch.setattr("app.srs.queue_stats.resolve_learning_steps", lambda db=None: ([1.0], "x"))
    assert 2 == 2


def test_c():
    assert 3 == 3
"""

ONE_FUNC = """\
def test_only(monkeypatch):
    monkeypatch.setattr("app.srs.queue_stats.resolve_learning_steps", lambda db=None: ([1.0], "x"))
    assert 1 == 1
"""


def test_g11a_whole_file_churn_with_identical_construct_does_not_fire():
    """A formatter sweep touches every scope but engages with nothing.

    `ruff format` runs inside the gate, so this is not hypothetical.
    """
    entries = [_entry(file="tests/test_three.py")]
    all_lines = set(range(1, len(THREE_FUNCS.splitlines()) + 1))
    msgs = check_touch(entries, {"tests/test_three.py": all_lines}, {"tests/test_three.py": THREE_FUNCS})
    assert msgs == [], f"mechanical churn fired: {msgs}"


def test_g11b_whole_file_churn_fires_when_the_construct_itself_changed():
    """The churn allowance requires the violation to be textually untouched.

    Here the ledgered construct no longer appears in the source at all, so the
    "unchanged" precondition fails and the rule must fire.
    """
    entries = [_entry(file="tests/test_three.py", construct="app.srs.queue_stats.renamed_resolver")]
    all_lines = set(range(1, len(THREE_FUNCS.splitlines()) + 1))
    msgs = check_touch(entries, {"tests/test_three.py": all_lines}, {"tests/test_three.py": THREE_FUNCS})
    assert msgs, "construct changed under a whole-file edit and the rule stayed silent"


def test_g11c_single_scope_file_is_not_vacuous_churn():
    """ "All scopes touched" must mean MANY scopes, not vacuously all-of-one.

    Otherwise every edit to a one-test file is laundered as mechanical churn —
    the cheapest possible route around the rule.
    """
    entries = [_entry(file="tests/test_one.py")]
    all_lines = set(range(1, len(ONE_FUNC.splitlines()) + 1))
    msgs = check_touch(entries, {"tests/test_one.py": all_lines}, {"tests/test_one.py": ONE_FUNC})
    assert msgs, "a one-scope file was treated as mechanical churn"


# ── G12: module-scope violations ──────────────────────────────────────────────


MODULE_SCOPE = """\
DEFAULT_VOICE = "sl-SI-PetraNeural"


def unrelated_helper():
    return 1
"""


def test_g12_module_scope_violation_fires_on_module_level_edit():
    """A violation outside any function belongs to module scope."""
    entries = [_entry(file="app/audio/x.py", construct="sl-SI-PetraNeural", count=1)]
    msgs = check_touch(entries, {"app/audio/x.py": {1}}, {"app/audio/x.py": MODULE_SCOPE})
    assert msgs, "module-scope violation did not fire on a module-level edit"


def test_g12b_module_scope_violation_ignores_unrelated_function_edits():
    """Module scope must not swallow the whole file."""
    entries = [_entry(file="app/audio/x.py", construct="sl-SI-PetraNeural", count=1)]
    msgs = check_touch(entries, {"app/audio/x.py": {5}}, {"app/audio/x.py": MODULE_SCOPE})
    assert msgs == [], f"module-scope entry fired on an unrelated function edit: {msgs}"


# ── scope helpers ─────────────────────────────────────────────────────────────


TWO_OCCURRENCES = """\
def test_first(monkeypatch):
    monkeypatch.setattr("app.x.y", 1)
    assert 1


def test_second(monkeypatch):
    monkeypatch.setattr("app.x.y", 2)
    assert 2
"""


@pytest.mark.parametrize(("changed", "which"), [(3, "first"), (8, "second")])
def test_g13_every_occurrence_scope_counts_not_just_the_first(changed: int, which: str):
    """An entry with count > 1 spread over several functions fires from ANY of them.

    Found by audit 2026-07-29: the first implementation checked only
    `violation_linenos[0]`, so an edit inside the SECOND occurrence's function
    passed silently. Entries like `sync_push 6` span multiple test functions, so
    this was a live hole, not a hypothetical one. Both parametrisations must fire;
    a first-occurrence-only implementation passes `first` and fails `second`.
    """
    entries = [_entry(file="tests/t.py", construct="app.x.y", count=2)]
    msgs = check_touch(entries, {"tests/t.py": {changed}}, {"tests/t.py": TWO_OCCURRENCES})
    assert msgs, f"edit in the {which} occurrence's scope did not fire"


def test_g14_on_main_with_a_dirty_worktree_the_changed_set_is_not_empty():
    """The local leg must examine the WORKING TREE, not just the merge-base.

    Found by live drill 2026-07-29: choosing merge-base first and falling back to
    worktree only on *exception* made the rule a silent no-op locally. On branch
    `main`, merge-base(HEAD, main) == HEAD, the diff is legitimately empty, no
    exception fires, and the working tree is never looked at. An edit inside a
    real ledgered violation's scope exited 0.

    Every injected-changed-set guardrail passed while the wired gate was inert —
    which is why this test drives `resolve_changed` itself.
    """
    calls: list[str] = []
    worktree_diff = "diff --git a/tests/t.py b/tests/t.py\n--- a/tests/t.py\n+++ b/tests/t.py\n@@ -3 +3 @@\n+changed\n"

    def fake_git(args: list[str]) -> str:
        calls.append(" ".join(args))
        if args[0] == "merge-base":
            return "deadbeef\n"
        if args[0] == "rev-parse" and "--show-prefix" in args:
            return "\n"  # checker running at the repo root: no prefix
        if args[0] == "rev-parse":
            return "deadbeef\n"  # merge-base == HEAD, i.e. we are ON main
        if args[0] == "diff" and "--cached" not in args:
            return worktree_diff
        return ""

    mode, changed = resolve_changed(git=fake_git)
    assert "skip" not in mode.lower(), f"degraded when git was fine: {mode}"
    assert "worktree" in mode, f"worktree source not consulted; mode={mode}"
    assert changed, "changed set empty despite a dirty worktree — the rule is inert"
    assert any(c.startswith("diff --unified=0 HEAD") for c in calls), f"never diffed the working tree; calls={calls}"


def test_g14b_mode_selection_is_not_switchable_by_environment(monkeypatch):
    """Mode must not key off $CI — that would be a one-variable bypass.

    On main with a dirty tree, merge-base contributes nothing; if $CI could force
    merge-base-only, setting it would silence the rule locally.
    """
    monkeypatch.setenv("CI", "true")

    def fake_git(args: list[str]) -> str:
        if args[0] == "rev-parse" and "--show-prefix" in args:
            return "\n"  # repo root: no prefix to strip
        if args[0] in {"merge-base", "rev-parse"}:
            return "deadbeef\n"
        if args[0] == "diff" and "--cached" not in args:
            return "diff --git a/tests/t.py b/tests/t.py\n--- a/tests/t.py\n+++ b/tests/t.py\n@@ -3 +3 @@\n+changed\n"
        return ""

    mode, changed = resolve_changed(git=fake_git)
    assert changed, f"CI=true emptied the changed set (mode={mode})"


def test_g15_git_paths_are_translated_into_the_ledger_path_domain():
    """git reports repo-root-relative paths; the ledgers key on backend-relative.

    Found by live drill 2026-07-29, AFTER the mode fix: git yields
    `backend/tests/test_x.py` while the ledger says `tests/test_x.py`, so the key
    sets never intersect and the rule is inert in every mode. The translation uses
    `git rev-parse --show-prefix`, never a hardcoded "backend/".

    Also asserts paths OUTSIDE the prefix are dropped — they cannot carry a ledger
    entry, and leaving them in would let `test.sh` masquerade as a ledger key.
    """

    def fake_git(args: list[str]) -> str:
        if args[0] == "merge-base":
            return "deadbeef\n"
        if args[0] == "rev-parse" and "--show-prefix" in args:
            return "backend/\n"
        if args[0] == "rev-parse":
            return "deadbeef\n"
        if args[0] == "diff" and "--cached" not in args:
            return (
                "diff --git a/backend/tests/test_x.py b/backend/tests/test_x.py\n"
                "--- a/backend/tests/test_x.py\n+++ b/backend/tests/test_x.py\n"
                "@@ -3 +3 @@\n+changed\n"
                "diff --git a/test.sh b/test.sh\n--- a/test.sh\n+++ b/test.sh\n"
                "@@ -9 +9 @@\n+outside the prefix\n"
            )
        return ""

    _mode, changed = resolve_changed(git=fake_git)
    assert "tests/test_x.py" in changed, f"git path not translated into the ledger domain; keys={list(changed)}"
    assert "backend/tests/test_x.py" not in changed, "left the untranslated key in"
    assert "test.sh" not in changed, "kept a path outside the prefix"


def test_all_scopes_finds_every_function():
    scopes = all_scopes(THREE_FUNCS)
    assert len(scopes) >= 3, f"expected >=3 scopes, got {scopes}"


def test_enclosing_scope_is_the_innermost_containing_range():
    start, end = _scope_of_violation(TWO_FUNCS, MOCK_TARGET)
    assert start <= 7 <= end
    assert start > 4, "returned the module range instead of the function's"


# ── G8c-e: PERMANENT must be recognised in the REAL ledger format ────────────


def test_g8c_permanent_survives_the_canonical_reason_prefix():
    """`# reason:` is the canonical field (6d1c455); PERMANENT rides inside it.

    The original matcher was `reason.startswith("PERMANENT:")`, which no real
    ledger line can satisfy: the parser keeps the whole comment, so the reason
    always begins `reason: `. Both the em-dash and colon separators must work —
    the entry in the tree uses the em dash.
    """
    line = 'app/cards/media/pixabay.py\tno\t1  # reason: PERMANENT — English word "no", dict key'
    entry = parse_ledger_line(line)
    assert entry is not None
    assert entry.permanent is True


def test_g8d_both_ledgers_are_empty_so_no_entry_can_be_re_added():
    """⚠️ REPLACES the on-disk PERMANENT guardrail, whose subject no longer exists.

    The original asserted the pixabay entry was present in the literal ledger and
    parsed as permanent — deliberately reading the real files rather than a
    synthetic line, because the bug it guarded (`ceeefac`) was a matcher that
    passed on synthetic `# PERMANENT:` input while recognising zero real entries.

    That entry has now been drained: its 353-row table moved to
    ``app/cards/media/data/image_query_map.json``, which removed the checker's
    trigger (a bare string literal in ``app/**/*.py``) rather than exempting it.
    Both ledgers are therefore empty, and there is no real committed data left
    for that assertion to read.

    A first rewrite tried to assert "the parser agrees with the files about how
    many entries are permanent". That was VACUOUS — with both files empty it
    reduced to ``0 == 0`` and survived two separate sabotages of the parser
    (dropping the permanent flag; silently discarding a line). Caught by drilling
    it, which is the only reason it isn't still in the tree.

    What is worth pinning instead is the state itself: both ledgers are empty and
    must stay that way. This fails the moment anyone re-adds a line, which is the
    "no additions, period" policy. The `# reason: PERMANENT` parse remains pinned
    by the synthetic-but-real-format test above.
    """
    for gf_path in (GRANDFATHER_PATH, LITERAL_GRANDFATHER_PATH):
        if not gf_path.exists():
            continue
        live = [raw for raw in gf_path.read_text(encoding="utf-8").splitlines() if raw.strip() and raw[0] != "#"]
        assert live == [], f"{gf_path.name} gained an entry; these ledgers are drained and closed: {live}"
    assert _load_entries() == []


def test_g8e_main_prints_the_drainable_and_permanent_counts(capsys, monkeypatch):
    """permanent_count's docstring promised it was 'printed every run'. It wasn't.

    Without the print, a growing PERMANENT set is invisible — which is how an
    exemption class quietly becomes a dumping ground.
    """
    monkeypatch.setattr("check_ledger_touch.resolve_changed", lambda: ("skip-no-diff", set()))
    main()
    out = capsys.readouterr().out
    assert "ledger:" in out, out
    assert "drainable" in out and "permanent" in out, out
