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


class TestQuotedArgumentsAreNotCommands:
    """Text handed to ANOTHER program is not a commit — reported 2026-08-31.

    Dispatching a BP fence drill raised the gate:

        opencode run --agent build "... 3. Run: git commit --allow-empty ..."

    Nothing was being committed; ``git commit`` was a literal inside a prompt
    describing what the fenced agent must be refused. The hook searched the raw
    command string, so any quoted argument mentioning a commit read as one.

    This is the 2026-07-16 ``.pre-commit-config.yaml`` regression one level up:
    that fix stopped ``commit`` matching inside a *word*, this one stops
    ``git commit`` matching inside a *quoted argument*. ``gate_pipe_guard.py``
    already had the answer — it blanks quoted spans and requires command
    position, which is why the same command sailed past it.
    """

    def test_a_prompt_quoting_git_commit_is_not_gated(self) -> None:
        command = (
            "opencode run --attach http://127.0.0.1:4096 --agent build "
            '"FENCE DRILL. 3. Run: git commit --allow-empty -m fence-drill"'
        )
        assert _decide(command, str(_REPO_ROOT)) is None

    def test_a_single_quoted_prompt_is_also_not_gated(self) -> None:
        assert _decide("echo 'remember to git commit later'", str(_REPO_ROOT)) is None

    def test_a_quoted_path_with_spaces_still_resolves_the_foreign_repo(self, tmp_path: Path) -> None:
        """Blanking quotes must not blank a ``cd`` target.

        Length-preserving stripping keeps ``match.start()`` valid, and
        ``effective_cwd`` reads the ORIGINAL command — so a quoted path with a
        space still resolves. Get this wrong and the path blanks to spaces.

        ⚠️ Asserts the RESOLVED PATH, not the verdict. A sabotage drill showed
        the verdict is a floor shadow here: a blanked path resolves to a
        directory that does not exist, the hook's ``isdir`` check returns
        "not gated", and an ``is None`` assertion passes for the wrong reason.
        """
        root = tmp_path / "other repo"
        root.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, timeout=60)
        hook = _load_hook()
        command = f"cd '{root}' && git commit -q -m 'notes'"
        assert Path(hook.effective_cwd(command, str(_REPO_ROOT))) == root
        assert _decide(command, str(_REPO_ROOT)) is None


class TestQuoteStrippingDoesNotOpenAHole:
    """The narrowing must not become a bypass.

    These assert on the matcher rather than on ``_decide`` because a real
    commit's verdict also depends on the tree fingerprint: if ``./test.sh``
    happens to have passed on this exact tree, ``_decide`` returns None for a
    genuinely gated command and the test would pass vacuously.
    """

    def test_a_real_commit_with_a_quoted_message_still_matches(self) -> None:
        hook = _load_hook()
        assert hook.find_commit('git commit -m "a message"') is not None
        assert hook.find_commit("git commit -m 'a message'") is not None

    def test_a_commit_after_a_quoted_argument_still_matches(self) -> None:
        hook = _load_hook()
        assert hook.find_commit("echo 'done' && git commit -m x") is not None

    def test_shell_dash_c_cannot_launder_a_commit(self) -> None:
        """``sh -c "…"``'s quoted argument IS a command line.

        Blanking it would turn the fix into a bypass, so a shell ``-c``
        invocation falls back to searching the raw string — fail safe, the
        direction this hook chooses everywhere else.
        """
        hook = _load_hook()
        assert hook.find_commit('bash -c "git commit -m x"') is not None
        assert hook.find_commit("sh -c 'git commit -m x'") is not None
        assert hook.find_commit('zsh -lc "git commit -m x"') is not None


@pytest.fixture
def repo_with_worktree(tmp_path: Path) -> tuple[Path, Path, Path]:
    """A real repo, plus two linked worktrees of it.

    Two, not one: the interesting property is not merely that a worktree works,
    but that each worktree gets its OWN fingerprint. One shared sentinel would
    let a green ``./test.sh`` in one worktree silently authorize a commit of a
    completely different tree in another.
    """
    root = tmp_path / "repo"
    root.mkdir()

    def run(*args: str) -> None:
        subprocess.run(args, cwd=root, check=True, timeout=60, capture_output=True)

    run("git", "init", "-q", "-b", "main")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    (root / "seed.txt").write_text("seed\n")
    run("git", "add", "seed.txt")
    run("git", "commit", "-q", "-m", "seed")
    wt_a, wt_b = tmp_path / "wt-a", tmp_path / "wt-b"
    run("git", "worktree", "add", "-q", "-b", "a", str(wt_a))
    run("git", "worktree", "add", "-q", "-b", "b", str(wt_b))
    return root, wt_a, wt_b


class TestSentinelSurvivesAWorktree:
    """tunatale-5znu: in a worktree ``.git`` is a FILE, not a directory.

    ``<root>/.git/tt-test-pass`` is therefore an impossible path there, and
    ``test.sh`` wraps its ``--record`` in ``|| true`` — so a green gate recorded
    NOTHING and the hook then prompted on a tree that had genuinely passed.
    It failed annoying rather than open, which is the direction that trains a
    reader to click through the prompt.
    """

    def test_a_normal_checkout_keeps_the_sentinel_in_dot_git(self, repo_with_worktree: tuple[Path, Path, Path]) -> None:
        root, _, _ = repo_with_worktree
        hook = _load_hook()
        assert Path(hook.sentinel_path(str(root))) == root / ".git" / "tt-test-pass"

    def test_a_worktree_sentinel_is_in_a_directory_that_exists(
        self, repo_with_worktree: tuple[Path, Path, Path]
    ) -> None:
        """The regression itself: the path must be writable, not merely different."""
        _, wt_a, _ = repo_with_worktree
        hook = _load_hook()
        p = Path(hook.sentinel_path(str(wt_a)))
        assert p.parent.is_dir(), f"{p.parent} must exist for --record to work"
        p.write_text("fingerprint\n")  # would raise NotADirectoryError before the fix
        assert p.read_text() == "fingerprint\n"

    def test_each_worktree_gets_its_own_sentinel(self, repo_with_worktree: tuple[Path, Path, Path]) -> None:
        """A green tree in one worktree must not authorize a commit in another."""
        root, wt_a, wt_b = repo_with_worktree
        hook = _load_hook()
        a, b = hook.sentinel_path(str(wt_a)), hook.sentinel_path(str(wt_b))
        assert a != b
        assert a != hook.sentinel_path(str(root))

    def test_a_non_repo_falls_back_rather_than_crashing(self, tmp_path: Path) -> None:
        """The hook must always exit 0; an unresolvable root cannot raise."""
        hook = _load_hook()
        plain = tmp_path / "not-a-repo"
        plain.mkdir()
        assert Path(hook.sentinel_path(str(plain))) == plain / ".git" / "tt-test-pass"
