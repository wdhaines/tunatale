"""Tests for select_review_collocations — the due-aware review sample.

Every test pins the clock by passing ``now``. There is no freezegun in this
repo (see tunatale-fgeq.1); the selector takes the clock as a parameter for
exactly this reason, and production is the only caller that lets it default.

⚠️ The fixtures are seeded RELATIVE to ``anki_today(NOW)``, never to a literal
date. ``anki_today`` resolves against the LOCAL 4am rollover, so a fixed UTC
``now`` lands on different Anki days in different zones — and CI deliberately
runs two hostile-timezone jobs. Every offset here is large enough that a
one-day shift cannot reclassify a word.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.common.guid import compute_guid
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today
from app.srs.database import SRSDatabase
from app.srs.review_selector import select_review_collocations

NOW = datetime(2026, 3, 15, 12, 0, tzinfo=UTC)
TODAY = anki_today(NOW)


def _seed(
    db: SRSDatabase,
    text: str,
    *,
    due_days: float,
    stability: float,
    reviewed_days_ago: float,
    state: SRSState = SRSState.REVIEW,
    disambig_key: str = "",
) -> None:
    """One collocation, one RECOGNITION direction, fully pinned.

    ``due_days`` is signed and relative to the Anki today (negative = overdue).
    ``reviewed_days_ago`` sets ``last_review`` off NOW; it is deliberately never
    midnight UTC, which is the marker ``compute_retrievability`` reads as
    "day-level, no lrt" and which would route the test through a different
    (col_crt-dependent) elapsed branch.
    """
    unit = SyntacticUnit(
        text=text,
        translation=f"gloss of {text}",
        word_count=1,
        difficulty=1,
        source="test",
        disambig_key=disambig_key,
    )
    db.add_collocation(unit, language_code="sl")
    guid = compute_guid(text, "sl", disambig_key)
    db.update_direction(
        guid,
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            state=state,
            due_at=datetime.combine(TODAY, datetime.min.time(), tzinfo=UTC) + timedelta(days=due_days, hours=4),
            stability=stability,
            last_review=NOW - timedelta(days=reviewed_days_ago),
            reps=3,
        ),
    )


@pytest.fixture
def db():
    with SRSDatabase(":memory:") as database:
        yield database


class TestSelectReviewCollocations:
    def test_empty_db_selects_nothing(self, db):
        assert select_review_collocations(db, now=NOW) == []

    def test_ranks_by_retrievability_not_by_due_date(self, db):
        """THE DISCRIMINATING TEST — plain due_at ordering fails it.

        `db.get_due_items` returns the pool already sorted by `due_at ASC`, so a
        selector that simply truncated that list would return `stabilno` first.
        It must not: `krhko` is only 2 days overdue but its stability is 2 days
        against 12 days elapsed, while `stabilno` is 40 days overdue on a
        stability of 600 and is barely decayed. Retrievability sees that
        difference and a due date cannot.
        """
        _seed(db, "stabilno", due_days=-40, stability=600.0, reviewed_days_ago=60)
        _seed(db, "krhko", due_days=-2, stability=2.0, reviewed_days_ago=12)

        assert select_review_collocations(db, now=NOW) == ["krhko", "stabilno"]

    def test_excludes_words_that_are_not_yet_due(self, db):
        _seed(db, "danes", due_days=-1, stability=5.0, reviewed_days_ago=6)
        _seed(db, "pozneje", due_days=30, stability=5.0, reviewed_days_ago=1)

        assert select_review_collocations(db, now=NOW) == ["danes"]

    @pytest.mark.parametrize("state", [SRSState.NEW, SRSState.KNOWN, SRSState.SUSPENDED, SRSState.BURIED])
    def test_excludes_states_the_review_queue_excludes(self, db, state):
        """The candidate pool IS the review queue's pool — not a second opinion.

        NEW matters most and for a non-obvious reason: a never-reviewed row has
        `last_review = NULL`, and `compute_retrievability` deliberately returns
        `desired_retention` (0.9) for that — a MID-range value that would sort
        such rows into the middle of the sample rather than out of it. They are
        excluded by STATE, not by the ranking happening to bury them.
        """
        _seed(db, "izbran", due_days=-3, stability=5.0, reviewed_days_ago=8)
        _seed(db, "izlocen", due_days=-90, stability=1.0, reviewed_days_ago=90, state=state)

        assert select_review_collocations(db, now=NOW) == ["izbran"]

    def test_respects_the_cap(self, db):
        for i in range(8):
            _seed(db, f"beseda{i}", due_days=-1 - i, stability=float(i + 1), reviewed_days_ago=20)

        selected = select_review_collocations(db, now=NOW, limit=3)
        assert len(selected) == 3
        # Lowest stability against the same elapsed time = lowest R = first.
        assert selected == ["beseda0", "beseda1", "beseda2"]

    def test_ties_break_on_text_so_the_sample_is_reproducible(self, db):
        """Determinism is the fgeq.1 contract, and identical FSRS state is the
        case that can violate it: without a content tie-break, which of two
        equally-decayed rows survives the cap falls to SQLite row order."""
        for text in ("zebra", "ananas", "mango"):
            _seed(db, text, due_days=-4, stability=9.0, reviewed_days_ago=15)

        assert select_review_collocations(db, now=NOW) == ["ananas", "mango", "zebra"]
        assert select_review_collocations(db, now=NOW) == select_review_collocations(db, now=NOW)

    def test_a_homograph_is_offered_once(self, db):
        """Two rows can legitimately share `text` — that is what `disambig_key`
        is for. Listing the same word twice in a prompt is noise, so the sample
        is deduplicated on the text the model actually sees."""
        _seed(db, "vel", due_days=-5, stability=3.0, reviewed_days_ago=10, disambig_key="adv")
        _seed(db, "vel", due_days=-5, stability=3.0, reviewed_days_ago=10, disambig_key="interj")
        _seed(db, "drugo", due_days=-5, stability=3.0, reviewed_days_ago=10)

        assert select_review_collocations(db, now=NOW) == ["drugo", "vel"]

    def test_horizon_widens_the_pool_into_the_future(self, db):
        """For tunatale-6r44: a learner who hears a story a week after it is
        generated should get the words that come due over that week, not only
        the ones due at generation time. The horizon defaults to 0 — today's
        behaviour — and this is the parameter that idea will fill in."""
        _seed(db, "zdaj", due_days=-1, stability=5.0, reviewed_days_ago=6)
        _seed(db, "cez_teden", due_days=4, stability=5.0, reviewed_days_ago=1)

        assert select_review_collocations(db, now=NOW) == ["zdaj"]
        # `zdaj` still leads: widening the pool adds candidates, it does not
        # reorder the ones already in it. `cez_teden` was reviewed yesterday
        # against the same stability, so it is the better-remembered of the two.
        assert select_review_collocations(db, now=NOW, horizon_days=7) == ["zdaj", "cez_teden"]
