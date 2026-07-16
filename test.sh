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
trap 'rm -f "$backend_log" "$frontend_log"' EXIT

echo "Running backend + frontend suites in parallel..."

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

  echo "=== Plugin import check ==="
  uv run python scripts/check_plugin_imports.py

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

echo "===================== BACKEND (exit $backend_rc) ====================="
cat "$backend_log"
echo "==================== FRONTEND (exit $frontend_rc) ===================="
cat "$frontend_log"

if [ "$backend_rc" -ne 0 ] || [ "$frontend_rc" -ne 0 ]; then
  echo "=== FAILED (backend=$backend_rc frontend=$frontend_rc) ==="
  exit 1
fi

echo "=== All checks passed ==="

# Record the tree fingerprint so the Claude Code commit gate
# (.claude/hooks/commit_gate.py) knows this exact state passed.
python3 "$ROOT/.claude/hooks/commit_gate.py" --record 2>/dev/null || true
