"""One-shot repositioning of already-minted production cards (Layer 83, tunatale-uze6).

Every production card TunaTale had minted sat at `MAX(due)+1` — the tail — where the
Norwegian deck's DECK gather arrives last, so none had ever been introduced. The
allocator is fixed for new mints; this module moves the ones that already exist.

What these tests cover:
- Band slots are assigned in NOTE id order, which is deck (frequency) order for the
  imported deck. The fixture gives note-id order and card-id order that DISAGREE,
  because card id correlates the other way (+0.509 vs zipf) and a fixture where they
  agree cannot tell the two apart.
- Idempotence: a second run moves nothing and leaves `col.mod` alone.
- Cloze notes (the non-imageable production branch) are included; recognition cards
  and already-graduated production cards are not.
- Deck scoping: another deck's production cards are untouched.
- The mutation contract — `usn = -1` and a fresh `mod` per row, one `col.mod` bump.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.plugins.anki_sync.reposition_production_cards import (
    apply_repositioning,
    mirror_positions_to_tt,
    plan_repositioning,
)
from app.plugins.anki_sync.sync_writer import _PRODUCTION_BAND_CEILING, _PRODUCTION_BAND_FLOOR
from app.srs.database import SRSDatabase

DECK_ID = 1
OTHER_DECK_ID = 2
VOCAB_MID = 1000
CLOZE_MID = 2000
TAIL = 1_000_287

_SCHEMA = """
CREATE TABLE col (id INTEGER PRIMARY KEY, mod INTEGER, scm INTEGER, usn INTEGER);
CREATE TABLE notes (id INTEGER PRIMARY KEY, mid INTEGER, mod INTEGER, usn INTEGER);
CREATE TABLE cards (
    id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER,
    mod INTEGER, usn INTEGER, type INTEGER, queue INTEGER, due INTEGER);
CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.execute("INSERT INTO col VALUES (1, 100, 5, 0)")
    conn.execute("INSERT INTO notetypes VALUES (?, 'Norwegian Vocabulary')", (VOCAB_MID,))
    conn.execute("INSERT INTO notetypes VALUES (?, 'Cloze')", (CLOZE_MID,))
    conn.execute("INSERT INTO templates VALUES (?, 0, 'Recognition')", (VOCAB_MID,))
    conn.execute("INSERT INTO templates VALUES (?, 1, 'Production')", (VOCAB_MID,))
    conn.execute("INSERT INTO templates VALUES (?, 0, 'Cloze')", (CLOZE_MID,))
    return conn


def _vocab_note(conn: sqlite3.Connection, note_id: int, card_id: int, *, due: int, did: int = DECK_ID) -> int:
    """A note with a graduated recognition card and a NEW production card."""
    conn.execute("INSERT INTO notes VALUES (?, ?, 100, 7)", (note_id, VOCAB_MID))
    conn.execute(
        "INSERT INTO cards VALUES (?, ?, ?, 0, 100, 7, 2, 2, 900)",
        (card_id, note_id, did),
    )
    conn.execute(
        "INSERT INTO cards VALUES (?, ?, ?, 1, 100, 7, 0, 0, ?)",
        (card_id + 1, note_id, did, due),
    )
    return card_id + 1


def _cloze_note(conn: sqlite3.Connection, note_id: int, card_id: int, *, due: int) -> int:
    conn.execute("INSERT INTO notes VALUES (?, ?, 100, 7)", (note_id, CLOZE_MID))
    conn.execute("INSERT INTO cards VALUES (?, ?, ?, 0, 100, 7, 0, 0, ?)", (card_id, note_id, DECK_ID, due))
    return card_id


def _due_of(conn: sqlite3.Connection, card_id: int) -> int:
    return conn.execute("SELECT due FROM cards WHERE id = ?", (card_id,)).fetchone()["due"]


class TestPlanRepositioning:
    def test_assigns_band_slots_in_note_id_order_not_card_id_order(self):
        """Note id is deck/frequency order for the imported deck; card id correlates
        the other way. The fixture makes them disagree so the two are separable — one
        where they agree passes either way and proves nothing."""
        conn = _conn()
        # Note ids ascend while card ids descend.
        first = _vocab_note(conn, note_id=10, card_id=900, due=TAIL + 2)
        second = _vocab_note(conn, note_id=20, card_id=700, due=TAIL + 1)
        third = _vocab_note(conn, note_id=30, card_id=500, due=TAIL)

        plan = plan_repositioning(conn, DECK_ID)

        assert plan.moves == [
            (first, _PRODUCTION_BAND_FLOOR),
            (second, _PRODUCTION_BAND_FLOOR + 1),
            (third, _PRODUCTION_BAND_FLOOR + 2),
        ]

    def test_includes_cloze_notes_and_excludes_recognition_and_graduated_cards(self):
        conn = _conn()
        prod = _vocab_note(conn, note_id=10, card_id=900, due=TAIL)
        cloze = _cloze_note(conn, note_id=1_700_000_000_000, card_id=950, due=TAIL + 1)
        # A production card that is no longer NEW: already introduced, leave it alone.
        _vocab_note(conn, note_id=40, card_id=800, due=TAIL + 5)
        conn.execute("UPDATE cards SET type = 2, queue = 2 WHERE id = 801")

        plan = plan_repositioning(conn, DECK_ID)

        assert [card_id for card_id, _ in plan.moves] == [prod, cloze]

    def test_ignores_other_decks(self):
        conn = _conn()
        mine = _vocab_note(conn, note_id=10, card_id=900, due=TAIL)
        _vocab_note(conn, note_id=20, card_id=700, due=TAIL + 1, did=OTHER_DECK_ID)

        plan = plan_repositioning(conn, DECK_ID)

        assert [card_id for card_id, _ in plan.moves] == [mine]

    def test_cards_already_in_their_slot_are_counted_not_moved(self):
        conn = _conn()
        _vocab_note(conn, note_id=10, card_id=900, due=_PRODUCTION_BAND_FLOOR)
        second = _vocab_note(conn, note_id=20, card_id=700, due=TAIL)

        plan = plan_repositioning(conn, DECK_ID)

        assert plan.already_placed == 1
        assert plan.moves == [(second, _PRODUCTION_BAND_FLOOR + 1)]
        assert plan.total == 2

    def test_raises_when_the_band_cannot_hold_them(self):
        """Band bounds are parameters, so the guard is reachable without patching a
        module global — which the mock-boundary check rejects, rightly: the seam here
        is the function's own signature."""
        conn = _conn()
        for i in range(3):
            _vocab_note(conn, note_id=10 + i, card_id=900 - i * 10, due=TAIL + i)

        with pytest.raises(ValueError, match="exceed the band"):
            plan_repositioning(conn, DECK_ID, band_floor=-2, band_ceiling=0)


class TestApplyRepositioning:
    def test_writes_positions_with_usn_minus_one_and_bumps_col_mod(self):
        conn = _conn()
        first = _vocab_note(conn, note_id=10, card_id=900, due=TAIL)
        second = _vocab_note(conn, note_id=20, card_id=700, due=TAIL + 1)
        before_mod = conn.execute("SELECT mod FROM col").fetchone()["mod"]

        apply_repositioning(conn, plan_repositioning(conn, DECK_ID))

        assert _due_of(conn, first) == _PRODUCTION_BAND_FLOOR
        assert _due_of(conn, second) == _PRODUCTION_BAND_FLOOR + 1
        rows = conn.execute("SELECT usn, mod FROM cards WHERE id IN (?, ?)", (first, second)).fetchall()
        assert [r["usn"] for r in rows] == [-1, -1]
        assert all(r["mod"] > 100 for r in rows)
        assert conn.execute("SELECT mod FROM col").fetchone()["mod"] != before_mod

    def test_never_touches_col_usn(self):
        """col.usn is the sync ANCHOR, not a dirty flag — clobbering it forces a full
        sync the moment another device advances the server's USN (Layer 61)."""
        conn = _conn()
        _vocab_note(conn, note_id=10, card_id=900, due=TAIL)

        apply_repositioning(conn, plan_repositioning(conn, DECK_ID))

        assert conn.execute("SELECT usn FROM col").fetchone()["usn"] == 0

    def test_leaves_the_recognition_sibling_alone(self):
        conn = _conn()
        _vocab_note(conn, note_id=10, card_id=900, due=TAIL)

        apply_repositioning(conn, plan_repositioning(conn, DECK_ID))

        rec = conn.execute("SELECT due, usn FROM cards WHERE id = 900").fetchone()
        assert (rec["due"], rec["usn"]) == (900, 7)

    def test_a_second_run_is_a_no_op(self):
        """Idempotence, and specifically that a no-op run does not bump col.mod — a
        gratuitous bump looks like a change to the next sync."""
        conn = _conn()
        _vocab_note(conn, note_id=10, card_id=900, due=TAIL)
        apply_repositioning(conn, plan_repositioning(conn, DECK_ID))
        settled_mod = conn.execute("SELECT mod FROM col").fetchone()["mod"]

        second = plan_repositioning(conn, DECK_ID)
        apply_repositioning(conn, second)

        assert second.moves == []
        assert second.already_placed == 1
        assert conn.execute("SELECT mod FROM col").fetchone()["mod"] == settled_mod


class TestMirrorPositionsToTt:
    def test_points_the_tt_mirror_at_the_new_positions(self):
        """Without this TunaTale keeps serving the old order until the next
        sync_pull, which on a queue-position change reads as "the fix did nothing"."""
        db = SRSDatabase(":memory:")
        conn = _conn()
        card_id = _vocab_note(conn, note_id=10, card_id=900, due=TAIL)
        with db._get_conn() as tt_conn:
            tt_conn.execute(
                "INSERT INTO collocations (id, text, translation, language_code) VALUES (1, 'x', 'y', 'sl')"
            )
            tt_conn.execute(
                "INSERT INTO collocation_directions (collocation_id, direction, due_at, anki_card_id, anki_due) "
                "VALUES (1, 'production', '2026-01-01T04:00:00+00:00', ?, ?)",
                (card_id, TAIL),
            )
            tt_conn.commit()

        plan = plan_repositioning(conn, DECK_ID)
        with db._get_conn() as tt_conn:
            updated = mirror_positions_to_tt(tt_conn, plan)
            tt_conn.commit()

        assert updated == 1
        with db._get_conn() as tt_conn:
            row = tt_conn.execute("SELECT anki_due FROM collocation_directions WHERE anki_card_id = ?", (card_id,))
            assert row.fetchone()["anki_due"] == _PRODUCTION_BAND_FLOOR

    def test_reports_zero_when_no_mirror_row_matches(self):
        db = SRSDatabase(":memory:")
        conn = _conn()
        _vocab_note(conn, note_id=10, card_id=900, due=TAIL)
        plan = plan_repositioning(conn, DECK_ID)

        with db._get_conn() as tt_conn:
            assert mirror_positions_to_tt(tt_conn, plan) == 0


assert _PRODUCTION_BAND_CEILING > _PRODUCTION_BAND_FLOOR  # band sanity, imported for the guard test
