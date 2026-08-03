"""Grave the redundant Anki note when one word has two notes in the deck.

A TT guid is ``(text, language, disambig_key)`` with POS as the disambig, so
two Anki notes sharing the same **(text, POS)** collapse to one collocation
with two candidate cards and nothing pins which ``anki_card_id`` references.
``foran`` — both notes tagged ``preposition`` — ping-ponged from 2026-07-14
onward, splitting one word's review history across two cards.

POS homonyms (``løfte`` noun/verb, ``vår`` noun/det) are NOT this: a different
POS yields a different guid and a separate pinned collocation. Only genuine
(text, POS) collapses belong in ``DUPLICATE_OPS``.

This script graves the redundant twin the Anki-safe way (``graves``, per
`.claude/rules/anki-sync.md`) so only the card TT tracks survives.

In-memory / tmp_path DBs only — never a real ``collection.anki2`` (conftest's
``fake_anki_db`` fixture, per `.claude/rules/testing.md`).
"""

from __future__ import annotations

import sqlite3

import pytest

from scripts.anki_archive.grave_duplicate_notes import (
    DuplicateOp,
    apply_graves,
    apply_repoints,
    ops_for_language,
    plan_graves,
    plan_repoints,
)

_GRAVE_KIND_CARD, _GRAVE_KIND_NOTE = 0, 1


def _anki_conn(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS graves (oid INTEGER NOT NULL, type INTEGER NOT NULL,"
        " usn INTEGER NOT NULL, PRIMARY KEY (oid, type))"
    )
    conn.commit()
    return conn


def _seed_note(conn: sqlite3.Connection, nid: int, cids: tuple[int, ...], word: str) -> None:
    conn.execute(
        "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data)"
        " VALUES (?, ?, 1, 0, 0, '', ?, ?, 0, 0, '')",
        (nid, f"g{nid}", f"1\x1f{word}\x1fpreposition", word),
    )
    for ord_, cid in enumerate(cids):
        conn.execute(
            "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl,"
            " factor, reps, lapses, left, odue, odid, flags, data)"
            " VALUES (?, ?, 12345, ?, 0, 0, 2, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (cid, nid, ord_),
        )
    conn.commit()


def _tt_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE collocations (id INTEGER PRIMARY KEY, text TEXT, anki_note_id INTEGER)")
    conn.execute("CREATE TABLE collocation_directions (collocation_id INTEGER, direction TEXT, anki_card_id INTEGER)")
    return conn


def _seed_tt(conn, cid: int, text: str, nid: int, anki_card_id: int) -> None:
    conn.execute("INSERT INTO collocations VALUES (?, ?, ?)", (cid, text, nid))
    conn.execute("INSERT INTO collocation_directions VALUES (?, 'recognition', ?)", (cid, anki_card_id))
    conn.commit()


_OP = DuplicateOp(word="foran", doomed_nid=5378, survivor_nid=232, language="no")


class TestPlanGraves:
    def test_plans_the_doomed_note_and_its_cards(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 232, (233,), "foran")
        _seed_note(anki, 5378, (5379,), "foran")
        tt = _tt_conn()
        _seed_tt(tt, 1478, "foran", nid=232, anki_card_id=233)

        items = plan_graves(anki, tt, [_OP])

        assert len(items) == 1
        assert items[0].anki_nid == 5378
        assert items[0].anki_cids == (5379,)

    def test_refuses_when_tt_points_at_the_doomed_note(self, fake_anki_db):
        """Guard: graving a note TT tracks would strand the collocation."""
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 232, (233,), "foran")
        _seed_note(anki, 5378, (5379,), "foran")
        tt = _tt_conn()
        _seed_tt(tt, 1478, "foran", nid=5378, anki_card_id=5379)

        with pytest.raises(ValueError, match="TT collocation 1478 points at doomed note 5378"):
            plan_graves(anki, tt, [_OP])

    def test_refuses_when_survivor_is_missing(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 5378, (5379,), "foran")
        tt = _tt_conn()

        with pytest.raises(ValueError, match="survivor note 232 not found"):
            plan_graves(anki, tt, [_OP])

    def test_is_idempotent_when_doomed_note_already_gone(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 232, (233,), "foran")
        tt = _tt_conn()
        _seed_tt(tt, 1478, "foran", nid=232, anki_card_id=233)

        assert plan_graves(anki, tt, [_OP]) == []


class TestApplyGraves:
    def test_writes_graves_and_deletes_rows(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 232, (233,), "foran")
        _seed_note(anki, 5378, (5379,), "foran")
        tt = _tt_conn()
        _seed_tt(tt, 1478, "foran", nid=232, anki_card_id=233)

        counts = apply_graves(anki, plan_graves(anki, tt, [_OP]))

        assert counts == {"notes_graved": 1, "cards_graved": 1}
        graves = {(r["oid"], r["type"], r["usn"]) for r in anki.execute("SELECT * FROM graves")}
        assert graves == {(5379, _GRAVE_KIND_CARD, -1), (5378, _GRAVE_KIND_NOTE, -1)}
        assert anki.execute("SELECT COUNT(*) FROM notes WHERE id = 5378").fetchone()[0] == 0
        assert anki.execute("SELECT COUNT(*) FROM cards WHERE id = 5379").fetchone()[0] == 0

    def test_survivor_is_untouched(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 232, (233,), "foran")
        _seed_note(anki, 5378, (5379,), "foran")
        tt = _tt_conn()
        _seed_tt(tt, 1478, "foran", nid=232, anki_card_id=233)

        apply_graves(anki, plan_graves(anki, tt, [_OP]))

        assert anki.execute("SELECT COUNT(*) FROM notes WHERE id = 232").fetchone()[0] == 1
        assert anki.execute("SELECT COUNT(*) FROM cards WHERE id = 233").fetchone()[0] == 1

    def test_bumps_col_mod_preserves_usn_and_scm(self, fake_anki_db):
        """Data-only delete: scm frozen, and usn is the sync anchor (Layer 61)."""
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 232, (233,), "foran")
        _seed_note(anki, 5378, (5379,), "foran")
        tt = _tt_conn()
        _seed_tt(tt, 1478, "foran", nid=232, anki_card_id=233)
        anki.execute("UPDATE col SET usn = 1149")
        anki.commit()
        before = anki.execute("SELECT mod, scm, usn FROM col").fetchone()

        apply_graves(anki, plan_graves(anki, tt, [_OP]))

        after = anki.execute("SELECT mod, scm, usn FROM col").fetchone()
        assert after["mod"] > before["mod"]
        assert after["scm"] == before["scm"]
        assert after["usn"] == 1149

    def test_empty_plan_leaves_col_untouched(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        before = anki.execute("SELECT mod FROM col").fetchone()["mod"]

        assert apply_graves(anki, []) == {"notes_graved": 0, "cards_graved": 0}

        assert anki.execute("SELECT mod FROM col").fetchone()["mod"] == before


_SL_OP = DuplicateOp(word="kako si", doomed_nid=9095, survivor_nid=7550, language="sl", repoint=True)


class TestOpsForLanguage:
    """One global op list, but each op is checked against ITS OWN language's db.

    ``plan_graves``' "TT points at the doomed note" guard reads whichever TT db
    the CLI opened. Running it against another language's db makes that guard
    pass vacuously — the exact footgun the module docstring warns about — so a
    Slovene op must never be planned during a Norwegian run.
    """

    def test_selects_only_the_named_language(self):
        assert ops_for_language("no", [_OP, _SL_OP]) == [_OP]
        assert ops_for_language("sl", [_OP, _SL_OP]) == [_SL_OP]

    def test_unknown_language_selects_nothing(self):
        assert ops_for_language("de", [_OP, _SL_OP]) == []


class TestPlanRepoints:
    """``repoint=True`` inverts the guard: TT tracks the DOOMED note, and the
    survivor is the twin it does not track (the pair-1 Slovene cloze shape)."""

    def test_plans_the_move_from_doomed_to_survivor(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 7550, (7550,), "kako si")
        _seed_note(anki, 9095, (9095,), "kako si")
        tt = _tt_conn()
        _seed_tt(tt, 294, "kako si", nid=9095, anki_card_id=9095)

        moves = plan_repoints(anki, tt, [_SL_OP])

        assert len(moves) == 1
        assert moves[0].tt_collocation_id == 294
        assert moves[0].from_nid == 9095
        assert moves[0].to_nid == 7550
        assert moves[0].card_moves == ((9095, 7550),)

    def test_ignores_ops_not_marked_repoint(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 232, (233,), "foran")
        _seed_note(anki, 5378, (5379,), "foran")
        tt = _tt_conn()
        _seed_tt(tt, 1478, "foran", nid=232, anki_card_id=233)

        assert plan_repoints(anki, tt, [_OP]) == []

    def test_is_idempotent_once_tt_already_points_at_the_survivor(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 7550, (7550,), "kako si")
        _seed_note(anki, 9095, (9095,), "kako si")
        tt = _tt_conn()
        _seed_tt(tt, 294, "kako si", nid=7550, anki_card_id=7550)

        assert plan_repoints(anki, tt, [_SL_OP]) == []

    def test_refuses_when_survivor_is_missing(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 9095, (9095,), "kako si")
        tt = _tt_conn()
        _seed_tt(tt, 294, "kako si", nid=9095, anki_card_id=9095)

        with pytest.raises(ValueError, match="survivor note 7550 not found"):
            plan_repoints(anki, tt, [_SL_OP])

    def test_refuses_when_ords_do_not_line_up(self, fake_anki_db):
        """A survivor missing an ord the collocation references would strand
        that direction on a dead card id — refuse rather than write a NULL."""
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 7550, (7550,), "kako si")
        _seed_note(anki, 9095, (9095, 9096), "kako si")
        tt = _tt_conn()
        _seed_tt(tt, 294, "kako si", nid=9095, anki_card_id=9095)
        tt.execute("INSERT INTO collocation_directions VALUES (294, 'production', 9096)")
        tt.commit()

        with pytest.raises(ValueError, match="ord 1"):
            plan_repoints(anki, tt, [_SL_OP])


class TestApplyRepoints:
    def test_moves_note_and_card_pointers(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 7550, (7550,), "kako si")
        _seed_note(anki, 9095, (9095,), "kako si")
        tt = _tt_conn()
        _seed_tt(tt, 294, "kako si", nid=9095, anki_card_id=9095)

        counts = apply_repoints(tt, plan_repoints(anki, tt, [_SL_OP]))

        assert counts == {"collocations_repointed": 1, "directions_repointed": 1}
        assert tt.execute("SELECT anki_note_id FROM collocations WHERE id = 294").fetchone()[0] == 7550
        assert (
            tt.execute("SELECT anki_card_id FROM collocation_directions WHERE collocation_id = 294").fetchone()[0]
            == 7550
        )

    def test_repoint_then_grave_leaves_one_live_note(self, fake_anki_db):
        """The pair-1 sequence end to end: re-point first, and the guard that
        would otherwise refuse the grave is satisfied."""
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 7550, (7550,), "kako si")
        _seed_note(anki, 9095, (9095,), "kako si")
        tt = _tt_conn()
        _seed_tt(tt, 294, "kako si", nid=9095, anki_card_id=9095)

        apply_repoints(tt, plan_repoints(anki, tt, [_SL_OP]))
        apply_graves(anki, plan_graves(anki, tt, [_SL_OP]))

        assert anki.execute("SELECT COUNT(*) FROM notes WHERE id = 9095").fetchone()[0] == 0
        assert anki.execute("SELECT COUNT(*) FROM notes WHERE id = 7550").fetchone()[0] == 1
        assert tt.execute("SELECT anki_note_id FROM collocations WHERE id = 294").fetchone()[0] == 7550

    def test_graving_before_repointing_still_refuses(self, fake_anki_db):
        """Order matters — the guard stays armed until the re-point lands."""
        anki = _anki_conn(fake_anki_db)
        _seed_note(anki, 7550, (7550,), "kako si")
        _seed_note(anki, 9095, (9095,), "kako si")
        tt = _tt_conn()
        _seed_tt(tt, 294, "kako si", nid=9095, anki_card_id=9095)

        with pytest.raises(ValueError, match="points at doomed note 9095"):
            plan_graves(anki, tt, [_SL_OP])

    def test_empty_plan_writes_nothing(self):
        tt = _tt_conn()

        assert apply_repoints(tt, []) == {"collocations_repointed": 0, "directions_repointed": 0}
