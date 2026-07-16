#!/usr/bin/env python3
"""Claude Code commit gate (wired in .claude/settings.json, PreToolUse/Bash).

Policy (AGENTS.md): ./test.sh must pass before every commit. This hook makes
that check deterministic instead of advisory, without hard-blocking: a
`git commit` Bash command triggers an "ask" confirmation unless the recorded
tree fingerprint matches the current tree — i.e. ./test.sh passed on exactly
this state.

Modes:
  --record    Write the current tree fingerprint to .git/tt-test-pass.
              Called by test.sh after the full suite passes.
  (default)   PreToolUse hook: reads the tool-call JSON on stdin, prints an
              "ask" decision when the fingerprint is missing or stale, exits
              0 silently otherwise (allow).

The fingerprint is sha256 over every path that differs from HEAD or is
untracked (non-ignored), paired with its working-tree content — so it is
staging-invariant (`git add` doesn't change it) but any edit after the
recorded pass invalidates it. After a commit, HEAD moves and the next commit
needs a fresh pass — matching the policy.
Commits in other repos (micro-demo-*, etc.) are ignored via a repo-root check
on the session cwd; `git -C <elsewhere> commit` is a known gap that at worst
asks unnecessarily.
"""

import hashlib
import json
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SENTINEL = os.path.join(REPO_ROOT, ".git", "tt-test-pass")
COMMIT_RE = re.compile(r"\bgit\b[^|;&]*\bcommit\b")


def _git(args, cwd):
    return subprocess.run(["git", *args], capture_output=True, cwd=cwd, timeout=60)


def tree_fingerprint(root):
    changed = _git(["diff", "HEAD", "--name-only", "-z"], root).stdout
    untracked = _git(["ls-files", "--others", "--exclude-standard", "-z"], root).stdout
    paths = sorted(
        {p for p in (changed + untracked).decode("utf-8", "replace").split("\0") if p}
    )
    h = hashlib.sha256()
    for rel in paths:
        h.update(rel.encode("utf-8", "replace") + b"\0")
        try:
            with open(os.path.join(root, rel), "rb") as fh:
                h.update(hashlib.sha256(fh.read()).digest())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()


def main():
    if "--record" in sys.argv:
        with open(SENTINEL, "w") as fh:
            fh.write(tree_fingerprint(REPO_ROOT) + "\n")
        return 0

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    command = (data.get("tool_input") or {}).get("command", "")
    if not COMMIT_RE.search(command):
        return 0

    cwd = data.get("cwd") or os.getcwd()
    probe = _git(["rev-parse", "--show-toplevel"], cwd)
    if probe.returncode != 0 or probe.stdout.decode().strip() != REPO_ROOT:
        return 0  # commit targets a different repo — not ours to gate

    try:
        with open(SENTINEL) as fh:
            recorded = fh.read().strip()
    except OSError:
        recorded = ""
    if recorded and recorded == tree_fingerprint(REPO_ROOT):
        return 0  # ./test.sh passed on exactly this tree state

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "ask",
                    "permissionDecisionReason": (
                        "./test.sh has not passed on the current tree state "
                        "(no fingerprint recorded, or files changed since the "
                        "last pass). Run ./test.sh first, or approve to "
                        "commit anyway."
                    ),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
