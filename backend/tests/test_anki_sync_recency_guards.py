"""A TT grade and a change made in Anki meet at a sync: which one each rule lets survive.

Three defects found by the adversarial read of 2026-11-15 (briefs/findings-anki-risk-paths-2026-11.md),
each with the control that already held:

- Layer 96 (tunatale-ta88): an Anki recompute reaches a card TT graded last.
- Layer 97 (tunatale-p0rk): the push defers to a LATER Anki answer or suspension.
- Layer 98 (tunatale-47dg): a grade made while a sync runs waits for the next sync.

Later rows: Layer 99 (tunatale-ruog), a change made while the push runs; Layer 100 (tunatale-t6zu), a TT lapse
and an Anki write that is not an answer.

Real OfflineWriter and OfflineReader over a synthetic collection file; the grade goes through the
HTTP route. Nothing here opens a user's collection.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import sqlite3
import time
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
    # TT's interval is read from its own row: the grade's fuzz and the day boundary move it (21 or 22).
    with db._get_conn() as conn:
        [(tt_ivl,)] = conn.execute(
            "SELECT interval FROM tt_revlog WHERE id = ?", (int(tt_at.timestamp() * 1000),)
        ).fetchall()
    tt_row = {"id": int(tt_at.timestamp() * 1000), "ease": 3, "ivl": tt_ivl, "type": 3}
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


# ── Layer 99: a grade made DURING the push, on a direction already in its list ─────────────────────────────────
# sync_push reads list_dirty() once; the clean at the end of each direction used to be unconditional, so a grade
# landing between the read and the clean was wiped (the push wrote the OLD grade, the pull took Anki's due date).


def _sync(db: SRSDatabase, conn: sqlite3.Connection, coll: SyntheticCollection) -> AnkiSync:
    return AnkiSync(
        db=db,
        _reader=OfflineReader(conn, coll.deck_name, language_code="no"),
        _writer=OfflineWriter(conn),
        _anki_col_crt=coll.col_crt,
        language_code="no",
    )


def _now_from_another_thread(action) -> None:
    """Run *action* (sync or async) the way a request handler would, while the push is mid-flight."""

    def run():
        out = action()
        if asyncio.iscoroutine(out):
            asyncio.run(out)

    with concurrent.futures.ThreadPoolExecutor(1) as ex:
        ex.submit(run).result()


async def _tt_undo(cid: int) -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/srs/items/{cid}/direction/recognition/undo")
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("when", ["during_push", "control_after_push"])
async def test_a_lapse_made_during_the_push_is_kept(synthetic_collection: SyntheticCollection, when, caplog) -> None:
    """The review probe of 2026-11-20 (findings F1): Good before the sync, Again while the push runs."""
    db, cid = _fresh(synthetic_collection, due_in=0)
    await _tt_grade(cid)  # grade 1: the direction is dirty and in the push's list
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.row_factory = sqlite3.Row
        sync = _sync(db, conn, synthetic_collection)
        if when == "during_push":
            real = sync._writer.set_due_date

            def graded_meanwhile(*a, **k):
                out = real(*a, **k)
                _now_from_another_thread(lambda: _tt_grade(cid, "again"))
                return out

            sync._writer.set_due_date = graded_meanwhile
        with caplog.at_level(logging.WARNING):
            sync.sync_push()
        if when == "control_after_push":
            await _tt_grade(cid, "again")
        lapsed = _tt_dir(db, cid)
        assert lapsed.state == SRSState.RELEARNING
        assert ("GRADED_DURING_PUSH" in caplog.text) is (when == "during_push")
        assert _dirty(db, cid) == 1  # the push did not clear the lapse
        sync.sync_pull()
    assert _tt_dir(db, cid) == lapsed  # the pull left it alone (DIRTY_AT_PULL)

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        _full_sync(db, synthetic_collection)  # the next sync carries it

    card = _anki_card(synthetic_collection.path)
    assert (card["type"], card["queue"]) == (3, 1)
    assert [r["ease"] for r in card["revlog"]] == [3, 1]
    assert _tt_dir(db, cid).state == SRSState.RELEARNING and _dirty(db, cid) == 0
    assert "GRADED_DURING_PUSH" not in caplog.text


def _setup_main(db, coll, cid):
    return _tt_grade(cid)


async def _setup_forget(db, coll, cid):
    db.reset_collocation(cid, REC)  # TT's "Forget": NEW, reps 0, dirty


async def _setup_suspend(db, coll, cid):
    db.set_suspended(cid, True, REC)  # queue_only: a suspension is the whole change


async def _setup_deferred(db, coll, cid):
    # Layer 97: the phone reviewed the card after TT's grade, so the push defers to it.
    await _tt_grade(cid)
    _backdate_tt_grade(db, cid, timedelta(hours=12))
    now = datetime.now(UTC)
    today = anki_today_col_day(coll.col_crt, now)
    _phone_review(coll.path, at=now - timedelta(hours=1), stability=33.0, due_col_day=today + 30, ivl=30)


async def _setup_anki_ahead(db, coll, cid):
    # Layer 69: TT's lapse is older than the phone's later graduation, so the steps branch defers to Anki.
    await _tt_grade(cid, "again")
    _backdate_tt_grade(db, cid, timedelta(hours=12))
    now = datetime.now(UTC)
    today = anki_today_col_day(coll.col_crt, now)
    _phone_review(coll.path, at=now - timedelta(hours=1), stability=33.0, due_col_day=today + 30, ivl=30)


@pytest.mark.parametrize(
    ("setup", "meanwhile"),
    [
        (_setup_main, lambda db, cid: _tt_grade(cid, "again")),
        (_setup_forget, lambda db, cid: _tt_grade(cid, "good")),
        (_setup_suspend, lambda db, cid: db.set_suspended(cid, False, REC)),
        (_setup_deferred, lambda db, cid: _tt_grade(cid, "again")),
        (_setup_anki_ahead, lambda db, cid: _tt_grade(cid, "good")),
    ],
    ids=["main_path", "forget_branch", "queue_only", "layer97_deferral", "anki_ahead"],
)
async def test_every_clean_in_the_push_keeps_a_change_made_after_its_list_was_read(
    synthetic_collection: SyntheticCollection, setup, meanwhile, caplog
) -> None:
    db, cid = _fresh(synthetic_collection, due_in=0)
    await setup(db, synthetic_collection, cid)
    assert _dirty(db, cid) == 1
    real_list = db.list_dirty

    def listed_then_changed(*a, **k):
        listed = real_list(*a, **k)
        _now_from_another_thread(lambda: meanwhile(db, cid))
        return listed

    db.list_dirty = listed_then_changed
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.row_factory = sqlite3.Row
        with caplog.at_level(logging.WARNING):
            _sync(db, conn, synthetic_collection).sync_push()
    del db.list_dirty
    changed = _tt_dir(db, cid)
    assert "GRADED_DURING_PUSH" in caplog.text
    assert _dirty(db, cid) == 1 and changed.dirty_fsrs


async def test_the_recently_graded_clean_loop_keeps_a_grade_made_after_its_list_was_read(
    synthetic_collection: SyntheticCollection, caplog
) -> None:
    db, cid = _fresh(synthetic_collection, due_in=0)
    await _tt_grade(cid)
    with db._get_conn() as conn:  # Anki won the schedule earlier; only the revlog row is owed
        conn.execute("UPDATE collocation_directions SET dirty_fsrs = 0 WHERE collocation_id = ?", (cid,))
        conn.commit()
    real_list = db.list_recently_graded_clean

    def listed_then_graded(*a, **k):
        listed = real_list(*a, **k)
        _now_from_another_thread(lambda: _tt_grade(cid, "again"))
        return listed

    db.list_recently_graded_clean = listed_then_graded
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.row_factory = sqlite3.Row
        with caplog.at_level(logging.WARNING):
            _sync(db, conn, synthetic_collection).sync_push()
    assert "GRADED_DURING_PUSH" in caplog.text
    assert _dirty(db, cid) == 1 and _tt_dir(db, cid).state == SRSState.RELEARNING


async def test_an_undo_made_during_the_push_is_pushed_next_sync(
    synthetic_collection: SyntheticCollection, caplog
) -> None:
    """The undo restores the pre-grade row verbatim, dirty_fsrs=0 included. The push has already written the
    grade, so the restored row must stay dirty and the next push must write it back over the grade."""
    db, cid = _fresh(synthetic_collection, due_in=0)
    before = _tt_dir(db, cid)
    await _tt_grade(cid)
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.row_factory = sqlite3.Row
        sync = _sync(db, conn, synthetic_collection)
        real = sync._writer.set_due_date

        def undone_meanwhile(*a, **k):
            out = real(*a, **k)
            _now_from_another_thread(lambda: _tt_undo(cid))
            return out

        sync._writer.set_due_date = undone_meanwhile
        with caplog.at_level(logging.WARNING):
            sync.sync_push()
        assert "GRADED_DURING_PUSH" in caplog.text
        assert _dirty(db, cid) == 1
        sync.sync_pull()
    restored = _tt_dir(db, cid)
    assert (restored.reps, restored.last_review, restored.due_at) == (before.reps, before.last_review, before.due_at)

    _full_sync(db, synthetic_collection)

    card = _anki_card(synthetic_collection.path)
    final = _tt_dir(db, cid)
    today = anki_today_col_day(synthetic_collection.col_crt, datetime.now(UTC))
    assert card["due"] == today  # the restored due date (due today), not the undone grade's
    # The restored memory state wins in both apps. The undo deleted its tt_revlog row before the push sent it,
    # and the push derives no row for a direction that changed after its read (review R7), so Anki's revlog
    # holds only the manual (type 4) row that moved the schedule back, and reps never counted the undone grade.
    assert (card["data"]["s"], card["data"]["d"], card["data"]["lrt"]) == (
        10.0,
        5.0,
        int(before.last_review.timestamp()),
    )
    assert [(r["ease"], r["type"]) for r in card["revlog"]] == [(0, 4)]
    assert (final.stability, final.difficulty, final.last_review) == (10.0, 5.0, before.last_review)
    assert final.anki_due == today and final.reps == card["reps"] == REPS
    assert _dirty(db, cid) == 0


def test_a_clean_matches_a_row_stored_in_an_older_last_review_format() -> None:
    """The compare-and-swap compares parsed values: a date-only or naive last_review (rows from before
    isoformat-with-zone) must still clean, or the row would stay dirty and warn on every sync."""
    db = SRSDatabase(":memory:")
    unit = SyntacticUnit(text="hus", translation="house", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="no")
    item = db.get_collocation("hus")
    cid = db.get_collocation_id_by_guid(item.guid)
    for stored in ("2026-11-15", "2026-11-15T09:30:00"):
        with db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET last_review = ?, reps = 3, dirty_fsrs = 1 WHERE collocation_id = ?",
                (stored, cid),
            )
            conn.commit()
        read = _tt_dir(db, cid)
        assert db.mark_direction_clean(item.guid, REC, read=read) is True
        assert _dirty(db, cid) == 0


def test_a_clean_without_a_read_is_unconditional() -> None:
    db = SRSDatabase(":memory:")
    unit = SyntacticUnit(text="hus", translation="house", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="no")
    item = db.get_collocation("hus")
    cid = db.get_collocation_id_by_guid(item.guid)
    with db._get_conn() as conn:
        conn.execute("UPDATE collocation_directions SET dirty_fsrs = 1 WHERE collocation_id = ?", (cid,))
        conn.commit()
    assert db.mark_direction_clean(item.guid, REC) is True
    assert _dirty(db, cid) == 0
    assert db.mark_direction_clean("no-such-guid", REC) is True


@pytest.mark.parametrize("stored", ["2026-07-01", "2026-07-01T10:00:00"], ids=["date_only", "naive"])
async def test_a_suspension_over_a_legacy_last_review_cleans_in_one_sync(
    synthetic_collection: SyntheticCollection, stored: str, caplog
) -> None:
    """The reviewer's control (ruog comment): a suspension does not rewrite last_review, so a row still holding
    an old-format value must clean on the first push and log nothing, or it would be re-pushed every sync."""
    db, cid = _fresh(synthetic_collection, due_in=0)
    with db._get_conn() as conn:
        conn.execute("UPDATE collocation_directions SET last_review = ? WHERE collocation_id = ?", (stored, cid))
        conn.commit()
    db.set_suspended(cid, True, REC)
    with caplog.at_level(logging.WARNING):
        _full_sync(db, synthetic_collection)
    assert _dirty(db, cid) == 0
    assert "GRADED_DURING_PUSH" not in caplog.text
    assert _anki_card(synthetic_collection.path)["queue"] == -1


def test_a_clean_of_a_direction_deleted_after_the_read_has_nothing_to_keep() -> None:
    db = SRSDatabase(":memory:")
    unit = SyntacticUnit(text="hus", translation="house", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="no")
    item = db.get_collocation("hus")
    cid = db.get_collocation_id_by_guid(item.guid)
    read = _tt_dir(db, cid)
    with db._get_conn() as conn:
        conn.execute(
            "DELETE FROM collocation_directions WHERE collocation_id = ? AND direction = 'recognition'", (cid,)
        )
        conn.commit()
    assert db.mark_direction_clean(item.guid, REC, read=read) is True


# ── Layer 99, review R1-R4: the change left dirty must also survive the NEXT sync ──────────────────────────────
# cards.mod is whole seconds. Push 1 writes the card after the grade; when a second passes in between, sync 2 read
# TT's own write as "Anki changed after the grade" (Layer 69's recency guard) and dropped the change. The tests
# cross the second inside the hook so the timing is deterministic, not a 0.3% flake.


def _to_next_second() -> None:
    time.sleep(1.0 - (time.time() % 1.0) + 0.05)


def _hook_after_list_dirty(db, action, *, cross_second: bool):
    real_list = db.list_dirty

    def listed_then_changed(*a, **k):
        listed = real_list(*a, **k)
        _now_from_another_thread(action)
        if cross_second:
            _to_next_second()
        return listed

    db.list_dirty = listed_then_changed


def _hook_in_set_due_date(sync, action, *, cross_second: bool):
    real = sync._writer.set_due_date

    def changed_meanwhile(*a, **k):
        out = real(*a, **k)
        _now_from_another_thread(action)
        if cross_second:
            _to_next_second()
        return out

    sync._writer.set_due_date = changed_meanwhile


@pytest.mark.parametrize("hook", ["in_set_due_date", "after_list_dirty"])
@pytest.mark.parametrize("cross_second", [False, True], ids=["same_second", "next_second"])
async def test_a_lapse_made_during_the_push_survives_the_next_sync(
    synthetic_collection: SyntheticCollection, hook, cross_second
) -> None:
    db, cid = _fresh(synthetic_collection, due_in=0)
    await _tt_grade(cid)
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.row_factory = sqlite3.Row
        sync = _sync(db, conn, synthetic_collection)
        action = lambda: _tt_grade(cid, "again")  # noqa: E731
        if hook == "in_set_due_date":
            _hook_in_set_due_date(sync, action, cross_second=cross_second)
        else:
            _hook_after_list_dirty(db, action, cross_second=cross_second)
        sync.sync_push()
        db.__dict__.pop("list_dirty", None)
        sync.sync_pull()
    _full_sync(db, synthetic_collection)
    card = _anki_card(synthetic_collection.path)
    assert (card["type"], card["queue"]) == (3, 1)
    assert _tt_dir(db, cid).state == SRSState.RELEARNING and _dirty(db, cid) == 0


async def test_control_a_phone_graduation_after_a_lapse_left_dirty_still_wins(
    synthetic_collection: SyntheticCollection,
) -> None:
    """Recording TT's own write must not blind the push to a real later change: the phone graduates the card
    between the two syncs (a different cards.mod, later than the lapse), and Layer 69 still defers to it."""
    db, cid = _fresh(synthetic_collection, due_in=0)
    await _tt_grade(cid)
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.row_factory = sqlite3.Row
        sync = _sync(db, conn, synthetic_collection)
        _hook_after_list_dirty(db, lambda: _tt_grade(cid, "again"), cross_second=True)
        sync.sync_push()
        db.__dict__.pop("list_dirty", None)
        sync.sync_pull()
    assert _dirty(db, cid) == 1
    _to_next_second()
    now = datetime.now(UTC)
    today = anki_today_col_day(synthetic_collection.col_crt, now)
    _phone_review(
        synthetic_collection.path, at=now + timedelta(seconds=2), stability=33.0, due_col_day=today + 30, ivl=30
    )
    _full_sync(db, synthetic_collection)
    card = _anki_card(synthetic_collection.path)
    assert (card["type"], card["queue"], card["due"]) == (2, 2, today + 30) and card["data"]["s"] == 33.0
    assert _tt_dir(db, cid).state == SRSState.REVIEW


@pytest.mark.parametrize("cross_second", [False, True], ids=["same_second", "next_second"])
@pytest.mark.parametrize("start", ["review", "relearning"])
async def test_an_undo_made_during_the_push_survives_the_next_sync(
    synthetic_collection: SyntheticCollection, start, cross_second
) -> None:
    """R3: when the undone Good graduated a RELEARNING card, the restored row is in steps and takes the steps
    branch's anki_ahead check, which read TT's own push-1 write as Anki being ahead."""
    db, cid = _fresh(synthetic_collection, due_in=0)
    if start == "relearning":
        await _tt_grade(cid, "again")
        _full_sync(db, synthetic_collection)  # both RELEARNING, clean
    before = _tt_dir(db, cid)
    await _tt_grade(cid)  # Good
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.row_factory = sqlite3.Row
        sync = _sync(db, conn, synthetic_collection)
        _hook_in_set_due_date(sync, lambda: _tt_undo(cid), cross_second=cross_second)
        sync.sync_push()
        sync.sync_pull()
    _full_sync(db, synthetic_collection)
    card = _anki_card(synthetic_collection.path)
    assert _tt_dir(db, cid).state == before.state
    assert (card["type"], card["queue"]) == ((3, 1) if start == "relearning" else (2, 2))


@pytest.mark.parametrize("undo", [False, True], ids=["control_plain_grade", "undo_during_push"])
async def test_a_phone_review_after_an_undo_during_the_push_still_wins(
    synthetic_collection: SyntheticCollection, undo
) -> None:
    """R2: the undo's forced write must not override a phone review made between the two syncs (Layer 97).
    Control: a plain TT grade the phone reviews over, Layer 97's own case."""
    db, cid = _fresh(synthetic_collection, due_in=0)
    await _tt_grade(cid)
    if undo:
        with sqlite3.connect(synthetic_collection.path) as conn:
            conn.row_factory = sqlite3.Row
            sync = _sync(db, conn, synthetic_collection)
            _hook_in_set_due_date(sync, lambda: _tt_undo(cid), cross_second=False)
            sync.sync_push()
            sync.sync_pull()
        assert _dirty(db, cid) == 1
    _to_next_second()
    now = datetime.now(UTC)
    today = anki_today_col_day(synthetic_collection.col_crt, now)
    _phone_review(
        synthetic_collection.path, at=now + timedelta(seconds=2), stability=33.0, due_col_day=today + 30, ivl=30
    )
    _full_sync(db, synthetic_collection)
    card = _anki_card(synthetic_collection.path)
    assert card["due"] == today + 30 and card["data"]["s"] == 33.0
    assert _tt_dir(db, cid).stability == 33.0


async def _undo_status(cid: int) -> int:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/srs/items/{cid}/direction/recognition/undo")
    return resp.status_code


@pytest.mark.parametrize("when", ["control_after_sync", "during_push"])
async def test_undo_refuses_a_grade_the_push_already_sent(
    synthetic_collection: SyntheticCollection, when, caplog
) -> None:
    """R4: undo reads dirty_fsrs as "not yet in Anki". A grade made during the push has its revlog row sent in
    that push yet stays dirty (Layer 99), so the push drops the undo snapshot when it sends that row."""
    db, cid = _fresh(synthetic_collection, due_in=0)
    await _tt_grade(cid)
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.row_factory = sqlite3.Row
        sync = _sync(db, conn, synthetic_collection)
        if when == "during_push":
            _hook_in_set_due_date(sync, lambda: _tt_grade(cid, "again"), cross_second=False)
        sync.sync_push()
        sync.sync_pull()
    if when == "control_after_sync":
        await _tt_grade(cid, "again")  # never pushed: undo is allowed
    status = await _undo_status(cid)
    assert status == (200 if when == "control_after_sync" else 409)
    caplog.clear()
    with caplog.at_level(logging.INFO):
        _full_sync(db, synthetic_collection)
    with db._get_conn() as c:
        tt_rev = [
            r[0] for r in c.execute("SELECT button_chosen FROM tt_revlog WHERE collocation_id = ? ORDER BY id", (cid,))
        ]
    card = _anki_card(synthetic_collection.path)
    assert [r["ease"] for r in card["revlog"]] == tt_rev
    assert "PUSH_DEFERRED_TO_LATER_ANKI_REVIEW" not in caplog.text


def _anki_touch(path, *, at: datetime, optimize: bool) -> None:
    """Anki writes the card without an answer: a housekeeping mod bump, or an Optimize (new s/d, a type-5 row)."""
    with sqlite3.connect(path) as conn:
        if optimize:
            [(raw,)] = conn.execute("SELECT data FROM cards WHERE id = ?", (CARD_ID,)).fetchall()
            data = json.loads(raw)
            data["s"] = data["s"] * 1.1
            conn.execute("UPDATE cards SET data = ? WHERE id = ?", (json.dumps(data), CARD_ID))
            conn.execute(
                "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) VALUES (?, ?, 9, 0, 0, 0, 0, 0, 5)",
                (int(at.timestamp() * 1000), CARD_ID),
            )
        conn.execute("UPDATE cards SET mod = ?, usn = 9 WHERE id = ?", (int(at.timestamp()), CARD_ID))
        conn.commit()


@pytest.mark.parametrize("touch", ["control_none", "mod_bump", "optimize"])
async def test_an_undo_during_the_push_survives_an_anki_touch_without_an_answer(
    synthetic_collection: SyntheticCollection, touch, caplog
) -> None:
    """Review R7. The undo deleted its tt_revlog row before the push sent it, and the push used to derive a row
    for the undone grade from the stale state: a review nobody kept. Once anything moved Anki's mod without an
    answer, the undo's force read that invented row as "Anki answered later" and deferred, losing the undo.
    The push no longer derives a row for a direction that changed after it read the list."""
    db, cid = _fresh(synthetic_collection, due_in=0)
    before = _tt_dir(db, cid)
    await _tt_grade(cid)
    with sqlite3.connect(synthetic_collection.path) as conn:
        conn.row_factory = sqlite3.Row
        sync = _sync(db, conn, synthetic_collection)
        _hook_in_set_due_date(sync, lambda: _tt_undo(cid), cross_second=False)
        sync.sync_push()
        sync.sync_pull()
    assert _anki_card(synthetic_collection.path)["revlog"] == []  # no review invented for the undone grade
    _to_next_second()
    if touch != "control_none":
        _anki_touch(
            synthetic_collection.path, at=datetime.now(UTC) + timedelta(seconds=2), optimize=touch == "optimize"
        )
    with caplog.at_level(logging.INFO):
        _full_sync(db, synthetic_collection)
    card = _anki_card(synthetic_collection.path)
    final = _tt_dir(db, cid)
    today = anki_today_col_day(synthetic_collection.col_crt, datetime.now(UTC))
    assert card["due"] == today and final.last_review == before.last_review
    assert "PUSH_DEFERRED_TO_LATER_ANKI_REVIEW" not in caplog.text


# ── Layer 100: a TT lapse and an Anki write that is not an answer ──────────────────────────────────────────────


@pytest.mark.parametrize("touch", ["control_none", "mod_bump", "optimize", "phone_graduation"])
async def test_a_tt_lapse_survives_an_anki_write_that_is_not_an_answer(
    synthetic_collection: SyntheticCollection, touch
) -> None:
    """Layer 100 (tunatale-t6zu). A synced card is lapsed in TT; before TT syncs, Anki writes the card without
    answering it (a flag, a deck move, an Optimize, housekeeping), so cards.mod moves past the lapse. Layer 69's
    steps branch read queue 2 as "graduated, Anki is ahead" and its recency guard read only cards.mod, so the push
    deferred and the lapse was lost in both apps. Only an answer newer than TT's grade is "ahead". A real phone
    graduation after the lapse still wins."""
    db, cid = _fresh(synthetic_collection, due_in=0)
    _full_sync(db, synthetic_collection)  # a synced start: anki_card_mod is the pulled mod
    assert _tt_dir(db, cid).anki_card_mod is not None
    await _tt_grade(cid, "again")
    assert _tt_dir(db, cid).state == SRSState.RELEARNING
    _to_next_second()
    now = datetime.now(UTC)
    today = anki_today_col_day(synthetic_collection.col_crt, now)
    if touch == "phone_graduation":
        _phone_review(
            synthetic_collection.path, at=now + timedelta(seconds=2), stability=33.0, due_col_day=today + 30, ivl=30
        )
    elif touch != "control_none":
        _anki_touch(synthetic_collection.path, at=now + timedelta(seconds=2), optimize=touch == "optimize")

    _full_sync(db, synthetic_collection)

    card = _anki_card(synthetic_collection.path)
    final = _tt_dir(db, cid)
    if touch == "phone_graduation":
        assert (card["type"], card["queue"], card["due"]) == (2, 2, today + 30) and card["data"]["s"] == 33.0
        assert final.state == SRSState.REVIEW
    else:
        assert (card["type"], card["queue"]) == (3, 1)
        assert final.state == SRSState.RELEARNING
    assert _dirty(db, cid) == 0
