#!/usr/bin/env bash
set -euo pipefail

cd backend

echo "=== Ruff lint ==="
uv run ruff check app tests

echo "=== Ruff format check ==="
uv run ruff format --check app tests

echo "=== Tests ==="
# -n auto parallelizes across CPU cores; pytest-cov combines per-worker
# coverage so the 100% gate still applies to the full run.
uv run pytest --run-oracle -n auto

cd ../frontend

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

# Clean up coverage data file left by pytest --cov
cd ../backend && uv run coverage erase

echo "=== All checks passed ==="
