"""The backfill_dictionary_extras CLI: a learner's TT DB with no Anki gets Wolff's layers (tunatale-h0q3.2)."""

from __future__ import annotations

import pytest

from app.cards.dictionary_extras import dictionary_extras
from app.models.syntactic_unit import BackField, SyntacticUnit
from app.srs.database import SRSDatabase
from scripts.backfill_dictionary_extras import main


def _unit(text: str, translation: str, **kw) -> SyntacticUnit:
    return SyntacticUnit(text=text, translation=translation, word_count=1, difficulty=1, source="corpus", **kw)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "users" / "2" / "tunatale_ceb.db"
    path.parent.mkdir(parents=True)
    db = SRSDatabase(str(path))
    db.add_collocation(_unit("maayo", "good"), language_code="ceb")
    # A card from before the layers were filled at creation: the case this script is for.
    db.set_extras(db.get_collocation_id_by_guid(db.get_collocation("maayo").guid), ())
    db.add_collocation(_unit("xyzzy", "nothing"), language_code="ceb")
    # Already carries a layer (a hand fix, or a previous run): left alone.
    db.add_collocation(_unit("tulo", "three", extras=(BackField("Spelling", "tuló (hand)", "headword"),)), "ceb")
    # Unrelated extras on a card: kept, and the layers are added after them.
    db.add_collocation(_unit("tan-aw", "to look", extras=(BackField("Note", "n", "details"),)), "ceb")
    db.close()
    return path


def _extras(path, text):
    db = SRSDatabase(str(path))
    try:
        return db.get_collocation(text).syntactic_unit.extras
    finally:
        db.close()


def test_a_dry_run_reports_and_writes_nothing(db_path, capsys):
    assert main(["--db", str(db_path), "--language", "ceb"]) == 0
    out = capsys.readouterr().out
    assert "[DRY RUN]" in out and "would fill 2" in out and "1 already" in out
    assert "xyzzy\tnothing" in out
    assert _extras(db_path, "maayo") == ()
    assert not list(db_path.parent.glob("*.pre-h0q32-*"))


def test_apply_backs_up_then_fills_and_never_overwrites(db_path, capsys):
    assert main(["--db", str(db_path), "--language", "ceb", "--apply"]) == 0
    assert "[DONE]" in capsys.readouterr().out
    assert len(list(db_path.parent.glob("tunatale_ceb.db.pre-h0q32-*"))) == 1
    assert _extras(db_path, "maayo") == dictionary_extras("ceb", "maayo", "good")
    assert _extras(db_path, "tulo") == (BackField("Spelling", "tuló (hand)", "headword"),)
    assert _extras(db_path, "tan-aw") == (
        BackField("Note", "n", "details"),
        *dictionary_extras("ceb", "tan-aw", "to look"),
    )
    assert _extras(db_path, "xyzzy") == ()


def test_refuses_a_db_whose_cards_come_from_anki(db_path, capsys):
    # There the pull owns extras and would wipe or fight these writes; the
    # owner's cards get the layers through Anki (backfill_dictionary_fields).
    db = SRSDatabase(str(db_path))
    db.set_anki_ids(db.get_collocation("maayo").guid, 123, {})
    db.close()
    assert main(["--db", str(db_path), "--language", "ceb", "--apply"]) == 1
    assert "linked to Anki" in capsys.readouterr().err
    assert _extras(db_path, "maayo") == ()


def test_refuses_a_language_without_a_dictionary(db_path, capsys):
    assert main(["--db", str(db_path), "--language", "no"]) == 1
    assert "no dictionary" in capsys.readouterr().err


def test_refuses_a_missing_db(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "nope.db"), "--language", "ceb"]) == 1
    assert "not found" in capsys.readouterr().err


def test_refresh_replaces_layers_that_differ_and_keeps_other_extras(db_path, capsys):
    assert main(["--db", str(db_path), "--language", "ceb", "--apply"]) == 0
    assert main(["--db", str(db_path), "--language", "ceb", "--apply", "--refresh"]) == 0
    out = capsys.readouterr().out
    assert "refreshed 1" in out  # tulo's hand-set layer
    assert _extras(db_path, "tulo") == dictionary_extras("ceb", "tulo", "three")
    assert _extras(db_path, "tan-aw") == (
        BackField("Note", "n", "details"),
        *dictionary_extras("ceb", "tan-aw", "to look"),
    )
