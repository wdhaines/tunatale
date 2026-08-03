"""Repair a collocation whose history crossed onto another Anki card.

When two Anki notes collapse to one TT guid (same text + POS), a collocation
can bind to either card. `foran` alternated for three weeks, so its `tt_revlog`
accumulated the twin's rows and its FSRS fields drifted onto the twin's grade.

Neither sync direction fixes this: `_tt_memory_newer` keeps TT's values when
TT's `last_review` is newer than Anki's `lrt` (pull declines), and `dirty_fsrs=0`
means push never fires — the two-way deadlock documented for the 2026-06-29
forced-download incident. Manual re-anchor to Anki is required.

In-memory / tmp_path DBs only — never a real ``collection.anki2`` (conftest's
``fake_anki_db`` fixture, per `.claude/rules/testing.md`).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.anki_archive.reanchor_crossed_collocation import CrossedRepair, apply_repair, plan_repair

SURV, TWIN = 300233, 305379
_OP = CrossedRepair(collocation_id=1478, direction="recognition", survivor_card_id=SURV)


def _anki_conn(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl,"
        " factor, reps, lapses, left, odue, odid, flags, data)"
        " VALUES (?, 1, 12345, 0, 0, 0, 2, 2, 4594, 3, 0, 45, 7, 0, 0, 0, 0, ?)",
        (SURV, json.dumps({"s": 2.8794, "d": 9.642, "lrt": 1785545939})),
    )
    for rid in (1000, 2000, 1785545939325):
        conn.execute(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type)"
            " VALUES (?, ?, 0, 3, 3, 1, 0, 0, 1)",
            (rid, SURV),
        )
    for rid in (3000, 4000, 1785633176382):
        conn.execute(
            "INSERT INTO revlog (id, cid, usn, ease, ivl, lastIvl, factor, time, type)"
            " VALUES (?, ?, 0, 3, 1, 3, 0, 0, 1)",
            (rid, TWIN),
        )
    conn.commit()
    return conn


def _tt_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE collocation_directions (collocation_id INTEGER, direction TEXT,"
        " anki_card_id INTEGER, stability REAL, fsrs_difficulty REAL, last_review TEXT,"
        " last_review_time_ms INTEGER, last_rating INTEGER, dirty_fsrs INTEGER)"
    )
    conn.execute("CREATE TABLE tt_revlog (id INTEGER PRIMARY KEY, collocation_id INTEGER, direction TEXT)")
    conn.execute("CREATE TABLE anki_state_cache (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute(
        "INSERT INTO collocation_directions VALUES (1478, 'recognition', ?, 3.0981, 9.629,"
        " '2026-08-02T01:12:33.246986+00:00', 0, NULL, 0)",
        (SURV,),
    )
    for rid in (1000, 2000, 1785545939325, 3000, 4000, 1785633176382):
        conn.execute("INSERT INTO tt_revlog VALUES (?, 1478, 'recognition')", (rid,))
    conn.execute("INSERT INTO anki_state_cache VALUES ('session_main_queue', '{\"day\": \"2026-08-02\"}')")
    conn.commit()
    return conn


class TestPlanRepair:
    def test_selects_only_rows_anki_attributes_elsewhere(self, fake_anki_db):
        plan = plan_repair(_tt_conn(), _anki_conn(fake_anki_db), [_OP])[0]
        assert sorted(plan.stray_revlog_ids) == [3000, 4000, 1785633176382]

    def test_reads_the_anchor_from_anki_card_data(self, fake_anki_db):
        plan = plan_repair(_tt_conn(), _anki_conn(fake_anki_db), [_OP])[0]
        assert plan.stability == pytest.approx(2.8794)
        assert plan.difficulty == pytest.approx(9.642)
        assert plan.last_review == "2026-08-01T00:58:59+00:00"

    def test_refuses_when_direction_points_at_another_card(self, fake_anki_db):
        tt = _tt_conn()
        tt.execute("UPDATE collocation_directions SET anki_card_id = ?", (TWIN,))
        tt.commit()
        with pytest.raises(ValueError, match="points at card 305379, not survivor 300233"):
            plan_repair(tt, _anki_conn(fake_anki_db), [_OP])

    def test_refuses_when_survivor_card_is_missing(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        anki.execute("DELETE FROM cards WHERE id = ?", (SURV,))
        anki.commit()
        with pytest.raises(ValueError, match="survivor card 300233 not found"):
            plan_repair(_tt_conn(), anki, [_OP])


class TestApplyRepair:
    def test_prunes_stray_rows_and_keeps_the_rest(self, fake_anki_db):
        tt, anki = _tt_conn(), _anki_conn(fake_anki_db)
        apply_repair(tt, plan_repair(tt, anki, [_OP]))
        remaining = sorted(r["id"] for r in tt.execute("SELECT id FROM tt_revlog WHERE collocation_id = 1478"))
        assert remaining == [1000, 2000, 1785545939325]

    def test_reanchors_fsrs_fields_to_anki(self, fake_anki_db):
        tt, anki = _tt_conn(), _anki_conn(fake_anki_db)
        apply_repair(tt, plan_repair(tt, anki, [_OP]))
        row = tt.execute("SELECT * FROM collocation_directions WHERE collocation_id = 1478").fetchone()
        assert row["stability"] == pytest.approx(2.8794)
        assert row["fsrs_difficulty"] == pytest.approx(9.642)
        assert row["last_review"] == "2026-08-01T00:58:59+00:00"
        assert row["last_review_time_ms"] == 0
        assert row["last_rating"] is None

    def test_clears_the_frozen_queue(self, fake_anki_db):
        """Queue order is cached; without this the old order replays until sync."""
        tt, anki = _tt_conn(), _anki_conn(fake_anki_db)
        apply_repair(tt, plan_repair(tt, anki, [_OP]))
        assert tt.execute("SELECT COUNT(*) FROM anki_state_cache WHERE key='session_main_queue'").fetchone()[0] == 0

    def test_is_idempotent(self, fake_anki_db):
        tt, anki = _tt_conn(), _anki_conn(fake_anki_db)
        apply_repair(tt, plan_repair(tt, anki, [_OP]))
        counts = apply_repair(tt, plan_repair(tt, anki, [_OP]))
        assert counts == {"revlog_rows_pruned": 0, "directions_reanchored": 1}
        remaining = sorted(r["id"] for r in tt.execute("SELECT id FROM tt_revlog WHERE collocation_id = 1478"))
        assert remaining == [1000, 2000, 1785545939325]
