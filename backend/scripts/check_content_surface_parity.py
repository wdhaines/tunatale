#!/usr/bin/env python3
"""Fail if the two content surfaces (lessons vs review sessions) drift apart.

The lesson-side verbs are spread across FOUR routers (``generation.py``,
``curriculum.py``, ``audio.py``, ``pipeline.py``), while the session-side verbs
all live in one (``review_sessions.py``) — so a router-vs-router comparison
reports ~8 false asymmetries and the gate means nothing. Instead this checker
holds a **verb map**: semantic verb → (lesson route, session route), where
either side may be absent only with a recorded reason in the shrink-only
allowlist at ``tests/content_surface_allowlist.txt``. (The map supersedes the
brief's §6 "same verb set" design; see §9.)

Three assertions, in both directions:

1. **Every route named in the map exists.** A renamed or deleted route fails.
2. **Every route on the two CONTENT routers** (``generation.router``,
   ``review_sessions.router``) is either named in the map or listed in the
   script's ``OUT_OF_SCOPE``. A new route on either that nobody classified
   fails. The sweep deliberately does NOT extend to ``curriculum.py`` /
   ``audio.py`` / ``pipeline.py`` — those carry planning/settings routes that
   are not content-surface verbs; for them only assertion 1 applies.
3. **Every absent cell has an allowlist entry, and every entry corresponds to
   an actually-absent cell** — shrink-only: a cell that gets filled in later
   must fail until its allowlist line is removed.

Route shapes are compared with path parameters normalized (``{session_id}``
and ``{id}`` are the SAME shape — a rename must not rot the map), while literal
segments stay distinct (``/api/review-sessions`` is not
``/api/review-sessions/prompt``).

Usage::

    # exit 0 = clean
    uv run python scripts/check_content_surface_parity.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from _checker_lib import load_allowlist

ALLOWLIST_PATH = Path("tests/content_surface_allowlist.txt")

# §9a — orchestrator-measured, use verbatim. ``None`` = the verb has no route
# on that side (each such cell REQUIRES an allowlist entry, §9b).
VERB_MAP: tuple[tuple[str, str | None, str | None], ...] = (
    ("create", "POST /api/story/generate", "POST /api/review-sessions"),
    ("create-from-paste", "POST /api/story/import", "POST /api/review-sessions/import"),
    ("replace-in-place", "POST /api/story/import", "POST /api/review-sessions/{session_id}/import"),
    (
        "regenerate",
        "POST /api/curriculum/{curriculum_id}/pipeline/regenerate",
        "POST /api/review-sessions/{session_id}/regenerate",
    ),
    ("prompt-pinned", "GET /api/story/prompt", "GET /api/review-sessions/{session_id}/prompt"),
    ("prompt-draft", None, "GET /api/review-sessions/prompt"),
    ("read", "GET /api/story/{lesson_id}", "GET /api/review-sessions/{session_id}"),
    ("read-by-position", "GET /api/curriculum/{curriculum_id}/days/{day}/lesson", None),
    ("source-export", "GET /api/story/{lesson_id}/source", None),
    ("list", "GET /api/curriculum/{curriculum_id}", "GET /api/review-sessions"),
    ("render", "POST /api/audio/render", "POST /api/review-sessions/{session_id}/render"),
    (
        "render-status",
        "GET /api/curriculum/{curriculum_id}/pipeline",
        "GET /api/review-sessions/{session_id}/render-status",
    ),
    ("regloss", None, "POST /api/review-sessions/{session_id}/regloss"),
    ("delete", "DELETE /api/curriculum/{curriculum_id}/days/{day}", "DELETE /api/review-sessions/{session_id}"),
)

# These two verbs share ONE lesson route: a lesson import is addressed by
# (curriculum_id, day) and both creates and replaces. Not a defect — do not
# "fix" it (§9a note).

# Routers swept for unclassified routes (§9c assertion 2).
CONTENT_ROUTERS = ("generation", "review_sessions")

# Routes on the content routers that are deliberately not content-surface
# verbs (planning/settings/…). Entries are (method, normalized-path) tuples —
# normalize params with ``_normalize_path`` before adding. Currently empty;
# add only with a reason.
OUT_OF_SCOPE: frozenset[tuple[str, str]] = frozenset()


def _normalize_path(path: str) -> str:
    """Route shape with path params canonicalized: ``{session_id}`` and
    ``{id}`` are the SAME shape, while literal segments stay distinct."""
    return re.sub(r"\{[^}]+\}", "{param}", path)


def _split_spec(spec: str) -> tuple[str, str]:
    """``"POST /api/story/generate"`` → ``("POST", "/api/story/generate")``."""
    method, path = spec.split(" ", 1)
    return method, path


def _route_shapes(router: object) -> tuple[set[tuple[str, str]], dict[tuple[str, str], list[str]]]:
    """Route shapes of *router*: ``{(method, normalized_path)}`` plus a
    shape → [original paths] map for readable failure output.

    HEAD is auto-added by Starlette on every GET route and is not a declared
    surface verb — it is dropped so it never shows up as unclassified.
    """
    from fastapi.routing import APIRoute

    shapes: set[tuple[str, str]] = set()
    originals: dict[tuple[str, str], list[str]] = {}
    for route in getattr(router, "routes", ()):
        if not isinstance(route, APIRoute):
            continue
        norm = _normalize_path(route.path)
        methods = {m for m in (route.methods or set()) if m != "HEAD"}
        for method in sorted(methods):
            shape = (method, norm)
            shapes.add(shape)
            originals.setdefault(shape, [])
            if route.path not in originals[shape]:
                originals[shape].append(route.path)
    return shapes, originals


def _default_routers() -> dict[str, object]:
    """The production routers — imported lazily so tests can inject synthetic
    ones without paying for the app import."""
    from app.api.audio import router as audio_router
    from app.api.curriculum import router as curriculum_router
    from app.api.generation import router as generation_router
    from app.api.pipeline import router as pipeline_router
    from app.api.review_sessions import router as review_sessions_router

    return {
        "generation": generation_router,
        "review_sessions": review_sessions_router,
        "curriculum": curriculum_router,
        "audio": audio_router,
        "pipeline": pipeline_router,
    }


def do_check(
    *,
    routers: dict[str, object] | None = None,
    allowlist_path: Path | None = None,
    verb_map: tuple[tuple[str, str | None, str | None], ...] = VERB_MAP,
    out_of_scope: frozenset[tuple[str, str]] = OUT_OF_SCOPE,
) -> int:
    """Run all three assertions. Returns 0 = clean, 1 = violation."""
    if routers is None:
        routers = _default_routers()
    if allowlist_path is None:
        allowlist_path = ALLOWLIST_PATH

    # Map cells → route shapes, and the absent (None) cells per side.
    lesson_cells: dict[tuple[str, str], str] = {}
    session_cells: dict[tuple[str, str], str] = {}
    absent_cells: set[tuple[str, str]] = set()
    spec_by_shape: dict[tuple[str, str], str] = {}
    for verb, lesson, session in verb_map:
        if lesson is None:
            absent_cells.add((verb, "lesson"))
        else:
            method, path = _split_spec(lesson)
            spec_by_shape[(method, _normalize_path(path))] = lesson
            lesson_cells[method, _normalize_path(path)] = verb
        if session is None:
            absent_cells.add((verb, "session"))
        else:
            method, path = _split_spec(session)
            spec_by_shape[(method, _normalize_path(path))] = session
            session_cells[method, _normalize_path(path)] = verb

    real: dict[str, set[tuple[str, str]]] = {}
    originals: dict[str, dict[tuple[str, str], list[str]]] = {}
    for name, router in routers.items():
        shapes, orig = _route_shapes(router)
        real[name] = shapes
        originals[name] = orig
    all_real = set().union(*real.values()) if real else set()

    failures: list[str] = []

    # ── Assertion 1: every route named in the map exists (across all routers).
    missing: dict[tuple[str, str], dict[str, object]] = {}
    for shape, verb in {**lesson_cells, **session_cells}.items():
        if shape in all_real:
            continue
        entry = missing.setdefault(shape, {"spec": spec_by_shape[shape], "verbs": set()})
        entry["verbs"].add(verb)  # type: ignore[union-attr]

    for entry in missing.values():
        verbs = ", ".join(sorted(entry["verbs"]))  # type: ignore[union-attr]
        failures.append(f"`{entry['spec']}` (verb `{verbs}`) is not registered on any router.")

    # ── Assertion 2: content-router sweep.
    classified = set(lesson_cells) | set(session_cells) | set(out_of_scope)
    for name in CONTENT_ROUTERS:
        if name not in real:
            failures.append(f"content router `{name}` is not available to the checker.")
            continue
        for shape in sorted(real[name]):
            if shape in classified:
                continue
            shown = originals[name][shape][0]
            failures.append(f"`{shown}` on the {name} router is not a content-surface verb and not in OUT_OF_SCOPE.")

    # ── Assertion 3: the shrink-only allowlist, both directions.
    entries: set[tuple[str, str]] = set()
    known_verbs = {verb for verb, _, _ in verb_map}
    for line in load_allowlist(allowlist_path):
        parts = line.split()
        if len(parts) != 2:
            failures.append(f"allowlist entry `{line}` is not `<verb> <lesson|session>`.")
            continue
        verb, side = parts
        if verb not in known_verbs:
            failures.append(f"allowlist entry `{line}` names unknown verb `{verb}`.")
            continue
        if side not in ("lesson", "session"):
            failures.append(f"allowlist entry `{line}` has unknown side `{side}`.")
            continue
        entries.add((verb, side))

    for verb, side in sorted(absent_cells):
        if (verb, side) not in entries:
            failures.append(
                f"verb `{verb}`/{side} is absent but has no allowlist entry — add "
                f"`{verb} {side}` with its reason (DELIBERATE or OPEN GAP)."
            )

    for verb, side in sorted(entries):
        if (verb, side) not in absent_cells:
            failures.append(
                f"allowlist entry `{verb} {side}` no longer matches an absent cell — REMOVE it (shrink-only)."
            )

    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(do_check())
