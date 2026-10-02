#!/usr/bin/env python3
"""Dump the FastAPI OpenAPI schema to a committed JSON artifact.

Writes ``frontend/src/lib/api-schema.json`` so the frontend can derive
TypeScript types without a running server.  Key order is stable (Python ≥3.7
dict + json.dumps default).

Usage::

    uv run python scripts/dump_openapi.py

The result does not depend on the local ``backend/.env``: see
:func:`build_schema`.

Exit 0 = wrote schema; exit 1 = generation failed.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "frontend" / "src" / "lib" / "api-schema.json"


def build_schema() -> dict[str, object]:
    """The app's OpenAPI schema. The one place the dump and the check get it.

    The snapshot describes the DEFAULT deployment, not this developer's.
    ``app/main.py`` mounts the anki router only when ``settings.sync_enabled``
    is true, and a laptop's ``backend/.env`` sets ``SYNC_ENABLED=false``
    whenever production is the live AnkiWeb syncer. Left to that file, a dump
    run by hand silently drops every ``/api/anki`` path. A process environment
    variable outranks the ``.env`` file, so pin it here, before ``app`` is
    imported and its settings are read.
    """
    os.environ["SYNC_ENABLED"] = "true"
    from app.main import app

    return app.openapi()


def dump() -> int:
    try:
        schema = build_schema()
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
