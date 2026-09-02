#!/usr/bin/env python3
"""Remind about a still-open bead the commit just shipped (PostToolUse/Bash).

THE GAP THIS FILLS is structural, not a discipline problem. AGENTS.md requires a
closure to cite the hash of the commit that shipped it, so the close CANNOT be
part of that commit — "closures land at most one commit late by construction".
That leaves the one moment of maximum context (writing the commit message) as
the one moment the close is impossible, and by the time the hash exists the
session has moved on or ended.

MEASURED BEFORE BUILDING (2026-09-01): 1 stale bead in 54 open, not the "we are
bad at closing beads" the symptom felt like. The one miss, tunatale-1wiw, is
what makes the case: fabd85f's message OPENS with "Defect #1 of the ferskt
incident (bd tunatale-1wiw)". Whoever wrote it knew the bead. Knowledge was
never the failure; timing was. So this hook fires AFTER the hash exists, which
is the only moment the close can actually be made.

⚠️ ADVISORY, NEVER A GATE, and that is not negotiable. Most bead citations in a
commit message are legitimately partial — the same sweep found `vpn` cited by
the commit that DISCOVERED it, `pse` (P2.3) cited by a P0.3 commit, and `4v1y`
cited by a deps pass that only changed its blocker. A gate here would fire on
all of them. It prints one line and exits 0; every failure path also exits 0.

WHY POSTTOOLUSE AND NOT PRE: PreToolUse runs before the command, when HEAD is
still the parent commit and the hash this reminder is about does not exist yet.
That is the same "the index this hook reads is not the index the commit will
use" trap that silently disabled stage_submodule_pointer.py for two weeks
(tunatale-0hj) — here it would be unfixable rather than merely subtle.

FILTERS, each one measured against a real false positive:
  * epics are containers, never "done" — kbb and 1l26 are cited constantly
  * a commit that FAILED leaves HEAD on the parent, whose beads were already
    reported; the freshness check below is what distinguishes them
  * `bd show --json`, never plain `bd show` (it mangles technical content), and
    the field is `issue_type` — `type` exists and is always null, a clean
    negative that would silently pass every epic through
"""

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from commit_gate import REPO_ROOT, _git, find_commit  # noqa: E402

# Bead ids as they appear in prose: tunatale-9yd0, tunatale-qi5.1, tunatale-kbb.3.2
BEAD_RE = re.compile(r"\btunatale-[a-z0-9]+(?:\.[0-9]+)*\b")

# How recently HEAD must have been authored for it to be the commit that just
# ran. A failed `git commit` leaves HEAD on the parent; without this the hook
# would re-report that parent's beads on every failed attempt.
FRESH_SECONDS = 120


def _out(proc):
    return proc.stdout.decode("utf-8", "replace").strip()


def _open_non_epic(bead_id):
    """True if the bead is open/in_progress and is not an epic. Fails CLOSED.

    Any doubt — bd missing, store unreachable, malformed record — returns False
    so the hook stays silent. A missed reminder is the status quo; a spurious
    one trains the reader to ignore the line.
    """
    try:
        proc = subprocess.run(
            ["bd", "show", bead_id, "--json"],
            capture_output=True,
            cwd=REPO_ROOT,
            timeout=10,
        )
        if proc.returncode != 0:
            return False
        records = json.loads(proc.stdout.decode("utf-8", "replace"))
        rec = records[0] if isinstance(records, list) and records else None
        if not isinstance(rec, dict):
            return False
        # `issue_type`, NOT `type` — see the module docstring.
        return rec.get("status") in ("open", "in_progress") and rec.get("issue_type") != "epic"
    except Exception:  # noqa: BLE001 — a hook must never break the session
        return False


def main():
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    command = (data.get("tool_input") or {}).get("command", "")
    if not find_commit(command):
        return 0

    try:
        cwd = data.get("cwd") or os.getcwd()
        probe = _git(["rev-parse", "--show-toplevel"], cwd)
        if probe.returncode != 0 or _out(probe) != REPO_ROOT:
            return 0  # a commit in some other repo — not our backlog

        head = _git(["log", "-1", "--format=%ct%n%H%n%B"], REPO_ROOT)
        if head.returncode != 0:
            return 0
        stamp, _, rest = _out(head).partition("\n")
        sha, _, message = rest.partition("\n")

        import time

        if time.time() - int(stamp) > FRESH_SECONDS:
            return 0  # the commit did not land; HEAD is still the parent

        ids = sorted(set(BEAD_RE.findall(message)))
        stale = [i for i in ids if _open_non_epic(i)]
        if not stale:
            return 0

        listed = ", ".join(stale)
        print(
            f"{sha[:9]} cites {listed}, still open. If this commit finished the "
            f"work, close it now — the hash it must cite exists only now:\n"
            f"  bd close {stale[0]} --reason \"...shipped by {sha[:9]}\"\n"
            f"  ./.beads-tasks/sync.sh\n"
            f"(Advisory. A commit may legitimately cite a bead it only partly addresses.)"
        )
    except Exception:  # noqa: BLE001 — a hook must never break the session
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
