#!/usr/bin/env bash
# Runs the full lint + test gate. Three independent groups run concurrently:
# backend (ruff + checkers + pytest), frontend (fmt + lint + svelte-check +
# vitest + e2e), and peer-sync (round-trips against a throwaway anki.syncserver).
# They share nothing — e2e boots one backend per Playwright worker (:8001, :8003),
# each with its own test DBs, and peer-sync's server binds an ephemeral port. Output is buffered
# per group and printed when all finish (live progress would interleave). Note:
# pytest -n auto already saturates the CPU, so the groups contend; the win is the
# overlap, not free parallelism.
#
# ⚠️ THIS GATE IS A SUBSET OF CI, BY CONSTRUCTION — never a superset.
# See `.claude/rules/testing.md` § "What a green gate means". Everything here
# also runs in CI; CI additionally runs the two hostile-timezone jobs. If you add
# a check here, add it to .github/workflows/ci.yml in the same commit, or you
# have quietly recreated the divergence tunatale-as5 existed to remove.
#
# No `set -e` at the top level: we must collect ALL exit codes before failing,
# so each group runs in its own `set -e` subshell and we aggregate afterwards.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Where the commit gate's pass fingerprint lives. NOT "$ROOT/.git": inside a
# linked worktree .git is a FILE (a gitdir pointer), so that path is impossible
# and the `rm -f` below silently no-opped while `--record` (wrapped in
# `|| true`) recorded nothing — every worktree commit then prompted on a tree
# that had genuinely passed. Must stay in lockstep with sentinel_path() in
# .claude/hooks/commit_gate.py; if these two disagree the gate prompts for ever.
# (tunatale-5znu, 2026-09-02.)
TT_SENTINEL="$(git -C "$ROOT" rev-parse --absolute-git-dir 2>/dev/null || echo "$ROOT/.git")/tt-test-pass"

# Each group's stdout is a capture file, so the tools would normally strip
# color (no TTY). Force color from every toolchain (FORCE_COLOR for ruff/bun/
# vitest/playwright/eslint, PY_COLORS for pytest) so the escape codes land in
# the logs and render when we cat them back to the terminal. Guard on a TTY so
# `./test.sh > file` or a pipe stays free of escape sequences.
if [ -t 1 ]; then
  export FORCE_COLOR=1 PY_COLORS=1
fi

# Pin the timezone, to the SAME value CI pins (.github/workflows/ci.yml).
#
# Unpinned, this gate ran at whatever the developer's host offset was (-0400
# here) and CI ran at UTC. Both sit inside the band where everything passes, so
# NEITHER gate could see time-zone fragility — and there was some: a /listen
# rollover fixture mixed UTC literals with locally-derived Anki-day bounds and
# failed from UTC+5 eastward (tunatale-3oz). A gate that structurally cannot see
# a class of bug is worse than one that occasionally trips on it.
#
# UTC is the pinned value because it is what a deployment box most likely runs
# and what CI already used, so pinning changes the local gate rather than the
# remote one. This makes both gates REPRODUCIBLE; it does not make them
# tolerant. Tolerance is proved by the hostile-offset job in CI, which runs the
# backend suite at UTC+14 — do not delete that job on the grounds that this pin
# makes it redundant. It is the opposite: the pin is what makes everything else
# blind, and that job is the eye.
export TZ=UTC

backend_log="$(mktemp)"
frontend_log="$(mktemp)"
peer_sync_log="$(mktemp)"
# Warn LAST (an EXIT trap outlives both the pass and fail paths, so the notice
# survives even a `| tail -1`) when stdout is a pipe: the caller's `$?` is then
# the pager's status, not this script's, and a red run reads as green. A file
# redirect is not a pipe, so `> log 2>&1` stays quiet — that is the good form.
# Claude Code refuses piped invocations outright (.claude/hooks/gate_pipe_guard.py);
# this covers a human shell, which no hook sees.
trap 'rm -f "$backend_log" "$frontend_log" "$peer_sync_log"
      if [ -p /dev/stdout ]; then
        printf "\n!! stdout is a PIPE: your \$? is the LAST command in that pipeline, NOT this gate.\n"
        printf "!! Read the verdict above, or use: ./test.sh > /tmp/gate.txt 2>&1; echo EXIT=\$?\n"
      fi' EXIT

# The full run is ALSO written here, whatever the caller does with stdout. A
# truncating pipe (`| tail -40`) throws away the one part of a failed run you
# needed; this file always has all of it. Inside .git on purpose: never
# committed, and — unlike an untracked file in the tree — it cannot perturb the
# commit gate's tree fingerprint.
full_log="$ROOT/.git/tt-test-last.log"

# Per-step pass/fail history, appended across EVERY local run (unlike
# tt-test-last.log above, which is overwritten each time). CI's flake rate is
# cheap to measure after the fact because GitHub keeps a queryable run archive;
# nothing local does that, so a step could be quietly flaking for weeks and the
# only evidence would be one-off manual sweeps (tunatale-xw6s). This gives every
# step — not just e2e — the same kind of history CI gets for free, so if one
# starts flaking the data is already there instead of needing another campaign.
tt_test_history="$ROOT/.git/tt-test-history.log"

# Wraps one check with a banner (replacing the old bare `echo "=== X ==="`) plus
# a history line: timestamp, group, step name, exit code, elapsed seconds, and
# 1-min load average. `"$@" && rc=0 || rc=$?` is the standard way to capture a
# failing command's status under `set -e` without tripping errexit on the spot
# — a bare `"$@"; rc=$?` would abort at the failing command before the second
# line ever ran. `return "$rc"` then re-raises it, so a failed step still aborts
# the rest of this group exactly as before.
#
# EPOCHREALTIME (bash 5+, confirmed on this box) avoids a `date` subprocess per
# step; macOS's BSD date has no %N, so `date +%s.%N` would silently print the
# literal string "%N" here instead of failing loudly.
log_step() {
  local group="$1" name="$2"
  shift 2
  echo "=== $name ==="
  local t0="$EPOCHREALTIME" rc elapsed load
  "$@" && rc=0 || rc=$?
  elapsed=$(awk -v a="$t0" -v b="$EPOCHREALTIME" 'BEGIN { printf "%.1f", b - a }')
  load=$(uptime | sed -E 's/.*load averages?: *//' | awk '{print $1}' | tr -d ',')
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$group" "$name" "$rc" "$elapsed" "${load:-?}" \
    >>"$tt_test_history"
  return "$rc"
}

(
  set -e
  cd "$ROOT/backend"

  # --no-cache: ruff's file cache is keyed on mtime and races on newly-added
  # files — a file created in the same coarse mtime window as a prior cache
  # write is treated as already-scanned, so its violations pass silently (~50%
  # of runs; verified 2026-07-03). CI is immune (fresh checkout, no cache); this
  # brings the local pre-commit gate up to CI's reliability. Cost is sub-second.
  # The NST pronunciation database is a gitignored BUILD ARTIFACT, and Norwegian
  # syllable boundaries are read from it. Without this step the lexicon is absent
  # on a fresh clone and in CI, every lookup degrades to None, and the boundary
  # tests would pass here (where the file happens to exist) while proving nothing
  # there. Build takes ~1s from the committed extract, so it is always built.
  log_step backend "Build NST lexicon" uv run python scripts/build_nst_lexicon.py build

  log_step backend "Ruff lint" uv run ruff check --no-cache app tests scripts

  log_step backend "Ruff format check" uv run ruff format --check --no-cache app tests scripts

  log_step backend "Mock boundary check" uv run python scripts/check_mock_boundaries.py

  log_step backend "Language literal check" uv run python scripts/check_language_literals.py

  log_step backend "Date today check" uv run python scripts/check_date_today.py

  log_step backend "Singular database_url check" uv run python scripts/check_singular_database_url.py

  log_step backend "Plugin import check" uv run python scripts/check_plugin_imports.py

  log_step backend "OpenAPI snapshot check" uv run python scripts/check_openapi_snapshot.py

  log_step backend "Prod env profile check" uv run python scripts/check_prod_env.py

  log_step backend "Main styling check" uv run python scripts/check_main_styling.py

  # pytest-cov combines per-worker coverage, so the 100% gate still applies to
  # the full run at any -n.
  #
  # -n 6, NOT `auto`, and this is LOCAL-ONLY: every CI pytest invocation lives in
  # ci.yml (lines 176/314/431/912) and keeps `-n auto`, which resolves to 4 on
  # GitHub's 4-core runner. Nothing here reaches CI.
  #
  # `auto` picks 10 on this box and that is faster STANDALONE — the whole point
  # is that standalone is the wrong measurement. Measured 2026-09-03, three
  # interleaved reps each, colima stopped:
  #
  #   backend suite alone      -n 4 39.5   -n 6 32.8   -n 8 32.4   -n 10 29.5   -n 12 32.3
  #   FULL ./test.sh           -n auto(10) 78.7/78.1/78.3     -n 6 70.5/70.7/69.7
  #
  # So -n 6 loses 3s of its own and buys 8s of gate, because the four cores it
  # stops holding go to the frontend group it runs beside. Ranges do not overlap.
  #
  # ⚠️ This was banked and REJECTED once (tunatale-1l26.1): in the 2026-09-01
  # shape the composite measured 89s against an 86s baseline, because the tail
  # was e2e running alone and nothing was waiting for the freed cores. Two things
  # changed it — the e2e preview-server build shipped, and a colima guest that
  # had been eating a full core for 16 days was stopped. The `-n 6` datapoint is
  # its own control: it reads 32.8s now against 33s then, unchanged, while -n 10
  # went 35s -> 29.5s. The core came back and it went to the high worker counts.
  #
  # Re-measure with the loop in tunatale-1l26.1 if the suite shape changes; the
  # flat 6..12 region is a property of this suite's size, not a constant.
  log_step backend "Tests" uv run pytest --run-oracle -n 6

  # Clean up coverage data file left by pytest --cov
  uv run coverage erase
) >"$backend_log" 2>&1 &
backend_pid=$!

(
  set -e
  cd "$ROOT/frontend"

  log_step frontend "Frontend format check" bun run fmt:check

  log_step frontend "Frontend lint" bun run lint

  log_step frontend "OpenAPI type check" bun run check:api

  log_step frontend "Svelte type check" bun run check

  log_step frontend "Frontend tests (with coverage)" bun run test:coverage

  log_step frontend "E2E smoke tests" bun run test:e2e
) >"$frontend_log" 2>&1 &
frontend_pid=$!

# Peer-sync was tier 2 and manual (`.claude/rules/testing.md`), which meant CI
# ran it on every push and the pre-commit gate never did — so a sync round-trip
# regression was discoverable only AFTER pushing. Promoted to tier 1 on
# 2026-08-14 (tunatale-as5) to close that direction of the gap.
#
# Its own group, not a step in the backend group: it spawns an isolated
# `uv run --with anki` subprocess and an anki.syncserver, so it is mostly waiting
# on IO and overlaps cleanly with the CPU-bound pytest run. --no-cov because the
# backend group owns the coverage gate; this group answers one question only.
#
# Under --run-peer-sync an unstartable server FAILS rather than skips, on purpose
# — a silently-skipped round-trip gate is indistinguishable from a passing one.
(
  set -e
  cd "$ROOT/backend"

  log_step peer_sync "Peer-sync round-trip" uv run pytest tests/test_anki_peer_sync_selfhost.py --run-peer-sync --no-cov
) >"$peer_sync_log" 2>&1 &
peer_sync_pid=$!

wait "$backend_pid"; backend_rc=$?
wait "$frontend_pid"; frontend_rc=$?
wait "$peer_sync_pid"; peer_sync_rc=$?

{
  echo "===================== BACKEND (exit $backend_rc) ====================="
  cat "$backend_log"
  echo "==================== FRONTEND (exit $frontend_rc) ===================="
  cat "$frontend_log"
  echo "=================== PEER-SYNC (exit $peer_sync_rc) ==================="
  cat "$peer_sync_log"
} | tee "$full_log"

if [ "$backend_rc" -ne 0 ] || [ "$frontend_rc" -ne 0 ] || [ "$peer_sync_rc" -ne 0 ]; then
  # A stale pass sentinel must never outlive a failing run: the commit gate
  # compares fingerprints, and a flaky green followed by a red on the SAME tree
  # would otherwise still let a commit through unchallenged.
  rm -f "$TT_SENTINEL"
  echo "=== FAILED (backend=$backend_rc frontend=$frontend_rc peer_sync=$peer_sync_rc) ==="
  echo "Full log (never truncated): $full_log"
  # Toolchain versions, because a red run is sometimes the TOOLCHAIN, not the
  # tree (bd tunatale-1l26.3). A Node upgrade installed mid-session broke the
  # whole vitest suite while CI stayed green on its own pinned Node, and the
  # first plausible story — "something I did to node_modules" — was wrong. What
  # actually identified it was an install timestamp on the node binary, which
  # nothing in the repo pointed at. Printing the versions here costs two lines
  # and makes the next such incident self-identifying.
  #
  # Deliberately NOT a pin or a version check: a warning nobody reads is worse
  # than nothing, and pinning means the developer now has a Node install to
  # manage. This attacks the cost that was actually paid, which was DIAGNOSIS.
  echo "Toolchain: node $(node --version 2>/dev/null || echo '?') | bun $(bun --version 2>/dev/null || echo '?') | python $(cd "$ROOT/backend" && uv run python --version 2>/dev/null | awk '{print $2}' || echo '?')"
  exit 1
fi

echo "=== All checks passed ==="

# Record the tree fingerprint so the Claude Code commit gate
# (.claude/hooks/commit_gate.py) knows this exact state passed.
python3 "$ROOT/.claude/hooks/commit_gate.py" --record 2>/dev/null || true
