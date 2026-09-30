---
paths:
  - "test.sh"
  - ".github/**"
  - ".claude/hooks/**"
  - ".claude/settings.json"
---

# The gate, the hooks, and CI — mechanism and history

Moved verbatim from AGENTS.md (2026-09-30), which keeps the operating rules. The
local/CI asymmetries are in `testing.md` § "What a green gate means".

## CI job layout

- **CI is authoritative; `./test.sh` is a strict SUBSET of it** (`tunatale-as5`, 2026-08-14). Green locally is necessary but not sufficient. **Adding a check to `test.sh` obliges you to add it to `ci.yml` in the same commit**; the reverse is not required. Six parallel job instances in `.github/workflows/ci.yml` — backend (ruff → checkers → pytest), `backend-hostile-tz` (×1, `Etc/GMT-3`), `backend-hostile-hour`, frontend, `e2e` (Playwright), and `anki-gates` (oracle-parity + peer-sync in one job, because job COUNT is what drives the tail). `backend-hostile-tz` runs ONE instance inside UTC+2..+4, not a matrix at the extremes, because that band is where the only offset bug this repo has ever found reproduces — an extremes matrix is measured blind to it. Job COUNT, not job speed, drives the CI tail. The measurement and the redundancy argument are in the comment above the job. **THREE jobs override the workflow's `TZ: UTC`** — `backend-hostile-tz` (offset), `backend-hostile-hour` (clock), and `anki-gates`, which runs BOTH Anki gates at the 04:00 rollover via `.github/actions/hostile-hour-tz`. The Anki gates must run at the rollover specifically: `backend-hostile-hour` sits in the band on every run but passes no `--run-oracle`, so it can never catch a parity bug there, and a parity job left at `TZ: UTC` reaches the band about once in 600 runs — which is not a defence. Neither job is misconfigured on its own; the hole is at their intersection. ⚠️ If `anki-gates` goes red at the boundary while `backend` is green, suspect PRODUCT code first — the opposite of the guidance for `backend-hostile-tz`, because only `anki-gates` has an oracle to tell "TT and Anki disagree about the day" from "a fixture encodes a wall-clock assumption". The clock/offset jobs are the only CI-only checks; there are no local-only ones. ⚠️ **CI RUNS LEAN, and that split is real** (`0344f42`, 2026-08-31, `tunatale-ouk.6`/`.9`): every backend job installs `uv sync --no-default-groups --group dev` with `UV_NO_SYNC: "1"` at job level, so classla/stanza/torch/transformers are absent there. **A test may import only what the `dev` group declares** — anything transitive through the language groups (e.g. `yaml` via transformers) is green locally and `ModuleNotFoundError` in all four backend jobs. Declare it in `dev`; never widen CI's groups. Full rationale: `.claude/rules/testing.md` § "What a green gate means".

## Hooks (`.claude/settings.json`)


- **Commit gate** (PreToolUse): `git commit` asks for confirmation unless `./test.sh` has passed on the exact current tree — `test.sh` records a tree fingerprint via `.claude/hooks/commit_gate.py --record` on success. A *failing* run deletes the fingerprint, so a flaky green cannot outlive a red on the same tree. It matches `git commit` only in **command position**, ignoring quoted arguments — `opencode run ... "Run: git commit"` commits nothing and is not gated (fixed 2026-08-31; a shell `-c` argument is exempt from that narrowing, since there the quoted string is a command line).
- **Pipe guard** (PreToolUse): `.claude/hooks/gate_pipe_guard.py` **denies** any command that pipes `./test.sh` (`| tail`, `| tee`, `| grep`). A pipeline's `$?` is the last command's, so a failed gate reads as 0, and `tail -n` throws away the failure detail you piped in order to see. Searching for the string (`grep test.sh …`, `cat test.sh | head`) is unaffected. test.sh also tees every run to `.git/tt-test-last.log` and names it in the FAILED banner.
- **Per-step history** (`.git/tt-test-history.log`, added 2026-09-03): every `./test.sh` check runs through a `log_step` wrapper that appends one TSV line per step, on *every* local run — `timestamp  group  step-name  exit-code  elapsed-seconds  1min-load  tree-id`. Column 7 (added 2026-09-10) is `commit_gate.py::tree_id`, HEAD plus the dirty-tree fingerprint, and it is what separates a flake (same tree, red then green) from a fix. Lines before it have six columns; `test.sh` carries the same-tree query in a comment. Unlike `tt-test-last.log` this is append-only and accumulates across runs, so a local flake rate can be read back the same way CI's is (CI's comes from GitHub's own run archive; nothing local kept an equivalent history before this — see `tunatale-xw6s`, `tunatale-hvbv`). Query it directly, e.g. `awk -F'\t' '$3=="E2E smoke tests"' .git/tt-test-history.log`.
- ⚠️ **Canonical gate invocation — absolute path, gate LAST, and the LOG is the
  only evidence:**
  ```bash
  cd /Users/wdhaines/CascadeProjects/tunatale
  /Users/wdhaines/CascadeProjects/tunatale/test.sh > /tmp/gate.txt 2>&1
  # ← NOTHING after this line. No `echo`, no cleanup, nothing.
  ```
  Then read `/tmp/gate.txt`: require `=== All checks passed ===` (the failure form
  is `=== FAILED (backend=N frontend=N peer_sync=N) ===`), 100.00% backend
  coverage, and a sane ruff count — it only ever grows, so compare it against the
  previous run rather than any number written here (which rots):
  `awk -F'\t' '$3=="Ruff format check"' .git/tt-test-history.log`. A DROP, or a
  two-digit N, means discovery broke.
  **Two fictional greens on 2026-07-29, same root class:**
  1. `./test.sh > log 2>&1; echo "EXIT=$?"` printed `EXIT=0` while the log said
     `no such file or directory: ./test.sh` — an earlier `cd` had persisted across
     tool calls, so the relative path missed.
  2. The "fix" of putting `echo "REAL_GATE_EXIT=$?"` on its **own line** was ALSO
     wrong: it prints the gate's status, but the *script's* exit status is still
     the echo's, so the harness reported success on a run whose log ended in
     `=== FAILED (backend=1 frontend=0) ===`.

  **The rule that actually holds: any command after the gate steals the exit
  status — separate line or not.** Make the gate the final statement, and treat
  the log tail as the sole evidence. Never trust a reported exit code, your own
  `echo`, or a log's size alone.
  **Rules, for every agent and every gate run:** absolute path always; never `cd`
  away from the repo in a session that will run the gate; keep `echo $?` in its own
  statement or omit it; and treat the log tail as the sole evidence — an exit code
  from a compound statement proves nothing. Same failure class the pipe guard was
  built for, reached from a different direction. The commit gate is the backstop
  (it prompts when no fingerprint matches the tree) — do not click past that
  prompt.
- **Submodule-pointer auto-stage** (PreToolUse): `.claude/hooks/stage_submodule_pointer.py` stages `.beads-tasks` onto any `git commit` that already carries other content, so the "pointer rides code commits" rule needs no reader. Guards (all fail open — it can never block a commit): initialised submodule, actually drifted, HEAD contained in a remote-tracking branch, not `--dry-run`, and something else already going in. **It is order-independent with the commit gate**: `ignore = all` keeps the gitlink out of `git diff HEAD --name-only`, which is what the gate fingerprints, so staging it cannot invalidate a green `./test.sh` (verified 2026-08-10 — byte-identical fingerprint before and after).
- **Coverage-artifact cleanup** (SessionEnd): deletes `backend/**/*.py,cover` and `backend/coverage.json` (pytest `--cov` leftovers; also gitignored).
