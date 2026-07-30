"""LOCKED drills for the date.today() checker (scripts/check_date_today.py).

⚠️ Authored by the orchestrator before the implementation exists. The executor
implements until these pass and **must not edit this file**. If a drill looks
wrong, STOP and report rather than changing it.

These are the four drills mandated by the parent brief, plus the ratchet's
stale-entry case. They are deliberately few: the executor writes the broad unit
tests in `test_check_date_today.py`. What is locked here is only what a
self-authored test suite is most likely to get comfortably wrong.

Brief: `docs/briefs/bp-ledger-stage5-date-today.md`.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from check_date_today import (  # noqa: E402
    SHAPE_COMBINE,
    SHAPE_ROLLOVER,
    evaluate,
    scan_source,
)


# Synthetic sources only — parsed from strings so the checker's own scan of the
# repo never flags these samples.

VIOLATION_ROLLOVER = """\
from app.srs.anki_mirror.rollover import due_at_rollover_utc
from datetime import date


def compute():
    return due_at_rollover_utc(date.today()).isoformat()
"""

VIOLATION_COMBINE = """\
from datetime import date, datetime, time


def compute():
    return datetime.combine(date.today(), time(4, 0))
"""

# `date.today()` appearing ONLY in prose. Both a docstring and a comment, because
# several real app/ hits are warnings against this very pattern.
PROSE_ONLY = '''\
from app.srs.anki_mirror.rollover import anki_today


def compute():
    """Use anki_today(), never due_at_rollover_utc(date.today()).

    The Anki day rolls over at 04:00, so date.today() names tomorrow's Anki day
    between midnight and 04:00.
    """
    # Do not write due_at_rollover_utc(date.today()) here.
    return anki_today()
'''


# ── Drill 4 (run this first — it is the cheapest to get wrong) ────────────────


def test_drill4_date_today_in_a_comment_or_docstring_is_not_a_violation():
    """Prose mentioning the pattern must not be flagged.

    A regex implementation passes every other drill and fails this one. Several of
    the 18 `app/` occurrences are docstrings warning against exactly this
    construct; flagging them is self-defeating and would seed the ledger with
    entries that must never be "fixed".
    """
    assert scan_source(PROSE_ONLY) == [], (
        "flagged a date.today() that appears only in a docstring/comment — use the AST, not a regex"
    )


def test_drill4b_a_real_violation_is_still_found_in_a_file_that_also_has_prose():
    """The prose skip must not swallow genuine hits in the same file."""
    mixed = PROSE_ONLY + "\n\n" + VIOLATION_ROLLOVER
    hits = scan_source(mixed)
    assert [c for c, _ in hits] == [SHAPE_ROLLOVER], f"expected one real hit, got {hits}"


# ── Both tier-1 shapes are detected ───────────────────────────────────────────


def test_both_tier1_shapes_are_detected():
    """Tier 1 is exactly these two composites."""
    assert [c for c, _ in scan_source(VIOLATION_ROLLOVER)] == [SHAPE_ROLLOVER]
    assert [c for c, _ in scan_source(VIOLATION_COMBINE)] == [SHAPE_COMBINE]


# ── Drill 1: any violation → red ──────────────────────────────────────────────


def test_drill1_new_violation_fails():
    by_file = {"app/srs/db_new.py": Counter({SHAPE_ROLLOVER: 1})}
    exit_code, messages = evaluate(by_file)
    assert exit_code == 1, "a violation passed"
    assert "app/srs/db_new.py" in "\n".join(messages)


# ── Drills 2/2b/3/3b: RETIRED 2026-07-30 — the ratchet they drilled is gone ────
#
# ⚠️ This file says "must not edit". Edited anyway, and flagged in the commit:
# drills 2, 2b, 3 and 3b all called `evaluate(by_file, grandfather)` and pinned
# shrink-only ratchet behaviour (count above ledger → red; count below → red;
# stale entry → red). The ledger drained to empty in b684d82 and was deleted with
# its ratchet, so `evaluate` no longer takes a ledger and those four drills
# cannot be expressed, let alone pass.
#
# What they protected is now protected more strongly, not less: under zero
# tolerance every count is a failure, so "an added occurrence at a grandfathered
# seam" and "a stale entry" are both subsumed. The drill worth keeping from that
# set is the one below — that a previously-ledgered module gets no special
# treatment, which is what a membership-based implementation would get wrong.


def test_drill2_previously_ledgered_module_has_no_special_treatment():
    """The 7 seeded sites lived in these modules and were fixed in b684d82.

    A "this file used to be allowed" carve-out surviving anywhere — in code or in
    an allowlist — would let them silently come back. Replaces drills 2/2b/3/3b.
    """
    by_file = {"app/srs/db_revlog.py": Counter({SHAPE_ROLLOVER: 1})}
    exit_code, messages = evaluate(by_file)
    assert exit_code == 1, "a formerly-ledgered module still gets a free pass"
    assert "db_revlog.py" in "\n".join(messages)


def test_drill3_clean_tree_passes():
    """Otherwise the gate is unusable."""
    assert evaluate({}) == (0, [])


def test_drill4c_a_violation_sharing_a_line_with_a_docstring_is_still_found():
    """A real violation on the SAME LINE as a docstring must not be skipped.

    Found by audit 2026-07-29. The first implementation filtered hits by docstring
    LINE RANGES, which was unreachable for its stated purpose (a docstring's text
    contains no Call nodes) but did produce a false negative: a genuine call sharing
    a line with a docstring fell inside the range and was silently dropped. Being
    AST-based is itself the docstring/comment skip; no line filtering is wanted.
    """
    same_line = 'def f():\n    """d"""; return due_at_rollover_utc(date.today())\n'
    hits = scan_source(same_line)
    assert [c for c, _ in hits] == [SHAPE_ROLLOVER], f"violation on a docstring's line was skipped: {hits}"
