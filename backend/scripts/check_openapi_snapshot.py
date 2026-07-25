#!/usr/bin/env python3
"""Fail if the committed OpenAPI snapshot is stale or has new untyped endpoints.

Two checks:

1. **Snapshot freshness** — regenerates the schema from ``app.openapi()`` in-
   memory and diffs it against ``frontend/src/lib/api-schema.json``.  A diff
   means the developer forgot to re-run ``dump_openapi.py``.

2. **Untyped endpoint gate** — every operation with a 2xx JSON response must
   declare a Pydantic response model.  The grandfather file
   ``tests/openapi_untyped_grandfather.txt`` lists the current exceptions (one
   operation-id per line).  A new untyped endpoint fails even if it is not yet
   in the ledger.  The ledger is **shrink-only**: an entry that is no longer
   untyped must be removed.

Usage::

    uv run python scripts/check_openapi_snapshot.py
    uv run python scripts/check_openapi_snapshot.py --write-grandfather

Exit 0 = all clean; exit 1 = violation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
SCHEMA_PATH = BACKEND_DIR.parent / "frontend" / "src" / "lib" / "api-schema.json"
DUMP_SCRIPT = Path(__file__).resolve().parent / "dump_openapi.py"
GRANDFATHER_PATH = BACKEND_DIR / "tests" / "openapi_untyped_grandfather.txt"


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


def _load_grandfather(path: Path | None = None) -> set[str]:
    path = path or GRANDFATHER_PATH
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }


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


def _check_untyped(schema: dict[str, object], grandfather_path: Path | None = None) -> int:
    """Return 0 if the ledger is consistent, else 1.

    Reports **both** directions in a single run (never short-circuited):
    - New untyped endpoints not yet in the ledger.
    - Stale ledger entries that are no longer untyped (the ratchet).
    """
    untyped = set(_collect_untyped(schema))
    grandfather = _load_grandfather(grandfather_path)

    new_untyped = sorted(untyped - grandfather)
    stale_entries = sorted(grandfather - untyped)

    rc = 0
    if new_untyped:
        print(
            f"FAIL: {len(new_untyped)} new untyped endpoint(s) (not in grandfather):\n"
            + "\n".join(f"  - {op}" for op in new_untyped)
            + "\n  Fix: add response_model= to the route, then uv run python scripts/dump_openapi.py"
        )
        rc = 1

    if stale_entries:
        print(
            f"FAIL: {len(stale_entries)} stale ledger entry(ies) — "
            "these endpoints now have a response model, remove them from the grandfather:\n"
            + "\n".join(f"  - {op}" for op in stale_entries)
            + f"\n  Fix: delete the line(s) from {grandfather_path or GRANDFATHER_PATH}"
        )
        rc = 1

    return rc


def do_write_grandfather(schema: dict[str, object] | None = None) -> None:
    """Print the current untyped set in grandfather format (to stdout)."""
    if schema is None:
        from app.main import app

        schema = app.openapi()
    for op in _collect_untyped(schema):
        print(op)


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check OpenAPI snapshot freshness and untyped-endpoint gate.",
    )
    parser.add_argument(
        "--write-grandfather",
        action="store_true",
        help="Print the current untyped set in grandfather format to stdout.",
    )
    args = parser.parse_args()

    if args.write_grandfather:
        do_write_grandfather()
        return 0

    return do_check()


if __name__ == "__main__":
    sys.exit(main())
