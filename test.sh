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
echo "=== Frontend tests ==="
npm test

echo "=== All checks passed ==="
