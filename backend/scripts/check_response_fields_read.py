#!/usr/bin/env python3
"""Fail when an API schema field has no reader in the frontend.

PR #421 shipped a review item's ``drill`` payload that no component read, so
the Review card showed a bare ``[...]`` for two days while every backend test
was green (fixed in #427). This check is the mechanical net for that class.

For every property name in ``frontend/src/lib/api-schema.json``
(``components.schemas.*.properties``), the name must occur as a whole word in
the CODE of ``frontend/src/**/*.{svelte,ts}``, excluding the generated
``api-types.d.ts``, ``*.test.ts`` files and ``frontend/src/test/``. Before the
words are collected, what names a field without reading it is removed:
comments (``//``, ``/* */``, ``<!-- -->``), a component's ``<style>`` block, and
``interface``/``type`` declarations (a hand-written ``api.ts`` interface is a
declaration, not a reader). An object-literal key that builds a request body
stays, because sending a field is using it. An unread name fails unless
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

Also still counted as a read (no type checker here, only text): an inline type
literal that is not a generic argument (``let x: { field: T }``, a parameter's
``(p: { field: T })``), a field name inside a string, and a component prop of
the same name.

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


_STYLE_BLOCK = re.compile(r"<style\b[^>]*>.*?</style>", re.DOTALL)
_DECLARATION = re.compile(r"\b(?:interface\s+[A-Za-z_$][\w$]*[^{;=]*\{|type\s+[A-Za-z_$][\w$]*\s*(?:<[^=]*?>)?\s*=)")
_GENERIC_LITERAL = re.compile(r"<\s*\{")
_OPEN = "([{<"
_CLOSE = ")]}>"


def _strip_comments(text: str) -> str:
    """Drop ``//``, ``/* */`` and ``<!-- -->`` comments. Quoted text is kept, and a
    marker inside a quote (a URL's ``//``) is not a comment."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
        elif text.startswith("/*", i) or text.startswith("<!--", i):
            close = "*/" if text[i] == "/" else "-->"
            end = text.find(close, i + 2)
            i = n if end < 0 else end + len(close)
        elif text[i] in "'\"`":
            quote, j = text[i], i + 1
            while j < n and text[j] != quote and not (quote != "`" and text[j] == "\n"):
                j += 2 if text[j] == "\\" else 1
            out.append(text[i : j + 1])
            i = j + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _declaration_end(text: str, start: int, *, braced: bool) -> int:
    """Index just past the declaration whose body starts at *start*.

    An ``interface`` ends at its closing brace. A ``type`` alias ends at a ``;``
    at depth 0, or at a depth-0 line break not followed by a ``|``/``&``
    continuation.
    """
    depth = 1 if braced else 0
    i, n = start, len(text)
    while i < n:
        ch = text[i]
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE and not (ch == ">" and text[i - 1] == "="):  # `=>` closes nothing
            depth -= 1
            if braced and depth == 0:
                return i + 1
        elif not braced and depth == 0:
            if ch == ";":
                return i + 1
            if ch == "\n" and text[start:i].strip() and not text[i:].lstrip().startswith(("|", "&")):
                return i
        i += 1
    return n


def _strip_declarations(text: str) -> str:
    out: list[str] = []
    pos = 0
    while (m := _DECLARATION.search(text, pos)) is not None:
        out.append(text[pos : m.start()])
        pos = _declaration_end(text, m.end(), braced=m.group().endswith("{"))
    out.append(text[pos:])
    return "".join(out)


def _strip_generic_literals(text: str) -> str:
    """Drop a ``{ ... }`` that opens a generic argument (``Promise<{ field: T }>``,
    ``request<{ field: T }>(...)``): after ``<`` a brace is a type, never a body."""
    out: list[str] = []
    pos = 0
    while (m := _GENERIC_LITERAL.search(text, pos)) is not None:
        out.append(text[pos : m.start() + 1])  # keep the `<`
        pos = _declaration_end(text, m.end(), braced=True)
    out.append(text[pos:])
    return "".join(out)


def code_text(text: str) -> str:
    """*text* with comments, ``<style>`` blocks and type declarations removed."""
    return _strip_generic_literals(_strip_declarations(_STYLE_BLOCK.sub("", _strip_comments(text))))


def frontend_words(src_root: Path) -> set[str]:
    """Every identifier-shaped word in the frontend's hand-written source."""
    test_dir = src_root / "test"
    words: set[str] = set()
    for path in src_root.rglob("*"):
        if path.suffix not in (".svelte", ".ts") or path.name in _EXCLUDED_NAMES:
            continue
        if path.name.endswith(".test.ts") or path.is_relative_to(test_dir):
            continue
        words.update(_WORD.findall(code_text(path.read_text(encoding="utf-8"))))
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
