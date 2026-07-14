"""Tests for replay_fsrs_from_revlog one-shot script."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models.srs_item import Direction, DirectionState, SRSState
from app.plugins.anki_sync.replay_fsrs_from_revlog import _parse_stored_due, _states_match, replay_fsrs_from_revlog
from app.srs.database import SRSDatabase
from tests.conftest import seed_direction


def _create_minimal_anki_db(tmp_path: Path) -> Path:
    path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE col (
            id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,
            dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT,
            decks TEXT, dconf TEXT, tags TEXT
        );
        CREATE TABLE notes (
            id INTEGER PRIMARY KEY, guid TEXT UNIQUE, mid INTEGER, mod INTEGER,
            usn INTEGER, tags TEXT, flds TEXT, sfld TEXT, csum INTEGER,
            flags INTEGER, data TEXT
        );
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER,
            mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER, due INTEGER,
            ivl INTEGER, factor INTEGER, reps INTEGER, lapses INTEGER,
            left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT
        );
        CREATE TABLE revlog (
            id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER,
            ivl INTEGER, lastIvl INTEGER, factor INTEGER, time INTEGER,
            type INTEGER
        );
        CREATE TABLE deck_config (
            id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER,
            usn INTEGER, config BLOB
        );
        CREATE TABLE decks (
            id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER,
            usn INTEGER, common BLOB
        );
    """)
    conn.execute(
        "INSERT INTO col VALUES (1, 1704067200, 0, 1000, 18, 0, 0, 0, '{}', '{}', '{}', '{}', '{}')",
    )
    conn.commit()
    conn.close()
    return path


def _add_revlog_to_anki(conn: sqlite3.Connection, card_id: int, rows: list[tuple]) -> None:
    conn.executemany(
        "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)",
        [(rid, card_id, *vals) for rid, *vals in rows],
    )


def _build_tt_db(tmp_path: Path, name: str = "tunatale.db") -> Path:
    path = tmp_path / name
    db = SRSDatabase(str(path))
    db.close()
    return path


def _add_revlog_to_tt(
    tt_path: Path,
    collocation_id: int,
    direction: str,
    rows: list[tuple],
    *,
    anki_card_id: int | None = None,
) -> None:
    conn = sqlite3.connect(str(tt_path))
    conn.executemany(
        """INSERT OR IGNORE INTO tt_revlog
           (id, collocation_id, direction, button_chosen,
            interval, last_interval, factor, taken_millis,
            review_kind, anki_card_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [(rid, collocation_id, direction, *vals, anki_card_id) for rid, *vals in rows],
    )
    conn.commit()
    conn.close()


def _open_tt(tt_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(tt_path))
    conn.row_factory = sqlite3.Row
    return conn


def _make_anki_linked_scenario(tmp_path: Path, revlog_rows: list[tuple] | None = None):
    anki = _create_minimal_anki_db(tmp_path)
    conn = sqlite3.connect(str(anki))
    card_id = 10001
    conn.execute(
        "INSERT INTO notes VALUES (1001, 'guid_anki', 0, 0, 0, '', 'text', 'text', 0, 0, '')",
    )
    conn.execute(
        "INSERT INTO cards VALUES (?, 1001, 0, 0, 0, 0, 2, 2, 10, 21, 2500, 5, 0, 0, 0, 0, 0, '{}')",
        (card_id,),
    )
    if revlog_rows:
        _add_revlog_to_anki(conn, card_id, revlog_rows)
    conn.commit()
    conn.close()

    tt = _build_tt_db(tmp_path)
    row_id = seed_direction(
        SRSDatabase(str(tt)),
        text="test",
        anki_card_id=card_id,
        reps=5,
        state=SRSState.REVIEW,
        stability=5.0,
        difficulty=5.0,
        due_at=datetime(2024, 1, 10, 4, 0, tzinfo=UTC),
        last_review=datetime(2024, 1, 9, 4, 0, tzinfo=UTC),
        last_review_time_ms=1700000000001,
        last_rating=3,
        introduced_at=datetime(2024, 1, 5, 4, 0, tzinfo=UTC),
    )

    if revlog_rows:
        _add_revlog_to_tt(tt, row_id, "recognition", revlog_rows, anki_card_id=card_id)

    return anki, tt, card_id, row_id


class TestRebuildEmpty:
    """Empty revlog returns default NEW state."""

    def test_empty_revlog_returns_default(self, tmp_path):
        tt = _build_tt_db(tmp_path)
        row_id = seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=None,
        )
        with SRSDatabase(str(tt)) as db:
            result = db.rebuild_from_revlog(row_id, Direction.RECOGNITION)
        assert result.state == SRSState.NEW
        assert result.reps == 0
        assert result.stability == 1.0
        assert result.difficulty == 5.0


class TestRebuildLearning:
    """Learning-step semantics."""

    def test_new_again_goes_to_learning(self, tmp_path):
        tt = _build_tt_db(tmp_path)
        row_id = seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=None,
        )
        _add_revlog_to_tt(
            tt,
            row_id,
            "recognition",
            [
                (1700000001000, 1, 0, 0, 0, 0, 0),
            ],
        )
        with SRSDatabase(str(tt)) as db:
            result = db.rebuild_from_revlog(row_id, Direction.RECOGNITION)
        assert result.state == SRSState.LEARNING
        assert result.reps == 1

    def test_new_easy_graduates(self, tmp_path):
        tt = _build_tt_db(tmp_path)
        row_id = seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=None,
        )
        _add_revlog_to_tt(
            tt,
            row_id,
            "recognition",
            [
                (1700000001000, 4, 0, 0, 0, 0, 0),
            ],
        )
        with SRSDatabase(str(tt)) as db:
            result = db.rebuild_from_revlog(row_id, Direction.RECOGNITION)
        assert result.state == SRSState.REVIEW
        assert result.reps == 1
        assert result.stability > 1.0


class TestRebuildReview:
    """Review -> AGAIN -> RELEARN -> REVIEW cycle."""

    def test_review_again_lapses(self, tmp_path):
        tt = _build_tt_db(tmp_path)
        row_id = seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=10001,
        )
        _add_revlog_to_tt(
            tt,
            row_id,
            "recognition",
            [
                (1700000000001, 3, 0, 0, 0, 0, 0),
                (1700000600001, 3, 0, 0, 0, 0, 0),
                (1700086400001, 1, 0, 0, 0, 0, 0),
            ],
        )
        with SRSDatabase(str(tt)) as db:
            result = db.rebuild_from_revlog(row_id, Direction.RECOGNITION)
        assert result.state == SRSState.RELEARNING
        assert result.reps == 3


class TestRebuildMixed:
    """Multi-row sequence."""

    def test_multi_row_sequence(self, tmp_path):
        tt = _build_tt_db(tmp_path)
        row_id = seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=10001,
        )
        _add_revlog_to_tt(
            tt,
            row_id,
            "recognition",
            [
                (1700000000001, 3, 0, 0, 0, 0, 0),
                (1700000600001, 3, 0, 0, 0, 0, 0),
                (1700008640001, 3, 0, 0, 0, 0, 1),
                (1700017280001, 1, 0, 0, 0, 0, 0),
                (1700017340001, 3, 0, 0, 0, 0, 2),
            ],
        )
        with SRSDatabase(str(tt)) as db:
            result = db.rebuild_from_revlog(row_id, Direction.RECOGNITION)
        assert result.state == SRSState.REVIEW
        assert result.reps == 5

    def test_skips_invalid_button_chosen(self, tmp_path):
        tt = _build_tt_db(tmp_path)
        row_id = seed_direction(SRSDatabase(str(tt)), text="test", anki_card_id=None)
        _add_revlog_to_tt(
            tt,
            row_id,
            "recognition",
            [
                (1700000000001, 0, 0, 0, 0, 0, 4),
                (1700000001001, 3, 0, 0, 0, 0, 0),
            ],
        )
        with SRSDatabase(str(tt)) as db:
            result = db.rebuild_from_revlog(row_id, Direction.RECOGNITION, exclude_review_kinds=set())
        assert result.reps == 1
        assert result.state in (SRSState.LEARNING, SRSState.REVIEW)


class TestRebuildFuzzSeed:
    """FSRS interval-fuzz seed must use the real anki_card_id."""

    def test_anki_card_id_affects_due_at(self, tmp_path):
        tt = _build_tt_db(tmp_path)
        row_id = seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=10001,
        )
        # NEW+GOOD -> LEARNING -> LEARNING+GOOD -> REVIEW (graduates with ~1d)
        # Then a REVIEW+GOOD at +30d to get an interval long enough for fuzz
        base = 1700000000001
        _add_revlog_to_tt(
            tt,
            row_id,
            "recognition",
            [
                (base, 3, 0, 0, 0, 0, 0),
                (base + 600_000, 3, 0, 0, 0, 0, 0),
                (base + 86400_000, 3, 0, 0, 0, 0, 1),
                (base + 86400_000 + 30 * 86400_000, 3, 0, 0, 0, 0, 1),
            ],
            anki_card_id=10001,
        )

        with SRSDatabase(str(tt)) as db:
            with_card = db.rebuild_from_revlog(row_id, Direction.RECOGNITION, anki_card_id=10001)
            without_card = db.rebuild_from_revlog(row_id, Direction.RECOGNITION, anki_card_id=None)
        assert with_card.reps == without_card.reps
        assert with_card.stability == without_card.stability
        assert with_card.difficulty == without_card.difficulty
        assert with_card.due_at != without_card.due_at, (
            "anki_card_id must affect interval-fuzz seed; different seeds should produce different due_at "
            "for long intervals"
        )


def _assert_state_eq(a: DirectionState, b: DirectionState, msg: str = "") -> None:
    """Compare the FSRS-derived fields of two DirectionStates."""
    assert a.state == b.state, f"{msg} state {a.state} != {b.state}"
    assert a.reps == b.reps, f"{msg} reps {a.reps} != {b.reps}"
    assert a.lapses == b.lapses, f"{msg} lapses {a.lapses} != {b.lapses}"
    assert a.stability == b.stability, f"{msg} stability {a.stability} != {b.stability}"
    assert a.difficulty == b.difficulty, f"{msg} difficulty {a.difficulty} != {b.difficulty}"
    assert a.due_at == b.due_at, f"{msg} due_at {a.due_at} != {b.due_at}"


class TestRebuildIncremental:
    """Stage 3b step 2: starting_state + since_id incremental replay.

    The collapse's soundness invariant is *composition*: replaying a prefix from
    NEW to reach state S_k, then replaying the suffix with ``starting_state=S_k``
    and ``since_id=<k-th row id>``, must equal a single replay from NEW over all
    rows. These tests pin that, plus the boundary cases (since_id=None ==
    legacy; zero new rows == identity).
    """

    _ROWS = [
        (1700000000001, 3, 0, 0, 0, 0, 0),  # NEW + GOOD -> learning
        (1700000600001, 3, 0, 0, 0, 0, 0),  # GOOD -> graduate
        (1700087000001, 3, 0, 0, 0, 0, 1),  # REVIEW + GOOD (+1d)
        (1700173400001, 1, 0, 0, 0, 0, 1),  # REVIEW + AGAIN -> relearning
        (1700173460001, 3, 0, 0, 0, 0, 2),  # GOOD -> graduate again
    ]
    _CARD_ID = 10001

    def _seed_rows(self, tmp_path, rows, *, name="tunatale.db", text="test") -> tuple:
        tt = _build_tt_db(tmp_path, name=name)
        row_id = seed_direction(SRSDatabase(str(tt)), text=text, anki_card_id=self._CARD_ID)
        _add_revlog_to_tt(tt, row_id, "recognition", rows, anki_card_id=self._CARD_ID)
        return tt, row_id

    def test_since_id_none_walks_from_new(self, tmp_path):
        """(a) Omitting starting_state + since_id reproduces the legacy from-NEW walk."""
        tt, row_id = self._seed_rows(tmp_path, self._ROWS)
        with SRSDatabase(str(tt)) as db:
            default = db.rebuild_from_revlog(row_id, Direction.RECOGNITION, anki_card_id=self._CARD_ID)
            explicit_none = db.rebuild_from_revlog(
                row_id, Direction.RECOGNITION, anki_card_id=self._CARD_ID, starting_state=None, since_id=None
            )
        _assert_state_eq(default, explicit_none, "since_id=None")

    def test_zero_new_rows_returns_starting_state(self, tmp_path):
        """(c) starting_state with since_id past every row returns it unchanged."""
        tt, row_id = self._seed_rows(tmp_path, self._ROWS)
        with SRSDatabase(str(tt)) as db:
            full = db.rebuild_from_revlog(row_id, Direction.RECOGNITION, anki_card_id=self._CARD_ID)
            max_id = max(r[0] for r in self._ROWS)
            incremental = db.rebuild_from_revlog(
                row_id,
                Direction.RECOGNITION,
                anki_card_id=self._CARD_ID,
                starting_state=full,
                since_id=max_id,
            )
        _assert_state_eq(full, incremental, "zero-new-rows identity")

    def test_starting_state_plus_one_row_matches_full(self, tmp_path):
        """(d) prefix-replay(N-1) then +1 row == full replay from NEW."""
        prefix_rows = self._ROWS[:-1]
        last_row = self._ROWS[-1]
        tt_prefix, prefix_id = self._seed_rows(tmp_path, prefix_rows, name="prefix.db", text="prefix")
        tt_full, full_id = self._seed_rows(tmp_path, self._ROWS, name="full.db", text="full")

        with SRSDatabase(str(tt_prefix)) as db:
            s_prefix = db.rebuild_from_revlog(prefix_id, Direction.RECOGNITION, anki_card_id=self._CARD_ID)
        with SRSDatabase(str(tt_full)) as db:
            full = db.rebuild_from_revlog(full_id, Direction.RECOGNITION, anki_card_id=self._CARD_ID)
            incremental = db.rebuild_from_revlog(
                full_id,
                Direction.RECOGNITION,
                anki_card_id=self._CARD_ID,
                starting_state=s_prefix,
                since_id=prefix_rows[-1][0],
            )
        # Sanity: exactly one row (the last) is newer than the split point.
        assert last_row[0] > prefix_rows[-1][0]
        _assert_state_eq(full, incremental, "starting_state + 1 row")

    def test_starting_state_plus_n_rows_matches_full(self, tmp_path):
        """(e) prefix-replay(2) then +3 rows == full replay from NEW."""
        split = 2
        prefix_rows = self._ROWS[:split]
        tt_prefix, prefix_id = self._seed_rows(tmp_path, prefix_rows, name="prefix.db", text="prefix")
        tt_full, full_id = self._seed_rows(tmp_path, self._ROWS, name="full.db", text="full")

        with SRSDatabase(str(tt_prefix)) as db:
            s_prefix = db.rebuild_from_revlog(prefix_id, Direction.RECOGNITION, anki_card_id=self._CARD_ID)
        with SRSDatabase(str(tt_full)) as db:
            full = db.rebuild_from_revlog(full_id, Direction.RECOGNITION, anki_card_id=self._CARD_ID)
            incremental = db.rebuild_from_revlog(
                full_id,
                Direction.RECOGNITION,
                anki_card_id=self._CARD_ID,
                starting_state=s_prefix,
                since_id=prefix_rows[-1][0],
            )
        _assert_state_eq(full, incremental, "starting_state + N rows")

    def test_since_id_filters_rows_below_threshold(self, tmp_path):
        """(b) since_id excludes rows at or below the threshold from the walk.

        With starting_state already past the prefix, a since_id set ABOVE some of
        the suffix rows must skip them — proving the filter is ``id > since_id``,
        not a no-op.
        """
        tt, row_id = self._seed_rows(tmp_path, self._ROWS)
        with SRSDatabase(str(tt)) as db:
            full = db.rebuild_from_revlog(row_id, Direction.RECOGNITION, anki_card_id=self._CARD_ID)
            # Replay from `full` with since_id = max_id skips everything (identity);
            # with since_id below the last row, it would re-walk the last row and
            # diverge. Pin that the threshold is exclusive and effective.
            second_last = sorted(r[0] for r in self._ROWS)[-2]
            walks_last = db.rebuild_from_revlog(
                row_id,
                Direction.RECOGNITION,
                anki_card_id=self._CARD_ID,
                starting_state=full,
                since_id=second_last,
            )
        # full already includes the last row; re-walking it from `full` changes state.
        assert walks_last.reps == full.reps + 1, "since_id=second_last must re-walk exactly the last row"


class TestRebuildAnkiLinked:
    """Anki-linked directions: MATCH and REPAIR paths."""

    def test_repair_then_match(self, tmp_path):
        revlog_rows = [
            (1700000000001, 3, 30, 10, 0, 1200, 1),
            (1700008640001, 3, 60, 30, 0, 1000, 1),
        ]
        anki, tt, card_id, row_id = _make_anki_linked_scenario(tmp_path, revlog_rows)

        result = replay_fsrs_from_revlog(tt, anki, dry_run=False)
        assert result["buckets"]["REPAIR"] == 1, "Stale state should be REPAIR"
        assert result["errors"] == []

        result2 = replay_fsrs_from_revlog(tt, anki, dry_run=True)
        assert result2["buckets"]["MATCH"] == 1, "After repair, state should MATCH"

    def test_repair_preserves_non_fsrs_fields(self, tmp_path):
        revlog_rows = [
            (1700000000001, 3, 30, 10, 0, 1200, 1),
            (1700008640001, 3, 60, 30, 0, 1000, 1),
        ]
        anki, tt, card_id, row_id = _make_anki_linked_scenario(tmp_path, revlog_rows)

        conn = sqlite3.connect(str(tt))
        conn.execute(
            "UPDATE collocation_directions SET "
            "dirty_fsrs = 1, "
            "last_synced_at = '2024-01-01T00:00:00+00:00', "
            "anki_due = 123, "
            "anki_card_mod = 456, "
            "bury_kind = 'user', "
            "prior_state = 'review', "
            "prior_left = 2, "
            "prior_stability = 3.0, "
            "introduced_at = '2024-01-01T00:00:00+00:00' "
            "WHERE collocation_id = ? AND direction = 'recognition'",
            (row_id,),
        )
        conn.commit()
        conn.close()

        replay_fsrs_from_revlog(tt, anki, dry_run=False)

        conn = _open_tt(tt)
        stored = conn.execute(
            "SELECT dirty_fsrs, last_synced_at, anki_due, anki_card_mod, bury_kind, "
            "prior_state, prior_left, prior_stability, introduced_at "
            "FROM collocation_directions "
            "WHERE collocation_id = ? AND direction = 'recognition'",
            (row_id,),
        ).fetchone()
        conn.close()

        assert stored["dirty_fsrs"] == 1
        assert stored["last_synced_at"] is not None
        assert stored["anki_due"] == 123
        assert stored["anki_card_mod"] == 456
        assert stored["bury_kind"] == "user"
        assert stored["prior_state"] == "review"
        assert stored["prior_left"] == 2
        assert stored["prior_stability"] == 3.0
        assert stored["introduced_at"] is not None


class TestRebuildTtOnly:
    """TT-only directions with synthetic rows."""

    def test_skip_synthetic_only(self, tmp_path):
        tt = _build_tt_db(tmp_path)
        row_id = seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=None,
            reps=3,
            state=SRSState.REVIEW,
            stability=5.0,
            difficulty=5.0,
            last_review=datetime(2024, 1, 4, 4, 0, tzinfo=UTC),
            last_review_time_ms=1700000001000,
            last_rating=3,
        )
        _add_revlog_to_tt(
            tt,
            row_id,
            "recognition",
            [
                (1700000001001, 3, 0, 0, 0, 0, 4),
            ],
        )

        with SRSDatabase(str(tt)) as db:
            replayed = db.rebuild_from_revlog(
                row_id,
                Direction.RECOGNITION,
            )
        assert replayed.state == SRSState.NEW, "Synthetic-only direction should replay to NEW"
        assert replayed.reps == 0


class TestRebuildPreFSRS:
    """Pre-FSRS SM2-era row detection."""

    def test_detects_pre_fsrs_factor(self, tmp_path):
        revlog_rows = [
            (1700000000001, 3, 30, 10, 2500, 1200, 1),
            (1700008640001, 3, 60, 30, 0, 1000, 1),
        ]
        anki, tt, card_id, row_id = _make_anki_linked_scenario(tmp_path, revlog_rows)

        from app.plugins.anki_sync.replay_fsrs_from_revlog import _has_pre_fsrs_rows

        conn = _open_tt(tt)
        assert _has_pre_fsrs_rows(conn, row_id, "recognition")
        conn.close()


class TestReplayBuckets:
    """End-to-end bucket classification through the script."""

    def test_e2e_skip_pre_fsrs_bucket(self, tmp_path):
        revlog_rows = [
            (1700000000001, 3, 30, 10, 2500, 1200, 1),
            (1700008640001, 3, 60, 30, 0, 1000, 1),
        ]
        anki, tt, card_id, row_id = _make_anki_linked_scenario(tmp_path, revlog_rows)

        result = replay_fsrs_from_revlog(tt, anki, dry_run=True)
        assert result["buckets"]["SKIP_PRE_FSRS"] == 1

    def test_e2e_skip_synthetic_only_bucket(self, tmp_path):
        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        row_id = seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=None,
            reps=3,
            state=SRSState.REVIEW,
            stability=5.0,
        )
        _add_revlog_to_tt(
            tt,
            row_id,
            "recognition",
            [
                (1700000001001, 3, 0, 0, 0, 0, 4),
            ],
        )

        result = replay_fsrs_from_revlog(tt, anki, dry_run=True)
        assert result["buckets"]["SKIP_SYNTHETIC_ONLY"] == 1

    def test_e2e_skip_unknown_divergence_bucket(self, tmp_path):
        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        row_id = seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=None,
            reps=2,
            state=SRSState.REVIEW,
            stability=5.0,
        )
        # A TT-only direction with non-synthetic revlog that doesn't match stored
        _add_revlog_to_tt(
            tt,
            row_id,
            "recognition",
            [
                (1700000000001, 3, 0, 0, 0, 0, 1),
                (1700008640001, 1, 0, 0, 0, 0, 1),
            ],
        )

        result = replay_fsrs_from_revlog(tt, anki, dry_run=True)
        assert result["buckets"]["SKIP_UNKNOWN_DIVERGENCE"] == 1

    def test_e2e_repair_bucket(self, tmp_path):
        revlog_rows = [
            (1700000000001, 3, 30, 10, 0, 1200, 1),
            (1700008640001, 3, 60, 30, 0, 1000, 1),
        ]
        anki, tt, card_id, row_id = _make_anki_linked_scenario(tmp_path, revlog_rows)

        result = replay_fsrs_from_revlog(tt, anki, dry_run=True)
        assert result["buckets"]["REPAIR"] == 1


class TestStatesMatch:
    """Direct tests for _parse_stored_due and _states_match branches."""

    def test_parse_stored_due_none_and_invalid(self):
        assert _parse_stored_due(None) is None
        assert _parse_stored_due("not-a-date") is None

    @staticmethod
    def _make_stored(**overrides) -> dict:
        defaults = dict(
            stability=5.0,
            fsrs_difficulty=5.0,
            reps=10,
            lapses=0,
            state="review",
            due_at=datetime(2024, 1, 15, 4, 0, tzinfo=UTC).isoformat(),
            last_review=datetime(2024, 1, 15, 4, 0, tzinfo=UTC).isoformat(),
        )
        defaults.update(overrides)
        return defaults

    @staticmethod
    def _make_replayed(**overrides) -> DirectionState:
        defaults = dict(
            direction=Direction.RECOGNITION,
            due_at=datetime(2024, 1, 15, 4, 0, tzinfo=UTC),
            stability=5.0,
            difficulty=5.0,
            reps=10,
            lapses=0,
            state=SRSState.REVIEW,
            last_review=datetime(2024, 1, 15, 4, 0, tzinfo=UTC),
        )
        defaults.update(overrides)
        return DirectionState(**defaults)

    def test_states_match_exact_match(self):
        assert _states_match(self._make_stored(), self._make_replayed())

    def test_states_match_stability_mismatch(self):
        assert not _states_match(
            self._make_stored(stability=5.1),
            self._make_replayed(),
        )

    def test_states_match_difficulty_mismatch(self):
        assert not _states_match(
            self._make_stored(),
            self._make_replayed(difficulty=7.5),
        )

    def test_states_match_lapses_mismatch(self):
        assert not _states_match(
            self._make_stored(lapses=5),
            self._make_replayed(),
        )

    def test_states_match_state_mismatch(self):
        assert not _states_match(
            self._make_stored(state="learning"),
            self._make_replayed(),
        )

    def test_states_match_due_at_mismatch(self):
        assert not _states_match(
            self._make_stored(due_at=datetime(2024, 1, 10, 4, 0, tzinfo=UTC).isoformat()),
            self._make_replayed(due_at=datetime(2024, 1, 15, 4, 0, tzinfo=UTC)),
        )

    def test_states_match_last_review_mismatch(self):
        assert not _states_match(
            self._make_stored(last_review=datetime(2024, 1, 12, 4, 0, tzinfo=UTC).isoformat()),
            self._make_replayed(last_review=datetime(2024, 1, 14, 4, 0, tzinfo=UTC)),
        )

    def test_states_match_due_at_none(self):
        assert not _states_match(
            self._make_stored(due_at=None),
            self._make_replayed(),
        ), "None due_at should not match"

    def test_states_match_both_last_review_null(self):
        """Both stored.last_review and replayed.last_review null → match by absence."""
        assert _states_match(
            self._make_stored(last_review=None),
            self._make_replayed(last_review=None),
        )

    def test_states_match_last_review_one_null(self):
        """One null and one set → mismatch (both directions)."""
        assert not _states_match(
            self._make_stored(last_review=None),
            self._make_replayed(),
        )
        assert not _states_match(
            self._make_stored(),
            self._make_replayed(last_review=None),
        )


class TestReplayScript:
    """Script-level CLI tests."""

    def test_dry_run_does_not_write(self, tmp_path):
        anki = _create_minimal_anki_db(tmp_path)
        conn = sqlite3.connect(str(anki))
        conn.execute("INSERT INTO notes VALUES (1001, 'guid_a', 0, 0, 0, '', 'text', 'text', 0, 0, '')")
        conn.execute(
            "INSERT INTO cards VALUES (10001, 1001, 0, 0, 0, 0, 2, 2, 10, 21, 0, 5, 0, 0, 0, 0, 0, '{}')",
        )
        _add_revlog_to_anki(conn, 10001, [(1700000000001, 3, 30, 10, 0, 1200, 1)])
        conn.commit()
        conn.close()

        tt = _build_tt_db(tmp_path)
        row_id = seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=10001,
            reps=2,
            state=SRSState.REVIEW,
            stability=5.0,
        )
        _add_revlog_to_tt(
            tt,
            row_id,
            "recognition",
            [
                (1700000000001, 3, 30, 10, 0, 1200, 1),
            ],
            anki_card_id=10001,
        )

        conn = _open_tt(tt)
        before = dict(
            conn.execute(
                "SELECT * FROM collocation_directions WHERE collocation_id = ? AND direction = 'recognition'",
                (row_id,),
            ).fetchone()
        )
        conn.close()

        result = replay_fsrs_from_revlog(tt, anki, dry_run=True)
        assert result["errors"] == []

        conn = _open_tt(tt)
        after = dict(
            conn.execute(
                "SELECT * FROM collocation_directions WHERE collocation_id = ? AND direction = 'recognition'",
                (row_id,),
            ).fetchone()
        )
        conn.close()

        assert before == after, "dry_run must not modify any columns"

    def test_main_cli_dry_run(self, tmp_path, monkeypatch):
        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=None,
        )
        monkeypatch.setattr("app.config.settings.anki_collection_path", str(anki))
        monkeypatch.setattr("app.config.settings.database_url", f"sqlite:///{tt}")

        from app.plugins.anki_sync.replay_fsrs_from_revlog import main

        assert main(["--dry-run"]) == 0

    def test_main_cli_with_orphan_error(self, tmp_path, monkeypatch, capsys):
        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=None,
            reps=1,
            state=SRSState.REVIEW,
        )
        monkeypatch.setattr("app.config.settings.anki_collection_path", str(anki))
        monkeypatch.setattr("app.config.settings.database_url", f"sqlite:///{tt}")

        from app.plugins.anki_sync.replay_fsrs_from_revlog import main

        assert main(["--dry-run"]) == 0
        captured = capsys.readouterr()
        assert "WARNING:" in captured.out

    def test_warns_when_fsrs_params_unavailable(self, tmp_path, caplog):
        """Minimal Anki DB has no usable deck_config; resolve_fsrs_params falls back
        to default and the script must emit a warning."""
        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        seed_direction(SRSDatabase(str(tt)), text="test", anki_card_id=None)

        caplog.set_level("WARNING", logger="app.plugins.anki_sync.replay_fsrs_from_revlog")
        replay_fsrs_from_revlog(tt, anki, dry_run=True)

        assert any("Using default FSRS params" in record.message for record in caplog.records), (
            "warning must fire when FSRS params cache is empty"
        )

    def test_no_warning_when_fsrs_params_cached(self, tmp_path, caplog):
        """Pre-populated cache → resolve_fsrs_params returns 'cache' source; no warning."""
        import json

        from app.srs.fsrs import _DEFAULT_WEIGHTS

        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        db = SRSDatabase(str(tt))
        seed_direction(db, text="test", anki_card_id=None)
        db.set_anki_state_cache(
            "fsrs_params",
            json.dumps({"weights": list(_DEFAULT_WEIGHTS), "desired_retention": 0.9}),
        )

        caplog.set_level("WARNING", logger="app.plugins.anki_sync.replay_fsrs_from_revlog")
        replay_fsrs_from_revlog(tt, anki, dry_run=True)

        assert not any("Using default FSRS params" in record.message for record in caplog.records), (
            "warning must NOT fire when cache is populated"
        )


class TestReplayConcurrency:
    """sqlite3.OperationalError abort path."""

    def test_busy_db_raises_system_exit(self, tmp_path):
        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        row_id = seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=None,
            reps=1,
            state=SRSState.REVIEW,
        )
        _add_revlog_to_tt(
            tt,
            row_id,
            "recognition",
            [
                (1700000000001, 3, 0, 0, 0, 0, 1),
            ],
        )

        blocker = sqlite3.connect(str(tt))
        blocker.execute("PRAGMA busy_timeout = 0")
        blocker.execute("BEGIN IMMEDIATE")

        with pytest.raises(SystemExit, match="Backend appears live"):
            replay_fsrs_from_revlog(tt, anki, dry_run=False)

        blocker.close()


class TestReplayValidation:
    """Validation query catches anomalies."""

    def test_orphan_detection(self, tmp_path):
        anki = _create_minimal_anki_db(tmp_path)
        tt = _build_tt_db(tmp_path)
        seed_direction(
            SRSDatabase(str(tt)),
            text="test",
            anki_card_id=None,
            reps=1,
            state=SRSState.REVIEW,
        )
        result = replay_fsrs_from_revlog(tt, anki, dry_run=True)
        orphan_errors = [e for e in result["errors"] if "Orphan" in e]
        assert len(orphan_errors) == 1

    def test_row_count_mismatch_warning(self, tmp_path):
        """Anki-linked direction with mismatched revlog row counts triggers warning."""
        revlog_rows = [
            (1700000000001, 3, 30, 10, 0, 1200, 1),
            (1700008640001, 3, 60, 30, 0, 1000, 1),
        ]
        anki, tt, card_id, row_id = _make_anki_linked_scenario(tmp_path, revlog_rows)

        # Add an extra TT revlog row the Anki side doesn't have
        extra_id = 1700017280001
        conn = sqlite3.connect(str(tt))
        conn.execute(
            "INSERT INTO tt_revlog (id, collocation_id, direction, button_chosen, "
            "interval, last_interval, factor, taken_millis, review_kind, anki_card_id) "
            "VALUES (?, ?, 'recognition', 3, 30, 10, 0, 1000, 1, ?)",
            (extra_id, row_id, card_id),
        )
        conn.commit()
        conn.close()

        result = replay_fsrs_from_revlog(tt, anki, dry_run=True)
        mismatch_errors = [e for e in result["errors"] if "Row count mismatch" in e and "tt_revlog=3" in e]
        assert len(mismatch_errors) == 1
