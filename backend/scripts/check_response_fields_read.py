#!/usr/bin/env python3
"""Fail when an API schema field has no reader in the frontend.

PR #421 shipped a review item's ``drill`` payload that no component read, so
the Review card showed a bare ``[...]`` for two days while every backend test
was green (fixed in #427). This check is the mechanical net for that class.

For every property name in ``frontend/src/lib/api-schema.json``
(``components.schemas.*.properties``), the name must occur as a whole word in
``frontend/src/**/*.{svelte,ts}``, excluding the generated ``api-types.d.ts``,
``*.test.ts`` files and ``frontend/src/test/``. An unread name fails unless
``tests/response_fields_unread_allowlist.txt`` lists it with a one-line reason.
The allowlist is shrink-only: an entry whose name is now read, or that no
schema has any more, fails until it is removed.

Measured oracle: at ``1c97d85b`` (the #421 merge) this check names five fields
of ``ItemDrill`` (``cue_audio_url``, ``before_cue``, ``before_audio_url``,
``before_cue_audio_url``, ``answer_audio_url``); at #427 they are read.

Known weakness: matching is by NAME, not by schema. A name read for another
schema satisfies it — ``drill`` itself was never flagged, because the lesson's
``content.drill`` was read. The check catches a new field with no reader, not a
field read in the wrong place, and a payload made only of common names (``kind``,
``text``) slips through. The Review card's drill journey in Playwright covers
the screen itself.

Usage::

    # exit 0 = clean (run from backend/)
    uv run python scripts/check_response_fields_read.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
SCHEMA_PATH = _REPO / "frontend" / "src" / "lib" / "api-schema.json"
SRC_ROOT = _REPO / "frontend" / "src"
ALLOWLIST_PATH = _REPO / "backend" / "tests" / "response_fields_unread_allowlist.txt"

_WORD = re.compile(r"[A-Za-z_$][\w$]*")
_EXCLUDED_NAMES = {"api-types.d.ts"}


def schema_property_names(schema: dict) -> dict[str, list[str]]:
    """Map each property name to the schemas that declare it."""
    owners: dict[str, list[str]] = {}
    for schema_name, body in schema["components"]["schemas"].items():
        for prop in body.get("properties", {}):
            owners.setdefault(prop, []).append(schema_name)
    return owners


def frontend_words(src_root: Path) -> set[str]:
    """Every identifier-shaped word in the frontend's hand-written source."""
    test_dir = src_root / "test"
    words: set[str] = set()
    for path in src_root.rglob("*"):
        if path.suffix not in (".svelte", ".ts") or path.name in _EXCLUDED_NAMES:
            continue
        if path.name.endswith(".test.ts") or path.is_relative_to(test_dir):
            continue
        words.update(_WORD.findall(path.read_text(encoding="utf-8")))
    return words


def load_reasoned_allowlist(path: Path) -> dict[str, str]:
    """``name  # reason`` per line; a line without a reason, or a repeat, raises."""
    if not path.exists():
        return {}
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        name, _, reason = stripped.partition("#")
        name, reason = name.strip(), reason.strip()
        if not reason:
            raise ValueError(f"allowlist entry `{name}` has no reason — write `{name}  # why it has no reader`.")
        if name in entries:
            raise ValueError(f"allowlist entry `{name}` is listed twice.")
        entries[name] = reason
    return entries


def do_check(schema: dict, words: set[str], allowlist: dict[str, str]) -> list[str]:
    owners = schema_property_names(schema)
    errors = [
        f"`{name}` ({', '.join(owners[name])}) has no reader in frontend/src — read it on a screen, "
        f"delete the field, or allowlist it with a reason."
        for name in sorted(owners)
        if name not in words and name not in allowlist
    ]
    for name in sorted(allowlist):
        if name not in owners:
            errors.append(f"allowlist entry `{name}` is stale: no schema has that field any more — remove it.")
        elif name in words:
            errors.append(f"allowlist entry `{name}` is stale: the frontend reads it now — remove it.")
    return errors


def main(
    schema_path: Path = SCHEMA_PATH,
    src_root: Path = SRC_ROOT,
    allowlist_path: Path = ALLOWLIST_PATH,
) -> int:
    try:
        allowlist = load_reasoned_allowlist(allowlist_path)
    except ValueError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = do_check(schema, frontend_words(src_root), allowlist)
    for error in errors:
        print(f"FAIL: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"OK: every schema field is read in frontend/src or allowlisted ({len(allowlist)} allowlisted).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
