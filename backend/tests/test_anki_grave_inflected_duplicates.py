"""Grave a TT card that duplicates an inflected form its own deck already teaches.

The `ferskt` incident (bd `tunatale-qi4b`): Stanza returned ``lemma='ferskt'``
for the neuter of ``fersk``, so ``/listen`` minted a second Norwegian card — no
gloss, no image query, pushed to Anki, failed 12 times. ``fersk``'s own
``Inflections`` table already lists ``ferskt``, and its example sentence is
*"Dette brødet er ferskt."*, so the duplicate teaches nothing the survivor does
not.

The guard that matters is the JUSTIFICATION guard: the survivor's inflection
table must actually list the doomed row's text. Without it this is a script that
deletes an arbitrary card by id, and a wrong id is a silently destroyed card with
its review history. Every other check (row exists, language matches, note id
agrees) can pass vacuously; that one cannot.

In-memory / tmp_path DBs only — never a real ``collection.anki2``
(`.claude/rules/testing.md`).
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.anki_archive.grave_inflected_duplicates import DuplicateOp, apply_graves, plan_graves

_GRAVE_KIND_CARD, _GRAVE_KIND_NOTE = 0, 1

FERSK_EXTRAS = json.dumps(
    [
        {"label": "Meaning", "html": "Recently made or obtained.", "tier": "summary"},
        {
            "label": "Inflections",
            "html": "<table><thead><tr><th>entall</th></tr></thead>"
            "<tbody><tr><td>fersk</td><td>ferskt</td><td>ferske</td></tr></tbody></table>",
            "tier": "details",
        },
    ]
)

FERSKT_OP = DuplicateOp(
    language="no",
    doomed_text="ferskt",
    doomed_cid=3054,
    survivor_cid=196,
    doomed_nid=1787228884083,
)


def _anki_conn(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS graves (oid INTEGER NOT NULL, type INTEGER NOT NULL,"
        " usn INTEGER NOT NULL, PRIMARY KEY (oid, type))"
    )
    conn.commit()
    return conn


def _seed_anki_note(conn: sqlite3.Connection, nid: int, cids: tuple[int, ...]) -> None:
    conn.execute(
        "INSERT INTO notes (id, guid, mid, mod, usn, tags, flds, sfld, csum, flags, data)"
        " VALUES (?, 'g', 1, 0, 0, '', 'front\x1fback', 'front', 0, 0, '')",
        (nid,),
    )
    for ord_, cid in enumerate(cids):
        conn.execute(
            "INSERT INTO cards (id, nid, did, ord, mod, usn, type, queue, due, ivl,"
            " factor, reps, lapses, left, odue, odid, flags, data)"
            " VALUES (?, ?, 12345, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (cid, nid, ord_),
        )
    conn.commit()


def _tt_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE collocations (id INTEGER PRIMARY KEY, text TEXT, language_code TEXT,"
        " anki_note_id INTEGER, extras TEXT NOT NULL DEFAULT '')"
    )
    conn.execute("CREATE TABLE collocation_directions (collocation_id INTEGER, direction TEXT)")
    conn.execute("CREATE TABLE tt_revlog (id INTEGER PRIMARY KEY, collocation_id INTEGER, direction TEXT)")
    conn.execute("CREATE TABLE media (id INTEGER PRIMARY KEY, collocation_id INTEGER, filename TEXT)")
    return conn


def _seed_tt(conn, cid: int, text: str, *, lang: str = "no", nid: int | None = None, extras: str = "") -> None:
    conn.execute("INSERT INTO collocations VALUES (?, ?, ?, ?, ?)", (cid, text, lang, nid, extras))
    for d in ("recognition", "production"):
        conn.execute("INSERT INTO collocation_directions VALUES (?, ?)", (cid, d))
    conn.execute("INSERT INTO tt_revlog (collocation_id, direction) VALUES (?, 'recognition')", (cid,))
    conn.execute("INSERT INTO media (collocation_id, filename) VALUES (?, 'x.mp3')", (cid,))
    conn.commit()


def _seeded(tmp_path, fake_anki_db):
    """The real shape: a survivor that inflects the doomed row's text."""
    anki = _anki_conn(fake_anki_db)
    _seed_anki_note(anki, 1787228884083, (1787228884083, 1787228884084))
    tt = _tt_conn()
    _seed_tt(tt, 196, "fersk", extras=FERSK_EXTRAS)
    _seed_tt(tt, 3054, "ferskt", nid=1787228884083)
    return anki, tt


class TestPlanRefusesWithoutJustification:
    """A wrong id destroys a real card. Every refusal below is a no-op, not a partial."""

    def test_refuses_when_the_survivor_does_not_inflect_the_doomed_text(self, tmp_path, fake_anki_db):
        anki, tt = _seeded(tmp_path, fake_anki_db)
        tt.execute("UPDATE collocations SET extras = '' WHERE id = 196")
        with pytest.raises(ValueError, match="does not list"):
            plan_graves(anki, tt, [FERSKT_OP])

    def test_refuses_when_the_doomed_text_does_not_match_the_row(self, tmp_path, fake_anki_db):
        anki, tt = _seeded(tmp_path, fake_anki_db)
        tt.execute("UPDATE collocations SET text = 'noe helt annet' WHERE id = 3054")
        with pytest.raises(ValueError, match="text"):
            plan_graves(anki, tt, [FERSKT_OP])

    def test_refuses_when_the_recorded_note_id_disagrees(self, tmp_path, fake_anki_db):
        anki, tt = _seeded(tmp_path, fake_anki_db)
        tt.execute("UPDATE collocations SET anki_note_id = 999 WHERE id = 3054")
        with pytest.raises(ValueError, match="note id"):
            plan_graves(anki, tt, [FERSKT_OP])

    def test_refuses_when_the_survivor_is_missing(self, tmp_path, fake_anki_db):
        anki, tt = _seeded(tmp_path, fake_anki_db)
        tt.execute("DELETE FROM collocations WHERE id = 196")
        with pytest.raises(ValueError, match="survivor"):
            plan_graves(anki, tt, [FERSKT_OP])

    def test_an_already_applied_op_plans_nothing_rather_than_raising(self, tmp_path, fake_anki_db):
        """Re-running after success must be a clean no-op — the doomed row is gone."""
        anki, tt = _seeded(tmp_path, fake_anki_db)
        apply_graves(anki, tt, plan_graves(anki, tt, [FERSKT_OP]))
        assert plan_graves(anki, tt, [FERSKT_OP]) == []


class TestApplyIsAnkiSafe:
    def test_writes_one_grave_per_card_and_one_per_note(self, tmp_path, fake_anki_db):
        anki, tt = _seeded(tmp_path, fake_anki_db)
        apply_graves(anki, tt, plan_graves(anki, tt, [FERSKT_OP]))

        graves = {(r["oid"], r["type"], r["usn"]) for r in anki.execute("SELECT * FROM graves")}
        assert graves == {
            (1787228884083, _GRAVE_KIND_CARD, -1),
            (1787228884084, _GRAVE_KIND_CARD, -1),
            (1787228884083, _GRAVE_KIND_NOTE, -1),
        }
        assert anki.execute("SELECT COUNT(*) FROM notes WHERE id = 1787228884083").fetchone()[0] == 0
        assert anki.execute("SELECT COUNT(*) FROM cards WHERE nid = 1787228884083").fetchone()[0] == 0

    def test_preserves_col_usn_and_col_scm(self, tmp_path, fake_anki_db):
        """Layer 61 + the 2026-08-02 forced-full-sync incident: a delete bumps
        ``col.mod`` only. ``col.usn`` is the sync anchor and ``col.scm`` would
        force a full upload."""
        anki, tt = _seeded(tmp_path, fake_anki_db)
        before = anki.execute("SELECT usn, scm, mod FROM col").fetchone()
        apply_graves(anki, tt, plan_graves(anki, tt, [FERSKT_OP]))
        after = anki.execute("SELECT usn, scm, mod FROM col").fetchone()
        assert after["usn"] == before["usn"]
        assert after["scm"] == before["scm"]
        assert after["mod"] != before["mod"]

    def test_drops_every_tt_row_the_doomed_card_owned(self, tmp_path, fake_anki_db):
        """Directions, revlog and media go with it — an orphan tt_revlog row for a
        collocation that no longer exists is exactly the silent state that made
        this bug survive 12 reviews."""
        anki, tt = _seeded(tmp_path, fake_anki_db)
        apply_graves(anki, tt, plan_graves(anki, tt, [FERSKT_OP]))

        for table in ("collocations", "collocation_directions", "tt_revlog", "media"):
            col = "id" if table == "collocations" else "collocation_id"
            n = tt.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} = 3054").fetchone()[0]
            assert n == 0, f"{table} kept rows for the graved collocation"

    def test_leaves_the_survivor_untouched(self, tmp_path, fake_anki_db):
        anki, tt = _seeded(tmp_path, fake_anki_db)
        apply_graves(anki, tt, plan_graves(anki, tt, [FERSKT_OP]))

        row = tt.execute("SELECT text, extras FROM collocations WHERE id = 196").fetchone()
        assert row["text"] == "fersk"
        assert row["extras"] == FERSK_EXTRAS
        assert tt.execute("SELECT COUNT(*) FROM tt_revlog WHERE collocation_id = 196").fetchone()[0] == 1

    def test_an_empty_plan_leaves_col_alone(self, tmp_path, fake_anki_db):
        anki, tt = _seeded(tmp_path, fake_anki_db)
        before = anki.execute("SELECT mod FROM col").fetchone()["mod"]
        assert apply_graves(anki, tt, []) == {"notes_graved": 0, "cards_graved": 0, "tt_collocations_deleted": 0}
        assert anki.execute("SELECT mod FROM col").fetchone()["mod"] == before
