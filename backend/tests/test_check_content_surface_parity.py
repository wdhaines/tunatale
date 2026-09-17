"""Tests for ``scripts/check_content_surface_parity.py``.

Uses **synthetic routers** — real ``fastapi.APIRouter`` objects built from the
verb map — never the live app routers, which change on every endpoint addition
(that is exactly what the checker exists to police).
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import APIRouter

# Allow importing from scripts/ one level up.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from check_content_surface_parity import (  # noqa: E402
    VERB_MAP,
    _normalize_path,
    do_check,
)

# The three absent cells of the orchestrator-measured map (§9b), in the exact
# shape the allowlist file uses: two DELIBERATE, one OPEN GAP. `source-export
# session` was the fourth until tunatale-w1fp.4 filled that cell.
_DELIBERATE = (
    "prompt-draft lesson",
    "read-by-position session",
)
_OPEN_GAPS = ("regloss lesson",)

# Lesson route → owning router, mirroring §9a's four-router spread ("the
# lesson-side verbs are spread across FOUR routers"); session cells all live on
# ``review_sessions``.
_LESSON_HOST = {
    "create": "generation",
    "create-from-paste": "generation",
    "replace-in-place": "generation",
    "regenerate": "pipeline",
    "prompt-pinned": "generation",
    "read": "generation",
    "read-by-position": "curriculum",
    "source-export": "generation",
    # `regloss` has no lesson route in the real map; this host exists so the
    # shrink-only tests can SIMULATE filling that cell (tunatale-w1fp.5 is the
    # bead that would fill it for real, on the story router beside the others).
    "regloss": "generation",
    "list": "curriculum",
    "render": "audio",
    "render-status": "pipeline",
    "delete": "curriculum",
}


def _stub() -> None:
    """Stub handler — the checker only reads route metadata, never calls."""


def _router(routes: list[tuple[str, str]]) -> APIRouter:
    r = APIRouter()
    for method, path in routes:
        r.add_api_route(path, _stub, methods=[method])
    return r


def _specs_by_router(verb_map=VERB_MAP) -> dict[str, list[tuple[str, str]]]:
    """Split *verb_map* into ``{router_name: [(method, path), …]}``."""
    specs: dict[str, list[tuple[str, str]]] = {
        name: [] for name in ("generation", "review_sessions", "curriculum", "audio", "pipeline")
    }
    for verb, lesson, session in verb_map:
        if lesson is not None:
            method, path = lesson.split(" ", 1)
            specs[_LESSON_HOST[verb]].append((method, path))
        if session is not None:
            method, path = session.split(" ", 1)
            specs["review_sessions"].append((method, path))
    return specs


def _build_routers(verb_map=VERB_MAP) -> dict[str, APIRouter]:
    return {name: _router(specs) for name, specs in _specs_by_router(verb_map).items()}


def _drop(specs: list[tuple[str, str]], target: tuple[str, str]) -> list[tuple[str, str]]:
    return [s for s in specs if s != target]


def _write_allowlist(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "content_surface_allowlist.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


# ── _normalize_path ───────────────────────────────────────────────────────────


class TestNormalizePath:
    def test_renamed_path_param_is_same_shape(self):
        assert _normalize_path("/api/review-sessions/{id}/render") == _normalize_path(
            "/api/review-sessions/{session_id}/render"
        )

    def test_literal_segments_stay_distinct(self):
        assert _normalize_path("/api/review-sessions") != _normalize_path("/api/review-sessions/prompt")

    def test_param_and_literal_segment_are_not_merged(self):
        assert _normalize_path("/api/review-sessions/{session_id}") != _normalize_path("/api/review-sessions/prompt")


# ── clean map — must NOT be reported ──────────────────────────────────────────


class TestLegitimateMap:
    def test_real_map_against_mirrored_routers_passes(self, tmp_path, capsys):
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS)
        assert do_check(routers=_build_routers(), allowlist_path=path) == 0
        assert capsys.readouterr().out == ""

    def test_renamed_session_param_is_same_route_shape(self, tmp_path):
        """``{session_id}`` → ``{id}`` must be treated as the SAME route shape."""
        routers = _build_routers()
        renamed = [
            (method, path.replace("{session_id}", "{id}")) for method, path in _specs_by_router()["review_sessions"]
        ]
        routers["review_sessions"] = _router(renamed)
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS)
        assert do_check(routers=routers, allowlist_path=path) == 0

    def test_shared_lesson_route_passes(self, tmp_path):
        """create-from-paste and replace-in-place SHARE ``POST /api/story/import``."""
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS)
        assert do_check(routers=_build_routers(), allowlist_path=path) == 0


# ── missing routes — must be detected ─────────────────────────────────────────


class TestMissingRouteDetection:
    def test_missing_lesson_route_fails(self, tmp_path, capsys):
        routers = _build_routers()
        routers["generation"] = _router(_drop(_specs_by_router()["generation"], ("GET", "/api/story/prompt")))
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS)
        assert do_check(routers=routers, allowlist_path=path) == 1
        assert "prompt-pinned" in capsys.readouterr().out

    def test_missing_session_route_fails(self, tmp_path, capsys):
        routers = _build_routers()
        routers["review_sessions"] = _router(
            _drop(_specs_by_router()["review_sessions"], ("POST", "/api/review-sessions"))
        )
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS)
        assert do_check(routers=routers, allowlist_path=path) == 1
        assert "create" in capsys.readouterr().out

    def test_prefix_route_does_not_satisfy_longer_route(self, tmp_path, capsys):
        """``GET /api/review-sessions`` must NOT count as ``GET /api/review-sessions/prompt``."""
        routers = _build_routers()
        routers["review_sessions"] = _router(
            _drop(_specs_by_router()["review_sessions"], ("GET", "/api/review-sessions/prompt"))
        )
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS)
        assert do_check(routers=routers, allowlist_path=path) == 1
        out = capsys.readouterr().out
        assert "prompt-draft" in out
        # …and the shorter route it DOES have must not be reported unclassified.
        assert "not a content-surface verb" not in out

    def test_empty_routers_fail_without_crashing(self, tmp_path):
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS)
        assert do_check(routers={}, allowlist_path=path) == 1


# ── the content-router sweep ──────────────────────────────────────────────────


class TestContentRouterSweep:
    def test_unclassified_route_on_generation_fails(self, tmp_path, capsys):
        routers = _build_routers()
        specs = _specs_by_router()["generation"] + [("GET", "/api/story/export/zip")]
        routers["generation"] = _router(specs)
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS)
        assert do_check(routers=routers, allowlist_path=path) == 1
        assert "not a content-surface verb" in capsys.readouterr().out

    def test_unclassified_route_on_review_sessions_fails(self, tmp_path, capsys):
        routers = _build_routers()
        specs = _specs_by_router()["review_sessions"] + [("GET", "/api/review-sessions/stats")]
        routers["review_sessions"] = _router(specs)
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS)
        assert do_check(routers=routers, allowlist_path=path) == 1
        assert "not a content-surface verb" in capsys.readouterr().out

    def test_out_of_scope_route_allowed(self, tmp_path, capsys):
        routers = _build_routers()
        specs = _specs_by_router()["generation"] + [("GET", "/api/story/export/zip")]
        routers["generation"] = _router(specs)
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS)
        assert do_check(routers=routers, allowlist_path=path, out_of_scope={("GET", "/api/story/export/zip")}) == 0
        assert capsys.readouterr().out == ""

    def test_curriculum_router_is_not_swept(self, tmp_path, capsys):
        """Only generation + review_sessions are swept — curriculum/audio/pipeline
        carry planning/settings routes, so only 'map route exists' applies."""
        routers = _build_routers()
        specs = _specs_by_router()["curriculum"] + [
            ("POST", "/api/curriculum/{curriculum_id}/plan"),
            ("GET", "/api/curriculum/{curriculum_id}/progress"),
        ]
        routers["curriculum"] = _router(specs)
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS)
        assert do_check(routers=routers, allowlist_path=path) == 0
        assert capsys.readouterr().out == ""


# ── the shrink-only allowlist ─────────────────────────────────────────────────


class TestAllowlist:
    def test_missing_allowlist_entry_fails(self, tmp_path, capsys):
        path = _write_allowlist(tmp_path, *_DELIBERATE)
        assert do_check(routers=_build_routers(), allowlist_path=path) == 1
        assert "regloss" in capsys.readouterr().out

    def test_missing_allowlist_file_fails(self, tmp_path):
        assert do_check(routers=_build_routers(), allowlist_path=tmp_path / "nope.txt") == 1

    def test_stale_allowlist_entry_fails(self, tmp_path, capsys):
        """Shrink-only: a filled-in cell must drop its allowlist line."""
        filled = tuple(
            ("regloss", "POST /api/story/{lesson_id}/regloss", "POST /api/review-sessions/{session_id}/regloss")
            if verb == "regloss"
            else (verb, lesson, session)
            for verb, lesson, session in VERB_MAP
        )
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS)  # still carries the stale line
        assert do_check(routers=_build_routers(filled), allowlist_path=path, verb_map=filled) == 1
        assert "shrink-only" in capsys.readouterr().out.lower()

    def test_filled_cell_requires_no_entry(self, tmp_path, capsys):
        filled = tuple(
            ("regloss", "POST /api/story/{lesson_id}/regloss", "POST /api/review-sessions/{session_id}/regloss")
            if verb == "regloss"
            else (verb, lesson, session)
            for verb, lesson, session in VERB_MAP
        )
        path = _write_allowlist(tmp_path, *_DELIBERATE)
        assert do_check(routers=_build_routers(filled), allowlist_path=path, verb_map=filled) == 0
        assert capsys.readouterr().out == ""

    def test_unknown_verb_entry_fails(self, tmp_path, capsys):
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS, "write lesson")
        assert do_check(routers=_build_routers(), allowlist_path=path) == 1
        assert "write" in capsys.readouterr().out

    def test_malformed_entry_fails(self, tmp_path, capsys):
        path = _write_allowlist(tmp_path, *_DELIBERATE, *_OPEN_GAPS, "read-by-position")
        assert do_check(routers=_build_routers(), allowlist_path=path) == 1
        assert "read-by-position" in capsys.readouterr().out

    def test_comments_and_inline_reasons_are_ignored(self, tmp_path, capsys):
        path = _write_allowlist(
            tmp_path,
            "# Content-surface verb-map — shrink-only.",
            "prompt-draft lesson  # DELIBERATE: no id-less draft to export.",
            "read-by-position session  # DELIBERATE: sessions have no position.",
            "regloss lesson  # OPEN GAP — bd tunatale-w1fp.5.",
        )
        assert do_check(routers=_build_routers(), allowlist_path=path) == 0
        assert capsys.readouterr().out == ""
