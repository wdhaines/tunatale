"""An early Again pushed to Anki: a lapse, a rep, and nothing on the home deck's review count.

Oracle cases D5 (entered through ``/feedback``) and G11 (the same, entered
through the Grade page's ``POST /content/{id}/grade``) of
``.beads-tasks/briefs/design-grade-page-2026-11.md`` § P1 measured. A grade on a
not-due REVIEW card logs revlog kind 3, Anki's review-ahead (see
``test_feedback_review_ahead.py``). Anki's own early path — a rescheduling
filtered deck — counts an Again there as a lapse (``lapses`` +1, ``reps`` +1) and
leaves the home deck's ``revToday`` alone, so TT's push must produce the same.

The push counts a lapse from ``review_kind == 1 AND Again``, so a kind-3 Again
reaches ``cards.lapses`` only through the ``MAX(lapses + n, ds_lapses)`` floor in
``OfflineWriter.write_revlog``. This pins that floor for the early case.

Not covered: Anki's stats screens (they read the revlog row's type as written)
and the filtered-deck move itself (TT never builds one; the card is graded in place).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.protobuf_wire import anki_today_col_day
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from app.srs.database import SRSDatabase
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests.anki_oracle.harness_fixtures import run_oracle
from tests.anki_oracle.synthetic_collection import SyntheticCollection

NOTE_ID, CARD_ID = 5001, 50010
REPS, LAPSES = 5, 0


def _seed(db: SRSDatabase, coll: SyntheticCollection, *, due_today: bool) -> int:
    """The same REVIEW card on both sides: s=10, d=5, last reviewed 5 days ago; due today or in 2 days."""
    now = datetime.now(UTC)
    last_review = now - timedelta(days=5)
    today = anki_today_col_day(coll.col_crt, now)
    coll.add_note(id=NOTE_ID, guid="g-early", fields=["hus", "house"])
    coll.add_card(
        id=CARD_ID,
        note_id=NOTE_ID,
        type=2,
        queue=2,
        due=today if due_today else today + 2,
        ivl=7,
        reps=REPS,
        lapses=LAPSES,
        stability=10.0,
        difficulty=5.0,
        last_review_secs=int(last_review.timestamp()),
        desired_retention=0.9,
    )
    coll.save()

    unit = SyntacticUnit(text="hus", translation="house", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="no")
    item = db.get_collocation("hus")
    assert item is not None
    db.set_anki_ids(item.guid, NOTE_ID, {Direction.RECOGNITION: CARD_ID})
    item = db.get_collocation("hus")
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.last_review = last_review
    rec.stability, rec.difficulty = 10.0, 5.0
    rec.reps, rec.lapses = REPS, LAPSES
    rec.due_at = due_at_rollover_utc(anki_today()) if due_today else now + timedelta(days=2)
    db.update_collocation(item)
    cid = db.get_collocation_id_by_guid(item.guid)
    assert cid is not None
    return cid


ENTRIES = ["feedback", "grade"]


def _install_lesson() -> None:
    """A one-word lesson saying ``hus``, for the Grade page's route (entry ``grade``)."""
    from app.models.lesson import Lesson, Phrase, Section, SectionType
    from app.storage.store import ContentStore

    store = ContentStore(":memory:")
    phrase = Phrase(text="hus", voice_id="female-1", language_code="no", role="female-1")
    store.save_lesson(
        "lesson-1",
        "curriculum-1",
        1,
        Lesson(title="Dag 1", language_code="no", sections=[Section(SectionType.NATURAL_SPEED, [phrase])]),
    )
    app.state.content_store = store


async def _again_then_push(
    synthetic_collection: SyntheticCollection, *, due_today: bool, entry: str = "feedback"
) -> tuple[int, dict, dict]:
    from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
    from tests._helpers.anki_sync_push import FakeReader

    db = SRSDatabase(":memory:")
    app.state.srs_db = db
    try:
        cid = _seed(db, synthetic_collection, due_today=due_today)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            if entry == "grade":
                _install_lesson()
                resp = await client.post(
                    "/api/srs/content/lesson-1/grade",
                    json={"card_grades": [{"item_id": cid, "direction": "recognition", "rating": "again"}]},
                )
            else:
                resp = await client.post(
                    f"/api/srs/items/{cid}/direction/recognition/feedback", json={"rating": "again"}
                )
        assert resp.status_code == 200, resp.text
        with db._get_conn() as conn:
            [kind] = [r[0] for r in conn.execute("SELECT review_kind FROM tt_revlog WHERE collocation_id = ?", (cid,))]

        with sqlite3.connect(synthetic_collection.path) as conn:
            conn.row_factory = sqlite3.Row
            AnkiSync(
                db=db, _reader=FakeReader(), _writer=OfflineWriter(conn), _anki_col_crt=synthetic_collection.col_crt
            ).sync_push()
    finally:
        db.close()

    raw = run_oracle(
        synthetic_collection.path,
        [{"op": "get_card_row", "card_id": CARD_ID}, {"op": "deck_today", "deck_id": 1}],
    ).raw()
    card = raw[next(k for k in raw if k.startswith("get_card_row"))]
    deck = raw[next(k for k in raw if k.startswith("deck_today"))]
    return kind, card, deck


def _rev_today(deck: dict) -> int:
    """Anki charges a counter only when its stamp is today; otherwise it reads as 0."""
    day, count = deck["rev_today"]
    return count if day == deck["today"] else 0


@pytest.mark.oracle
@pytest.mark.parametrize("entry", ENTRIES)
async def test_early_again_is_a_lapse_and_leaves_rev_today(synthetic_collection: SyntheticCollection, entry) -> None:
    kind, card, deck = await _again_then_push(synthetic_collection, due_today=False, entry=entry)

    assert kind == 3
    assert (card["reps"], card["lapses"]) == (REPS + 1, LAPSES + 1), card
    assert _rev_today(deck) == 0, f"an early grade charged the home deck's review count: {deck}"


@pytest.mark.oracle
@pytest.mark.parametrize("entry", ENTRIES)
async def test_control_due_again_is_a_lapse_and_charges_rev_today(
    synthetic_collection: SyntheticCollection, entry
) -> None:
    kind, card, deck = await _again_then_push(synthetic_collection, due_today=True, entry=entry)

    assert kind == 1
    assert (card["reps"], card["lapses"]) == (REPS + 1, LAPSES + 1), card
    assert _rev_today(deck) == 1, deck
