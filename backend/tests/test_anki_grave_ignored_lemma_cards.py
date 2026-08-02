"""Grave the Anki notes for lemmas on TT's card-less ignore list.

The ignore list (``ignored_lemmas``) only ever suppressed card *creation* — a
lemma that already had a card kept it, and the ignore row went inert (the word
stops rendering as ignored in the transcript and counts as ordinary vocabulary).
This script closes that gap after the fact: for every ignored lemma that
nonetheless has a card, remove the Anki note the Anki-safe way (``graves``, per
`.claude/rules/anki-sync.md`) and drop the TT collocation.

In-memory / tmp_path DBs only — never a real ``collection.anki2`` (conftest's
``fake_anki_db`` fixture, per `.claude/rules/testing.md`).
"""

from __future__ import annotations

import sqlite3

import pytest

from scripts.anki_archive.grave_ignored_lemma_cards import apply_graves, plan_graves

_GRAVE_KIND_CARD, _GRAVE_KIND_NOTE = 0, 1


def _anki_conn(path) -> sqlite3.Connection:
    """Open the fixture collection and add the `graves` table it omits."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS graves (oid INTEGER NOT NULL, type INTEGER NOT NULL, usn INTEGER NOT NULL, PRIMARY KEY (oid, type))"
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
    """Minimal TT shape: the three columns the planner reads, plus directions."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE collocations (id INTEGER PRIMARY KEY, text TEXT, language_code TEXT, anki_note_id INTEGER)"
    )
    conn.execute("CREATE TABLE collocation_directions (collocation_id INTEGER, direction TEXT)")
    conn.execute(
        "CREATE TABLE ignored_lemmas (language_code TEXT NOT NULL, lemma TEXT NOT NULL, PRIMARY KEY (language_code, lemma))"
    )
    return conn


def _seed_tt(conn, cid: int, text: str, lang: str = "no", nid: int | None = None) -> None:
    conn.execute("INSERT INTO collocations VALUES (?, ?, ?, ?)", (cid, text, lang, nid))
    for d in ("recognition", "production"):
        conn.execute("INSERT INTO collocation_directions VALUES (?, ?)", (cid, d))
    conn.commit()


def _ignore(conn, lemma: str, lang: str = "no") -> None:
    conn.execute("INSERT INTO ignored_lemmas VALUES (?, ?)", (lang, lemma))
    conn.commit()


class TestPlanGraves:
    def test_plans_only_ignored_lemmas_that_have_cards(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        tt = _tt_conn()
        _seed_anki_note(anki, 9001, (9101, 9102))
        _seed_tt(tt, 1, "hansen", nid=9001)
        _seed_tt(tt, 2, "snømann", nid=9002)  # carded but NOT ignored
        _ignore(tt, "hansen")
        _ignore(tt, "anna")  # ignored but never carded

        plan = plan_graves(anki, tt, "no")

        assert [p.text for p in plan] == ["hansen"]
        assert plan[0].anki_nid == 9001
        assert plan[0].anki_cids == (9101, 9102)
        assert plan[0].tt_collocation_id == 1

    def test_matches_ignore_rows_case_insensitively(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        tt = _tt_conn()
        _seed_anki_note(anki, 9001, (9101,))
        _seed_tt(tt, 1, "Hansen", nid=9001)
        _ignore(tt, "hansen")

        assert [p.tt_collocation_id for p in plan_graves(anki, tt, "no")] == [1]

    def test_other_languages_are_untouched(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        tt = _tt_conn()
        _seed_anki_note(anki, 9001, (9101,))
        _seed_tt(tt, 1, "hansen", lang="sl", nid=9001)
        _ignore(tt, "hansen", lang="no")

        assert plan_graves(anki, tt, "no") == []

    def test_tt_only_row_is_planned_with_no_anki_work(self, fake_anki_db):
        """Never pushed to Anki: nothing to grave, but the TT row still goes."""
        anki = _anki_conn(fake_anki_db)
        tt = _tt_conn()
        _seed_tt(tt, 1, "hansen", nid=None)
        _ignore(tt, "hansen")

        plan = plan_graves(anki, tt, "no")

        assert len(plan) == 1
        assert plan[0].anki_nid is None
        assert plan[0].anki_cids == ()

    def test_note_already_gone_from_anki_still_clears_the_tt_row(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        tt = _tt_conn()
        _seed_tt(tt, 1, "hansen", nid=9001)  # nid points at a note that isn't there
        _ignore(tt, "hansen")

        plan = plan_graves(anki, tt, "no")

        assert len(plan) == 1
        assert plan[0].anki_nid is None, "a missing note must not be graved by nid"
        assert plan[0].tt_collocation_id == 1


class TestApplyGraves:
    def test_writes_one_note_grave_and_one_grave_per_card(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        tt = _tt_conn()
        _seed_anki_note(anki, 9001, (9101, 9102))
        _seed_tt(tt, 1, "hansen", nid=9001)
        _ignore(tt, "hansen")

        counts = apply_graves(anki, tt, plan_graves(anki, tt, "no"))

        graves = {(r["oid"], r["type"], r["usn"]) for r in anki.execute("SELECT * FROM graves")}
        assert graves == {
            (9101, _GRAVE_KIND_CARD, -1),
            (9102, _GRAVE_KIND_CARD, -1),
            (9001, _GRAVE_KIND_NOTE, -1),
        }
        assert anki.execute("SELECT COUNT(*) FROM notes WHERE id = 9001").fetchone()[0] == 0
        assert anki.execute("SELECT COUNT(*) FROM cards WHERE nid = 9001").fetchone()[0] == 0
        assert counts == {"notes_graved": 1, "cards_graved": 2, "tt_collocations_deleted": 1}

    def test_deletes_the_tt_collocation_and_its_directions(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        tt = _tt_conn()
        _seed_anki_note(anki, 9001, (9101,))
        _seed_tt(tt, 1, "hansen", nid=9001)
        _seed_tt(tt, 2, "beholder", nid=9002)
        _ignore(tt, "hansen")

        apply_graves(anki, tt, plan_graves(anki, tt, "no"))

        assert tt.execute("SELECT COUNT(*) FROM collocations WHERE id = 1").fetchone()[0] == 0
        assert tt.execute("SELECT COUNT(*) FROM collocation_directions WHERE collocation_id = 1").fetchone()[0] == 0
        # The un-ignored neighbour is untouched.
        assert tt.execute("SELECT COUNT(*) FROM collocations WHERE id = 2").fetchone()[0] == 1
        assert tt.execute("SELECT COUNT(*) FROM collocation_directions WHERE collocation_id = 2").fetchone()[0] == 2

    def test_bumps_col_mod_but_never_col_scm(self, fake_anki_db):
        """Deletes are data-only: touching scm would force a full re-upload."""
        anki = _anki_conn(fake_anki_db)
        tt = _tt_conn()
        _seed_anki_note(anki, 9001, (9101,))
        _seed_tt(tt, 1, "hansen", nid=9001)
        _ignore(tt, "hansen")
        before = anki.execute("SELECT mod, scm FROM col").fetchone()

        apply_graves(anki, tt, plan_graves(anki, tt, "no"))

        after = anki.execute("SELECT mod, scm FROM col").fetchone()
        assert after["mod"] > before["mod"]
        assert after["scm"] == before["scm"]

    def test_preserves_col_usn(self, fake_anki_db):
        """`col.usn` is the sync *anchor* (the server's last USN), not a dirty flag.

        Layer 61: clobbering it to -1 is invisible while the desktop is the only
        writer, but the moment another device advances the server's USN the
        collection can no longer reconcile incrementally — AnkiWeb demands a full
        sync and Anki stamps a fresh `scm`. The graved rows carry their own
        `usn = -1`, which is what actually pushes; `col.mod` signals the change.

        Observed in the wild 2026-08-02: a delete run left `col.usn = -1`, a phone
        session advanced the server, and the next desktop sync forced a full
        download. `scm` is asserted above; without this, the delete path re-arms
        that trap on every run.
        """
        anki = _anki_conn(fake_anki_db)
        tt = _tt_conn()
        _seed_anki_note(anki, 9001, (9101,))
        _seed_tt(tt, 1, "hansen", nid=9001)
        _ignore(tt, "hansen")
        anki.execute("UPDATE col SET usn = 1149")
        anki.commit()

        apply_graves(anki, tt, plan_graves(anki, tt, "no"))

        assert anki.execute("SELECT usn FROM col").fetchone()["usn"] == 1149
        # The rows themselves still push.
        assert {r["usn"] for r in anki.execute("SELECT usn FROM graves")} == {-1}

    def test_tt_only_row_writes_no_graves(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        tt = _tt_conn()
        _seed_tt(tt, 1, "hansen", nid=None)
        _ignore(tt, "hansen")

        counts = apply_graves(anki, tt, plan_graves(anki, tt, "no"))

        assert anki.execute("SELECT COUNT(*) FROM graves").fetchone()[0] == 0
        assert counts == {"notes_graved": 0, "cards_graved": 0, "tt_collocations_deleted": 1}

    def test_is_idempotent(self, fake_anki_db):
        """Second run finds nothing: the TT rows are gone, so nothing re-graves."""
        anki = _anki_conn(fake_anki_db)
        tt = _tt_conn()
        _seed_anki_note(anki, 9001, (9101, 9102))
        _seed_tt(tt, 1, "hansen", nid=9001)
        _ignore(tt, "hansen")

        apply_graves(anki, tt, plan_graves(anki, tt, "no"))
        second = plan_graves(anki, tt, "no")

        assert second == []
        assert apply_graves(anki, tt, second) == {
            "notes_graved": 0,
            "cards_graved": 0,
            "tt_collocations_deleted": 0,
        }
        assert anki.execute("SELECT COUNT(*) FROM graves").fetchone()[0] == 3

    def test_empty_plan_touches_nothing(self, fake_anki_db):
        anki = _anki_conn(fake_anki_db)
        tt = _tt_conn()
        before = anki.execute("SELECT mod FROM col").fetchone()["mod"]

        assert apply_graves(anki, tt, []) == {
            "notes_graved": 0,
            "cards_graved": 0,
            "tt_collocations_deleted": 0,
        }
        assert anki.execute("SELECT mod FROM col").fetchone()["mod"] == before


class TestMainCli:
    def test_dry_run_reports_the_plan_and_writes_nothing(self, fake_anki_db, tmp_path, capsys, monkeypatch):
        from scripts.anki_archive import grave_ignored_lemma_cards as mod

        anki = _anki_conn(fake_anki_db)
        _seed_anki_note(anki, 9001, (9101, 9102))
        anki.close()

        tt_path = tmp_path / "tt.db"
        tt = sqlite3.connect(str(tt_path))
        tt.row_factory = sqlite3.Row
        tt.execute(
            "CREATE TABLE collocations (id INTEGER PRIMARY KEY, text TEXT, language_code TEXT, anki_note_id INTEGER)"
        )
        tt.execute("CREATE TABLE collocation_directions (collocation_id INTEGER, direction TEXT)")
        tt.execute(
            "CREATE TABLE ignored_lemmas (language_code TEXT NOT NULL, lemma TEXT NOT NULL, PRIMARY KEY (language_code, lemma))"
        )
        _seed_tt(tt, 1, "hansen", nid=9001)
        _ignore(tt, "hansen")
        tt.close()

        rc = mod.main(["--dry-run", "--language", "no", "--anki-db", str(fake_anki_db), "--tt-db", str(tt_path)])

        out = capsys.readouterr().out
        assert rc == 0
        assert "hansen" in out
        assert "no changes applied" in out
        check = sqlite3.connect(str(fake_anki_db))
        assert check.execute("SELECT COUNT(*) FROM notes WHERE id = 9001").fetchone()[0] == 1
        check.close()

    def test_missing_db_paths_exit_nonzero(self, tmp_path, capsys):
        from scripts.anki_archive import grave_ignored_lemma_cards as mod

        rc = mod.main(["--dry-run", "--anki-db", str(tmp_path / "nope.anki2"), "--tt-db", str(tmp_path / "nope.db")])
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_dry_run_with_nothing_to_do_says_so(self, fake_anki_db, tmp_path, capsys):
        from scripts.anki_archive import grave_ignored_lemma_cards as mod

        tt_path = tmp_path / "tt.db"
        tt = sqlite3.connect(str(tt_path))
        tt.execute(
            "CREATE TABLE collocations (id INTEGER PRIMARY KEY, text TEXT, language_code TEXT, anki_note_id INTEGER)"
        )
        tt.execute("CREATE TABLE collocation_directions (collocation_id INTEGER, direction TEXT)")
        tt.execute(
            "CREATE TABLE ignored_lemmas (language_code TEXT NOT NULL, lemma TEXT NOT NULL, PRIMARY KEY (language_code, lemma))"
        )
        tt.commit()
        tt.close()

        rc = mod.main(["--dry-run", "--language", "no", "--anki-db", str(fake_anki_db), "--tt-db", str(tt_path)])

        assert rc == 0
        assert "Nothing to grave" in capsys.readouterr().out


@pytest.mark.parametrize("bad_mode", ["rw"])
def test_apply_requires_explicit_plan(fake_anki_db, bad_mode):
    """Guard the shape of the API: apply_graves never re-derives its own plan."""
    import inspect

    from scripts.anki_archive.grave_ignored_lemma_cards import apply_graves as fn

    params = list(inspect.signature(fn).parameters)
    assert params == ["anki_conn", "tt_conn", "items"]
