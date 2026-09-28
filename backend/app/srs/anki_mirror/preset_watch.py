"""Detect FSRS preset changes at sync — detection only, never a schedule write.

An Optimize, or a desired-retention edit, changes no card, so Anki writes no
revlog row for it. The due dates stay where they were while the stabilities
under them move, and ``(due_at - last_review) / stability`` silently decouples.
That is what tunatale-xzp6 was: Slovene at a median ratio of 7.06 where ~1.5 was
expected, undiagnosable for three days because nothing recorded the change
(user, 2026-09-13: "I ran optimize on all presets ... without due date
reschedule").

So each sync snapshots the synced deck's preset — the weights and retention
``_read_fsrs_params_from_deck_config_table`` already decodes for the scheduler,
plus that median ratio — and diffs against the previous snapshot. A change is
reported with the one fact that separates the safe case from the dangerous one:
how many ``type=5`` (Rescheduled) revlog rows Anki wrote for the deck since the
last snapshot. "Reschedule cards on change" writes exactly those
(``rslib/src/scheduler/fsrs/memory_state.rs`` → ``log_rescheduled_review``);
their ABSENCE beside a weight change is the xzp6 shape.

⚠️ Nothing here writes a due date, and nothing may. Reading the collection is
all this module does with it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.srs.anki_mirror.queue_stats import _read_fsrs_params_from_deck_config_table
from app.srs.db_base import _parse_last_review

if TYPE_CHECKING:
    from app.srs.database import SRSDatabase

_log = logging.getLogger(__name__)

PRESET_SNAPSHOT_KEY = "fsrs_preset_snapshot"
PRESET_CHANGE_KEY = "last_preset_change"

# RevlogReviewKind::Rescheduled (rslib/src/revlog/mod.rs). Distinct from 4
# (Manual: set-due-date / reset), which a preset change never writes.
_REVLOG_TYPE_RESCHEDULED = 5


@dataclass(frozen=True)
class PresetSnapshot:
    """What the synced deck's preset was at one sync."""

    weights: tuple[float, ...]
    desired_retention: float
    due_ratio_median: float | None
    taken_at_ms: int

    def to_json(self) -> str:
        return json.dumps(
            {
                "weights": list(self.weights),
                "desired_retention": self.desired_retention,
                "due_ratio_median": self.due_ratio_median,
                "taken_at_ms": self.taken_at_ms,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> PresetSnapshot:
        data = json.loads(raw)
        return cls(
            weights=tuple(data["weights"]),
            desired_retention=data["desired_retention"],
            due_ratio_median=data["due_ratio_median"],
            taken_at_ms=data["taken_at_ms"],
        )


@dataclass(frozen=True)
class PresetChangeAlert:
    """One unrescheduled preset change, recorded so the app can tell the user.

    The ``sync.log`` line is the diagnosis; this is the notification, and it
    exists ONLY for the NOT_RESCHEDULED case. A change Anki rescheduled itself is
    the already-safe one, so there is nothing to warn about — a banner that also
    fired on safe changes would teach the reader to dismiss it unread.

    ``dismissed_at_ms`` is set by the dismiss endpoint, never by the detector,
    and a later unrescheduled change REPLACES this record wholesale — which is
    why the write below always stores ``None``. A dismissal belongs to ONE
    change, not to the deck: a permanent "I have seen this" would silently hide
    the next Optimize, which is the same class of silent decoupling this module
    exists to end.
    """

    deck_name: str
    detected_at_ms: int
    desired_retention_old: float
    desired_retention_new: float
    weights_changed: bool
    due_ratio_median_old: float | None
    due_ratio_median_new: float | None
    dismissed_at_ms: int | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "deck_name": self.deck_name,
                "detected_at_ms": self.detected_at_ms,
                "desired_retention_old": self.desired_retention_old,
                "desired_retention_new": self.desired_retention_new,
                "weights_changed": self.weights_changed,
                "due_ratio_median_old": self.due_ratio_median_old,
                "due_ratio_median_new": self.due_ratio_median_new,
                "dismissed_at_ms": self.dismissed_at_ms,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> PresetChangeAlert:
        data = json.loads(raw)
        return cls(
            deck_name=data["deck_name"],
            detected_at_ms=data["detected_at_ms"],
            desired_retention_old=data["desired_retention_old"],
            desired_retention_new=data["desired_retention_new"],
            weights_changed=data["weights_changed"],
            due_ratio_median_old=data["due_ratio_median_old"],
            due_ratio_median_new=data["due_ratio_median_new"],
            # An ABSENT key is the same fact as a null one — nobody has dismissed
            # it — so a record written before the field existed reads as new.
            dismissed_at_ms=data.get("dismissed_at_ms"),
        )


@dataclass(frozen=True)
class PresetWatchReport:
    """One sync's view of the preset: now, last time, and what Anki did between.

    ``current`` is ``None`` when the deck's preset could not be read — which is
    reported as UNREADABLE, never as "unchanged". ``rescheduled_rows`` is 0 when
    there is no previous snapshot to measure a window from.
    """

    deck_name: str
    current: PresetSnapshot | None
    previous: PresetSnapshot | None
    rescheduled_rows: int

    @property
    def weights_changed(self) -> bool:
        return self.current is not None and self.previous is not None and self.current.weights != self.previous.weights

    @property
    def retention_changed(self) -> bool:
        return (
            self.current is not None
            and self.previous is not None
            and self.current.desired_retention != self.previous.desired_retention
        )

    @property
    def changed(self) -> bool:
        return self.weights_changed or self.retention_changed

    @property
    def not_rescheduled(self) -> bool:
        """The xzp6 shape: the preset moved and Anki wrote no reschedule for it.

        The one predicate that decides whether the user is told. A property
        rather than a re-derivation at the call site, so the stored record, the
        log line and this flag cannot come to disagree about what is dangerous.
        """
        return self.changed and self.rescheduled_rows == 0


def _alert_from_report(report: PresetWatchReport, now_ms: int) -> PresetChangeAlert:
    """The stored record for one NOT_RESCHEDULED change, built from what was logged."""
    old, new = report.previous, report.current
    assert old is not None and new is not None  # only reached when changed
    return PresetChangeAlert(
        deck_name=report.deck_name,
        detected_at_ms=now_ms,
        desired_retention_old=old.desired_retention,
        desired_retention_new=new.desired_retention,
        weights_changed=report.weights_changed,
        due_ratio_median_old=old.due_ratio_median,
        due_ratio_median_new=new.due_ratio_median,
    )


def due_ratio_median(db: SRSDatabase) -> float | None:
    """Median ``(due_at - last_review) / stability`` in days, over REVIEW rows.

    FSRS schedules a review at roughly ``stability`` times a factor set by the
    desired retention, so this sits near a small constant for a coupled deck and
    jumps when a preset change moves stabilities without moving due dates.
    ``None`` when there is nothing to measure — a clean negative must not read
    as a ratio of 0.
    """
    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT due_at, last_review, stability FROM collocation_directions "
            "WHERE state = 'review' AND stability > 0 AND last_review IS NOT NULL"
        ).fetchall()
    ratios = []
    for due_at, last_review, stability in rows:
        due = _parse_last_review(due_at)
        last = _parse_last_review(last_review)
        ratios.append((due - last).total_seconds() / 86400 / stability)  # type: ignore[operator]
    return statistics.median(ratios) if ratios else None


def _rescheduled_rows_since(conn: sqlite3.Connection, deck_name: str, since_ms: int) -> int:
    """``type=5`` revlog rows on this deck's cards after *since_ms*."""
    row = conn.execute(
        "SELECT COUNT(*) FROM revlog r JOIN cards c ON c.id = r.cid JOIN decks d ON d.id = c.did "
        "WHERE d.name = ? AND r.type = ? AND r.id > ?",
        (deck_name, _REVLOG_TYPE_RESCHEDULED, since_ms),
    ).fetchone()
    return int(row[0])


def watch_preset(db: SRSDatabase, conn: sqlite3.Connection, deck_name: str, *, now_ms: int) -> PresetWatchReport:
    """Snapshot the deck's preset, diff it against the last one, store the new one.

    Call AFTER the pull: the median ratio is meant to reflect the stabilities
    Anki just recomputed. An unreadable preset keeps the previous snapshot, so
    the next readable sync still diffs against the last good one rather than
    against nothing.

    A change Anki did NOT reschedule is additionally recorded under
    ``PRESET_CHANGE_KEY`` for the app to show as a banner (tunatale-c649). Every
    other path — rescheduled, unchanged, first sync, unreadable — leaves any
    existing record exactly as it was, so the detector cannot clear an alert it
    has no new information about.
    """
    stored = db.get_anki_state_cache(PRESET_SNAPSHOT_KEY)
    previous = PresetSnapshot.from_json(stored[0]) if stored is not None else None

    params = _read_fsrs_params_from_deck_config_table(conn, deck_name)
    if params is None:
        _log.warning("FSRS_PRESET deck=%r UNREADABLE: preset-change detection is blind this sync", deck_name)
        return PresetWatchReport(deck_name=deck_name, current=None, previous=previous, rescheduled_rows=0)

    current = PresetSnapshot(
        weights=tuple(params.weights),
        desired_retention=params.desired_retention,
        due_ratio_median=due_ratio_median(db),
        taken_at_ms=now_ms,
    )
    rescheduled = _rescheduled_rows_since(conn, deck_name, previous.taken_at_ms) if previous is not None else 0
    db.set_anki_state_cache(PRESET_SNAPSHOT_KEY, current.to_json())

    report = PresetWatchReport(deck_name=deck_name, current=current, previous=previous, rescheduled_rows=rescheduled)
    if report.changed:
        _log.warning("%s", _change_line(report))
        if report.not_rescheduled:
            db.set_anki_state_cache(PRESET_CHANGE_KEY, _alert_from_report(report, now_ms).to_json())
    return report


def _ratio(value: float | None) -> str:
    return "none" if value is None else f"{value:.2f}"


def _weights_sha(weights: tuple[float, ...]) -> str:
    return hashlib.sha256(json.dumps(list(weights)).encode()).hexdigest()[:12]


def _change_line(report: PresetWatchReport) -> str:
    old, new = report.previous, report.current
    assert old is not None and new is not None  # only called when changed
    # NOT_RESCHEDULED is the xzp6 shape and gets a word of its own, so a grep
    # for it finds every occurrence without parsing a number.
    verdict = "rescheduled" if report.rescheduled_rows else "NOT_RESCHEDULED"
    return (
        f'PRESET_CHANGE deck="{report.deck_name}" {verdict} rescheduled={report.rescheduled_rows} '
        f"dr={old.desired_retention:.4f}->{new.desired_retention:.4f} "
        f"weights_changed={int(report.weights_changed)} "
        f"due_ratio_median={_ratio(old.due_ratio_median)}->{_ratio(new.due_ratio_median)} "
        f"old_w={[round(w, 4) for w in old.weights]} new_w={[round(w, 4) for w in new.weights]}"
    )


def format_preset_lines(report: PresetWatchReport, ts: str) -> list[str]:
    """The ``sync.log`` lines for one sync: a heartbeat always, a change line when changed.

    The heartbeat is written even when nothing changed, for the same reason as
    ``SYNC_SOAK``: without it, "no PRESET_CHANGE line" cannot tell "unchanged"
    from "the detector never ran".
    """
    cur = report.current
    if cur is None:
        return [f'{ts} FSRS_PRESET deck="{report.deck_name}" UNREADABLE']
    lines = [
        f'{ts} FSRS_PRESET deck="{report.deck_name}" weights_sha={_weights_sha(cur.weights)} '
        f"n_weights={len(cur.weights)} dr={cur.desired_retention:.4f} "
        f"due_ratio_median={_ratio(cur.due_ratio_median)}"
    ]
    if report.changed:
        lines.append(f"{ts}   {_change_line(report)}")
    return lines
