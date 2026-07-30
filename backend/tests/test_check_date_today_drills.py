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


# ── Drill 1: a new violation in a NON-ledgered module → red ───────────────────


def test_drill1_new_violation_in_unledgered_module_fails():
    by_file = {"app/srs/db_new.py": Counter({SHAPE_ROLLOVER: 1})}
    exit_code, messages = evaluate(by_file, {})
    assert exit_code == 1, "a brand-new violation in an unledgered module passed"
    assert messages
    assert "app/srs/db_new.py" in "\n".join(messages)


# ── Drill 2: the ratchet counts occurrences, not membership ───────────────────


def test_drill2_extra_occurrence_in_a_ledgered_module_fails():
    """Ledgered at 1, actual 2 → red.

    This is the drill that proves the ratchet counts. A membership-only
    implementation ("the file is in the ledger, so it's fine") passes drill 1 and
    fails here — and would let the 7 production sites quietly become 8.
    """
    by_file = {"app/srs/db_revlog.py": Counter({SHAPE_ROLLOVER: 2})}
    grandfather = {("app/srs/db_revlog.py", SHAPE_ROLLOVER): 1}
    exit_code, messages = evaluate(by_file, grandfather)
    assert exit_code == 1, "an added occurrence at a grandfathered seam passed"
    assert messages


def test_drill2b_matching_count_passes():
    """The ledgered count exactly → green. Otherwise the gate is unusable."""
    by_file = {"app/srs/db_revlog.py": Counter({SHAPE_ROLLOVER: 2})}
    grandfather = {("app/srs/db_revlog.py", SHAPE_ROLLOVER): 2}
    exit_code, messages = evaluate(by_file, grandfather)
    assert exit_code == 0, f"exact ledgered count failed: {messages}"


# ── Drill 3: a fixed site must not leave its ledger line behind ───────────────


def test_drill3_stale_ledger_entry_fails():
    """Fix the site properly but leave the ledger line → red (stale entry).

    Without this, the ledger rots into a list of things that used to be true, and
    the shrink-only ratchet stops meaning anything.
    """
    exit_code, messages = evaluate({}, {("app/srs/db_revlog.py", SHAPE_ROLLOVER): 1})
    assert exit_code == 1, "a stale ledger entry passed"
    assert messages


def test_drill3b_partial_fix_must_shrink_the_ledger_line():
    """Ledgered at 2, one fixed, actual 1 → red, telling you to edit the line."""
    by_file = {"app/srs/db_revlog.py": Counter({SHAPE_ROLLOVER: 1})}
    grandfather = {("app/srs/db_revlog.py", SHAPE_ROLLOVER): 2}
    exit_code, messages = evaluate(by_file, grandfather)
    assert exit_code == 1, "a below-ledger count passed silently"
    assert messages


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
