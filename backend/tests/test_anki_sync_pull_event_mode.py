"""sync_pull's recompute detector (Stage 3b, post flag-decommission).

The ``event_sync_pull`` flag (legacy / compare / new) was removed once the merge
collapsed: there is now a single sync_pull path — the collapsed take-Anki merge
plus an unconditional forward-step replay that runs *only* as a recompute
DETECTOR. On a divergence it records a ``recompute_divergence`` (Optimize /
FSRS-param / retention / restore event) but keeps Anki's value — stored state is
take-Anki-verbatim regardless. These tests pin the detector's behaviour:

- within tolerance → no divergence, Anki's value written verbatim;
- outside tolerance → divergence recorded + greppable WARNING, Anki's value kept;
- the Layer-70 skips (tt-memory-newer, fsrs_unknown, pre-link revlog rows);
- suspend/bury and dry-run paths.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.models.srs_item import Direction, DirectionState, SRSState
from app.plugins.anki_sync.sync import AnkiSync, PullReport
from app.srs.database import SRSDatabase
from app.srs.queue_stats import resolve_fsrs_params
from tests._helpers.anki_sync_pull import FakeReader, FakeWriter, _add_banka, _make_tt_db
from tests.conftest import make_card_record, make_note_record

_CARD_ID = 90010


class RevlogReader(FakeReader):
    """FakeReader that also serves Anki revlog rows for one card."""

    def __init__(self, records, revlog_rows: list[dict] | None = None):
        super().__init__(records)
        self._revlog_rows = revlog_rows or []

    def get_revlog_for_card(self, card_id: int, after_ms: int = 0) -> list:
        return list(self._revlog_rows) if card_id == _CARD_ID else []


def _run_pull(db, records, revlog_rows, *, dry_run: bool = False) -> None:
    """Run a sync_pull with the given note records + Anki revlog rows."""
    AnkiSync(db=db, _reader=RevlogReader(records, revlog_rows), _writer=FakeWriter()).sync_pull(dry_run=dry_run)


def _seed_review_direction(db: SRSDatabase, guid: str) -> DirectionState:
    """Seed banka RECOGNITION in REVIEW state 10 days back; return the stored state."""
    last_review = datetime.now(UTC) - timedelta(days=10)
    ds = DirectionState(
        direction=Direction.RECOGNITION,
        due_at=last_review + timedelta(days=10),
        stability=5.0,
        difficulty=4.5,
        reps=3,
        lapses=0,
        state=SRSState.REVIEW,
        last_review=last_review,
        last_review_time_ms=int(last_review.timestamp() * 1000),
        anki_card_id=_CARD_ID,
        last_synced_at=last_review.isoformat(),
    )
    db.update_direction(guid, Direction.RECOGNITION, ds)
    return ds


def _good_revlog_row() -> dict:
    """A single fresh Anki GOOD review row (type=1)."""
    return {
        "id": int(datetime.now(UTC).timestamp() * 1000),
        "ease": 3,
        "ivl": 12,
        "lastIvl": 10,
        "factor": 2500,
        "time": 4200,
        "type": 1,
    }


def _run_pull_report(db, records, revlog_rows, *, dry_run: bool = False) -> PullReport:
    """Run a sync_pull and return the PullReport."""
    return AnkiSync(db=db, _reader=RevlogReader(records, revlog_rows), _writer=FakeWriter()).sync_pull(dry_run=dry_run)


def _compute_replay(db, coll_id, stored):
    """Compute the forward-step replay result for a single GOOD grade from stored state."""

    params = resolve_fsrs_params(db)[0]
    return db.rebuild_from_revlog(
        coll_id,
        Direction.RECOGNITION,
        params=params,
        col_crt=None,
        anki_card_id=_CARD_ID,
        starting_state=stored,
        since_id=None,
    )


def test_new_mode_writes_anki_verbatim_within_tolerance():
    """new mode, replay within tolerance of Anki -> writes ANKI's value verbatim.

    Take-Anki-verbatim (fork resolved 2026-06-02): Anki is the source of truth.
    Discriminating: card_rec is *within* 0.01 tolerance but *not equal* to the
    replay, so the assertions can only pass if the code writes Anki's value
    (card.stability) and NOT the replay-derived value (expected.stability). When
    within tolerance, no divergence is recorded.
    """
    db = _make_tt_db()
    guid = _add_banka(db)
    stored = _seed_review_direction(db, guid)
    coll_id = db.get_collocation_id_by_guid(guid)

    revlog = [_good_revlog_row()]
    records = [make_note_record(anki_guid=guid, cards=[])]

    # Pre-ingest the revlog row so we can compute the replay result to discriminate against.
    sync = AnkiSync(db=db, _reader=RevlogReader(records, revlog), _writer=FakeWriter())
    sync._ingest_anki_revlog_for_card(_CARD_ID, coll_id, Direction.RECOGNITION)
    expected = _compute_replay(db, coll_id, stored)

    # Remove the pre-ingested row so the real pull can re-ingest it.
    db._conn.execute("DELETE FROM tt_revlog WHERE collocation_id = ?", (coll_id,))
    db._conn.commit()

    # card_rec within 0.01 tolerance but NOT equal to the replay output.
    card = make_card_record(
        anki_card_id=_CARD_ID,
        ord=0,
        reps=4,
        stability=expected.stability + 0.005,
        difficulty=expected.difficulty + 0.005,
    )
    records2 = [make_note_record(anki_guid=guid, cards=[card])]
    report = _run_pull_report(db, records2, revlog)

    after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
    assert len(report.recompute_divergences) == 0  # within tolerance -> no divergence record
    assert after.stability == card.stability  # took Anki's value verbatim
    assert after.difficulty == card.difficulty
    assert after.stability != expected.stability  # NOT the replay-derived value
    assert after.reps == 4
    assert after.state == SRSState.REVIEW


def test_new_mode_divergence_takes_anki(caplog):
    """new mode, replay diverges -> takes Anki's value + records recompute_divergence."""
    db = _make_tt_db()
    guid = _add_banka(db)
    stored = _seed_review_direction(db, guid)
    coll_id = db.get_collocation_id_by_guid(guid)

    revlog = [_good_revlog_row()]
    card = make_card_record(anki_card_id=_CARD_ID, ord=0, reps=4, stability=7.25, difficulty=4.6)
    records = [make_note_record(anki_guid=guid, cards=[card])]

    # Pre-ingest so we can compute expected replay.
    sync = AnkiSync(db=db, _reader=RevlogReader(records, revlog), _writer=FakeWriter())
    sync._ingest_anki_revlog_for_card(_CARD_ID, coll_id, Direction.RECOGNITION)
    expected = _compute_replay(db, coll_id, stored)

    # Remove pre-ingested revlog so the real pull can re-ingest it.
    db._conn.execute("DELETE FROM tt_revlog WHERE collocation_id = ?", (coll_id,))
    db._conn.commit()

    # Set Anki's stability/difficulty far outside 0.01 tolerance.
    card2 = make_card_record(
        anki_card_id=_CARD_ID,
        ord=0,
        reps=4,
        stability=expected.stability * 2,
        difficulty=expected.difficulty + 5.0,
    )
    records2 = [make_note_record(anki_guid=guid, cards=[card2])]
    with caplog.at_level(logging.WARNING, logger="app.anki.sync"):
        report = _run_pull_report(db, records2, revlog)

    # Soak signal: every recorded divergence emits a greppable WARNING line.
    assert f"RECOMPUTE_DIVERGENCE cid={coll_id} dir={Direction.RECOGNITION.value}" in caplog.text

    after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
    assert after.stability == expected.stability * 2
    assert after.difficulty == expected.difficulty + 5.0
    assert len(report.recompute_divergences) == 1
    d = report.recompute_divergences[0]
    assert d.collocation_id == coll_id
    assert d.direction == Direction.RECOGNITION.value
    assert d.anki_stability == expected.stability * 2
    assert d.anki_difficulty == expected.difficulty + 5.0
    assert d.replay_stability == expected.stability
    assert d.replay_difficulty == expected.difficulty
    assert len(report.conflicts) == 0


def test_new_mode_suspend_branch():
    """new mode, suspend branch unchanged from legacy."""
    db = _make_tt_db()
    guid = _add_banka(db)
    _seed_review_direction(db, guid)

    card = make_card_record(anki_card_id=_CARD_ID, ord=0, reps=4, stability=7.25, difficulty=4.6, queue=-1)
    _run_pull(db, [make_note_record(anki_guid=guid, cards=[card])], [_good_revlog_row()])

    after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
    assert after.state == SRSState.SUSPENDED


def test_new_mode_bury_branch():
    """new mode, bury branch unchanged from legacy."""
    db = _make_tt_db()
    guid = _add_banka(db)
    _seed_review_direction(db, guid)

    card = make_card_record(anki_card_id=_CARD_ID, ord=0, reps=4, stability=7.25, difficulty=4.6, queue=-2)
    _run_pull(db, [make_note_record(anki_guid=guid, cards=[card])], [_good_revlog_row()])

    after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
    assert after.state == SRSState.BURIED
    assert after.bury_kind is not None


def test_new_mode_zero_new_rows_noop():
    """new mode, zero new revlog rows -> no-op on FSRS state."""
    db = _make_tt_db()
    guid = _add_banka(db)
    stored = _seed_review_direction(db, guid)

    card = make_card_record(anki_card_id=_CARD_ID, ord=0, reps=3, stability=5.0, difficulty=4.5)
    report = _run_pull_report(db, [make_note_record(anki_guid=guid, cards=[card])], revlog_rows=[])

    after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
    assert after.stability == stored.stability
    assert after.difficulty == stored.difficulty
    assert len(report.recompute_divergences) == 0


def test_legacy_default_regression():
    """legacy default unchanged (regression guard)."""
    revlog = [_good_revlog_row()]
    card_kwargs = dict(anki_card_id=_CARD_ID, ord=0, reps=4, stability=7.25, difficulty=4.6)

    db_a = _make_tt_db()
    guid_a = _add_banka(db_a)
    _seed_review_direction(db_a, guid_a)
    _run_pull(db_a, [make_note_record(anki_guid=guid_a, cards=[make_card_record(**card_kwargs)])], revlog)
    d1 = db_a.get_collocation_by_guid(guid_a).directions[Direction.RECOGNITION]

    db_b = _make_tt_db()
    guid_b = _add_banka(db_b)
    _seed_review_direction(db_b, guid_b)
    _run_pull(db_b, [make_note_record(anki_guid=guid_b, cards=[make_card_record(**card_kwargs)])], revlog)
    d2 = db_b.get_collocation_by_guid(guid_b).directions[Direction.RECOGNITION]

    assert d1.stability == d2.stability
    assert d1.difficulty == d2.difficulty
    assert d1.state == d2.state
    assert d1.due_at == d2.due_at
    assert d1.reps == d2.reps
    assert d1.last_review == d2.last_review


def test_new_mode_dry_run_writes_nothing():
    """new mode, dry_run writes nothing to authoritative columns."""
    db = _make_tt_db()
    guid = _add_banka(db)
    stored = _seed_review_direction(db, guid)

    card = make_card_record(anki_card_id=_CARD_ID, ord=0, reps=4, stability=7.25, difficulty=4.6)
    _run_pull(
        db,
        [make_note_record(anki_guid=guid, cards=[card])],
        [_good_revlog_row()],
        dry_run=True,
    )

    after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
    assert after.stability == stored.stability
    assert after.difficulty == stored.difficulty


def test_new_mode_detector_skips_when_tt_memory_newer():
    """Layer 70: a TT grade newer than Anki's lrt is a known-stale Anki value,
    not a recompute event — the detector must not count it (and the recency
    guard keeps TT's memory state)."""
    db = _make_tt_db()
    guid = _add_banka(db)
    stored = _seed_review_direction(db, guid)

    stale_lrt = stored.last_review - timedelta(days=20)
    card = make_card_record(
        anki_card_id=_CARD_ID,
        ord=0,
        reps=4,
        stability=8.2442,
        difficulty=8.385,
        last_review=stale_lrt,
    )
    report = _run_pull_report(db, [make_note_record(anki_guid=guid, cards=[card])], revlog_rows=[])

    assert len(report.recompute_divergences) == 0
    after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
    assert after.stability == stored.stability  # recency guard kept TT's value


def test_new_mode_detector_skips_fsrs_unknown():
    """Layer 70: fsrs_known=False cards carry placeholder s/d (1.0/5.0) — the
    detector comparing replay vs placeholder re-fires every sync forever (the
    854/858/866/882-886 soak-noise cohort). Gate it off."""
    db = _make_tt_db()
    guid = _add_banka(db)
    stored = _seed_review_direction(db, guid)

    card = make_card_record(
        anki_card_id=_CARD_ID,
        ord=0,
        reps=4,
        stability=1.0,
        difficulty=5.0,
        fsrs_known=False,
    )
    report = _run_pull_report(db, [make_note_record(anki_guid=guid, cards=[card])], [_good_revlog_row()])

    assert len(report.recompute_divergences) == 0
    after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
    assert after.stability == stored.stability  # fsrs_known=False keeps local


def test_new_mode_detector_ignores_unlinked_revlog_rows():
    """Layer 71: tt_revlog rows written before sync_create_new minted the Anki
    card carry anki_card_id=NULL. The replay anchor must be keyed by
    (collocation_id, direction) — the domain rebuild_from_revlog walks — or
    since_id resolves to None and the replay re-walks the full history on top
    of the already-evolved stored state, firing identical phantom divergences
    on every sync (the 858/866 ste/si perma-fire, 2026-06-11)."""
    from app.models.srs_item import RevlogRow

    db = _make_tt_db()
    guid = _add_banka(db)
    stored = _seed_review_direction(db, guid)
    coll_id = db.get_collocation_id_by_guid(guid)

    # Two pre-link TT-native grades (anki_card_id=NULL), already reflected in
    # the stored direction state — replay must NOT re-apply them.
    for i, ts in enumerate((10, 5)):
        db.append_revlog(
            RevlogRow(
                id=int((stored.last_review - timedelta(days=ts)).timestamp() * 1000),
                collocation_id=coll_id,
                direction=Direction.RECOGNITION,
                button_chosen=3,
                interval=0,
                last_interval=0,
                factor=0,
                taken_millis=4000,
                review_kind=i,
                anki_card_id=None,
            )
        )

    # Anki agrees with TT exactly; no new Anki revlog rows.
    card = make_card_record(anki_card_id=_CARD_ID, ord=0, reps=3, stability=5.0, difficulty=4.5)
    report = _run_pull_report(db, [make_note_record(anki_guid=guid, cards=[card])], revlog_rows=[])

    assert len(report.recompute_divergences) == 0
    after = db.get_collocation_by_guid(guid).directions[Direction.RECOGNITION]
    assert after.stability == stored.stability
