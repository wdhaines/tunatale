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


def test_position_refuses_a_descending_deck(env, capsys):
    db, _, list_path = env
    db.set_anki_state_cache("new_card_gather_priority", "2")
    assert main(["--language", "ceb", "--list", str(list_path), "--position", "--apply"]) == 2
    assert "REFUSING" in capsys.readouterr().out
