"""Bringing a live deck in line with first-class short forms (tunatale-0rlg).

A shared card's comma headword is renamed to the long form, and a second sense
whose Short form line now tells its front apart gets its recognition back. Both
are read off the tables, never a typed list of card ids.
"""

from __future__ import annotations

import pytest

from app.common.guid import compute_guid
from app.models.srs_item import Direction
from app.models.syntactic_unit import SyntacticUnit
from app.plugins.anki_sync.short_form_cards import plan_front_renames, plan_unsuspends
from app.srs.database import SRSDatabase

LANG = "ceb"


def _add(db: SRSDatabase, text: str, gloss: str, key: str = "", language: str = LANG) -> int:
    db.add_collocation(
        SyntacticUnit(text=text, translation=gloss, word_count=1, difficulty=1, source="user", disambig_key=key),
        language,
    )
    return db.get_collocation_id_by_guid(compute_guid(text, language, key))


def _state(db: SRSDatabase, coll_id: int, direction: str) -> str:
    with db._get_conn() as conn:
        return conn.execute(
            "SELECT state FROM collocation_directions WHERE collocation_id = ? AND direction = ?", (coll_id, direction)
        ).fetchone()[0]


class TestFrontRenames:
    def test_a_comma_headword_of_a_shared_pair_is_renamed_to_the_long_form(self):
        db = SRSDatabase(":memory:")
        dili = _add(db, "dili, di", "no; not")
        inday = _add(db, "inday, day", "Inday")
        with db._get_conn() as conn:
            assert plan_front_renames(conn, LANG) == [(dili, "dili, di", "dili"), (inday, "inday, day", "inday")]

    def test_anything_else_with_a_comma_is_left_alone(self):
        """A phrase with a comma is two words, and a pronoun pair has two cards."""
        db = SRSDatabase(":memory:")
        _add(db, "Oo, salamat", "yes, thanks")
        _add(db, "kamo, mo", "you (plural)")
        _add(db, "dili", "no; not")
        with db._get_conn() as conn:
            assert plan_front_renames(conn, LANG) == []

    def test_a_cloze_or_another_language_is_left_alone(self):
        db = SRSDatabase(":memory:")
        _add(db, "dili, di", "no; not", language="no")
        db.add_collocation(
            SyntacticUnit(
                text="unya, nya", translation="later", word_count=1, difficulty=1, source="user", card_type="cloze"
            ),
            LANG,
        )
        with db._get_conn() as conn:
            assert plan_front_renames(conn, LANG) == []

    def test_a_card_already_holding_the_long_form_is_a_loud_refusal(self):
        """Two cards for one word need the owner's call; the rename would break the unique guid."""
        db = SRSDatabase(":memory:")
        _add(db, "dili, di", "no; not")
        _add(db, "dili", "no")
        with db._get_conn() as conn, pytest.raises(ValueError, match="dili"):
            plan_front_renames(conn, LANG)

    def test_a_language_with_no_pairs_plans_nothing(self):
        db = SRSDatabase(":memory:")
        _add(db, "ja, jo", "yes", language="no")
        with db._get_conn() as conn:
            assert plan_front_renames(conn, "no") == []


class TestUnsuspends:
    def test_a_second_sense_with_a_short_form_line_gets_its_recognition_back(self):
        db = SRSDatabase(":memory:")
        ids = {w: _add(db, w, "x", k) for w, k in [("ko", "genitive"), ("mo", "genitive"), ("kita", "pronoun")]}
        for coll_id in ids.values():
            db.set_suspended(coll_id, True, Direction.RECOGNITION)
        with db._get_conn() as conn:
            assert plan_unsuspends(conn, LANG) == sorted(
                [(ids["ko"], "ko", "genitive"), (ids["mo"], "mo", "genitive"), (ids["kita"], "kita", "pronoun")]
            )

    def test_a_second_sense_with_no_line_stays_suspended(self):
        db = SRSDatabase(":memory:")
        ako = _add(db, "ako", "my, mine; by me", "possessive")
        assert _state(db, ako, "recognition") == "suspended"  # the control: it is a candidate but for the line
        with db._get_conn() as conn:
            assert plan_unsuspends(conn, LANG) == []

    def test_an_ordinary_suspended_card_is_the_owners(self):
        db = SRSDatabase(":memory:")
        maayo = _add(db, "maayo", "good")
        db.set_suspended(maayo, True, Direction.RECOGNITION)
        with db._get_conn() as conn:
            assert plan_unsuspends(conn, LANG) == []

    def test_a_suspended_production_side_is_the_owners(self):
        db = SRSDatabase(":memory:")
        ko = _add(db, "ko", "my; by me", "genitive")
        db.set_suspended(ko, True, Direction.PRODUCTION)
        with db._get_conn() as conn:
            assert plan_unsuspends(conn, LANG) == []


class TestScript:
    def _db(self, tmp_path) -> tuple[str, SRSDatabase]:
        path = str(tmp_path / "ceb.db")
        return path, SRSDatabase(path)

    def _main(self, path, *argv) -> int:
        from scripts.apply_short_forms import main

        return main(["--language", LANG, "--db", path, *argv])

    def _deck(self, db) -> dict[str, int]:
        ids = {
            "dili": _add(db, "dili, di", "no; not"),
            "mo": _add(db, "mo", "you (plural); your (one person)"),
            "ko": _add(db, "ko", "my; by me", "genitive"),
        }
        db.set_suspended(ids["ko"], True, Direction.RECOGNITION)
        return ids

    def test_a_dry_run_lists_and_writes_nothing(self, tmp_path, capsys):
        url, db = self._db(tmp_path)
        ids = self._deck(db)
        assert self._main(url) == 0
        out = capsys.readouterr().out
        assert "RENAME" in out and "GLOSS" in out and "UNSUSPEND" in out and "DRY RUN" in out
        with db._get_conn() as conn:
            assert conn.execute("SELECT text FROM collocations WHERE id=?", (ids["dili"],)).fetchone()[0] == "dili, di"
        assert _state(db, ids["ko"], "recognition") == "suspended"

    def test_apply_renames_reglosses_unsuspends_and_marks_them_for_the_push(self, tmp_path, capsys):
        url, db = self._db(tmp_path)
        ids = self._deck(db)
        assert self._main(url, "--apply") == 0
        with db._get_conn() as conn:
            row = conn.execute(
                "SELECT text, guid, dirty_fields FROM collocations WHERE id=?", (ids["dili"],)
            ).fetchone()
            assert (row["text"], row["guid"]) == ("dili", compute_guid("dili", LANG, ""))
            assert "text" in row["dirty_fields"]
            mo = conn.execute("SELECT translation, dirty_fields FROM collocations WHERE id=?", (ids["mo"],)).fetchone()
            assert mo["translation"] == "you (plural)" and "translation" in mo["dirty_fields"]
            dirty = conn.execute(
                "SELECT dirty_fsrs FROM collocation_directions WHERE collocation_id=? AND direction='recognition'",
                (ids["ko"],),
            ).fetchone()[0]
        assert dirty == 1
        assert _state(db, ids["ko"], "recognition") != "suspended"

        # Nothing left: the second run is a no-op.
        assert self._main(url, "--apply") == 0
        assert "Nothing to do" in capsys.readouterr().out
