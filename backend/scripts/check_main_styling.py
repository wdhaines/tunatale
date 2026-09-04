#!/usr/bin/env python3
"""Every ``+page.svelte`` (and ``+layout.svelte``) that renders ``<main>``
must style that element — via the ``main`` element selector or via a class/id
the element carries.

WHY — a real bug, not a hypothetical (22fc16b, 2026-09-03)
  The review-session reader rendered at full viewport width: a phone layout on a
  desktop.  Its ``<main>`` had NO rule at all — missing the width cap, the
  centering, the padding, and the ``gap`` that spaced its bands.  Svelte styles
  are component-scoped, so the lesson page's identical rule never applied.

WHY A CHECKER AND NOT A TEST
  Component tier CANNOT catch it: jsdom performs no layout, so every element
  reports 0x0.  A Playwright geometry spec would catch it, but only on routes
  it visits, and no e2e seeds a review session today.  The checker covers
  every route at once and costs no browser.

Detection is regex-based.  Three narrowings, each of which was a silent false
result before it was added:

  * HTML comments are stripped before looking for the render, so
    ``<!-- <main> is unstyled -->`` does not count as one.  ``review-sessions``
    discusses ``<main>`` twice inside a CSS comment.
  * The selector search runs ONLY inside ``<style>`` blocks, with CSS comments
    stripped.  Searching whole-file text let a commented-out rule, or the string
    ``main {`` anywhere in script or markup, satisfy the check.
  * Selector matches are boundary-anchored and accept ``,`` as well as ``{``.
    Without the anchors ``.main``, ``.remain`` and ``.domain`` all satisfied the
    ``main`` ELEMENT check; without the comma, a valid ``main, footer { }`` rule
    was reported as a violation.

The ``settings`` route styles ``<main class="settings">`` via ``.settings`` — it
is NOT a violation.

ZERO TOLERANCE, no allowlist.  All routes conform today; the checker ships
already-green.  An empty shrink-only ledger and "no additions, period" are the
same rule.

Usage::

    uv run python scripts/check_main_styling.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ROUTES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "routes"

_TAG_RE = re.compile(r"<main\b([^>]*)>", re.DOTALL)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_STYLE_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.DOTALL | re.IGNORECASE)


def _strip_comments(source: str) -> str:
    return _CSS_COMMENT_RE.sub("", _COMMENT_RE.sub("", source))


def _style_text(source: str) -> str:
    """Concatenated CSS from every ``<style>`` block, comments stripped.

    The invariant is about a rule in the component's OWN style block; matching
    anywhere in the file let script strings and commented-out rules count.
    """
    return "\n".join(_CSS_COMMENT_RE.sub("", m) for m in _STYLE_RE.findall(source))


def _extract_selectors(tag: str) -> list[str]:
    selectors: list[str] = ["main"]
    for attr in re.finditer(r'(class|id)\s*=\s*"([^"]*)"', tag):
        prefix = "." if attr.group(1) == "class" else "#"
        for value in attr.group(2).split():
            selectors.append(f"{prefix}{value}")
    return selectors


def check_source(source: str) -> list[str]:
    """Return a list of violation messages for *source*.

    Empty list means conforming (or no ``<main>`` rendered).  Only the first
    ``<main>`` element is checked — if there are multiple, styling one is
    sufficient (the claim is presence, never count).
    """
    tag_match = _TAG_RE.search(_strip_comments(source))
    if tag_match is None:
        return []
    css = _style_text(source)
    for selector in _extract_selectors(tag_match.group()):
        # Boundary-anchored: `.main`/`.remain` must not satisfy bare `main`, and
        # `main` must not satisfy `main-thing`. A rule may end in `,` (selector
        # list), `{`, or a further compound part such as `.wide` / `:hover`.
        pattern = r"(?<![\w.#-])" + re.escape(selector) + r"(?![\w-])[^{;}]*\{"
        if re.search(pattern, css, re.DOTALL):
            return []
    return ["missing style for <main>"]


def evaluate(results: dict[str, list[str]]) -> tuple[int, list[str]]:
    """Decide exit code and messages from per-file check_source results."""
    messages = []
    for path, violations in sorted(results.items()):
        for v in violations:
            messages.append(f"FAIL: {path} — {v}")
    return (1 if messages else 0), messages


def do_check() -> int:
    """Walk routes, check each +page.svelte / +layout.svelte, print results."""
    results: dict[str, list[str]] = {}
    for svelte in sorted(_ROUTES.rglob("*.svelte")):
        if svelte.name not in ("+page.svelte", "+layout.svelte"):
            continue
        rel = str(svelte.relative_to(_ROUTES.parent))
        results[rel] = check_source(svelte.read_text(encoding="utf-8"))
    code, messages = evaluate(results)
    for msg in messages:
        print(msg)
    return code


if __name__ == "__main__":  # pragma: no cover - CLI guard
    sys.exit(do_check())
