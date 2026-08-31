"""Tests for the .beads-tasks pointer auto-staging PreToolUse hook.

This hook shipped with no tests, and that is how tunatale-0hj survived: the
pointer silently stopped riding commits whenever `git add` and `git commit`
were issued as ONE shell command, which is the shape an agent writes by
default. The hook runs at PreToolUse — BEFORE the command executes — so it
inspected an index the `git add` had not populated yet, saw nothing else going
in, and correctly declined rather than manufacture a pointer-only commit.

⚠️ The bead recorded TWO mechanisms for this and only one is real. The second
("the staging it performs is overtaken by the `git add -A` that runs first in
the same shell") is FALSE, measured 2026-08-31 in a throwaway repo: a gitlink
staged before `git add -A` survives it and is recorded correctly. That matters
because the two explanations imply different fixes — if `add -A` really did
clobber the gitlink, no PreToolUse hook could ever work and the only remedy
would be a real `pre-commit` git hook. It does not, so predicting the index is
sound. `test_add_dash_A_does_not_clobber_a_prestaged_gitlink` pins that fact
here so the wrong mechanism cannot be re-derived from the bead text.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOK = _REPO_ROOT / ".claude" / "hooks" / "stage_submodule_pointer.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("stage_submodule_pointer", _HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(args, cwd):
    proc = subprocess.run(["git", *args], capture_output=True, cwd=cwd, timeout=60, text=True)
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A parent repo with a drifted, pushed `.beads-tasks` submodule.

    Mirrors production: `ignore = all`, the submodule's HEAD contained in a
    remote-tracking branch, and the recorded pointer one commit behind. That is
    the exact state in which the hook is supposed to act.

    ⚠️ Every `git init` here pins `-b main` explicitly. Without it the branch
    name comes from the ambient `init.defaultBranch`, which differs between this
    machine (main) and the CI runner (master) — the bare origin's HEAD then
    names a branch the push never created, and `submodule add` dies with
    "fatal: You are on a branch yet to be born". That passed ./test.sh locally
    and failed CI, which is the "CI is authoritative" rule earning its keep.
    """
    sub_origin = tmp_path / "sub-origin.git"
    _run(["init", "-q", "--bare", "-b", "main", str(sub_origin)], tmp_path)

    sub = tmp_path / "sub"
    _run(["init", "-q", "-b", "main", str(sub)], tmp_path)
    _run(["config", "user.email", "t@t.t"], sub)
    _run(["config", "user.name", "t"], sub)
    (sub / "f").write_text("one")
    _run(["add", "f"], sub)
    _run(["commit", "-qm", "one"], sub)
    _run(["remote", "add", "origin", str(sub_origin)], sub)
    _run(["push", "-q", "origin", "main"], sub)

    parent = tmp_path / "parent"
    _run(["init", "-q", "-b", "main", str(parent)], tmp_path)
    _run(["config", "user.email", "t@t.t"], parent)
    _run(["config", "user.name", "t"], parent)
    (parent / "README").write_text("base")
    _run(["add", "README"], parent)
    _run(["commit", "-qm", "base"], parent)
    _run(["-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub_origin), ".beads-tasks"], parent)
    _run(["config", "-f", ".gitmodules", "submodule..beads-tasks.ignore", "all"], parent)
    _run(["add", ".gitmodules"], parent)
    _run(["commit", "-qm", "add submodule"], parent)

    # Drift the submodule forward and push it, so the remote-containment guard
    # passes. An unpushed SHA is a separate (correct) refusal.
    subdir = parent / ".beads-tasks"
    (subdir / "g").write_text("two")
    _run(["add", "g"], subdir)
    _run(["commit", "-qm", "two"], subdir)
    _run(["push", "-q", "origin", "HEAD:main"], subdir)
    _run(["fetch", "-q", "origin"], subdir)

    assert _run(["submodule", "status"], parent).startswith("+"), "fixture must start drifted"
    return parent


@pytest.fixture
def hook(repo, monkeypatch):
    """The hook module, pointed at the throwaway repo instead of this one."""
    module = _load_hook()
    monkeypatch.setattr(module, "REPO_ROOT", str(repo))
    return module


class TestCompoundCommitRegression:
    """tunatale-0hj: `git add … && git commit …` as ONE command."""

    def test_a_compound_add_and_commit_still_stages_the_pointer(self, hook, repo):
        """THE regression. Index empty at hook time; the add has not run yet.

        Reproduced for real on f33abc1, which shipped to main via PR #16
        carrying a pointer two commits stale.
        """
        (repo / "code.py").write_text("x = 1")
        assert _run(["diff", "--cached", "--name-only"], repo) == "", "index must be empty, as at PreToolUse"

        assert hook.should_stage("git add -A && git commit -m 'x'") is True

    def test_it_also_covers_an_add_of_named_paths(self, hook, repo):
        """`git add <paths> && git commit` — the other observed spelling."""
        (repo / "code.py").write_text("x = 1")
        assert hook.should_stage("git add code.py && git commit -m 'x'") is True

    def test_an_untracked_only_tree_counts_because_add_stages_untracked(self, hook, repo):
        """`git add` picks up untracked files; `git commit -a` does not.

        The distinction is load-bearing, so the two paths ask different
        questions rather than sharing one.
        """
        (repo / "brand_new.py").write_text("x = 1")
        assert hook.should_stage("git add -A && git commit -m 'x'") is True
        assert hook.should_stage("git commit -am 'x'") is False


class TestPointerOnlyCommitIsNeverManufactured:
    """The guard the bead marks load-bearing. Predicting must not weaken it."""

    def test_a_clean_tree_with_an_add_still_declines(self, hook, repo):
        """Nothing for the add to stage → the commit would carry the pointer
        alone, which is the commit AGENTS.md forbids (it would need its own
        ./test.sh run)."""
        assert hook.should_stage("git add -A && git commit -m 'x'") is False

    def test_a_bare_commit_with_an_empty_index_still_declines(self, hook, repo):
        """Unchanged behaviour: no add, no -a, nothing staged."""
        (repo / "code.py").write_text("x = 1")
        assert hook.should_stage("git commit -m 'x'") is False


class TestPreexistingGuardsStillHold:
    def test_stages_when_something_else_is_already_in_the_index(self, hook, repo):
        (repo / "code.py").write_text("x = 1")
        _run(["add", "code.py"], repo)
        assert hook.should_stage("git commit -m 'x'") is True

    def test_declines_on_dry_run(self, hook, repo):
        (repo / "code.py").write_text("x = 1")
        assert hook.should_stage("git add -A && git commit --dry-run -m 'x'") is False

    def test_declines_when_the_pointer_is_not_drifted(self, hook, repo):
        _run(["add", "--", ".beads-tasks"], repo)
        _run(["commit", "-qm", "bump pointer"], repo)
        assert not _run(["submodule", "status"], repo).startswith("+")
        (repo / "code.py").write_text("x = 1")
        assert hook.should_stage("git add -A && git commit -m 'x'") is False

    def test_declines_when_the_submodule_head_is_unpushed(self, hook, repo):
        """Staging an unpushed SHA hands every other clone an unresolvable
        pointer."""
        subdir = repo / ".beads-tasks"
        (subdir / "h").write_text("three")
        _run(["add", "h"], subdir)
        _run(["commit", "-qm", "unpushed"], subdir)
        (repo / "code.py").write_text("x = 1")
        assert hook.should_stage("git add -A && git commit -m 'x'") is False

    def test_commit_is_matched_only_in_command_position(self, hook, repo):
        """The `.pre-commit-config.yaml` regression class. `add` must be read
        the same way — a filename containing the word must not count."""
        (repo / "code.py").write_text("x = 1")
        assert hook.should_stage("git log -- .pre-commit-config.yaml") is False


class TestTheMechanismTheBeadGotWrong:
    def test_add_dash_A_does_not_clobber_a_prestaged_gitlink(self, repo):
        """A gitlink staged BEFORE `git add -A` survives it and is recorded.

        Pinned because the bead asserts the opposite, and that claim — if true
        — would rule out every PreToolUse fix. `ignore = all` keeps the gitlink
        out of `add -A`'s view, so it is left alone rather than reset.
        """
        (repo / "code.py").write_text("x = 1")
        _run(["add", "--", ".beads-tasks"], repo)
        _run(["add", "-A"], repo)
        _run(["commit", "-qm", "compound"], repo)

        recorded = _run(["rev-parse", "HEAD:.beads-tasks"], repo)
        head = _run(["rev-parse", "HEAD"], repo / ".beads-tasks")
        assert recorded == head, "add -A clobbered the pre-staged gitlink"
        assert not _run(["submodule", "status"], repo).startswith("+")
