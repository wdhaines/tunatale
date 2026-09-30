---
paths:
  - "test.sh"
  - ".github/**"
  - ".claude/hooks/**"
  - ".claude/settings.json"
---

# The gate, the hooks, and CI

AGENTS.md carries the operating rules; this is the mechanism behind them. The
local/CI asymmetries are in `testing.md` § "What a green gate means".

## Running the gate

```bash
/Users/wdhaines/CascadeProjects/tunatale/test.sh > /tmp/gate.txt 2>&1
```

Nothing may follow the gate in the same command — not an `echo $?`, and not one
on its own line either. Any later command supplies the exit status the harness
reports, so a failed gate can read as success. A relative `./test.sh` silently
misses when an earlier `cd` has persisted across tool calls. Both mistakes have
reported success on runs whose log ended in `=== FAILED ... ===`, so the log tail
is the only evidence.

In the log, require `=== All checks passed ===` (the failure form is
`=== FAILED (backend=N frontend=N peer_sync=N) ===`), 100.00% backend coverage,
and a sane ruff count: the `N files already formatted` line only grows, so
compare it with the previous run's log — a drop, or a two-digit N, means
discovery broke. (`.git/tt-test-history.log` records each step's exit code and
timing, not this count.) Never `cd` away from the repo in a session that will run
the gate. The commit gate is the backstop: it prompts when no recorded
fingerprint matches the tree, and you should not click past it.

## CI job layout

CI is authoritative and `./test.sh` is a strict subset of it, so green locally is
necessary but not sufficient. Adding a check to `test.sh` obliges you to add it to
`ci.yml` in the same commit; the reverse is not required.

`.github/workflows/ci.yml` runs six parallel jobs: `backend` (ruff → checkers →
pytest), `backend-hostile-tz`, `backend-hostile-hour`, `frontend`, `e2e`, and
`anki-gates` (oracle parity and peer-sync in one job). Job count, not job speed,
drives CI's tail latency — which is why the two Anki gates share a job and why
none of these is a matrix.

- `backend-hostile-tz` runs one instance at `Etc/GMT-3`, inside UTC+2..+4. That
  band is where the only offset bug this repo has found reproduces; a matrix at
  the extremes (UTC+14, UTC−12) passes it. The measurement and the redundancy
  argument are in the comment above the job.
- Three jobs override the workflow's `TZ: UTC`: `backend-hostile-tz` (offset),
  `backend-hostile-hour` (clock), and `anki-gates`, which runs both Anki gates at
  the 04:00 rollover via `.github/actions/hostile-hour-tz`. The Anki gates need
  the rollover specifically: `backend-hostile-hour` is in the band on every run
  but passes no `--run-oracle`, so it cannot catch a parity bug there, and a
  parity job left at `TZ: UTC` would reach the band about once in 600 runs.
- If `anki-gates` is red at the boundary while `backend` is green, suspect
  product code first. That is the opposite of the guidance for
  `backend-hostile-tz`, because only `anki-gates` has an oracle that can tell "TT
  and Anki disagree about the day" from "a fixture encodes a wall-clock
  assumption".
- The clock/offset jobs are the only CI-only checks; there are no local-only
  ones.
- CI installs lean: every backend job runs `uv sync --no-default-groups --group
  dev` with `UV_NO_SYNC: "1"` at job level, so classla, stanza, torch and
  transformers are absent. A test may import only what the `dev` group declares;
  anything transitive through the language groups (e.g. `yaml` via transformers)
  passes locally and fails all four backend jobs with `ModuleNotFoundError`.
  Declare it in `dev` and never widen CI's groups.

## Hooks (`.claude/settings.json`)

- **Commit gate** (PreToolUse). `git commit` asks for confirmation unless
  `./test.sh` has passed on the exact current tree. On success `test.sh` records a
  tree fingerprint via `.claude/hooks/commit_gate.py --record`; a failing run
  deletes it, so a flaky green cannot outlive a red on the same tree. The
  fingerprint covers every path that differs from HEAD or is untracked, with its
  content, so staging does not change it. The hook matches `git commit` only in
  command position and ignores quoted arguments (`opencode run ... "Run: git
  commit"` commits nothing and is not gated); a shell `-c` argument is exempt from
  that narrowing, because there the quoted string is a command line.
- **Pipe guard** (PreToolUse). `.claude/hooks/gate_pipe_guard.py` denies any
  command that pipes `./test.sh` (`| tail`, `| tee`, `| grep`). A pipeline's `$?`
  is the last command's, so a failed gate reads as 0, and `tail -n` discards the
  failure detail you piped in order to see. Searching for the string (`grep
  test.sh …`, `cat test.sh | head`) is unaffected. `test.sh` tees every run to
  `.git/tt-test-last.log` and names it in the FAILED banner.
- **Per-step history** (`.git/tt-test-history.log`). Every `./test.sh` step runs
  through a `log_step` wrapper that appends one TSV line per step on every local
  run: `timestamp  group  step-name  exit-code  elapsed-seconds  1min-load
  tree-id`. The tree id is `commit_gate.py::tree_id` (HEAD plus the dirty-tree
  fingerprint) and separates a flake (same tree, red then green) from a fix; older
  lines have six columns. The log is append-only, so a local flake rate can be
  read back the way CI's can, e.g.
  `awk -F'\t' '$3=="E2E smoke tests"' .git/tt-test-history.log`.
- **Submodule-pointer auto-stage** (PreToolUse).
  `.claude/hooks/stage_submodule_pointer.py` stages `.beads-tasks` onto any
  `git commit` that already carries other content. Its guards all fail open, so
  it can never block a commit: initialised submodule, actually drifted, HEAD
  contained in a remote-tracking branch, not `--dry-run`, and something else
  already going in. It is order-independent with the commit gate, because
  `ignore = all` keeps the gitlink out of `git diff HEAD --name-only`, so staging
  it cannot invalidate a green `./test.sh`.
- **Agent mail and claims** (SessionStart). Lists the count and the newest three
  unread messages from `./.beads-tasks/mail.sh unread`, then every bead in
  progress with its claimant (see the `beads` skill § "Claiming work"). Prints
  nothing when bd is not installed.
- **Coverage-artifact cleanup** (SessionEnd). Deletes `backend/**/*.py,cover`
  and `backend/coverage.json` (pytest `--cov` leftovers, also gitignored).
