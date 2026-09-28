"""FSRS preset-change detection at sync (tunatale-sf6h).

An Optimize, or a desired-retention edit, writes no revlog row: no card changes,
so due dates stay put while the stabilities under them move. That silent
decoupling is what tunatale-xzp6 was (Slovene at a median due/stability of 7.06
where ~1.5 was expected), and it was undiagnosable for days because nothing
recorded it. These tests pin the detector: a snapshot per sync, a loud line when
it changes, and the one fact that separates the safe case from the dangerous
one — whether Anki wrote ``type=5`` (Rescheduled) revlog rows alongside it.

Detection only. Nothing here may write a due date, and no test here expects one
to move.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.common.guid import compute_guid
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.preset_watch import (
    PRESET_CHANGE_KEY,
    PRESET_SNAPSHOT_KEY,
    PresetChangeAlert,
    due_ratio_median,
    format_preset_lines,
    watch_preset,
)
from tests.anki_oracle.synthetic_collection import DEFAULT_WEIGHTS, SyntheticCollection

DECK = "Norwegian (TunaTale)"
DECK_ID = 1700000000001
T0_MS = 1_790_000_000_000  # the first sync
T1_MS = T0_MS + 86_400_000  # the second, a day later
T2_MS = T1_MS + 86_400_000  # the third
# An Optimize's signature: every weight moves. Built from the stock tuple so the
# length (19, field 5) stays readable by the real parser.
OPTIMIZED = tuple(round(w * 1.25, 4) for w in DEFAULT_WEIGHTS)
TS = "2026-09-28T12:00:00"


def _collection(tmp_path, *, weights=DEFAULT_WEIGHTS, retention=0.9, name="c.anki2") -> SyntheticCollection:
    coll = SyntheticCollection(tmp_path / name)
    coll.set_deck(DECK, DECK_ID)
    coll.enable_fsrs(weights=weights, retention=retention)
    coll.add_note(id=5001, guid="g1", fields=["hei", "hi"])
    coll.add_card(id=6001, note_id=5001, type=2, queue=2, stability=3.0, difficulty=5.0)
    return coll


def _conn(coll: SyntheticCollection) -> sqlite3.Connection:
    coll.save()
    return sqlite3.connect(coll.path)


def _review(db, word: str, *, stability: float, days_after_review: float, state=SRSState.REVIEW) -> None:
    """One direction whose (due_at - last_review) / stability is known exactly."""
    db.add_collocation(
        SyntacticUnit(text=word, translation="gloss", word_count=1, difficulty=1, source="test"),
        language_code="no",
    )
    last = datetime(2026, 9, 1, 4, 0, tzinfo=UTC)
    db.update_direction(
        compute_guid(word, "no", ""),
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            state=state,
            stability=stability,
            last_review=last,
            due_at=last + timedelta(days=days_after_review),
        ),
    )


# ---------------------------------------------------------------------------
# due_ratio_median — the number that made xzp6 visible at all
# ---------------------------------------------------------------------------


def test_due_ratio_median_is_the_median_over_review_rows(srs_db):
    _review(srs_db, "en", stability=2.0, days_after_review=2.0)  # 1.0
    _review(srs_db, "to", stability=2.0, days_after_review=4.0)  # 2.0
    _review(srs_db, "tre", stability=1.0, days_after_review=10.0)  # 10.0
    assert due_ratio_median(srs_db) == pytest.approx(2.0)


def test_due_ratio_median_ignores_non_review_rows(srs_db):
    _review(srs_db, "en", stability=2.0, days_after_review=4.0)  # 2.0 — the only review row
    _review(srs_db, "to", stability=1.0, days_after_review=50.0, state=SRSState.LEARNING)
    _review(srs_db, "tre", stability=1.0, days_after_review=90.0, state=SRSState.RELEARNING)
    assert due_ratio_median(srs_db) == pytest.approx(2.0)


def test_due_ratio_median_is_none_with_no_review_rows(srs_db):
    """A clean negative must read as 'no data', never as a ratio of 0."""
    assert due_ratio_median(srs_db) is None


# ---------------------------------------------------------------------------
# watch_preset — snapshot, diff, and the reschedule discriminator
# ---------------------------------------------------------------------------


def test_first_sync_records_a_baseline_and_reports_no_change(srs_db, tmp_path):
    report = watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    assert report.previous is None
    assert report.current is not None
    assert report.current.weights == pytest.approx(DEFAULT_WEIGHTS, abs=1e-6)
    assert report.current.desired_retention == pytest.approx(0.9, abs=1e-6)
    assert report.changed is False
    assert srs_db.get_anki_state_cache(PRESET_SNAPSHOT_KEY) is not None


def test_unchanged_preset_is_not_a_change(srs_db, tmp_path):
    watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    report = watch_preset(srs_db, _conn(_collection(tmp_path, name="c2.anki2")), DECK, now_ms=T1_MS)
    assert report.previous is not None
    assert report.changed is False
    lines = format_preset_lines(report, TS)
    assert not any("PRESET_CHANGE" in line for line in lines)


def test_optimize_without_reschedule_is_reported_loudly(srs_db, tmp_path, caplog):
    """The xzp6 case: weights moved, no type=5 rows. It must say so in words."""
    watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    with caplog.at_level(logging.WARNING):
        report = watch_preset(
            srs_db, _conn(_collection(tmp_path, weights=OPTIMIZED, name="c2.anki2")), DECK, now_ms=T1_MS
        )
    assert report.changed is True
    assert report.weights_changed is True
    assert report.retention_changed is False
    assert report.rescheduled_rows == 0
    change = [line for line in format_preset_lines(report, TS) if "PRESET_CHANGE" in line]
    assert len(change) == 1
    assert "rescheduled=0" in change[0]
    assert "NOT_RESCHEDULED" in change[0]
    assert "PRESET_CHANGE" in caplog.text


def test_retention_only_change_is_a_change(srs_db, tmp_path):
    watch_preset(srs_db, _conn(_collection(tmp_path, retention=0.9)), DECK, now_ms=T0_MS)
    report = watch_preset(srs_db, _conn(_collection(tmp_path, retention=0.86, name="c2.anki2")), DECK, now_ms=T1_MS)
    assert report.changed is True
    assert report.weights_changed is False
    assert report.retention_changed is True
    change = next(line for line in format_preset_lines(report, TS) if "PRESET_CHANGE" in line)
    assert "dr=0.9000->0.8600" in change


def test_reschedule_rows_count_only_this_decks_type5_since_the_baseline(srs_db, tmp_path):
    """Exactly one row qualifies; each decoy fails exactly one condition."""
    watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)

    coll = _collection(tmp_path, weights=OPTIMIZED, name="c2.anki2")
    coll.add_note(id=5002, guid="g2", fields=["annen", "other"])
    coll.add_card(id=6002, note_id=5002, type=2, queue=2)
    coll.cards[-1]["did"] = DECK_ID + 1  # a card in ANOTHER deck
    coll.add_revlog(id=T0_MS + 1000, card_id=6001, ease=0, ivl=5, last_ivl=3, time=0, type=5)  # counts
    coll.add_revlog(id=T0_MS - 1000, card_id=6001, ease=0, ivl=5, last_ivl=3, time=0, type=5)  # before baseline
    coll.add_revlog(id=T0_MS + 2000, card_id=6002, ease=0, ivl=5, last_ivl=3, time=0, type=5)  # other deck
    coll.add_revlog(id=T0_MS + 3000, card_id=6001, ease=3, ivl=5, last_ivl=3, time=900, type=1)  # a real grade
    # Two set-due-date rows, so a detector counting type=4 instead would read 2, not 1.
    coll.add_revlog(id=T0_MS + 4000, card_id=6001, ease=0, ivl=5, last_ivl=3, time=0, type=4)
    coll.add_revlog(id=T0_MS + 5000, card_id=6001, ease=0, ivl=6, last_ivl=5, time=0, type=4)

    conn = _conn(coll)
    # The builder writes one deck; without a real row for the other deck the
    # join drops its card whatever the name filter says, and that decoy is inert.
    conn.execute("INSERT INTO decks VALUES (?, 'Other deck', 0, -1, x'', x'')", (DECK_ID + 1,))
    report = watch_preset(srs_db, conn, DECK, now_ms=T1_MS)
    assert report.rescheduled_rows == 1
    change = next(line for line in format_preset_lines(report, TS) if "PRESET_CHANGE" in line)
    assert "rescheduled=1" in change
    assert "NOT_RESCHEDULED" not in change


def test_change_line_carries_the_ratio_before_and_after(srs_db, tmp_path):
    _review(srs_db, "en", stability=2.0, days_after_review=3.0)  # 1.5 at the baseline
    watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    # The pull after an Optimize brings Anki's recomputed stabilities in while
    # the due date stays: same interval, smaller stability, bigger ratio.
    srs_db.update_direction(
        compute_guid("en", "no", ""),
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            stability=0.5,
            last_review=datetime(2026, 9, 1, 4, 0, tzinfo=UTC),
            due_at=datetime(2026, 9, 4, 4, 0, tzinfo=UTC),
        ),
    )
    report = watch_preset(srs_db, _conn(_collection(tmp_path, weights=OPTIMIZED, name="c2.anki2")), DECK, now_ms=T1_MS)
    change = next(line for line in format_preset_lines(report, TS) if "PRESET_CHANGE" in line)
    assert "due_ratio_median=1.50->6.00" in change


def test_every_sync_writes_a_heartbeat_line(srs_db, tmp_path):
    """Positive confirmation the detector ran — absence must not read as 'no change'."""
    report = watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    lines = format_preset_lines(report, TS)
    assert len(lines) == 1
    assert lines[0].startswith(f"{TS} FSRS_PRESET ")
    assert f'deck="{DECK}"' in lines[0]
    assert "n_weights=19" in lines[0]
    assert "dr=0.9000" in lines[0]


def test_unreadable_preset_is_loud_and_keeps_the_baseline(srs_db, tmp_path, caplog):
    """A 5-float weight field is not a preset TT can read. It must not be
    reported as 'unchanged', and it must not overwrite the last good baseline —
    or the next readable sync would diff against nothing."""
    watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    baseline = srs_db.get_anki_state_cache(PRESET_SNAPSHOT_KEY)[0]

    with caplog.at_level(logging.WARNING):
        report = watch_preset(
            srs_db, _conn(_collection(tmp_path, weights=(0.1, 0.2, 0.3, 0.4, 0.5), name="c2.anki2")), DECK, now_ms=T1_MS
        )
    assert report.current is None
    assert report.changed is False
    assert srs_db.get_anki_state_cache(PRESET_SNAPSHOT_KEY)[0] == baseline
    lines = format_preset_lines(report, TS)
    assert any("FSRS_PRESET" in line and "UNREADABLE" in line for line in lines)
    assert "UNREADABLE" in caplog.text

    # ...and the next readable sync still diffs against the T0 baseline.
    report = watch_preset(srs_db, _conn(_collection(tmp_path, weights=OPTIMIZED, name="c3.anki2")), DECK, now_ms=T1_MS)
    assert report.changed is True


def test_watch_never_writes_the_collection(srs_db, tmp_path):
    """Detection only: the collection's bytes are identical after a changed-preset watch."""
    watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    coll = _collection(tmp_path, weights=OPTIMIZED, name="c2.anki2")
    conn = _conn(coll)
    before = coll.path.read_bytes()
    watch_preset(srs_db, conn, DECK, now_ms=T1_MS)
    conn.close()
    assert coll.path.read_bytes() == before


# ---------------------------------------------------------------------------
# the alert record — the NOT_RESCHEDULED change as data (tunatale-c649)
# ---------------------------------------------------------------------------
#
# The sync.log line is the diagnosis; this record is the notification. It exists
# only for the dangerous case (`rescheduled_rows == 0`), because a safe change
# needs no banner: Anki already moved the due dates for it.


def _stored_alert(db) -> PresetChangeAlert:
    row = db.get_anki_state_cache(PRESET_CHANGE_KEY)
    assert row is not None, "no preset-change alert was recorded"
    return PresetChangeAlert.from_json(row[0])


def _dismiss(db, at_ms: int) -> None:
    """Dismiss by editing the stored record — the same field the API endpoint sets.

    No mock and no side door: a dismissal produced by anything other than the
    real payload could pass a test that a real one would fail.
    """
    db.set_anki_state_cache(PRESET_CHANGE_KEY, replace(_stored_alert(db), dismissed_at_ms=at_ms).to_json())


def test_a_not_rescheduled_change_records_the_alert_with_its_exact_values(srs_db, tmp_path):
    """The xzp6 shape as stored data: exactly the numbers the banner prints."""
    _review(srs_db, "en", stability=2.0, days_after_review=3.0)  # median 1.50
    watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    # The pull after an Optimize: same interval, smaller stability, bigger ratio.
    srs_db.update_direction(
        compute_guid("en", "no", ""),
        Direction.RECOGNITION,
        DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            stability=0.5,
            last_review=datetime(2026, 9, 1, 4, 0, tzinfo=UTC),
            due_at=datetime(2026, 9, 4, 4, 0, tzinfo=UTC),
        ),
    )
    watch_preset(srs_db, _conn(_collection(tmp_path, weights=OPTIMIZED, name="c2.anki2")), DECK, now_ms=T1_MS)

    alert = _stored_alert(srs_db)
    assert alert.deck_name == DECK
    assert alert.detected_at_ms == T1_MS
    assert alert.desired_retention_old == pytest.approx(0.9, abs=1e-6)
    assert alert.desired_retention_new == pytest.approx(0.9, abs=1e-6)
    assert alert.weights_changed is True
    assert alert.due_ratio_median_old == pytest.approx(1.5)
    assert alert.due_ratio_median_new == pytest.approx(6.0)
    assert alert.dismissed_at_ms is None


def test_a_rescheduled_change_records_no_alert(srs_db, tmp_path):
    """Anki moved the due dates itself, so there is nothing to warn about."""
    watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    coll = _collection(tmp_path, weights=OPTIMIZED, name="c2.anki2")
    coll.add_revlog(id=T0_MS + 1000, card_id=6001, ease=0, ivl=5, last_ivl=3, time=0, type=5)
    report = watch_preset(srs_db, _conn(coll), DECK, now_ms=T1_MS)
    assert report.rescheduled_rows == 1
    assert srs_db.get_anki_state_cache(PRESET_CHANGE_KEY) is None


def test_an_unchanged_sync_records_no_alert(srs_db, tmp_path):
    watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    watch_preset(srs_db, _conn(_collection(tmp_path, name="c2.anki2")), DECK, now_ms=T1_MS)
    assert srs_db.get_anki_state_cache(PRESET_CHANGE_KEY) is None


def test_a_first_sync_records_no_alert(srs_db, tmp_path):
    """No previous snapshot is not a change, and must not read as one."""
    report = watch_preset(srs_db, _conn(_collection(tmp_path, weights=OPTIMIZED)), DECK, now_ms=T0_MS)
    assert report.previous is None
    assert srs_db.get_anki_state_cache(PRESET_CHANGE_KEY) is None


def test_a_retention_only_change_with_nothing_to_measure_records_null_medians(srs_db, tmp_path):
    """An unmeasurable median is null, never 0 — a ratio of 0 is a real ratio."""
    watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    watch_preset(srs_db, _conn(_collection(tmp_path, retention=0.86, name="c2.anki2")), DECK, now_ms=T1_MS)
    alert = _stored_alert(srs_db)
    assert alert.weights_changed is False
    assert alert.due_ratio_median_old is None
    assert alert.due_ratio_median_new is None


def test_an_unreadable_preset_records_no_alert(srs_db, tmp_path):
    watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    watch_preset(
        srs_db, _conn(_collection(tmp_path, weights=(0.1, 0.2, 0.3, 0.4, 0.5), name="c2.anki2")), DECK, now_ms=T1_MS
    )
    assert srs_db.get_anki_state_cache(PRESET_CHANGE_KEY) is None


def test_an_unreadable_preset_leaves_an_existing_alert_untouched(srs_db, tmp_path):
    """A blind sync knows nothing, so it may not clear, refresh or resurrect a record."""
    watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    watch_preset(srs_db, _conn(_collection(tmp_path, weights=OPTIMIZED, name="c2.anki2")), DECK, now_ms=T1_MS)
    _dismiss(srs_db, T1_MS + 5)
    stored = srs_db.get_anki_state_cache(PRESET_CHANGE_KEY)[0]

    watch_preset(
        srs_db, _conn(_collection(tmp_path, weights=(0.1, 0.2, 0.3, 0.4, 0.5), name="c3.anki2")), DECK, now_ms=T2_MS
    )
    assert srs_db.get_anki_state_cache(PRESET_CHANGE_KEY)[0] == stored
    assert _stored_alert(srs_db).dismissed_at_ms == T1_MS + 5


def test_a_later_change_reopens_a_dismissed_alert(srs_db, tmp_path):
    """A dismissal belongs to ONE change, not to the deck: the next unrescheduled
    change is news again, and must arrive undismissed or the user never sees it."""
    watch_preset(srs_db, _conn(_collection(tmp_path)), DECK, now_ms=T0_MS)
    watch_preset(srs_db, _conn(_collection(tmp_path, weights=OPTIMIZED, name="c2.anki2")), DECK, now_ms=T1_MS)
    _dismiss(srs_db, T1_MS + 5)
    assert _stored_alert(srs_db).dismissed_at_ms == T1_MS + 5

    report = watch_preset(
        srs_db,
        _conn(_collection(tmp_path, weights=OPTIMIZED, retention=0.85, name="c3.anki2")),
        DECK,
        now_ms=T2_MS,
    )
    assert report.changed is True
    assert report.rescheduled_rows == 0

    alert = _stored_alert(srs_db)
    assert alert.dismissed_at_ms is None
    assert alert.detected_at_ms == T2_MS
    assert alert.weights_changed is False
    assert alert.desired_retention_old == pytest.approx(0.9, abs=1e-6)
    assert alert.desired_retention_new == pytest.approx(0.85, abs=1e-6)


def test_the_alert_key_is_tt_state_and_is_never_deck_config():
    """ANKI_CONFIG keys are asserted fresh after EVERY sync and copied into a
    second learner's deck. This record is written only on a change, is alert
    state, and another learner must not inherit it — so TT_STATE, not
    ANKI_CONFIG, and with no day-scoping (a dismissal must survive the rollover).
    """
    from app.srs.anki_mirror.cache_registry import REGISTRY, CacheSource
    from app.srs.user_deck_seed import CONFIG_KEYS

    spec = REGISTRY[PRESET_CHANGE_KEY]
    assert spec.source is CacheSource.TT_STATE
    assert (spec.day_scoped, spec.max_age_days, spec.logic_version) == (False, None, None)
    assert PRESET_CHANGE_KEY not in CONFIG_KEYS
