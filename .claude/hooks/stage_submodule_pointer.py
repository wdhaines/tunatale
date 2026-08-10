#!/usr/bin/env python3
"""Auto-stage the .beads-tasks pointer onto code commits (PreToolUse/Bash).

AGENTS.md's rule was "when you make a code commit, stage .beads-tasks with it."
That is a rule a human or an agent has to remember, and the failure mode is
silent: the public repo shows a stale backlog tree, which reads as "the sync
did not run" even when everything is fully synced. This hook does the staging
instead, so the rule needs no reader.

WHY THIS CANNOT BE "the pointer always tracks HEAD": git stores a submodule as
a literal commit SHA (a gitlink) inside the parent's tree object. A commit must
name an immutable tree, so there is no "follow the branch" value to store —
`branch = main` in .gitmodules is only an input to `git submodule update
--remote`, never a recorded tracking mode. Auto-staging is the closest
achievable thing: the pointer equals the submodule's HEAD *at commit time*.

ORDER-INDEPENDENT WITH THE COMMIT GATE, and that is load-bearing. commit_gate.py
fingerprints `git diff HEAD --name-only` + untracked files; `ignore = all` on the
submodule keeps the gitlink out of that list whether it is staged or not
(verified 2026-08-10: fingerprint byte-identical before and after `git add
.beads-tasks`). So this hook cannot invalidate a green ./test.sh run, and the two
PreToolUse hooks may run in either order or in parallel.

Every guard below fails OPEN — this hook never blocks a commit and never reports
an error. The worst case is that it declines to stage and the pointer stays
stale, which is exactly the old behaviour.

Guards:
  * the command is a `git commit` in THIS repo (shared regex + root check with
    commit_gate, so the `.pre-commit-config.yaml` regression cannot recur here)
  * not --dry-run (which would leave the index dirty with nothing committed)
  * the submodule is initialised, and actually drifted
  * the submodule's HEAD is contained in some remote-tracking branch — staging an
    unpushed SHA hands everyone else a pointer they cannot resolve
  * the commit already carries other content, so this can never manufacture the
    pointer-only commit AGENTS.md forbids
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from commit_gate import COMMIT_RE, REPO_ROOT, _git  # noqa: E402

SUBMODULE = ".beads-tasks"


def _out(text):
    return text.stdout.decode("utf-8", "replace").strip()


def should_stage(command):
    """True when staging the gitlink is both wanted and safe. Never raises."""
    if "--dry-run" in command:
        return False

    sub_path = os.path.join(REPO_ROOT, SUBMODULE)
    # An uninitialised submodule is an empty directory; staging that would record
    # a pointer nobody asked for. A fresh clone without --init lands here.
    if not os.path.exists(os.path.join(sub_path, ".git")):
        return False

    # `git submodule status` prefixes a drifted pointer with '+'. No '+' means the
    # recorded SHA already matches the submodule's HEAD — nothing to do.
    status = _git(["submodule", "status", "--", SUBMODULE], REPO_ROOT)
    if status.returncode != 0 or not _out(status).startswith("+"):
        return False

    # The SHA about to be recorded must exist on the remote, or every other clone
    # gets a pointer that cannot be fetched. Remote-tracking refs are updated by
    # the push inside sync.sh, so this is true right after the normal workflow and
    # false for a submodule commit that was never pushed.
    contained = _git(["branch", "--remotes", "--contains", "HEAD"], sub_path)
    if contained.returncode != 0 or not _out(contained):
        return False

    # Never create a pointer-only commit: a bump that needs its own ./test.sh run
    # costs more than the staleness it fixes (AGENTS.md). Something else must
    # already be going in.
    staged = _git(["diff", "--cached", "--name-only"], REPO_ROOT)
    others = [p for p in _out(staged).splitlines() if p and p != SUBMODULE]
    if others:
        return True
    # `git commit -a` stages tracked modifications at commit time, so an empty
    # index does not mean an empty commit.
    if " -a" in f" {command}" or "--all" in command:
        tracked = _git(["diff", "--name-only"], REPO_ROOT)
        return bool(_out(tracked))
    return False


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    command = (data.get("tool_input") or {}).get("command", "")
    if not COMMIT_RE.search(command):
        return 0

    cwd = data.get("cwd") or os.getcwd()
    probe = _git(["rev-parse", "--show-toplevel"], cwd)
    if probe.returncode != 0 or _out(probe) != REPO_ROOT:
        return 0  # commit targets a different repo — not ours to touch

    try:
        if not should_stage(command):
            return 0
        added = _git(["add", "--", SUBMODULE], REPO_ROOT)
        if added.returncode != 0:
            return 0
        head = _out(_git(["rev-parse", "--short", "HEAD"], os.path.join(REPO_ROOT, SUBMODULE)))
        print(f"Staged the {SUBMODULE} pointer ({head}) onto this commit.")
    except Exception:  # noqa: BLE001 — a hook must never break `git commit`
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
