"""Tests for the .beads-tasks pointer auto-staging PreToolUse hook.

This hook shipped with no tests, and that is how tunatale-0hj survived: the
pointer silently stopped riding commits whenever `git add` and `git commit`
were issued as ONE shell command, which is the shape an agent writes by
default. The hook runs at PreToolUse — BEFORE the command executes — so it
inspected an index the `git add` had not populated yet, saw nothing else going
in, and correctly declined rather than manufacture a pointer-only commit.

⚠️ The bead recorded TWO mechanisms for this and they imply different fixes. The
second ("the staging it performs is overtaken by the `git add -A` that runs
first in the same shell") is FALSE: a gitlink staged before `git add -A`
survives it and is recorded correctly. That is what justifies fixing this in a
PreToolUse hook at all — if `add -A` really did clobber the gitlink, only a real
`pre-commit` git hook could work.

⚠️ SECOND BUG, found while testing the first (tunatale-ov0m). From git 2.55 the
`ignore = all` that keeps the gitlink out of the commit gate's fingerprint ALSO
suppresses `git add` of that submodule:

    hint: Skipping submodule due to ignore=all: .beads-tasks
    hint: Use --force if you really want to add the submodule.

The add exits 0 having staged nothing. Apple git 2.50.1 — what this machine
ships — stages without --force, so the whole class is invisible locally and
surfaced only on the CI runner. The hook now passes --force, which is a no-op on
the older git. `_stage_pointer` below uses the same invocation, so a test can
never assert a staging the hook cannot perform.

⚠️ `-c submodule.<name>.ignore=none` does NOT override this and looks like it
disproves the diagnosis — the runner still logs `ignore=all` with the override
in place. Do not re-derive "the ignore setting is not the mechanism" from that;
it is, and --force is the documented escape hatch.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
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


# A git environment that reads NO ambient config, developer or runner.
#
# This fixture drives real git, and git's behaviour is configurable almost
# everywhere — so anything left unpinned is read from whoever runs the suite.
# Two separate CI failures came from exactly that, both green locally:
#   * init.defaultBranch — main here, master on the runner, so the bare origin's
#     HEAD named a branch the push never created and `submodule add` died with
#     "fatal: You are on a branch yet to be born"
#   * user.name / user.email — set per-repo on `sub` and `parent`, but NOT on
#     the submodule working tree that `submodule add` creates, and the runner
#     has no global identity: "fatal: empty ident name ... not allowed"
#
# Patching those one at a time is whack-a-mole; commit.gpgsign, core.hooksPath
# and init.templateDir are all still out there. Pointing both config scopes at
# os.devnull and supplying the identity through the environment closes the
# class instead of the two instances. `-b main` stays explicit rather than
# relying on git's built-in default, so the branch name is stated where it is
# used.
_GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t.t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t.t",
}


def _stage_pointer(repo):
    """Stage the gitlink exactly the way the hook does — with --force.

    A plain `git add -- .beads-tasks` is version-dependent: git 2.50 stages it,
    git 2.55 lets `ignore = all` suppress the add entirely (tunatale-ov0m).
    Tests that stage the pointer must use the hook's own invocation, or they
    assert a behaviour the hook does not have.
    """
    _run(["add", "--force", "--", ".beads-tasks"], repo)


def _run(args, cwd):
    proc = subprocess.run(["git", *args], capture_output=True, cwd=cwd, timeout=60, text=True, env=_GIT_ENV)
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


@pytest.fixture(scope="session")
def _repo_template(tmp_path_factory):
    """Built ONCE. Every test gets a fresh copy of it — see `repo` below.

    Mirrors production: `ignore = all`, the submodule's HEAD contained in a
    remote-tracking branch, and the recorded pointer one commit behind. That is
    the exact state in which the hook is supposed to act.

    Every git call runs under `_GIT_ENV`, which reads no ambient config — see
    the comment there for the two CI failures that bought that rule.

    This was function-scoped until 2026-09-01. Building it costs ~20 git
    subprocesses, and at 13 tests that was 12.2s — the single largest module in
    the backend suite's aggregate profile, and the suite is work-bound at CI's
    four workers, so aggregate is the only thing that moves the wall clock.
    """
    tmp_path = tmp_path_factory.mktemp("hook-repo-template")
    sub_origin = tmp_path / "sub-origin.git"
    _run(["init", "-q", "--bare", "-b", "main", str(sub_origin)], tmp_path)

    sub = tmp_path / "sub"
    _run(["init", "-q", "-b", "main", str(sub)], tmp_path)
    (sub / "f").write_text("one")
    _run(["add", "f"], sub)
    _run(["commit", "-qm", "one"], sub)
    _run(["remote", "add", "origin", str(sub_origin)], sub)
    _run(["push", "-q", "origin", "main"], sub)

    parent = tmp_path / "parent"
    _run(["init", "-q", "-b", "main", str(parent)], tmp_path)
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
def repo(_repo_template, tmp_path):
    """A private copy of the template, so tests still mutate freely in isolation.

    ⚠️ THE ASSERTION BELOW IS THE CONTROL, and it is why the copy is safe to make.
    A git repo carries absolute paths (`remote.origin.url`, and the parent's
    `submodule..beads-tasks.url`), so a copied tree still points at the TEMPLATE's
    `sub-origin.git`. That is fine because nothing here writes to the origin —
    the fixture's one push happens during template construction, and the hook
    itself only ever stages. If that ever stops being true, or a copy lands
    subtly wrong, re-asserting the drifted state on the COPY is what catches it
    rather than letting every hook test pass vacuously against a clean repo.
    """
    parent = tmp_path / "parent"
    shutil.copytree(_repo_template, parent, symlinks=True)
    assert _run(["submodule", "status"], parent).startswith("+"), "the copy must start drifted"
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
        _stage_pointer(repo)
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
        _stage_pointer(repo)
        _run(["add", "-A"], repo)
        _run(["commit", "-qm", "compound"], repo)

        recorded = _run(["rev-parse", "HEAD:.beads-tasks"], repo)
        head = _run(["rev-parse", "HEAD"], repo / ".beads-tasks")
        assert recorded == head, "add -A clobbered the pre-staged gitlink"
        assert not _run(["submodule", "status"], repo).startswith("+")


class TestTheHookActuallyStages:
    """End-to-end through main(), not just should_stage.

    Nothing covered this before, which is how the git 2.55 `ignore = all` break
    (tunatale-ov0m) got as far as it did: every other test here stops at the
    DECISION and none of them performed the staging the decision leads to.

    ⚠️ Vacuous on git 2.50 — a plain add would pass it too. It discriminates
    only on git >= 2.55, i.e. on the CI runner, which is exactly where the bug
    lives. Kept because CI is the authoritative gate; the whitebox assertion
    below is what gives local coverage of the same requirement.
    """

    def _fire(self, hook, repo, monkeypatch, command="git commit -m 'x'"):
        import io
        import json as _json

        monkeypatch.setattr(hook, "REPO_ROOT", _run(["rev-parse", "--show-toplevel"], repo))
        payload = _json.dumps({"tool_input": {"command": command}, "cwd": str(repo)})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        return hook.main()

    def test_the_gitlink_really_lands_in_the_index(self, hook, repo, monkeypatch):
        (repo / "code.py").write_text("x = 1")
        _run(["add", "code.py"], repo)

        assert self._fire(hook, repo, monkeypatch) == 0

        staged_sha = _run(["ls-files", "-s", ".beads-tasks"], repo).split()[1]
        assert staged_sha == _run(["rev-parse", "HEAD"], repo / ".beads-tasks")
        # And it survives into the commit, which is the property that actually
        # matters — `git commit` is what reported "nothing to commit" on 2.55.
        _run(["commit", "-qm", "code"], repo)
        assert _run(["rev-parse", "HEAD:.beads-tasks"], repo) == _run(["rev-parse", "HEAD"], repo / ".beads-tasks")

    def test_the_staging_add_passes_force(self, hook):
        """Whitebox, and deliberately so.

        The requirement is "must stage even when ignore = all suppresses add",
        and on git 2.50 no outcome test can distinguish a hook that meets it
        from one that does not. Asserting the flag is the only check that goes
        red on THIS machine if someone removes it — an outcome test would stay
        green here and break the pointer for every reader on a newer git.
        """
        import inspect

        src = inspect.getsource(hook.main)
        assert '"add", "--force", "--", SUBMODULE' in src, "the staging add must pass --force"
