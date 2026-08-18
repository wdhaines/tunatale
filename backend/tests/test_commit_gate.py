"""Tests for the ``./test.sh``-must-pass-before-commit PreToolUse hook.

The gate exists to make the policy deterministic rather than advisory. These
tests are about the OTHER half: it must not fire on commits it does not govern.

Regression guard for the friction reported 2026-08-17 — every ``.beads-tasks``
commit raised a confirmation prompt. Beads sync is standing-authorized and runs
several times a session, so the gate was asking constantly about a *different
repository*, one ``./test.sh`` does not even cover. The cause was documented in
the hook's own docstring as a known gap: the repo-root probe ran from the
SESSION cwd, so ``cd <submodule> && git commit`` and ``git -C <submodule>
commit`` both resolved to the main repo root and were gated.

A gate that cries wolf on work it does not govern trains the reader to click
through it — which is exactly the failure mode the commit gate exists to
prevent, reached from the other direction.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOK = _REPO_ROOT / ".claude" / "hooks" / "commit_gate.py"


def _load_hook():
    """Import the hook module by path (it lives outside any package)."""
    spec = importlib.util.spec_from_file_location("commit_gate", _HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _decide(command: str, cwd: str) -> str | None:
    """Run the hook against *command*; return its ask reason, or ``None``."""
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps({"tool_input": {"command": command}, "cwd": cwd}),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, f"hook must always exit 0, got {proc.returncode}: {proc.stderr}"
    if not proc.stdout.strip():
        return None
    payload = json.loads(proc.stdout)
    out = payload["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "ask"
    return out["permissionDecisionReason"]


@pytest.fixture
def foreign_repo(tmp_path: Path) -> Path:
    """A real git repo that is NOT this one — stands in for .beads-tasks."""
    root = tmp_path / "other-repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, timeout=60)
    return root


class TestForeignRepoIsNotGated:
    """The reported bug: commits in another repo must pass silently."""

    def test_cd_into_another_repo_is_not_gated(self, foreign_repo: Path) -> None:
        command = f"cd {foreign_repo} && git commit -q -m 'notes'"
        assert _decide(command, str(_REPO_ROOT)) is None

    def test_git_dash_c_into_another_repo_is_not_gated(self, foreign_repo: Path) -> None:
        command = f"git -C {foreign_repo} commit -q -m 'notes'"
        assert _decide(command, str(_REPO_ROOT)) is None

    def test_git_dash_c_wins_over_a_preceding_cd(self, foreign_repo: Path) -> None:
        """``-C`` is applied by git regardless of the shell's cwd."""
        command = f"cd {_REPO_ROOT} && git -C {foreign_repo} commit -q -m 'notes'"
        assert _decide(command, str(_REPO_ROOT)) is None

    def test_add_then_commit_in_another_repo_is_not_gated(self, foreign_repo: Path) -> None:
        """The real beads shape: stage and commit in one compound command."""
        command = f"cd {foreign_repo} && git add briefs/x.md && git commit -q -m 'notes'"
        assert _decide(command, str(_REPO_ROOT)) is None


class TestThisRepoIsStillGated:
    """The narrowing must not open a hole in the gate it is narrowing."""

    def test_a_trailing_cd_does_not_launder_a_local_commit(self, foreign_repo: Path) -> None:
        """``cd`` AFTER the commit must not affect where the commit ran.

        Without this, ``git commit && cd /elsewhere`` would silently bypass the
        gate — the exact hole a naive "find any cd in the command" fix opens.
        """
        hook = _load_hook()
        command = f"git commit -q -m 'code' && cd {foreign_repo}"
        resolved = hook.effective_cwd(command, str(_REPO_ROOT))
        assert Path(resolved) == _REPO_ROOT

    def test_plain_commit_here_resolves_to_this_repo(self) -> None:
        hook = _load_hook()
        resolved = hook.effective_cwd("git commit -q -m 'code'", str(_REPO_ROOT))
        assert Path(resolved) == _REPO_ROOT

    def test_unparseable_cd_falls_back_to_the_session_cwd(self) -> None:
        """Fail SAFE: if the target cannot be read, keep gating."""
        hook = _load_hook()
        resolved = hook.effective_cwd('cd "$SOME_VAR" && git commit -m x', str(_REPO_ROOT))
        assert Path(resolved) == _REPO_ROOT


class TestNonCommitCommandsAreIgnored:
    """Pre-existing behavior that must survive the change."""

    def test_git_log_naming_a_hyphenated_file_is_not_gated(self) -> None:
        """Regression 2026-07-16: ``\\bcommit\\b`` matched inside a filename."""
        command = "git log --oneline .pre-commit-config.yaml"
        assert _decide(command, str(_REPO_ROOT)) is None

    def test_unrelated_command_is_not_gated(self) -> None:
        assert _decide("ls -la", str(_REPO_ROOT)) is None
