#!/usr/bin/env python3
"""Claude Code commit gate (wired in .claude/settings.json, PreToolUse/Bash).

Policy (AGENTS.md): ./test.sh must pass before every commit. This hook makes
that check deterministic instead of advisory, without hard-blocking: a
`git commit` Bash command triggers an "ask" confirmation unless the recorded
tree fingerprint matches the current tree — i.e. ./test.sh passed on exactly
this state.

Modes:
  --record    Write the current tree fingerprint to <git-dir>/tt-test-pass
              (per-worktree; see sentinel_path).
              Called by test.sh after the full suite passes.
  (default)   PreToolUse hook: reads the tool-call JSON on stdin, prints an
              "ask" decision when the fingerprint is missing or stale, exits
              0 silently otherwise (allow).

The fingerprint is sha256 over every path that differs from HEAD or is
untracked (non-ignored), paired with its working-tree content — so it is
staging-invariant (`git add` doesn't change it) but any edit after the
recorded pass invalidates it. After a commit, HEAD moves and the next commit
needs a fresh pass — matching the policy.

Commits in other repos (the .beads-tasks submodule, micro-demo-*, etc.) are
ignored via a repo-root check. That check resolves the command's EFFECTIVE cwd
— honouring `cd <path> &&` prefixes and `git -C <path>` — rather than the
session cwd.

⚠️ Fixed 2026-08-17. Probing from the session cwd was a documented gap ("at
worst asks unnecessarily"), and it turned out not to be harmless: every
`.beads-tasks` commit is `cd <submodule> && git commit`, which resolved to the
main repo root and raised a prompt. Beads sync is standing-authorized and runs
several times a session, so the gate asked constantly about a repository
`./test.sh` does not cover. A gate that cries wolf on work it does not govern
trains the reader to click through it — the same failure the gate exists to
prevent, reached from the other side.

Resolution FAILS SAFE: anything unparseable (a shell variable, command
substitution) falls back to the session cwd, i.e. keeps gating. Only `cd`
segments occurring BEFORE the `git commit` are applied — otherwise
`git commit && cd /elsewhere` would launder a local commit past the gate.

⚠️ Fixed 2026-08-31. `git commit` inside a QUOTED ARGUMENT to another program
is not a commit. Dispatching a BP fence drill —
`opencode run --agent build "... Run: git commit --allow-empty ..."` — raised
the gate on a command that commits nothing; the drill's whole point is to name
forbidden commands as literals. Same class as the 2026-07-16
`.pre-commit-config.yaml` false positive one level up: that stopped `commit`
matching inside a word, this stops `git commit` matching inside a string.
`gate_pipe_guard.py` already had the answer and is why it allowed the same
command — this borrows its length-preserving `_strip_quoted`.

Length preservation is load-bearing: `effective_cwd` slices the ORIGINAL
command at the match offset, so a quoted `cd` target keeps its content.
Stripping only decides WHETHER there is a commit, never WHERE it runs.

A shell `-c` argument is exempt, because there the quoted string IS a command
line and blanking it would turn this narrowing into a bypass — fail safe, the
direction chosen everywhere else here.
"""

import hashlib
import json
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Match `commit` only as git's subcommand — immediately after `git` bar global
# options (`-C <path>`, `-c k=v`, `--no-pager`, …) — NOT as any later word.
# Regression (2026-07-16): `git log … .pre-commit-config.yaml` triggered the
# gate because hyphens are word boundaries, so `\bcommit\b` matched inside the
# filename. Filenames/refs can never match now: they follow a non-option token
# (the real subcommand), which ends the option loop before `commit` is required.
COMMIT_RE = re.compile(r"\bgit(\s+-\S+(\s+[^\s-]\S*)?)*\s+commit\b")


# A quoted or bare path argument. Bare form stops at shell metacharacters so a
# `cd foo && …` doesn't swallow the rest of the line.
_PATH = r"""'[^']*'|"[^"]*"|[^\s;&|]+"""
# `cd <path>` at the start of the command or after a shell separator.
CD_RE = re.compile(rf"(?:^|&&|\|\||;)\s*cd\s+(?P<p>{_PATH})")
# git's own -C, allowing the global options that may precede it.
GIT_C_RE = re.compile(rf"\bgit\s+(?:-c\s+\S+\s+|--no-pager\s+|--git-dir\s+\S+\s+)*-C\s+(?P<p>{_PATH})")
# Anything we cannot resolve statically. Falling back to the session cwd keeps
# the gate ON, which is the safe direction.
UNRESOLVABLE = ("$", "`", "~")

# A shell asked to run a command line: its quoted argument is code, not data.
SHELL_C_RE = re.compile(r"\b(?:ba|z|k|da)?sh\s+(?:-\w+\s+)*-\w*c\b")


def _strip_quoted(command):
    """Blank the CONTENTS of quoted spans, preserving length and the quotes.

    Length preservation keeps every offset into the result valid as an offset
    into the original, which is what lets `effective_cwd` read the real text.
    """
    out = []
    quote = None
    for ch in command:
        if quote:
            out.append(" " if ch != quote else ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        else:
            out.append(ch)
    return "".join(out)


def find_commit(command):
    """Match `git commit` as a COMMAND, not as text inside a quoted argument.

    Returns the match (offsets valid against *command*) or None. A shell `-c`
    invocation is searched raw: its quoted argument is a command line.
    """
    if SHELL_C_RE.search(command):
        return COMMIT_RE.search(command)
    return COMMIT_RE.search(_strip_quoted(command))


def _git(args, cwd):
    return subprocess.run(["git", *args], capture_output=True, cwd=cwd, timeout=60)


def sentinel_path(root):
    """Where the tree fingerprint for *root* lives.

    NOT ``<root>/.git/tt-test-pass``. In a linked worktree ``.git`` is a FILE
    (a gitdir pointer), so that path is impossible — and because ``test.sh``
    wraps its ``--record`` in ``|| true``, a green gate silently recorded
    NOTHING and this hook then prompted on a tree that had genuinely passed.
    It failed annoying rather than open, which is worse than it sounds: a gate
    that asks on work it has already cleared trains the reader to click through
    it, the exact failure the gate exists to prevent. (tunatale-5znu, fixed
    2026-09-02; it bit every commit of the wake-word spike.)

    ``--absolute-git-dir`` resolves to ``<main>/.git`` in an ordinary checkout
    and ``<main>/.git/worktrees/<name>`` inside a worktree. That per-worktree
    location is the CORRECT semantics, not an accident of the implementation:
    each worktree has its own working tree, so a fingerprint recorded in one
    must never satisfy a commit in another. ``--git-common-dir`` would share a
    single sentinel across all of them and is the wrong call here.
    """
    probe = _git(["rev-parse", "--absolute-git-dir"], root)
    git_dir = probe.stdout.decode("utf-8", "replace").strip()
    if probe.returncode != 0 or not git_dir:
        # Not a repo, or git is unavailable. Fall back rather than raise — this
        # hook must always exit 0, and a missing sentinel keeps the gate ON.
        git_dir = os.path.join(root, ".git")
    return os.path.join(git_dir, "tt-test-pass")


def _unquote(token):
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "'\"":
        return token[1:-1]
    return token


def effective_cwd(command, cwd):
    """Resolve the directory *command*'s `git commit` actually runs in.

    Honours `cd <path> &&` prefixes (which compose, and where an absolute path
    resets the base) and `git -C <path>` (which wins, since git applies it
    regardless of the shell's cwd). Only text BEFORE the `git commit` is
    considered. Unresolvable paths leave the base unchanged — fail safe.
    """
    match = find_commit(command)
    head = command[: match.start()] if match else command

    base = cwd
    for cd in CD_RE.finditer(head):
        target = _unquote(cd.group("p"))
        if any(ch in target for ch in UNRESOLVABLE):
            return cwd  # can't know where we ended up — keep gating
        base = os.path.join(base, target)

    dash_c = GIT_C_RE.search(command)
    if dash_c:
        target = _unquote(dash_c.group("p"))
        if any(ch in target for ch in UNRESOLVABLE):
            return cwd
        base = os.path.join(base, target)

    return os.path.normpath(base)


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
        with open(sentinel_path(REPO_ROOT), "w") as fh:
            fh.write(tree_fingerprint(REPO_ROOT) + "\n")
        return 0

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    command = (data.get("tool_input") or {}).get("command", "")
    if not find_commit(command):
        return 0

    cwd = effective_cwd(command, data.get("cwd") or os.getcwd())
    if not os.path.isdir(cwd):
        return 0  # target directory doesn't exist — nothing of ours to gate
    probe = _git(["rev-parse", "--show-toplevel"], cwd)
    if probe.returncode != 0 or probe.stdout.decode().strip() != REPO_ROOT:
        return 0  # commit targets a different repo — not ours to gate

    try:
        with open(sentinel_path(REPO_ROOT)) as fh:
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
