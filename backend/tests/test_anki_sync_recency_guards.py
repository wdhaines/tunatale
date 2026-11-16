"""A TT grade and a change made in Anki meet at a sync: which one each rule lets survive.

Three defects found by the adversarial read of 2026-11-15 (briefs/findings-anki-risk-paths-2026-11.md),
each with the control that already held:

- Layer 96 (tunatale-ta88): an Anki recompute reaches a card TT graded last.
- Layer 97 (tunatale-p0rk): the push defers to a LATER Anki answer or suspension.
- Layer 98 (tunatale-47dg): a grade made while a sync runs waits for the next sync.

Real OfflineWriter and OfflineReader over a synthetic collection file; the grade goes through the
HTTP route. Nothing here opens a user's collection.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.sync import AnkiSync, OfflineReader, OfflineWriter
from app.srs.anki_mirror.protobuf_wire import anki_today_col_day
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests.anki_oracle.synthetic_collection import SyntheticCollection

NOTE_ID, CARD_ID = 5001, 50010
REPS = 5
REC = Direction.RECOGNITION


def _seed(db: SRSDatabase, coll: SyntheticCollection, *, due_in: int, state: str = "review") -> int:
    """The same REVIEW card on both sides: s=10, d=5, last reviewed 5 days ago, due in *due_in* days."""
    now = datetime.now(UTC)
    last_review = now - timedelta(days=5)
    today = anki_today_col_day(coll.col_crt, now)
    coll.enable_fsrs()
    coll.add_note(id=NOTE_ID, guid="g-early", fields=["hus", "house"])
    coll.add_card(
        id=CARD_ID,
        note_id=NOTE_ID,
        type=2,
        queue=2,
        due=today + due_in,
        ivl=5 + due_in,
        reps=REPS,
        lapses=0,
        stability=10.0,
        difficulty=5.0,
        last_review_secs=int(last_review.timestamp()),
        desired_retention=0.9,
    )
    coll.save()

    unit = SyntacticUnit(text="hus", translation="house", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="no")
    item = db.get_collocation("hus")
    db.set_anki_ids(item.guid, NOTE_ID, {REC: CARD_ID})
    item = db.get_collocation("hus")
    rec = item.directions[REC]
    rec.state = SRSState.REVIEW
    rec.last_review = last_review
    rec.stability, rec.difficulty = 10.0, 5.0
    rec.reps, rec.lapses = REPS, 0
    rec.due_at = due_at_rollover_utc(anki_today() + timedelta(days=due_in))
    db.update_collocation(item)
    cid = db.get_collocation_id_by_guid(item.guid)
    # a clean, synced start: nothing dirty
    with db._get_conn() as conn:
        conn.execute(
            "UPDATE collocation_directions SET dirty_fsrs = 0, last_rating = NULL WHERE collocation_id = ?", (cid,)
        )
        conn.commit()
    return cid


def _phone_review(path, *, at: datetime, stability: float, due_col_day: int, ivl: int) -> None:
    """A review made in Anki on another device and already synced into this collection: card row + revlog row."""
    with sqlite3.connect(path) as conn:
        data = json.dumps({"s": stability, "d": 4.9, "lrt": int(at.timestamp()), "dr": 0.9})
        conn.execute(
            "UPDATE cards SET type = 2, queue = 2, due = ?, ivl = ?, reps = reps + 1, mod = ?, usn = 5, data = ? WHERE id = ?",
            (due_col_day, ivl, int(at.timestamp()), data, CARD_ID),
        )
        conn.execute(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) VALUES (?, ?, 5, 3, ?, 7, 533, 4000, 1)",
            (int(at.timestamp() * 1000), CARD_ID, ivl),
        )
        conn.commit()


def _anki_card(path) -> dict:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM cards WHERE id = ?", (CARD_ID,)).fetchone())
        row["data"] = json.loads(row["data"] or "{}")
        row["revlog"] = [
            dict(r)
            for r in conn.execute("SELECT id, ease, ivl, type FROM revlog WHERE cid = ? ORDER BY id", (CARD_ID,))
        ]
        return row


def _full_sync(db: SRSDatabase, coll: SyntheticCollection):
    with sqlite3.connect(coll.path) as conn:
        conn.row_factory = sqlite3.Row
        writer = OfflineWriter(conn)
        reader = OfflineReader(conn, coll.deck_name, language_code="no")
        sync = AnkiSync(db=db, _reader=reader, _writer=writer, _anki_col_crt=coll.col_crt, language_code="no")
        push = sync.sync_push()
        pull = sync.sync_pull()
        return push, pull


async def _tt_grade(cid: int, rating: str = "good") -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/srs/items/{cid}/direction/recognition/feedback", json={"rating": rating})
    assert resp.status_code == 200, resp.text


def _tt_dir(db, cid):
    _, item, _ = db.get_collocation_by_id(cid)
    return item.directions[REC]


def _backdate_tt_grade(db: SRSDatabase, cid: int, by: timedelta) -> datetime:
    """Move TT's just-made grade *by* into the past (its revlog row and the direction's last_review)."""
    d = _tt_dir(db, cid)
    new_lr = d.last_review - by
    with db._get_conn() as conn:
        [(old_id,)] = conn.execute("SELECT id FROM tt_revlog WHERE collocation_id = ?", (cid,)).fetchall()
        conn.execute("UPDATE tt_revlog SET id = ? WHERE id = ?", (int(new_lr.timestamp() * 1000), old_id))
        conn.execute(
            "UPDATE collocation_directions SET last_review = ? WHERE collocation_id = ? AND direction = 'recognition'",
            (new_lr.isoformat(), cid),
        )
        conn.commit()
    return new_lr


def _dirty(db: SRSDatabase, cid: int) -> int:
    with db._get_conn() as conn:
        [(dirty,)] = conn.execute(
            "SELECT dirty_fsrs FROM collocation_directions WHERE collocation_id = ? AND direction = 'recognition'",
            (cid,),
        ).fetchall()
    return dirty


def _fresh(synthetic_collection: SyntheticCollection, *, due_in: int) -> tuple[SRSDatabase, int]:
    db = SRSDatabase(":memory:")
    app.state.srs_db = db
    return db, _seed(db, synthetic_collection, due_in=due_in)


# ── Layer 96: an Anki recompute (Optimize, a weights change) after TT's grade has round-tripped ───────────────


def _anki_recompute(path, *, stability: float, difficulty: float) -> None:
    """What Optimize does to a card: new s and d, the same lrt, a newer mod."""
    with sqlite3.connect(path) as conn:
        [(raw, mod)] = conn.execute("SELECT data, mod FROM cards WHERE id = ?", (CARD_ID,)).fetchall()
        data = json.loads(raw)
        data["s"], data["d"] = stability, difficulty
        conn.execute("UPDATE cards SET data = ?, mod = ?, usn = 9 WHERE id = ?", (json.dumps(data), mod + 60, CARD_ID))
        conn.commit()


@pytest.mark.parametrize("last_grader", ["tt", "anki"], ids=["last_graded_in_tt", "control_last_graded_in_anki"])
async def test_an_anki_recompute_reaches_tt(synthetic_collection: SyntheticCollection, last_grader: str) -> None:
    db, cid = _fresh(synthetic_collection, due_in=0)
    now = datetime.now(UTC)
    today = anki_today_col_day(synthetic_collection.col_crt, now)
    if last_grader == "tt":
        await _tt_grade(cid)
        graded_at = _tt_dir(db, cid).last_review
        assert graded_at.microsecond, "the defect needs a sub-second grade time; TT stamps one"
    else:
        _phone_review(
            synthetic_collection.path, at=now - timedelta(minutes=5), stability=20.0, due_col_day=today + 20, ivl=20
        )
    _, first_pull = _full_sync(db, synthetic_collection)
    synced = _tt_dir(db, cid)
    assert abs(synced.stability - _anki_card(synthetic_collection.path)["data"]["s"]) < 1e-3
    if last_grader == "tt":
        assert first_pull.recompute_divergences == []  # a round trip is not a recompute
        assert synced.last_review == graded_at  # TT keeps its own grade time, microseconds and all

    _anki_recompute(synthetic_collection.path, stability=99.0, difficulty=3.3)
    _, pull = _full_sync(db, synthetic_collection)

    tt = _tt_dir(db, cid)
    assert (tt.stability, tt.difficulty) == (99.0, 3.3)
    assert len(pull.recompute_divergences) == 1  # and the recompute is logged, once
    _, again = _full_sync(db, synthetic_collection)
    assert again.recompute_divergences == []


async def test_a_tt_grade_not_yet_in_anki_still_keeps_its_memory_state(
    synthetic_collection: SyntheticCollection,
) -> None:
    """Layer 70's guard is still there: a pull that meets a card whose lrt is OLDER than TT's grade keeps TT's s and d."""
    db, cid = _fresh(synthetic_collection, due_in=0)
    await _tt_grade(cid)
    graded = _tt_dir(db, cid)
    with db._get_conn() as conn:  # as after a push that wrote the schedule but whose cards.data is still pre-grade
        conn.execute("UPDATE collocation_directions SET dirty_fsrs = 0 WHERE collocation_id = ?", (cid,))
        conn.commit()
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.row_factory = sqlite3.Row
        reader = OfflineReader(conn, synthetic_collection.deck_name, language_code="no")
        sync = AnkiSync(
            db=db,
            _reader=reader,
            _writer=OfflineWriter(conn),
            _anki_col_crt=synthetic_collection.col_crt,
            language_code="no",
        )
        pull = sync.sync_pull()
    tt = _tt_dir(db, cid)
    assert (tt.stability, tt.last_review) == (graded.stability, graded.last_review)
    assert pull.recompute_divergences == []


# ── Layer 97: the same REVIEW card graded in TT (ahead of due) and LATER on the phone; then one TT sync ─────────


@pytest.mark.parametrize("tt_first", [True, False], ids=["tt_earlier_phone_later", "control_phone_earlier_tt_later"])
async def test_the_later_review_survives(synthetic_collection: SyntheticCollection, tt_first: bool) -> None:
    db, cid = _fresh(synthetic_collection, due_in=1)
    now = datetime.now(UTC)
    today = anki_today_col_day(synthetic_collection.col_crt, now)
    await _tt_grade(cid)  # ahead of due: kind 3
    if tt_first:
        tt_at = _backdate_tt_grade(db, cid, timedelta(hours=12))  # last night
        phone_at = now - timedelta(hours=1)  # this morning, the card was still due in Anki
    else:
        tt_at = _tt_dir(db, cid).last_review
        phone_at = now - timedelta(hours=12)
    _phone_review(synthetic_collection.path, at=phone_at, stability=33.0, due_col_day=today + 30, ivl=30)
    tt_graded = _tt_dir(db, cid)

    _full_sync(db, synthetic_collection)

    card = _anki_card(synthetic_collection.path)
    tt = _tt_dir(db, cid)
    # Both answers are in the revlog either way, TT's at its own time and as the kind it was (3, ahead of due).
    tt_row = {"id": int(tt_at.timestamp() * 1000), "ease": 3, "ivl": 21, "type": 3}
    phone_row = {"id": int(phone_at.timestamp() * 1000), "ease": 3, "ivl": 30, "type": 1}
    assert card["revlog"] == sorted([tt_row, phone_row], key=lambda r: r["id"])
    assert card["reps"] == REPS + 2 == tt.reps
    assert _dirty(db, cid) == 0
    if tt_first:
        assert (card["data"]["s"], card["due"], card["data"]["lrt"]) == (33.0, today + 30, int(phone_at.timestamp()))
        assert tt.stability == 33.0 and tt.last_review == phone_at.replace(microsecond=0)
    else:
        assert card["data"]["lrt"] == int(tt_at.timestamp())
        assert abs(card["data"]["s"] - tt_graded.stability) < 1e-3 and tt.stability == tt_graded.stability


async def test_a_later_lapse_on_the_phone_survives(synthetic_collection: SyntheticCollection) -> None:
    db, cid = _fresh(synthetic_collection, due_in=1)
    now = datetime.now(UTC)
    await _tt_grade(cid)  # Good, ahead of due
    _backdate_tt_grade(db, cid, timedelta(hours=12))
    phone_at = now - timedelta(minutes=30)
    with sqlite3.connect(synthetic_collection.path) as conn:  # the phone: Again -> relearning, a 10-minute step
        data = json.dumps({"s": 2.1, "d": 6.8, "lrt": int(phone_at.timestamp()), "dr": 0.9})
        conn.execute(
            "UPDATE cards SET type = 3, queue = 1, due = ?, ivl = 1, left = 1001, reps = reps + 1, lapses = lapses + 1, "
            "mod = ?, usn = 5, data = ? WHERE id = ?",
            (int(phone_at.timestamp()) + 600, int(phone_at.timestamp()), data, CARD_ID),
        )
        conn.execute(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) VALUES (?, ?, 5, 1, -600, 7, 633, 4000, 1)",
            (int(phone_at.timestamp() * 1000), CARD_ID),
        )
        conn.commit()

    _full_sync(db, synthetic_collection)

    card = _anki_card(synthetic_collection.path)
    tt = _tt_dir(db, cid)
    assert (card["queue"], card["type"], card["left"], card["lapses"], card["data"]["s"]) == (1, 3, 1001, 1, 2.1)
    assert (tt.state, tt.lapses, tt.stability) == (SRSState.RELEARNING, 1, 2.1)
    assert [r["type"] for r in card["revlog"]] == [3, 1]


async def test_a_reschedule_row_is_not_a_later_review(synthetic_collection: SyntheticCollection) -> None:
    """Control. An Optimize that reschedules writes a type-5 revlog row and a new mod; TT's grade still goes up."""
    db, cid = _fresh(synthetic_collection, due_in=1)
    now = datetime.now(UTC)
    await _tt_grade(cid)
    _backdate_tt_grade(db, cid, timedelta(hours=12))
    graded = _tt_dir(db, cid)
    at = now - timedelta(hours=1)
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.execute("UPDATE cards SET mod = ?, usn = 5 WHERE id = ?", (int(at.timestamp()), CARD_ID))
        conn.execute(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) VALUES (?, ?, 5, 0, 9, 7, 533, 0, 5)",
            (int(at.timestamp() * 1000), CARD_ID),
        )
        conn.commit()

    _full_sync(db, synthetic_collection)

    card = _anki_card(synthetic_collection.path)
    assert card["data"]["lrt"] == int(graded.last_review.timestamp())
    assert abs(card["data"]["s"] - graded.stability) < 1e-3 and card["reps"] == REPS + 1


async def test_control_a_tt_lapse_defers_to_a_later_phone_review(synthetic_collection: SyntheticCollection) -> None:
    """The (re)learning branch of the push already had its recency rule (Layer 69); it is unchanged."""
    db, cid = _fresh(synthetic_collection, due_in=0)
    now = datetime.now(UTC)
    today = anki_today_col_day(synthetic_collection.col_crt, now)
    await _tt_grade(cid, "again")
    assert _tt_dir(db, cid).state == SRSState.RELEARNING
    _backdate_tt_grade(db, cid, timedelta(hours=12))
    _phone_review(
        synthetic_collection.path, at=now - timedelta(hours=1), stability=33.0, due_col_day=today + 30, ivl=30
    )

    _full_sync(db, synthetic_collection)

    card = _anki_card(synthetic_collection.path)
    assert card["data"]["s"] == 33.0 and card["queue"] == 2


# ── Layer 97: a suspension made on the phone, and a TT grade the phone did not know about ──────────────────────


def _phone_suspend(path, at: datetime) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE cards SET queue = -1, mod = ?, usn = 5 WHERE id = ?", (int(at.timestamp()), CARD_ID))
        conn.commit()


@pytest.mark.parametrize("rating", ["good", "again"], ids=["review_grade", "lapse_into_steps"])
async def test_a_suspension_made_after_the_tt_grade_survives_the_push(
    synthetic_collection: SyntheticCollection, rating: str
) -> None:
    """The owner's decision of 2026-11-15: the grade reaches the revlog and the schedule; the card stays suspended."""
    db, cid = _fresh(synthetic_collection, due_in=1 if rating == "good" else 0)
    now = datetime.now(UTC)
    today = anki_today_col_day(synthetic_collection.col_crt, now)
    await _tt_grade(cid, rating)
    _backdate_tt_grade(db, cid, timedelta(hours=12))
    graded = _tt_dir(db, cid)
    _phone_suspend(synthetic_collection.path, now - timedelta(hours=1))

    _full_sync(db, synthetic_collection)

    card = _anki_card(synthetic_collection.path)
    assert card["queue"] == -1
    assert len(card["revlog"]) == 1 and card["reps"] == REPS + 1
    assert card["data"]["lrt"] == int(graded.last_review.timestamp())
    if rating == "good":
        assert (card["type"], card["due"]) == (2, today + (graded.due_at.date() - anki_today()).days)
        assert card["due"] > today + 1  # no longer the seeded due date
    else:
        assert (card["type"], card["left"]) == (3, graded.left)
    assert _tt_dir(db, cid).state == SRSState.SUSPENDED


async def test_a_card_suspended_before_an_unknowing_tt_grade_is_unsuspended(
    synthetic_collection: SyntheticCollection,
) -> None:
    """NOT decided by the owner (tunatale-p0rk): this pins what the push has always done, so a change is seen."""
    db, cid = _fresh(synthetic_collection, due_in=1)
    _phone_suspend(synthetic_collection.path, datetime.now(UTC) - timedelta(hours=12))
    await _tt_grade(cid)

    _full_sync(db, synthetic_collection)

    assert _anki_card(synthetic_collection.path)["queue"] == 2
    assert _tt_dir(db, cid).state == SRSState.REVIEW


# ── Layer 98: a grade made in TT while a sync is running (after the push read its list, before the pull) ───────


@pytest.mark.parametrize(
    "during_sync", [True, False], ids=["graded_between_push_and_pull", "control_graded_before_the_sync"]
)
async def test_a_grade_made_during_a_sync_is_kept(
    synthetic_collection: SyntheticCollection, during_sync: bool, caplog
) -> None:
    db, cid = _fresh(synthetic_collection, due_in=0)
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.row_factory = sqlite3.Row
        reader = OfflineReader(conn, synthetic_collection.deck_name, language_code="no")
        sync = AnkiSync(
            db=db,
            _reader=reader,
            _writer=OfflineWriter(conn),
            _anki_col_crt=synthetic_collection.col_crt,
            language_code="no",
        )
        if not during_sync:
            await _tt_grade(cid)
        sync.sync_push()
        if during_sync:
            await _tt_grade(cid)
        graded = _tt_dir(db, cid)
        with caplog.at_level(logging.WARNING):
            pull = sync.sync_pull()
    after = _tt_dir(db, cid)
    assert ("DIRTY_AT_PULL" in caplog.text) is during_sync
    # TT's row holds the grade's own schedule, not Anki's pre-grade one.
    assert (after.reps, after.stability, after.last_review) == (
        REPS + 1,
        graded.stability,
        graded.last_review,
    )
    assert _dirty(db, cid) == (1 if during_sync else 0)
    if during_sync:
        assert pull.directions_updated == 0 and after == graded  # the row was not touched
        assert _anki_card(synthetic_collection.path)["reps"] == REPS  # Anki has not had it yet

    _full_sync(db, synthetic_collection)  # the next sync carries it

    card = _anki_card(synthetic_collection.path)
    final = _tt_dir(db, cid)
    assert card["reps"] == REPS + 1 == final.reps and len(card["revlog"]) == 1
    assert abs(card["data"]["s"] - graded.stability) < 1e-3 and card["data"]["lrt"] == int(
        graded.last_review.timestamp()
    )
    today = anki_today_col_day(synthetic_collection.col_crt, datetime.now(UTC))
    assert card["due"] == today + (graded.due_at.date() - anki_today()).days
    assert final.due_at.date() >= graded.due_at.date() and _dirty(db, cid) == 0
