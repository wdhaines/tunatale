#!/usr/bin/env python3
"""Fail if the committed OpenAPI snapshot is stale or has untyped endpoints.

Two checks:

1. **Snapshot freshness** — regenerates the schema from ``app.openapi()`` in-
   memory and diffs it against ``frontend/src/lib/api-schema.json``.  A diff
   means the developer forgot to re-run ``dump_openapi.py``.

2. **Untyped endpoint gate — zero tolerance.** Every operation with a 2xx JSON
   response must declare a Pydantic response model.  There is no ledger and no
   escape hatch: any untyped operation fails.

   The shrink-only ``tests/openapi_untyped_grandfather.txt`` drained 70 -> 0 over
   eleven batches and was deleted on 2026-08-02, along with the ratchet that
   enforced it, exactly as the mock / language-literal / date-today ledgers were
   in ``7b34c73``.  "Shrink-only ledger, currently at zero" and "no additions,
   period" are the same rule; only the second needs machinery.

Usage::

    uv run python scripts/check_openapi_snapshot.py

Exit 0 = all clean; exit 1 = violation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCHEMA_PATH = BACKEND_DIR.parent / "frontend" / "src" / "lib" / "api-schema.json"
DUMP_SCRIPT = Path(__file__).resolve().parent / "dump_openapi.py"


def _is_typed_schema(schema: dict | list, *, _depth: int = 0) -> bool:
    """Return True if *schema* carries a structural type ($ref or properties).

    Recurses through ``items``, ``anyOf``, ``allOf``, and ``oneOf`` wrappers
    so that ``list[Model]``, ``Model | None``, and ``allOf(Model)`` are
    recognised as typed.  Bare ``{}``, ``additionalProperties``, and scalar
    type hints (``{"type": "string"}``) are **not** typed.

    Bounded to 10 levels to guard against pathological schemas.
    """
    if _depth > 10:
        return False
    if isinstance(schema, list):
        return any(_is_typed_schema(s, _depth=_depth + 1) for s in schema)
    if not isinstance(schema, dict):
        return False
    if "$ref" in schema or "properties" in schema:
        return True
    for key in ("items", "additionalProperties"):
        if key in schema and _is_typed_schema(schema[key], _depth=_depth + 1):
            return True
    for combiner in ("anyOf", "allOf", "oneOf"):
        if combiner in schema and _is_typed_schema(schema[combiner], _depth=_depth + 1):
            return True
    return False


def _collect_untyped(schema: dict[str, object]) -> list[str]:
    """Return sorted operation-ids whose 2xx JSON response has no schema."""
    untyped: list[str] = []
    for path, methods in schema.get("paths", {}).items():
        for method, op in methods.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            for code, resp in op.get("responses", {}).items():
                if not code.startswith("2"):
                    continue
                content = resp.get("content", {})
                if "application/json" not in content:
                    continue
                schema_obj = content["application/json"].get("schema", {})
                if not _is_typed_schema(schema_obj):
                    op_id = op.get("operationId", f"{method} {path}")
                    untyped.append(op_id)
    return sorted(untyped)


def _check_staleness(current: dict[str, object]) -> int:
    """Return 0 if committed snapshot matches *current*, else 1 with a hint."""
    if not SCHEMA_PATH.exists():
        print(f"FAIL: snapshot not found at {SCHEMA_PATH}\n  Fix: uv run python {DUMP_SCRIPT}")
        return 1

    committed = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    # Compare as JSON round-trips to avoid non-JSON-native values causing
    # permanent red (e.g. datetime objects in the schema dict).
    current_json = json.loads(json.dumps(current))
    if current_json == committed:
        return 0

    current_str = json.dumps(current_json, indent=2, sort_keys=True)
    committed_str = json.dumps(committed, indent=2, sort_keys=True)
    current_lines = current_str.splitlines()
    committed_lines = committed_str.splitlines()
    diff_hint = ""
    for i, (c, r) in enumerate(zip(current_lines, committed_lines, strict=False)):
        if c != r:
            diff_hint = f"  First diff at line {i + 1}:\n    current:  {c}\n    committed: {r}"
            break
    else:
        if len(current_lines) != len(committed_lines):
            diff_hint = f"  Schema length changed: {len(current_lines)} vs {len(committed_lines)} lines"

    print(f"FAIL: OpenAPI snapshot is stale.\n  {diff_hint}\n  Fix: uv run python {DUMP_SCRIPT}")
    return 1


def _check_untyped(schema: dict[str, object]) -> int:
    """Return 0 if every 2xx JSON operation is typed, else 1.

    Zero tolerance — no ledger, no exceptions. A binary endpoint that trips this
    should declare its real media type (``responses=`` / ``response_class=``)
    rather than advertising JSON; see ``TestBinaryEndpointsDeclareNonJson``.
    """
    untyped = _collect_untyped(schema)
    if not untyped:
        return 0
    print(
        f"FAIL: {len(untyped)} untyped endpoint(s) — a 2xx JSON response with no schema:\n"
        + "\n".join(f"  - {op}" for op in untyped)
        + "\n  Fix: add response_model= to the route, then uv run python scripts/dump_openapi.py"
    )
    return 1


def do_check() -> int:
    try:
        from app.main import app

        current = app.openapi()
    except Exception as exc:
        print(f"FAIL: could not generate OpenAPI schema: {exc}", file=sys.stderr)
        return 1

    rc = 0
    if _check_staleness(current) != 0:
        rc = 1
    if _check_untyped(current) != 0:
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(do_check())
