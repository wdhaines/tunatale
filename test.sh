#!/usr/bin/env bash
set -euo pipefail

cd backend

echo "=== Ruff lint ==="
uv run ruff check app tests

echo "=== Ruff format check ==="
uv run ruff format --check app tests

echo "=== Tests ==="
uv run pytest

cd ../frontend
echo "=== Svelte type check ==="
bun run check

echo "=== Frontend tests (with coverage) ==="
bun run test:coverage

echo "=== E2E smoke tests ==="
bun run test:e2e

echo "=== All checks passed ==="
