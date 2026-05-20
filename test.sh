#!/usr/bin/env bash
set -euo pipefail

cd backend

echo "=== Ruff lint ==="
uv run ruff check app tests

echo "=== Ruff format check ==="
uv run ruff format --check app tests

echo "=== Tests ==="
uv run pytest --run-oracle

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
