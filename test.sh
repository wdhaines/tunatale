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
npm run check

echo "=== Frontend tests (with coverage) ==="
npm run test:coverage

echo "=== E2E smoke tests ==="
npm run test:e2e

echo "=== All checks passed ==="
