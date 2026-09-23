"""A language's deck includes its subdecks (tunatale-w4m7.8, design step 1).

The Pimsleur Tagalog deck keeps ALL 1,218 cards in 60 lesson subdecks
(`2. Pimsleur Tagalog::Level 1::Lesson 01` …); the parent and both Level decks
hold none (measured read-only, 2026-09-23). An exact-deck read therefore imports
nothing. The Slovene and Norwegian decks have no subdecks at all, so reading the
tree leaves their card sets unchanged — pinned below.

Anki stores subdeck names two ways: `::` in the legacy `col.decks` JSON, and the
unit separator `\x1f` in the modern `decks` table. Both are exercised.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from app.plugins.anki_sync.sqlite_reader import fetch_notes_for_deck_tree, find_deck_id, find_deck_tree_ids

ROOT = "2. Pimsleur Tagalog"
DECKS = {
    10: ROOT,  # the parent: no cards of its own
    11: f"{ROOT}::Level 1",
    12: f"{ROOT}::Level 1::Lesson 01",
    13: f"{ROOT}::Level 2::Lesson 30",  # its Level parent is absent: still a descendant
    20: f"{ROOT} Extra",  # a SIBLING that merely shares the prefix — not a descendant
    21: f"{ROOT}Lesson",  # no separator at all — not a descendant
    30: "1. Slovene",  # unrelated, flat
}
# note id -> deck id of its cards
NOTES = {100: 12, 101: 13, 102: 20, 103: 21, 104: 30, 105: 11}


def _build(modern: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE notes (id INTEGER, guid TEXT, mid INTEGER, mod INTEGER, tags TEXT, flds TEXT)")
    conn.execute("CREATE TABLE cards (id INTEGER, nid INTEGER, did INTEGER, ord INTEGER)")
    if modern:
        conn.execute("CREATE TABLE col (decks TEXT)")
        conn.execute("INSERT INTO col VALUES ('{}')")
        conn.execute("CREATE TABLE decks (id INTEGER, name TEXT)")
        for did, name in DECKS.items():
            conn.execute("INSERT INTO decks VALUES (?, ?)", (did, name.replace("::", "\x1f")))
    else:
        conn.execute("CREATE TABLE col (decks TEXT)")
        conn.execute("INSERT INTO col VALUES (?)", (json.dumps({str(d): {"name": n} for d, n in DECKS.items()}),))
    for nid, did in NOTES.items():
        conn.execute("INSERT INTO notes VALUES (?, ?, 1, 0, '', ?)", (nid, f"g{nid}", f"front{nid}\x1fback{nid}"))
        conn.execute("INSERT INTO cards VALUES (?, ?, ?, 0)", (nid * 10, nid, did))
        conn.execute("INSERT INTO cards VALUES (?, ?, ?, 1)", (nid * 10 + 1, nid, did))
    return conn


@pytest.fixture(params=[False, True], ids=["legacy-json", "modern-table"])
def conn(request):
    c = _build(request.param)
    yield c
    c.close()


def test_the_tree_is_the_deck_and_every_descendant_and_nothing_else(conn):
    assert sorted(find_deck_tree_ids(conn, ROOT)) == [10, 11, 12, 13]


def test_a_flat_deck_is_its_own_tree(conn):
    assert find_deck_tree_ids(conn, "1. Slovene") == [30]


def test_a_missing_deck_has_no_tree(conn):
    assert find_deck_tree_ids(conn, "No Such Deck") == []


def test_notes_come_from_every_subdeck_even_when_the_parent_holds_none(conn):
    notes = fetch_notes_for_deck_tree(conn, ROOT)
    assert sorted(n.id for n in notes) == [100, 101, 105]


def test_a_flat_deck_reads_exactly_what_the_exact_read_did(conn):
    # The sl/no guarantee: no subdecks, so the tree read equals the old read.
    assert [n.id for n in fetch_notes_for_deck_tree(conn, "1. Slovene")] == [104]


def test_a_note_with_cards_in_two_subdecks_is_read_once(conn):
    conn.execute("INSERT INTO cards VALUES (999, 100, 13, 1)")
    assert [n.id for n in fetch_notes_for_deck_tree(conn, ROOT)].count(100) == 1


def test_exact_lookup_finds_a_subdeck_by_its_double_colon_name(conn):
    # The mint deck is a subdeck (`...::TunaTale`); the modern table stores it
    # with \x1f, so an exact lookup must normalize the separator.
    assert find_deck_id(conn, f"{ROOT}::Level 1::Lesson 01") == 12


# ── end to end: the importer and the sync reader both read the tree ───────────


@pytest.fixture
def subdeck_only_collection(tmp_path):
    """The Pimsleur shape: every card in a LESSON subdeck, none in the parent."""
    from tests.conftest import build_minimal_anki_db

    path = build_minimal_anki_db(tmp_path, deck_name="Parent::Lesson 01", use_decks_table=True)
    with sqlite3.connect(str(path)) as c:
        c.execute("INSERT INTO decks VALUES (?, ?, 0, 0, '{}')", (777, "Parent"))
    return path


def test_the_importer_reads_a_deck_whose_cards_are_all_in_subdecks(subdeck_only_collection, tmp_path):
    from app.plugins.anki_sync.import_seed import import_seed

    result = import_seed(
        anki_collection_path=subdeck_only_collection,
        anki_backup_dir=tmp_path / "bak",
        anki_media_path=tmp_path / "fake_media",
        deck_name="Parent",
        language_code="sl",
        tunatale_db_path=str(tmp_path / "tunatale.db"),
        media_dir=tmp_path / "media",
        fallback_log_path=tmp_path / "fallback.log",
    )
    assert result["new_parents"] == 5


def test_the_sync_reader_reads_a_deck_whose_cards_are_all_in_subdecks(subdeck_only_collection):
    from app.plugins.anki_sync.sync_reader import OfflineReader

    with sqlite3.connect(str(subdeck_only_collection)) as c:
        records = OfflineReader(c, "Parent", language_code="sl").get_note_records()
    assert len(records) == 5


def test_malformed_legacy_deck_entries_are_skipped_not_fatal():
    # A non-dict value, or a dict with no string name, is not a deck.
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE col (decks TEXT)")
    decks = {"1": "not-a-dict", "2": {"id": 2}, "3": {"name": 7}, "4": {"name": ROOT}}
    conn.execute("INSERT INTO col VALUES (?)", (json.dumps(decks),))
    assert find_deck_tree_ids(conn, ROOT) == [4]
    conn.close()
