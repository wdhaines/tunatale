"""Tests for the ``./test.sh``-must-not-be-piped PreToolUse hook.

Regression guard for the 2026-07-29 incident: ``./test.sh 2>&1 | tail -40``
reported ``$? == 0`` while the script printed ``=== FAILED (backend=1) ===``,
because a pipeline's exit status is the LAST command's — ``tail`` always
succeeds. The same pipe truncated the actual error out of the captured log, so
neither the status nor the log could answer "did the gate pass?".

Both halves are structural, not a matter of remembering: the exit code is
wrong by construction, and ``tail -n`` discards the failure detail by
construction. This hook removes the construction.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOK = _REPO_ROOT / ".claude" / "hooks" / "gate_pipe_guard.py"


def _decide(command: str) -> str | None:
    """Run the hook against *command*; return its denial reason, or ``None``."""
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps({"tool_input": {"command": command}}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, f"hook must always exit 0, got {proc.returncode}: {proc.stderr}"
    if not proc.stdout.strip():
        return None
    payload = json.loads(proc.stdout)
    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


BLOCKED = [
    "./test.sh 2>&1 | tail -40",
    "./test.sh | tail -5",
    "./test.sh | tee /tmp/log.txt",
    "./test.sh 2>&1 | grep FAILED",
    "bash test.sh | head",
    "cd /Users/x/tunatale && ./test.sh | tail -20",
    "sh ./test.sh|cat",
    "$ROOT/test.sh 2>&1 | tail",
]

ALLOWED = [
    "./test.sh",
    "./test.sh > /tmp/gate.txt 2>&1; echo EXIT=$?",
    "./test.sh 2>&1 > /tmp/gate.txt",
    "./test.sh || echo failed",
    "./test.sh && git commit -m x",
    # References that are not invocations — searching for the string must work.
    "grep -n 'test.sh' docs/*.md | head -20",
    "rg test.sh --files-with-matches | wc -l",
    "cat test.sh | head -40",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_piped_gate_invocations_are_denied(command):
    reason = _decide(command)
    assert reason is not None, f"should have been denied: {command!r}"
    # The message must carry the fix, not just the complaint.
    assert ">" in reason and "$?" in reason


@pytest.mark.parametrize("command", ALLOWED)
def test_unpiped_and_non_invocations_are_allowed(command):
    assert _decide(command) is None, f"should have been allowed: {command!r}"


def test_malformed_stdin_allows():
    """A hook that crashes on junk input would block every Bash call."""
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input="not json",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_hook_is_wired_in_settings():
    """A guard that is not wired is decoration."""
    settings = json.loads((_REPO_ROOT / ".claude" / "settings.json").read_text())
    commands = [
        hook["command"]
        for entry in settings["hooks"]["PreToolUse"]
        if entry.get("matcher") == "Bash"
        for hook in entry["hooks"]
    ]
    assert any("gate_pipe_guard.py" in c for c in commands), (
        "gate_pipe_guard.py exists but is not wired into settings.json PreToolUse/Bash"
    )


def test_wired_hook_scripts_are_git_tracked():
    """The db6fcf7 failure mode, one directory over.

    ``test_gate_scripts_tracked.py`` covers ``backend/scripts/*.py`` referenced
    by test.sh and CI. Hook scripts referenced by settings.json are the same
    class of thing — wired into the gate, and useless on a fresh checkout if
    gitignored.
    """
    settings_text = (_REPO_ROOT / ".claude" / "settings.json").read_text()
    tracked = subprocess.run(
        ["git", "ls-files", ".claude/hooks"],
        capture_output=True,
        text=True,
        check=True,
        cwd=_REPO_ROOT,
    ).stdout.split()

    referenced = {
        name
        for name in (".claude/hooks/commit_gate.py", ".claude/hooks/gate_pipe_guard.py")
        if name.rsplit("/", 1)[1] in settings_text
    }
    assert referenced, "no hook scripts found in settings.json — pattern may be wrong"
    for path in referenced:
        assert path in tracked, f"{path} is wired into settings.json but not git-tracked"
