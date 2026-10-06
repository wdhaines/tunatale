"""The seed_base_list CLI: dry run, --add in list order, --position behind the waiting cards."""

from __future__ import annotations

import sqlite3

import pytest

from app.config import settings
from app.models.srs_item import Direction
from app.srs.base_list import BACK_BASE
from app.srs.database import SRSDatabase
from scripts.seed_base_list import main

TSV = """category\tenglish\tcebuano\tstatus
animal\tdog\tiro\tdictionary
animal\tcat\tiring\tdictionary
location\thotel\thotel\tunconfirmed
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'ceb.db'}"
    monkeypatch.setattr(settings, "database_urls", {"ceb": url})
    collection = tmp_path / "tt_collection.anki2"
    conn = sqlite3.connect(collection)
    conn.executescript(
        """
        CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, dty INTEGER,
            usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT);
        CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
            tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT);
        CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
            usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, factor INTEGER,
            reps INTEGER, lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT);
        INSERT INTO col VALUES (1, 1704067200, 0, 0, 11, 0, 5, 0, '{}', '{}', '{}', '{}', '{}');
        INSERT INTO notes VALUES (7, 'g', 1, 0, 0, '', 'iring', 'iring', 0, 0, '');
        INSERT INTO cards VALUES (70, 7, 1, 0, 0, 0, 0, 0, -1000300, 0, 0, 0, 0, 0, 0, 0, 0, '{}');
        INSERT INTO cards VALUES (71, 7, 1, 1, 0, 0, 0, 0, -1000299, 0, 0, 0, 0, 0, 0, 0, 0, '{}');
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings, "tt_collection_path", collection)
    monkeypatch.setattr(settings, "anki_backup_dir", tmp_path / "backups")
    list_path = tmp_path / "base.tsv"
    list_path.write_text(TSV, encoding="utf-8")
    return SRSDatabase(url), collection, list_path


def test_a_dry_run_writes_nothing(env, capsys):
    db, _, list_path = env
    assert main(["--language", "ceb", "--list", str(list_path), "--add", "5"]) == 0
    out = capsys.readouterr().out
    assert "2 to add" in out and "1 unconfirmed" in out and "hotel (hotel)" in out
    assert db.count_collocations() == 0


def test_add_then_position(env, capsys):
    db, collection, list_path = env
    main(["--language", "ceb", "--list", str(list_path), "--add", "1", "--apply"])
    assert db.get_collocation("iro") is not None and db.get_collocation("iring") is None  # list order
    main(["--language", "ceb", "--list", str(list_path), "--add", "1", "--apply"])
    iring = db.get_collocation("iring")
    db.set_anki_ids(iring.guid, 7, {Direction.RECOGNITION: 70, Direction.PRODUCTION: 71})  # the sync

    main(["--language", "ceb", "--list", str(list_path), "--position", "--apply"])
    assert "Moved 2 card(s)" in capsys.readouterr().out
    conn = sqlite3.connect(collection)
    rows = conn.execute("SELECT id, due, usn FROM cards ORDER BY id").fetchall()
    assert rows == [(70, BACK_BASE + 2, -1), (71, BACK_BASE + 3, -1)]
    assert conn.execute("SELECT usn FROM col").fetchone() == (5,)  # the sync anchor is untouched
    conn.close()
    assert db.get_collocation("iring").directions[Direction.PRODUCTION].anki_due == BACK_BASE + 3

    main(["--language", "ceb", "--list", str(list_path), "--position", "--apply"])
    assert "Moved 0 card(s)" in capsys.readouterr().out


def test_no_anki_positions_a_learner_deck_without_opening_the_collection(env, tmp_path, monkeypatch, capsys):
    _, _, list_path = env
    learner_path = tmp_path / "learner.db"
    learner = SRSDatabase(f"sqlite:///{learner_path}")
    main(["--language", "ceb", "--list", str(list_path), "--tt-db", str(learner_path), "--add", "2", "--apply"])
    learner.set_anki_state_cache("session_main_queue", "stale")
    # No collection to open: touching it would raise.
    monkeypatch.setattr(settings, "tt_collection_path", tmp_path / "absent.anki2")
    args = ["--language", "ceb", "--list", str(list_path), "--tt-db", str(learner_path), "--position", "--no-anki"]

    assert main(args) == 0
    assert "4 list card(s) to place" in capsys.readouterr().out
    assert learner.get_collocation("iring").directions[Direction.PRODUCTION].anki_due != BACK_BASE + 3  # dry run

    assert main([*args, "--apply"]) == 0
    assert "Moved 4 card(s) in TunaTale" in capsys.readouterr().out
    assert learner.get_collocation("iro").directions[Direction.RECOGNITION].anki_due == BACK_BASE
    assert learner.get_collocation("iring").directions[Direction.PRODUCTION].anki_due == BACK_BASE + 3
    # The frozen queue is dropped: no sync will ever come along to rebuild it.
    assert learner.get_anki_state_cache("session_main_queue") is None

    assert main([*args, "--apply"]) == 0
    assert "Moved 0 card(s) in TunaTale" in capsys.readouterr().out


def test_no_anki_refuses_a_deck_linked_to_anki(env, capsys):
    db, collection, list_path = env
    main(["--language", "ceb", "--list", str(list_path), "--add", "2", "--apply"])
    iring = db.get_collocation("iring")
    db.set_anki_ids(iring.guid, 7, {Direction.RECOGNITION: 70, Direction.PRODUCTION: 71})
    before = collection.read_bytes()
    assert main(["--language", "ceb", "--list", str(list_path), "--position", "--no-anki", "--apply"]) == 2
    assert "REFUSING" in capsys.readouterr().out
    assert db.get_collocation("iring").directions[Direction.PRODUCTION].anki_due != BACK_BASE + 3
    assert collection.read_bytes() == before


def test_position_refuses_when_a_planned_card_is_no_longer_new_in_the_collection(env, capsys):
    """For a new card ``due`` is its queue position; for a review card it is the
    due DAY. TunaTale plans from its own state, so a card it still calls new but
    the collection has moved on must stop the run, not have its due date
    overwritten with a position."""
    db, collection, list_path = env
    main(["--language", "ceb", "--list", str(list_path), "--add", "2", "--apply"])
    iring = db.get_collocation("iring")
    db.set_anki_ids(iring.guid, 7, {Direction.RECOGNITION: 70, Direction.PRODUCTION: 71})
    conn = sqlite3.connect(collection)
    conn.execute("UPDATE cards SET type = 2, queue = 2, due = 4700 WHERE id = 71")
    conn.commit()
    conn.close()
    capsys.readouterr()

    assert main(["--language", "ceb", "--list", str(list_path), "--position", "--apply"]) == 2
    out = capsys.readouterr().out
    assert "REFUSING" in out and "1 planned card(s)" in out and "71" in out
    conn = sqlite3.connect(collection)
    # Nothing moved: not the review card, and not the new card planned beside it.
    assert conn.execute("SELECT id, due, usn FROM cards ORDER BY id").fetchall() == [(70, -1000300, 0), (71, 4700, 0)]
    conn.close()
    assert db.get_collocation("iring").directions[Direction.RECOGNITION].anki_due != BACK_BASE + 2


def test_position_refuses_a_descending_deck(env, capsys):
    db, _, list_path = env
    db.set_anki_state_cache("new_card_gather_priority", "2")
    assert main(["--language", "ceb", "--list", str(list_path), "--position", "--apply"]) == 2
    assert "REFUSING" in capsys.readouterr().out


def test_list_name_labels_the_minted_card(env):
    """A second list (the frequency seed) must say which list minted a card."""
    db, _, list_path = env
    main(["--language", "ceb", "--list", str(list_path), "--add", "1", "--apply", "--list-name", "Next words"])
    assert db.get_collocation("iro").syntactic_unit.note == "Next words · animal"


def test_the_default_list_name_is_the_625(env):
    db, _, list_path = env
    main(["--language", "ceb", "--list", str(list_path), "--add", "1", "--apply"])
    assert db.get_collocation("iro").syntactic_unit.note == "Fluent Forever 625 · animal"
