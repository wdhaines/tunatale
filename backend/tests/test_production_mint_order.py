"""The production mint's queue order (tunatale-qf6.11 + tunatale-byw).

Two beads meet in one ``ORDER BY``, and the second is why this is a latency test
as much as a pedagogy one.

**Pedagogy (qf6.11).** ``list_words_awaiting_production`` ordered by
``r.last_review DESC`` — "freshest first". For someone working a
frequency-ordered deck front-to-back that is close to the inverse of what is
wanted: the words you touched most recently are the ones furthest into the deck,
i.e. the rarest. The mint drained from the rare end.

**Latency (byw).** ``last_review`` is a *moving* target, and that is what made
syncs slow. ``prestage_production_images`` stages images for the head of this
queue off the critical path; ``promote_production_cards`` mints from that head.
When a review session lands in between, every ``last_review`` it touches rewrites
the head — so the mint asks for words the pre-stage never saw, finds no image and
fetches inline: a measured 10.0s median per image, up to 10 per sync. Measured on
the real deck 2026-08-23, the shape these tests encode:

    top 10 of the mint queue spanned   2.8 min of last_review
    top 20                             8.2 min
    top 50                          2850   min

The head *was* one review session. Ordering on something immutable is therefore
not a nicety — it is what lets the pre-stage buffer hit, which is the latency fix.

THE ORDER IS ``anki_note_id`` ASCENDING, and it is not a new invention: it is the
rule ``reposition_production_cards.py`` already uses to lay the minted band out
("``notes.id`` ascending, which for the imported deck is deck (frequency) order").
Re-measured here 2026-08-23 against ``tunatale_no.db`` (n=2987 imported,
zipf-scored), reproducing 30dd0ec's figures:

    Spearman(anki_note_id,    zipf) = -0.706   <- deck order
    Spearman(collocations.id, zipf) = +0.514   <- BACKWARDS, not a substitute
    Spearman(introduced_at,   zipf) = -0.683   <- a weaker proxy for the same thing

⚠️ Two traps this file exists to keep future readers out of:

1. ``c.id`` is NOT deck order. The bead proposed dropping the ``last_review``
   term and letting the existing ``c.id ASC`` tiebreaker carry deck order; at
   +0.514 that mints the RAREST words first — the very complaint qf6.11 was filed
   about. ``corpus_frequency`` is no substitute either: 100% populated, every
   value 0.
2. Measure ``anki_note_id`` with a RANK correlation. It holds epoch millis
   spanning ~9e10, so Pearson on the raw values reads -0.019 and looks like no
   signal at all. That near-zero is an artefact of the statistic, not a property
   of the deck.

Every fixture below seeds insertion order and recency order to DISAGREE with note
order; a fixture where they agree cannot fail and would be worthless here.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase

LANG = "no"

#: Deck order = ascending Anki note id. Listed front-of-deck first, which IS the
#: expected mint order. Note ids are epoch-millis, as Anki assigns them.
DECK = [
    ("være", "be", 1696398299715),
    ("og", "and", 1696398299942),
    ("i", "in", 1696398300388),
    ("hus", "house", 1701000000000),
    ("forfatter", "author", 1740000000000),
    ("tannlege", "dentist", 1770000000000),
    ("bokhylle", "bookshelf", 1787228884083),
]


def _add_word(db: SRSDatabase, word: str, english: str, *, note_id: int, last_review: datetime) -> int:
    """Seed a graduated recognition card with no production direction."""
    unit = SyntacticUnit(
        text=word, translation=english, word_count=1, difficulty=1, source="anki", frequency=0, disambig_key="noun"
    )
    directions = {
        Direction.RECOGNITION: DirectionState(
            direction=Direction.RECOGNITION,
            due_at=due_at_rollover_utc(anki_today()),
            state=SRSState.REVIEW,
            reps=9,
            anki_card_id=note_id // 1000,
            last_review=last_review,
        )
    }
    return db.upsert_by_guid(unit, LANG, directions, anki_note_id=note_id)


@pytest.fixture
def db() -> SRSDatabase:
    return SRSDatabase(":memory:")


@pytest.fixture
def seeded(db) -> SRSDatabase:
    """Insert BACK-of-deck first, and make the back of the deck freshest.

    Both wrong signals are pointed the wrong way at once:
      * ``c.id ASC``         -> bokhylle .. være  (insertion order, reversed)
      * ``last_review DESC`` -> bokhylle .. være  (recency, reversed)
      * ``anki_note_id ASC`` -> være .. bokhylle  (the answer)
    """
    base = datetime.fromisoformat("2026-08-01T12:00:00+00:00")
    for i, (word, english, note_id) in enumerate(reversed(DECK)):
        _add_word(db, word, english, note_id=note_id, last_review=base - timedelta(minutes=i))
    return db


class TestMintOrder:
    def test_head_is_deck_order(self, seeded) -> None:
        """The mint drains the front of the deck, not the rare end."""
        got = [c.item.syntactic_unit.text for c in seeded.list_words_awaiting_production(limit=len(DECK))]

        assert got == [w for w, _e, _n in DECK]

    def test_a_short_limit_takes_the_front_of_the_deck(self, seeded) -> None:
        """PRODUCTIONS_PER_SYNC is small, so the limit is what the learner actually meets."""
        got = [c.item.syntactic_unit.text for c in seeded.list_words_awaiting_production(limit=3)]

        assert got == ["være", "og", "i"]

    def test_a_review_does_not_reorder_the_head(self, seeded) -> None:
        """The byw oracle: a review session must NOT move the mint head.

        This is the property the pre-stage buffer depends on. Under the old
        ``last_review DESC`` order, reviewing a back-of-deck word promoted it to
        position 0, so the image pre-staged for the real head went unused — which
        is how a 0.08s mint became a 63s one.
        """
        head_before = [c.collocation_id for c in seeded.list_words_awaiting_production(limit=3)]
        off_head = seeded.get_collocation("tannlege")
        assert off_head is not None
        assert "tannlege" not in [c.item.syntactic_unit.text for c in seeded.list_words_awaiting_production(limit=3)]

        # The learner reviews it — far in the future, so under a recency order it
        # would jump straight to position 0 and displace a pre-staged word.
        state = off_head.directions[Direction.RECOGNITION]
        state.last_review = datetime.fromisoformat("2026-09-01T12:00:00+00:00")
        seeded.update_direction(off_head.guid, Direction.RECOGNITION, state)

        head_after = [c.collocation_id for c in seeded.list_words_awaiting_production(limit=3)]

        assert head_after == head_before, "a review churned the mint head — the pre-stage staged `head_before`"

    def test_order_is_not_insertion_order(self, seeded) -> None:
        """Guard the falsified shortcut: `c.id ASC` is measurably reverse-frequency."""
        got = [c.collocation_id for c in seeded.list_words_awaiting_production(limit=len(DECK))]

        assert got != sorted(got), "ordering by c.id would mint the back of the deck first"

    def test_matches_the_repositioner(self, seeded) -> None:
        """The mint and `reposition_production_cards` must agree on deck order.

        That module lays the reserved production band out by `notes.id ASC`; if the
        mint picked words in a different order, mint order and serve order would
        diverge — the FIFO property 30dd0ec's band exists to preserve.
        """
        got = [c.item.anki_note_id for c in seeded.list_words_awaiting_production(limit=len(DECK))]

        assert got == sorted(got), "mint order must be notes.id ascending, as the repositioner assumes"
