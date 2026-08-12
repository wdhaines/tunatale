#!/usr/bin/env bash
# Runs the full lint + test gate. The backend group (ruff + pytest) and the
# frontend group (fmt + lint + svelte-check + vitest + e2e) are independent —
# e2e boots its own backend on port 8001 with a dedicated tunatale-test.db, so
# nothing is shared with backend pytest — and run concurrently. This mirrors
# CI's two-job split. Output is buffered per group and printed when both finish
# (live progress would interleave). Note: pytest -n auto already saturates the
# CPU, so the two groups contend; the win is the overlap, not free parallelism.
#
# No `set -e` at the top level: we must collect BOTH exit codes before failing,
# so each group runs in its own `set -e` subshell and we aggregate afterwards.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Each group's stdout is a capture file, so the tools would normally strip
# color (no TTY). Force color from every toolchain (FORCE_COLOR for ruff/bun/
# vitest/playwright/eslint, PY_COLORS for pytest) so the escape codes land in
# the logs and render when we cat them back to the terminal. Guard on a TTY so
# `./test.sh > file` or a pipe stays free of escape sequences.
if [ -t 1 ]; then
  export FORCE_COLOR=1 PY_COLORS=1
fi

backend_log="$(mktemp)"
frontend_log="$(mktemp)"
# Warn LAST (an EXIT trap outlives both the pass and fail paths, so the notice
# survives even a `| tail -1`) when stdout is a pipe: the caller's `$?` is then
# the pager's status, not this script's, and a red run reads as green. A file
# redirect is not a pipe, so `> log 2>&1` stays quiet — that is the good form.
# Claude Code refuses piped invocations outright (.claude/hooks/gate_pipe_guard.py);
# this covers a human shell, which no hook sees.
trap 'rm -f "$backend_log" "$frontend_log"
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

(
  set -e
  cd "$ROOT/backend"

  # --no-cache: ruff's file cache is keyed on mtime and races on newly-added
  # files — a file created in the same coarse mtime window as a prior cache
  # write is treated as already-scanned, so its violations pass silently (~50%
  # of runs; verified 2026-07-03). CI is immune (fresh checkout, no cache); this
  # brings the local pre-commit gate up to CI's reliability. Cost is sub-second.
  echo "=== Ruff lint ==="
  uv run ruff check --no-cache app tests scripts

  echo "=== Ruff format check ==="
  uv run ruff format --check --no-cache app tests scripts

  echo "=== Mock boundary check ==="
  uv run python scripts/check_mock_boundaries.py

  echo "=== Language literal check ==="
  uv run python scripts/check_language_literals.py

  echo "=== Date today check ==="
  uv run python scripts/check_date_today.py

  echo "=== Singular database_url check ==="
  uv run python scripts/check_singular_database_url.py

  echo "=== Plugin import check ==="
  uv run python scripts/check_plugin_imports.py

  echo "=== OpenAPI snapshot check ==="
  uv run python scripts/check_openapi_snapshot.py

  echo "=== Prod env profile check ==="
  uv run python scripts/check_prod_env.py

  echo "=== Tests ==="
  # -n auto parallelizes across CPU cores; pytest-cov combines per-worker
  # coverage so the 100% gate still applies to the full run.
  uv run pytest --run-oracle -n auto

  # Clean up coverage data file left by pytest --cov
  uv run coverage erase
) >"$backend_log" 2>&1 &
backend_pid=$!

(
  set -e
  cd "$ROOT/frontend"

  echo "=== Frontend format check ==="
  bun run fmt:check

  echo "=== Frontend lint ==="
  bun run lint

  echo "=== OpenAPI type check ==="
  bun run check:api

  echo "=== Svelte type check ==="
  bun run check

  echo "=== Frontend tests (with coverage) ==="
  bun run test:coverage

  echo "=== E2E smoke tests ==="
  bun run test:e2e
) >"$frontend_log" 2>&1 &
frontend_pid=$!

wait "$backend_pid"; backend_rc=$?
wait "$frontend_pid"; frontend_rc=$?

{
  echo "===================== BACKEND (exit $backend_rc) ====================="
  cat "$backend_log"
  echo "==================== FRONTEND (exit $frontend_rc) ===================="
  cat "$frontend_log"
} | tee "$full_log"

if [ "$backend_rc" -ne 0 ] || [ "$frontend_rc" -ne 0 ]; then
  # A stale pass sentinel must never outlive a failing run: the commit gate
  # compares fingerprints, and a flaky green followed by a red on the SAME tree
  # would otherwise still let a commit through unchallenged.
  rm -f "$ROOT/.git/tt-test-pass"
  echo "=== FAILED (backend=$backend_rc frontend=$frontend_rc) ==="
  echo "Full log (never truncated): $full_log"
  exit 1
fi

echo "=== All checks passed ==="

# Record the tree fingerprint so the Claude Code commit gate
# (.claude/hooks/commit_gate.py) knows this exact state passed.
python3 "$ROOT/.claude/hooks/commit_gate.py" --record 2>/dev/null || true
