"""A starter card begins life as a REVIEW card with a seeded memory state (u8nz.7).

``SRSDatabase.seed_review_state`` turns a freshly minted, never-studied card into
a review card carrying stability the learner earned in a related language. The
contract, and the oracle for "what Anki shows for these cards":

* TT: state REVIEW, the seeded stability/difficulty/due, ``reps`` still 0, no
  ``introduced_at`` (nothing was studied, and the new-card budget must not be
  charged), dirty + force-next so the push writes it. ``last_review`` is the
  review the schedule implies, ``due - ivl``: the reference point FSRS measures
  elapsed time from, not a claim that a review happened.
* Anki after the push: a review card (queue 2, type 2) due on the seeded day,
  ``ivl`` = round(stability), ``cards.data`` carrying s/d and ``lrt`` = that
  implied review, ``reps`` 0, and **no revlog row** — a fabricated review would
  be trained on by the FSRS optimizer as if the learner had answered it.
* TT after the same sync's pull: unchanged. No sibling is buried and no
  recompute divergence is reported.

The first version of this design left ``last_review`` NULL and let Anki date the
card by ``due - ivl``. This test caught it: the pull reads no ``last_review`` for
a card with ``reps == 0`` unless ``lrt`` is present, so TT kept NULL and served
the card at retrievability 0.9 while Anki computed it from the interval.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.sync import AnkiSync, OfflineReader, OfflineWriter
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase
from app.srs.fsrs import compute_retrievability


def _db_with_word(text: str = "banka", language_code: str = "no") -> tuple[SRSDatabase, int, str]:
    db = SRSDatabase(":memory:")
    db.add_collocation(
        SyntacticUnit(text=text, translation="bank", word_count=1, difficulty=1, source="test"), language_code
    )
    guid = db.get_collocation(text).guid
    return db, db.get_collocation_id_by_guid(guid), guid


def _link(db: SRSDatabase, guid: str, nid: int = 9001, rec: int = 90010, prod: int = 90011) -> None:
    db.set_anki_ids(guid, nid, {Direction.RECOGNITION: rec, Direction.PRODUCTION: prod})


class TestSeedReviewState:
    def test_a_linked_new_card_becomes_a_seeded_review_card(self):
        db, row_id, guid = _db_with_word()
        _link(db, guid)
        now = datetime.now(UTC)

        assert db.seed_review_state(
            row_id, Direction.RECOGNITION, stability=20.0, difficulty=6.1, due_in_days=3, now=now
        )

        rec = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert rec.state == SRSState.REVIEW
        assert rec.stability == 20.0
        assert rec.difficulty == 6.1
        assert rec.due_at == due_at_rollover_utc(anki_today(now) + timedelta(days=3))
        assert rec.reps == 0
        # One 20-day interval before the due date: 17 days ago, at this time of day.
        assert rec.last_review == now - timedelta(days=17)
        assert rec.introduced_at is None
        assert rec.dirty_fsrs is True
        assert rec.fsrs_force_next is True

    def test_only_the_named_direction_is_seeded(self):
        db, row_id, guid = _db_with_word()
        _link(db, guid)
        db.seed_review_state(row_id, Direction.RECOGNITION, stability=20.0, difficulty=6.1, due_in_days=3)
        prod = db.get_collocation_by_guid(guid).directions[Direction.PRODUCTION]
        assert prod.state == SRSState.NEW
        assert prod.dirty_fsrs is False

    @pytest.mark.parametrize(("offset", "stability"), [(20, 20.0), (25, 20.0), (1, 1.2), (0, 20.0)])
    def test_a_due_date_outside_the_interval_is_a_caller_bug(self, offset, stability):
        """Due in 20 days on a 20-day interval dates the implied review today, and
        Anki would count the card as answered today; due today lands in a queue
        that is already built. 1.2 rounds to a 1-day interval, which fits neither."""
        db, row_id, guid = _db_with_word()
        _link(db, guid)
        with pytest.raises(ValueError, match="must be at least 1 and less than"):
            db.seed_review_state(row_id, Direction.RECOGNITION, stability=stability, difficulty=6.1, due_in_days=offset)
        assert db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION].state == SRSState.NEW

    def test_an_unminted_card_is_refused(self):
        """The push skips a direction with no Anki card, so a seed there would sit
        dirty and diverged until some later sync — refuse it instead."""
        db, row_id, guid = _db_with_word()
        assert not db.seed_review_state(row_id, Direction.RECOGNITION, stability=20.0, difficulty=6.1, due_in_days=3)
        assert db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION].state == SRSState.NEW

    @pytest.mark.parametrize("already", ["studied", "learning"])
    def test_a_card_with_any_history_is_never_overwritten(self, already):
        db, row_id, guid = _db_with_word()
        _link(db, guid)
        item = db.get_collocation_by_guid(guid)
        ds = item.directions[Direction.RECOGNITION]
        if already == "studied":
            ds.reps = 1
        else:
            ds.state = SRSState.LEARNING
        db.update_direction(guid, Direction.RECOGNITION, ds)

        assert not db.seed_review_state(row_id, Direction.RECOGNITION, stability=20.0, difficulty=6.1, due_in_days=3)
        after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert after.stability != 20.0
        assert after.fsrs_force_next is False


class TestASeededCardStaysAReviewCard:
    """``reps == 0`` used to be read as "has no schedule". Anki restores a buried
    or suspended card from its ``type``, and a seeded card's type is review, so a
    TT-side restore that turned it NEW would serve it as a new card until the
    next pull, and grading it there would throw the head start away."""

    def _seeded(self) -> tuple[SRSDatabase, int, str]:
        db, row_id, guid = _db_with_word()
        _link(db, guid)
        assert db.seed_review_state(row_id, Direction.RECOGNITION, stability=20.0, difficulty=6.1, due_in_days=3)
        return db, row_id, guid

    def test_the_daily_unbury_sweep_restores_review(self):
        db, row_id, guid = self._seeded()
        ds = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        ds.state, ds.bury_kind = SRSState.BURIED, "sched"
        db.update_direction(guid, Direction.RECOGNITION, ds)

        assert db.unbury_if_needed(anki_today()) == 1
        assert db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION].state == SRSState.REVIEW

    def test_unsuspending_restores_review(self):
        db, row_id, guid = self._seeded()
        db.set_suspended(row_id, True, direction=Direction.RECOGNITION)
        db.set_suspended(row_id, False, direction=Direction.RECOGNITION)
        assert db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION].state == SRSState.REVIEW

    def test_a_card_with_no_schedule_still_restores_new(self):
        db, row_id, guid = self._seeded()
        db.set_suspended(row_id, True, direction=Direction.PRODUCTION)
        db.set_suspended(row_id, False, direction=Direction.PRODUCTION)
        assert db.get_collocation_by_guid(guid).directions[Direction.PRODUCTION].state == SRSState.NEW


def test_a_seeded_card_survives_push_and_pull_as_a_review_card_with_no_revlog(fake_anki_db):
    """The oracle for the whole design, against a real collection file."""
    conn = sqlite3.connect(str(fake_anki_db))
    conn.row_factory = sqlite3.Row  # production uses safe_open, which sets this
    try:
        nid = conn.execute("SELECT id FROM notes WHERE flds LIKE 'banka%'").fetchone()[0]
        rec_cid, prod_cid = [r[0] for r in conn.execute("SELECT id FROM cards WHERE nid = ? ORDER BY ord", (nid,))]
        col_crt = conn.execute("SELECT crt FROM col").fetchone()[0]
        # The fixture's cards are all reviewed; make banka's the freshly minted,
        # never-studied pair that sync_create_new leaves behind.
        conn.execute(
            "UPDATE cards SET type = 0, queue = 0, due = 7, ivl = 0, factor = 0, reps = 0, lapses = 0,"
            " left = 0, data = '{}' WHERE nid = ?",
            (nid,),
        )
        conn.commit()
        revlog_before = conn.execute("SELECT COUNT(*) FROM revlog").fetchone()[0]
        prod_before = dict(conn.execute("SELECT queue, type, due FROM cards WHERE id = ?", (prod_cid,)).fetchone())

        db, row_id, guid = _db_with_word(language_code="sl")
        _link(db, guid, nid, rec_cid, prod_cid)
        offset, stability, difficulty = 3, 20.0, 6.1
        now = datetime.now(UTC)
        assert db.seed_review_state(
            row_id, Direction.RECOGNITION, stability=stability, difficulty=difficulty, due_in_days=offset, now=now
        )
        seeded_review = now - timedelta(days=round(stability) - offset)

        sync = AnkiSync(
            db=db, _reader=OfflineReader(conn, "0. Slovene", language_code="sl"), _writer=OfflineWriter(conn)
        )
        sync.sync_push()
        pull = sync.sync_pull()

        # Anki: a review card on the seeded day, memory state but no review history.
        card = conn.execute("SELECT * FROM cards WHERE id = ?", (rec_cid,)).fetchone()
        days_since_crt = (anki_today() - date.fromtimestamp(col_crt)).days
        assert (card["queue"], card["type"]) == (2, 2)
        assert card["due"] == days_since_crt + offset
        assert card["ivl"] == round(stability)
        assert card["reps"] == 0
        assert card["usn"] == -1
        data = json.loads(card["data"])
        assert (data["s"], data["d"]) == (stability, difficulty)
        assert data["lrt"] == int(seeded_review.timestamp())
        assert card["due"] - card["ivl"] == days_since_crt + offset - round(stability)
        assert conn.execute("SELECT COUNT(*) FROM revlog").fetchone()[0] == revlog_before
        assert conn.execute("SELECT COUNT(*) FROM revlog WHERE cid = ?", (rec_cid,)).fetchone()[0] == 0
        # The sibling was not buried: nothing was answered today.
        assert dict(conn.execute("SELECT queue, type, due FROM cards WHERE id = ?", (prod_cid,)).fetchone()) == (
            prod_before
        )

        # TT: the pull kept the seed and dated it the way Anki does.
        rec = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
        assert rec.state == SRSState.REVIEW
        assert (rec.stability, rec.difficulty) == (stability, difficulty)
        assert rec.reps == 0
        assert rec.introduced_at is None
        assert rec.dirty_fsrs is False
        assert rec.fsrs_force_next is False
        assert int(rec.last_review.timestamp()) == int(seeded_review.timestamp())
        assert pull.recompute_divergences == []

        # And it reads as partly-forgotten knowledge, not as a card due at R=0.9 today.
        r = compute_retrievability(rec, anki_today(), now=datetime.now(UTC), col_crt=col_crt)
        assert 0.9 < r < 1.0
    finally:
        conn.close()
