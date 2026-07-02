"""Tests for build_learner_snapshot."""

import pytest

from app.models.srs_item import Direction, SRSState
from app.srs.database import SRSDatabase
from app.srs.planner_snapshot import build_learner_snapshot
from tests.conftest import seed_direction

# ── Helpers ──────────────────────────────────────────────────────────


def _seed(db: SRSDatabase, text: str, state: SRSState, lapses: int = 0, direction: Direction = Direction.RECOGNITION):
    """Seed one collocation with one direction in the given state."""
    seed_direction(
        db,
        text=text,
        translation=text,
        direction=direction,
        state=state,
        lapses=lapses,
    )


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def populated_db():
    """In-memory DB with ~10 collocations across states with fixed lapses."""
    db = SRSDatabase(":memory:")
    #  4 NEW items
    _seed(db, "nov_beseda", SRSState.NEW)
    _seed(db, "drug_nov", SRSState.NEW)
    _seed(db, "tretji_nov", SRSState.NEW)
    _seed(db, "cetrti_nov", SRSState.NEW)
    #  2 LEARNING items
    _seed(db, "ucim_se_a", SRSState.LEARNING, lapses=1)
    _seed(db, "ucim_se_b", SRSState.LEARNING, lapses=0)
    #  1 RELEARNING item
    _seed(db, "ponavljam", SRSState.RELEARNING, lapses=2)
    #  3 REVIEW items (known-like but state=REVIEW)
    _seed(db, "dober_dan", SRSState.REVIEW, lapses=0)
    _seed(db, "hvala_lepa", SRSState.REVIEW, lapses=0)
    _seed(db, "prosim", SRSState.REVIEW, lapses=0)
    #  2 KNOWN items
    _seed(db, "znam_to", SRSState.KNOWN, lapses=0)
    _seed(db, "vem_tudi", SRSState.KNOWN, lapses=0)
    return db


@pytest.fixture
def struggled_db():
    """DB with items having various lapse counts for struggling tests."""
    db = SRSDatabase(":memory:")
    _seed(db, "easy_word", SRSState.REVIEW, lapses=0)
    _seed(db, "medium_lapse", SRSState.REVIEW, lapses=5)
    _seed(db, "high_lapse", SRSState.REVIEW, lapses=10)
    _seed(db, "no_lapse_but_learning", SRSState.LEARNING, lapses=0)
    return db


# ── Tests ────────────────────────────────────────────────────────────


class TestBuildLearnerSnapshot:
    def test_golden_snapshot(self, populated_db):
        snapshot = build_learner_snapshot(populated_db)
        # count_new_available counts direction rows (2 per collocation);
        # 4 NEW collocations * 2 directions = 8, plus 8 other collocations
        # each have 1 NEW direction (the other direction not explicitly set)
        # = 8 + 8 = 16.
        expected = (
            "Learner vocabulary snapshot:\n"
            "- Tracked collocations: 12\n"
            "- Currently learning: 3\n"
            "- New (not yet introduced): 16\n"
            "Known (sample of 5/5): dober_dan, hvala_lepa, prosim, vem_tudi, znam_to\n"
            "Learning (sample of 3/3): ponavljam, ucim_se_a, ucim_se_b\n"
            "Struggling (most lapses): ponavljam (2 lapses), ucim_se_a (1 lapses)"
        )
        assert snapshot == expected

    def test_empty_db(self):
        db = SRSDatabase(":memory:")
        snapshot = build_learner_snapshot(db)
        assert snapshot == "(no tracked vocabulary yet — assume a beginner at the stated CEFR level)"

    def test_known_sample_truncation(self):
        """Known sample is capped at known_limit, denominator shows total."""
        db = SRSDatabase(":memory:")
        for i in range(10):
            _seed(db, f"word_{i:03d}", SRSState.REVIEW, lapses=0)

        snapshot = build_learner_snapshot(db, known_limit=3)
        assert "Known (sample of 3/10):" in snapshot
        # Verify only 3 are shown
        start = snapshot.index("Known (sample of 3/10):")
        line = snapshot[start:]
        # Count commas as separator between items
        items_part = line.split(":")[1].strip()
        assert items_part.count(", ") == 2  # 3 items = 2 commas

    def test_learning_sample_truncation(self):
        """Learning sample is capped at learning_limit."""
        db = SRSDatabase(":memory:")
        for i in range(10):
            _seed(db, f"learn_{i:03d}", SRSState.LEARNING, lapses=0)

        snapshot = build_learner_snapshot(db, learning_limit=3)
        assert "Learning (sample of 3/10):" in snapshot
        start = snapshot.index("Learning (sample of 3/10):")
        line = snapshot[start:]
        items_part = line.split(":")[1].strip()
        assert items_part.count(", ") == 2

    def test_struggling_limit(self):
        """Struggling sample capped at struggling_limit."""
        db = SRSDatabase(":memory:")
        # Seed 15 items with increasing lapses
        for i in range(1, 16):
            _seed(db, f"lapse_{i:03d}", SRSState.REVIEW, lapses=i)

        snapshot = build_learner_snapshot(db, struggling_limit=5)
        assert "Struggling (most lapses):" in snapshot
        # Count items shown (each has "lapses" in the line)
        struggling_line = snapshot.split("Struggling (most lapses):")[1].strip()
        # Each item ends with " lapses)" — count them
        assert struggling_line.count(" lapses)") == 5

    def test_struggling_excludes_lapses_zero(self):
        """Items with lapses=0 don't appear in struggling list."""
        db = SRSDatabase(":memory:")
        _seed(db, "lapse_5", SRSState.REVIEW, lapses=5)
        _seed(db, "lapse_3", SRSState.REVIEW, lapses=3)
        _seed(db, "no_lapse", SRSState.REVIEW, lapses=0)

        snapshot = build_learner_snapshot(db, struggling_limit=10)
        struggling_section = snapshot.split("Struggling (most lapses):")[1]
        assert "lapse_5" in struggling_section
        assert "lapse_3" in struggling_section
        assert "no_lapse" not in struggling_section

    def test_struggling_tiebreak_by_text(self):
        """Equal lapses are tie-broken by text ascending."""
        db = SRSDatabase(":memory:")
        _seed(db, "banana", SRSState.REVIEW, lapses=3)
        _seed(db, "apple", SRSState.REVIEW, lapses=3)

        snapshot = build_learner_snapshot(db, struggling_limit=10)
        # apple should come before banana
        apple_idx = snapshot.index("apple")
        banana_idx = snapshot.index("banana")
        assert apple_idx < banana_idx

    def test_missing_direction_does_not_crash(self):
        """Item without a recognition direction is handled gracefully."""
        db = SRSDatabase(":memory:")
        # Seed with PRODUCTION direction only (no RECOGNITION)
        _seed(db, "prod_only", SRSState.REVIEW, lapses=5, direction=Direction.PRODUCTION)

        snapshot = build_learner_snapshot(db)
        # Should not raise, and item should appear in struggling (lapses>0 via shim = 0)
        # The item's _rec falls through to PRODUCTION for non-cloze items... actually
        # wait. For non-cloze items, _rec returns RECOGNITION, which doesn't exist.
        # So the item should be treated as lapses=0.
        assert "Struggling: (none yet)" in snapshot or "prod_only" not in snapshot

    def test_no_known_items_shows_none_yet(self):
        db = SRSDatabase(":memory:")
        _seed(db, "just_learning", SRSState.LEARNING, lapses=0)
        snapshot = build_learner_snapshot(db)
        assert "Known: (none yet)" in snapshot
        assert "Struggling: (none yet)" in snapshot

    def test_no_known_but_has_learning(self):
        db = SRSDatabase(":memory:")
        _seed(db, "learning_item", SRSState.LEARNING, lapses=1)
        snapshot = build_learner_snapshot(db)
        assert "Known: (none yet)" in snapshot
        assert "Learning (sample of 1/1): learning_item" in snapshot

    def test_determinism(self):
        """Same data seeded in different order yields identical snapshot."""
        db1 = SRSDatabase(":memory:")
        _seed(db1, "c_item", SRSState.REVIEW)
        _seed(db1, "a_item", SRSState.REVIEW)
        _seed(db1, "b_item", SRSState.REVIEW)

        db2 = SRSDatabase(":memory:")
        _seed(db2, "a_item", SRSState.REVIEW)
        _seed(db2, "b_item", SRSState.REVIEW)
        _seed(db2, "c_item", SRSState.REVIEW)

        s1 = build_learner_snapshot(db1)
        s2 = build_learner_snapshot(db2)
        assert s1 == s2

    def test_determinism_with_ties_beyond_fetch_limit(self):
        """Tied lapse counts straddling the SQL fetch limit must not depend on insertion order.

        The struggling query fetches struggling_limit*2 rows ordered by lapses;
        without a content-based tie-breaker in the SQL, which tied rows survive
        the LIMIT depends on rowid (insertion) order and the Python re-sort
        cannot recover the dropped rows.
        """
        names = [f"tie_{i:03d}" for i in range(25)]

        def build(order):
            db = SRSDatabase(":memory:")
            for name in order:
                _seed(db, name, SRSState.REVIEW, lapses=1)
            return build_learner_snapshot(db, struggling_limit=10)

        forward = build(names)
        backward = build(list(reversed(names)))
        assert forward == backward
        # The (-lapses, text) sort makes the alphabetically-first ties the winners.
        struggling_line = forward.split("Struggling (most lapses):")[1]
        assert "tie_000" in struggling_line
        assert "tie_009" in struggling_line
        assert "tie_010" not in struggling_line

    def test_struggling_reuses_direction_lapses(self):
        """Struggling uses RECOGNITION direction lapses, not the flat shim (which is same)."""
        db = SRSDatabase(":memory:")
        seed_direction(
            db,
            text="lapse_word",
            translation="t",
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            lapses=7,
        )
        snapshot = build_learner_snapshot(db, struggling_limit=5)
        assert "lapse_word (7 lapses)" in snapshot
