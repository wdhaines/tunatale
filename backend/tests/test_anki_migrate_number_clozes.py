"""Retiring the number words' cloze production cards (tunatale-y27g).

A cloze on a numeral is a broken card — ``jeg har ___ barn`` admits every number.
The routing fix serves new promotions only; words already clozed sit permanently
outside the promotion queue because ``_AWAITING_PRODUCTION_WHERE`` excludes any
word a cloze points at. This migration removes the cloze so the base word
re-enters, and carries the cloze card's scheduling onto its replacement.

What these tests are about:
- ``is_inheritable`` reads ``state``/``reps``, never the FSRS numbers, because
  those columns DEFAULT to 1.0/5.0 rather than NULL. Four of the eight real
  words are ``state='new'`` and must inherit NOTHING.
- The Anki delete goes through ``graves`` with ``usn=-1``, bumps ``col.mod``,
  and leaves ``col.usn`` and ``col.scm`` alone.
- The state capture happens BEFORE the TT delete, because the FK cascade takes
  the direction row with the collocation.
- Step 2 refuses politely when the mint has not reached the word yet.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.plugins.anki_sync.migrate_number_clozes import (
    InheritedState,
    apply_inheritance,
    bump_col_mod,
    delete_tt_clozes,
    grave_cloze_note,
    is_inheritable,
    plan_migration,
    state_from_json,
    state_to_json,
)

_ANKI_SCHEMA = """
CREATE TABLE col (id INTEGER PRIMARY KEY, mod INTEGER, scm INTEGER, usn INTEGER);
CREATE TABLE notes (id INTEGER PRIMARY KEY, mid INTEGER, flds TEXT, mod INTEGER, usn INTEGER);
CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER, usn INTEGER);
CREATE TABLE graves (oid INTEGER NOT NULL, type INTEGER NOT NULL, usn INTEGER NOT NULL, PRIMARY KEY (oid, type));
"""

_TT_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE collocations (
    id INTEGER PRIMARY KEY, text TEXT, card_type TEXT, anki_note_id INTEGER,
    base_collocation_id INTEGER REFERENCES collocations(id) ON DELETE CASCADE);
CREATE TABLE collocation_directions (
    collocation_id INTEGER REFERENCES collocations(id) ON DELETE CASCADE,
    direction TEXT, stability REAL DEFAULT 1.0, fsrs_difficulty REAL DEFAULT 5.0,
    due_at TEXT, reps INTEGER DEFAULT 0, lapses INTEGER DEFAULT 0,
    state TEXT DEFAULT 'new', last_review TEXT, last_review_time_ms INTEGER,
    last_rating INTEGER, "left" INTEGER, prior_state TEXT, prior_left INTEGER,
    prior_stability REAL, introduced_at TEXT, anki_card_id INTEGER, dirty_fsrs INTEGER DEFAULT 0,
    PRIMARY KEY (collocation_id, direction));
"""


@pytest.fixture
def anki() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_ANKI_SCHEMA)
    conn.execute("INSERT INTO col VALUES (1, 100, 5, 42)")
    return conn


@pytest.fixture
def tt() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_TT_SCHEMA)
    return conn


def _base(tt: sqlite3.Connection, cid: int, text: str, note_id: int = 900) -> int:
    tt.execute(
        "INSERT INTO collocations (id, text, card_type, anki_note_id) VALUES (?, ?, 'vocab', ?)", (cid, text, note_id)
    )
    return cid


def _cloze(tt: sqlite3.Connection, cid: int, base_id: int, note_id: int | None, **direction) -> int:
    tt.execute(
        "INSERT INTO collocations (id, text, card_type, anki_note_id, base_collocation_id) "
        "VALUES (?, 'x', 'cloze', ?, ?)",
        (cid, note_id, base_id),
    )
    cols = ["collocation_id", "direction", *direction]
    vals = [cid, "production", *direction.values()]
    ph = ",".join("?" * len(cols))
    quoted = ",".join(f'"{c}"' for c in cols)
    tt.execute(f"INSERT INTO collocation_directions ({quoted}) VALUES ({ph})", vals)
    return cid


class TestIsInheritable:
    """⚠️ The FSRS columns cannot answer this and must not be asked.

    ``stability`` defaults to 1.0 and ``fsrs_difficulty`` to 5.0 — not NULL — so
    a direction that has never been graded is numerically indistinguishable from
    one that has. Four of the eight real number words sit at exactly those
    defaults with ``state='new'``; treating them as history would invent a memory
    state the learner never earned.
    """

    def test_a_new_direction_carries_nothing(self):
        assert is_inheritable("new", 0) is False

    def test_a_review_direction_carries(self):
        assert is_inheritable("review", 3) is True

    def test_a_relearning_direction_carries(self):
        assert is_inheritable("relearning", 4) is True

    def test_reps_alone_are_enough(self):
        """A graded card whose state was reset still has a history."""
        assert is_inheritable("new", 2) is True

    def test_a_missing_state_reads_as_new(self):
        assert is_inheritable(None, 0) is False


class TestInheritedStateAccessors:
    """What the operator reads in the plan before typing --apply.

    These two are the whole of the dry run's per-word verdict, so they are worth
    pinning rather than reaching only through the CLI (which is outside the
    coverage source and would let a wrong answer ship unnoticed).
    """

    def test_reports_the_state_and_reps_it_carries(self):
        inherited = InheritedState({"state": "relearning", "reps": 4})

        assert (inherited.state, inherited.reps) == ("relearning", 4)

    def test_a_missing_state_reads_as_none_not_a_crash(self):
        assert InheritedState({}).state is None

    def test_null_reps_read_as_zero(self):
        """``reps`` is nullable in the schema, and the plan line formats it."""
        assert InheritedState({"reps": None}).reps == 0


class TestPlanMigration:
    def test_captures_state_for_a_graded_cloze(self, tt: sqlite3.Connection):
        _base(tt, 1, "to")
        _cloze(tt, 10, 1, 555, state="review", reps=3, stability=12.4385, fsrs_difficulty=4.119, due_at="2026-09-08")

        [item] = plan_migration(tt, ["to"])

        assert (item.base_id, item.base_text, item.cloze_id, item.cloze_note_id) == (1, "to", 10, 555)
        assert item.inherits
        assert item.inherited.values["stability"] == 12.4385
        assert item.inherited.values["state"] == "review"
        assert item.inherited.values["reps"] == 3

    def test_a_never_graded_cloze_inherits_nothing(self, tt: sqlite3.Connection):
        """The default-value trap, as a test rather than a comment."""
        _base(tt, 1, "seksten")
        _cloze(tt, 10, 1, 555)  # every scheduling column at its schema default

        [item] = plan_migration(tt, ["seksten"])

        assert item.inherits is False
        assert item.inherited is None

    def test_the_capture_excludes_the_old_cards_identity(self, tt: sqlite3.Connection):
        """anki_card_id describes the CLOZE card. Carrying it onto a different
        card would point the new direction at a note that is about to be graved."""
        _base(tt, 1, "tre")
        _cloze(tt, 10, 1, 555, state="review", reps=6, anki_card_id=999)

        [item] = plan_migration(tt, ["tre"])

        assert "anki_card_id" not in item.inherited.values

    def test_a_cloze_with_no_anki_note_is_still_planned(self, tt: sqlite3.Connection):
        """fem and fire: TT holds the state, Anki holds no note. The TT delete is
        still owed — and is the ONLY thing owed."""
        _base(tt, 1, "fem")
        _cloze(tt, 10, 1, None, state="relearning", reps=4)

        [item] = plan_migration(tt, ["fem"])

        assert item.cloze_note_id is None
        assert item.inherits

    def test_ignores_words_not_named(self, tt: sqlite3.Connection):
        _base(tt, 1, "to")
        _cloze(tt, 10, 1, 555, state="review", reps=3)
        _base(tt, 2, "denne")
        _cloze(tt, 11, 2, 556, state="review", reps=9)

        assert [i.base_text for i in plan_migration(tt, ["to"])] == ["to"]

    def test_no_names_is_a_no_op(self, tt: sqlite3.Connection):
        assert plan_migration(tt, []) == []


class TestGraveClozeNote:
    def test_writes_one_grave_per_card_and_one_for_the_note(self, anki: sqlite3.Connection):
        anki.execute("INSERT INTO notes VALUES (555, 1, 'f', 1, 7)")
        anki.execute("INSERT INTO cards VALUES (5551, 555, 1, 0, 1, 7)")

        assert grave_cloze_note(anki, 555) == 1

        graves = {(r["oid"], r["type"], r["usn"]) for r in anki.execute("SELECT * FROM graves")}
        assert graves == {(5551, 0, -1), (555, 1, -1)}
        assert anki.execute("SELECT COUNT(*) FROM notes WHERE id=555").fetchone()[0] == 0
        assert anki.execute("SELECT COUNT(*) FROM cards WHERE nid=555").fetchone()[0] == 0

    def test_a_missing_note_is_a_no_op_and_records_no_grave(self, anki: sqlite3.Connection):
        """fem and fire. There is nothing to delete, so there is nothing to tell
        the server about — a grave for a note the server never had is noise."""
        assert grave_cloze_note(anki, 1788404936746) == 0
        assert anki.execute("SELECT COUNT(*) FROM graves").fetchone()[0] == 0

    def test_leaves_col_usn_and_scm_alone(self, anki: sqlite3.Connection):
        """col.usn is the sync ANCHOR (Layer 61) and col.scm forces a full upload.
        A data-only delete must touch neither."""
        anki.execute("INSERT INTO notes VALUES (555, 1, 'f', 1, 7)")
        anki.execute("INSERT INTO cards VALUES (5551, 555, 1, 0, 1, 7)")

        grave_cloze_note(anki, 555)
        bump_col_mod(anki)

        row = anki.execute("SELECT mod, scm, usn FROM col").fetchone()
        assert (row["scm"], row["usn"]) == (5, 42)
        assert row["mod"] != 100

    def test_other_notes_are_untouched(self, anki: sqlite3.Connection):
        anki.execute("INSERT INTO notes VALUES (555, 1, 'f', 1, 7)")
        anki.execute("INSERT INTO cards VALUES (5551, 555, 1, 0, 1, 7)")
        anki.execute("INSERT INTO notes VALUES (777, 1, 'g', 1, 7)")
        anki.execute("INSERT INTO cards VALUES (7771, 777, 1, 0, 1, 7)")

        grave_cloze_note(anki, 555)

        assert anki.execute("SELECT COUNT(*) FROM notes WHERE id=777").fetchone()[0] == 1
        assert anki.execute("SELECT COUNT(*) FROM cards WHERE nid=777").fetchone()[0] == 1


class TestDeleteTtClozes:
    def test_the_cascade_takes_the_direction_with_it(self, tt: sqlite3.Connection):
        """Which is exactly why the capture must run first."""
        _base(tt, 1, "to")
        _cloze(tt, 10, 1, 555, state="review", reps=3)

        assert delete_tt_clozes(tt, [10]) == 1

        assert tt.execute("SELECT COUNT(*) FROM collocations WHERE id=10").fetchone()[0] == 0
        assert tt.execute("SELECT COUNT(*) FROM collocation_directions WHERE collocation_id=10").fetchone()[0] == 0

    def test_the_base_word_survives(self, tt: sqlite3.Connection):
        _base(tt, 1, "to")
        _cloze(tt, 10, 1, 555)

        delete_tt_clozes(tt, [10])

        assert tt.execute("SELECT text FROM collocations WHERE id=1").fetchone()["text"] == "to"

    def test_empty_is_a_no_op(self, tt: sqlite3.Connection):
        assert delete_tt_clozes(tt, []) == 0


class TestApplyInheritance:
    def _minted(self, tt: sqlite3.Connection, base_id: int) -> None:
        """What the sync's mint leaves behind: a NEW production direction."""
        tt.execute(
            "INSERT INTO collocation_directions (collocation_id, direction, anki_card_id) VALUES (?, 'production', 4242)",
            (base_id,),
        )

    def test_writes_the_captured_scheduling(self, tt: sqlite3.Connection):
        _base(tt, 1, "to")
        self._minted(tt, 1)
        inherited = InheritedState({"state": "review", "reps": 3, "stability": 12.4385, "due_at": "2026-09-08"})

        assert apply_inheritance(tt, 1, inherited) is True

        row = tt.execute(
            "SELECT * FROM collocation_directions WHERE collocation_id=1 AND direction='production'"
        ).fetchone()
        assert (row["state"], row["reps"], row["stability"], row["due_at"]) == ("review", 3, 12.4385, "2026-09-08")

    def test_marks_the_row_dirty_so_the_push_carries_it_into_anki(self, tt: sqlite3.Connection):
        """Without this TunaTale knows the card is a review card and Anki still
        thinks it is new — the two halves disagree until something regrades it."""
        _base(tt, 1, "to")
        self._minted(tt, 1)

        apply_inheritance(tt, 1, InheritedState({"state": "review", "reps": 3}))

        row = tt.execute(
            "SELECT dirty_fsrs FROM collocation_directions WHERE collocation_id=1 AND direction='production'"
        ).fetchone()
        assert row["dirty_fsrs"] == 1

    def test_does_not_repoint_the_new_card(self, tt: sqlite3.Connection):
        """The state moves; the card identity does not."""
        _base(tt, 1, "to")
        self._minted(tt, 1)

        apply_inheritance(tt, 1, InheritedState({"state": "review", "reps": 3}))

        row = tt.execute(
            "SELECT anki_card_id FROM collocation_directions WHERE collocation_id=1 AND direction='production'"
        ).fetchone()
        assert row["anki_card_id"] == 4242

    def test_refuses_politely_when_the_mint_has_not_reached_the_word(self, tt: sqlite3.Connection):
        """Not an error: the queue is ~1000 deep and deck-ordered, so a word can
        legitimately still be waiting. The caller syncs again."""
        _base(tt, 1, "to")

        assert apply_inheritance(tt, 1, InheritedState({"state": "review"})) is False


class TestTheCaptureFileRoundTrips:
    def test_json_survives_the_gap_between_the_two_steps(self, tt: sqlite3.Connection):
        _base(tt, 1, "to")
        _cloze(tt, 10, 1, 555, state="review", reps=3, stability=12.4385)
        _base(tt, 2, "seksten")
        _cloze(tt, 11, 2, 556)

        planned = plan_migration(tt, ["to", "seksten"])
        restored = state_from_json(json.loads(json.dumps(state_to_json(planned))))

        assert [i.base_text for i in restored] == ["seksten", "to"]
        by_text = {i.base_text: i for i in restored}
        assert by_text["seksten"].inherited is None
        assert by_text["to"].inherited.values["stability"] == 12.4385
        assert by_text["to"].inherited.values["reps"] == 3
