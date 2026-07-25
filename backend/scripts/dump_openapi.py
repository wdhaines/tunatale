#!/usr/bin/env python3
"""Dump the FastAPI OpenAPI schema to a committed JSON artifact.

Writes ``frontend/src/lib/api-schema.json`` so the frontend can derive
TypeScript types without a running server.  Key order is stable (Python ≥3.7
dict + json.dumps default).

Usage::

    uv run python scripts/dump_openapi.py

Exit 0 = wrote schema; exit 1 = generation failed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "lib" / "api-schema.json"


def dump() -> int:
    try:
        from app.main import app

        schema = app.openapi()
    except Exception as exc:
        print(f"FAIL: could not generate OpenAPI schema: {exc}", file=sys.stderr)
        return 1

    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {SCHEMA_PATH} ({SCHEMA_PATH.stat().st_size} bytes)")
    return 0


def main() -> int:
    return dump()


if __name__ == "__main__":
    sys.exit(main())
