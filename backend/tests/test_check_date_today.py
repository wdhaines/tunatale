"""Unit tests for the date.today() checker (scripts/check_date_today.py).

Uses parsed-from-string sources so the checker's own scan never flags these
samples.
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


# ── scan_source ───────────────────────────────────────────────────────────────


class TestScanSource:
    def test_detects_due_at_rollover_utc(self):
        source = "due_at_rollover_utc(date.today())"
        hits = scan_source(source)
        assert hits == [(SHAPE_ROLLOVER, 1)]

    def test_detects_datetime_combine(self):
        source = "datetime.combine(date.today(), time(4, 0))"
        hits = scan_source(source)
        assert hits == [(SHAPE_COMBINE, 1)]

    def test_detects_combine_with_more_args(self):
        source = "datetime.combine(date.today(), time(4, 0, tzinfo=UTC))"
        hits = scan_source(source)
        assert hits == [(SHAPE_COMBINE, 1)]

    def test_ignores_datetime_combine_with_non_4_time(self):
        source = "datetime.combine(date.today(), time(1, 0))"
        assert scan_source(source) == []

    def test_ignores_datetime_combine_with_time_max(self):
        source = "datetime.combine(date.today(), time.max)"
        assert scan_source(source) == []

    def test_ignores_bare_date_today(self):
        source = "x = date.today()"
        assert scan_source(source) == []

    def test_ignores_bare_due_at_rollover_utc(self):
        source = "x = due_at_rollover_utc()"
        assert scan_source(source) == []

    def test_empty_source(self):
        assert scan_source("") == []

    def test_only_comments(self):
        source = "# due_at_rollover_utc(date.today())"
        assert scan_source(source) == []

    def test_inside_module_docstring_is_skipped(self):
        source = '"""due_at_rollover_utc(date.today()) is wrong here."""\nx = 1'
        assert scan_source(source) == []

    def test_inside_function_docstring_is_skipped(self):
        source = 'def foo():\n    """due_at_rollover_utc(date.today()) warning."""\n    pass'
        assert scan_source(source) == []

    def test_inside_class_docstring_is_skipped(self):
        source = 'class Foo:\n    """due_at_rollover_utc(date.today()) warning."""\n    pass'
        assert scan_source(source) == []

    def test_real_code_outside_docstring_is_detected(self):
        source = (
            "def foo():\n"
            '    """due_at_rollover_utc(date.today()) is wrong."""\n'
            "    return due_at_rollover_utc(date.today())\n"
        )
        hits = scan_source(source)
        assert hits == [(SHAPE_ROLLOVER, 3)]

    def test_multiple_hits(self):
        source = (
            "a = due_at_rollover_utc(date.today())\n"
            "b = datetime.combine(date.today(), time(4, 0))\n"
            "c = due_at_rollover_utc(date.today())\n"
        )
        hits = scan_source(source)
        assert len(hits) == 3
        assert hits[0] == (SHAPE_ROLLOVER, 1)
        assert hits[1] == (SHAPE_COMBINE, 2)
        assert hits[2] == (SHAPE_ROLLOVER, 3)

    def test_async_function_docstring_skipped(self):
        source = 'async def bar():\n    """docstring with date.today() pattern"""\n    pass'
        assert scan_source(source) == []

    def test_rollover_with_qualified_name(self):
        source = "rollover.due_at_rollover_utc(date.today())"
        hits = scan_source(source)
        assert hits == [(SHAPE_ROLLOVER, 1)]

    def test_rollover_deeply_qualified(self):
        source = "srs.anki_mirror.rollover.due_at_rollover_utc(date.today())"
        hits = scan_source(source)
        assert hits == [(SHAPE_ROLLOVER, 1)]

    def test_real_app_pattern_line(self):
        source = (
            "from app.srs.anki_mirror.rollover import due_at_rollover_utc\n"
            "from datetime import date\n\n"
            "today_due_at = due_at_rollover_utc(date.today()).isoformat()\n"
        )
        hits = scan_source(source)
        assert hits == [(SHAPE_ROLLOVER, 4)]


# ── evaluate ──────────────────────────────────────────────────────────────────


class TestEvaluate:
    """Zero tolerance since 2026-07-30: the ledger and its shrink-only ratchet are
    gone, so there is exactly one rule — any hit fails."""

    def test_message_names_the_fix(self):
        """The failure has to say what to do; this checker has no escape hatch,
        so an unhelpful message is the thing that gets it disabled."""
        _, messages = evaluate({"app/foo.py": Counter({SHAPE_ROLLOVER: 1})})
        assert "anki_today()" in messages[0]

    def test_previously_ledgered_file_is_no_longer_exempt(self):
        """The 7 seeded sites were fixed in b684d82. Their file re-offending must
        fail like any other — there is no ledger left to grant it a free pass."""
        by_file = {"app/srs/db_revlog.py": Counter({SHAPE_ROLLOVER: 2})}
        exit_code, _ = evaluate(by_file)
        assert exit_code == 1

    def test_every_file_with_a_hit_is_reported(self):
        by_file = {
            "app/a.py": Counter({SHAPE_ROLLOVER: 1}),
            "app/b.py": Counter({SHAPE_ROLLOVER: 1}),
        }
        exit_code, messages = evaluate(by_file)
        assert exit_code == 1
        assert len(messages) == 2

    def test_combine_shape_also_fails(self):
        exit_code, _ = evaluate({"app/foo.py": Counter({SHAPE_COMBINE: 1})})
        assert exit_code == 1


# ── Drills ────────────────────────────────────────────────────────────────────

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
