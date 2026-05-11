"""Tests for count_anki_review_remaining_today() — mirrors Anki's deck-overview count."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from app.srs.queue_stats import count_anki_review_remaining_today
from tests._helpers.protobuf import pb_len_field, pb_varint_field


def _build_deck_common_blob(last_day_studied: int, new_studied: int = 0, review_studied: int = 0) -> bytes:
    """Build a minimal DeckCommon protobuf blob."""
    blob = b""
    if last_day_studied:
        blob += pb_varint_field(3, last_day_studied)
    if new_studied:
        blob += pb_varint_field(4, new_studied)
    if review_studied:
        blob += pb_varint_field(5, review_studied)
    return blob


def _build_deck_config_blob(
    reviews_per_day: int = 9999,
    new_per_day: int = 20,
    new_cards_ignore_review_limit: bool = False,
    bury_reviews: bool = False,
) -> bytes:
    """Build a minimal DeckConfig.Config protobuf blob."""
    blob = b""
    blob += pb_varint_field(7, 1 if new_cards_ignore_review_limit else 0)
    blob += pb_varint_field(9, new_per_day)
    blob += pb_varint_field(10, reviews_per_day)
    blob += pb_varint_field(28, 1 if bury_reviews else 0)
    return blob


def _build_deck_kind_blob(conf_id: int) -> bytes:
    """Build a minimal NormalKind protobuf (field 1 = conf_id)."""
    return pb_len_field(1, pb_varint_field(1, conf_id))


def build_review_test_db(tmp_path: Path, **kwargs) -> Path:
    """Create a minimal Anki collection.anki2 for review-count testing."""
    deck_name = kwargs.get("deck_name", "0. Slovene")
    deck_id = kwargs.get("deck_id", 12345)
    col_crt = kwargs.get("col_crt", 1704067200)
    num_notes = kwargs.get("num_notes", 5)
    cards_per_note = kwargs.get("cards_per_note", 2)
    bury_reviews = kwargs.get("bury_reviews", False)
    reviews_per_day = kwargs.get("reviews_per_day", 9999)
    new_cards_ignore_review_limit = kwargs.get("new_cards_ignore_review_limit", False)
    review_studied = kwargs.get("review_studied", 0)
    new_studied = kwargs.get("new_studied", 0)
    today_col_day = kwargs.get("today_col_day", 100)
    last_day_studied = kwargs.get("last_day_studied", today_col_day)

    db_path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(db_path))

    conn.execute("""CREATE TABLE col (
        id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER,
        dty INTEGER, usn INTEGER, ls INTEGER, conf TEXT, models TEXT,
        decks TEXT, dconf TEXT, tags TEXT)""")
    conn.execute("""CREATE TABLE notes (
        id INTEGER, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
        tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT)""")
    conn.execute("""CREATE TABLE cards (
        id INTEGER, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
        usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER,
        factor INTEGER, reps INTEGER, lapses INTEGER, left INTEGER,
        odue INTEGER, odid INTEGER, flags INTEGER, data TEXT)""")
    conn.execute("""CREATE TABLE decks (
        id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER,
        common BLOB, kind BLOB)""")
    conn.execute("""CREATE TABLE deck_config (
        id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER,
        config BLOB)""")

    conn.execute("INSERT INTO col VALUES (1,?,0,0,18,0,0,0,'{}','{}','{}','{}','{}')", (col_crt,))

    conf_id = 999
    kind_blob = _build_deck_kind_blob(conf_id)
    common_blob = _build_deck_common_blob(last_day_studied, new_studied, review_studied)
    conn.execute("INSERT INTO decks VALUES (?,?,0,0,?,?)", (deck_id, deck_name, common_blob, kind_blob))

    config_blob = _build_deck_config_blob(
        reviews_per_day=reviews_per_day,
        new_cards_ignore_review_limit=new_cards_ignore_review_limit,
        bury_reviews=bury_reviews,
    )
    conn.execute("INSERT INTO deck_config VALUES (?,?,0,0,?)", (conf_id, deck_name, config_blob))

    notes = []
    for i in range(num_notes):
        nid = 1000 + i
        guid = f"guid_{i}"
        notes.append((nid, guid, 1, 0, 0, "", f"word{i}\x1ftranslation{i}", f"word{i}", 0, 0, ""))
    conn.executemany("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?)", notes)

    review_cards = kwargs.get("review_cards")
    if review_cards is None:
        cards = []
        for i in range(num_notes):
            nid = 1000 + i
            for ord in range(cards_per_note):
                cid = nid * 10 + ord
                cards.append((cid, nid, deck_id, ord, 0, 0, 2, 2, today_col_day, 21, 2500, 5, 0, 0, 0, 0, 0, ""))
    else:
        cards = review_cards

    conn.executemany("INSERT INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", cards)
    conn.commit()
    conn.close()

    return db_path


def test_returns_none_when_collection_missing():
    result = count_anki_review_remaining_today(collection_path=Path("/nonexistent.anki2"))
    assert result is None


def test_register_unicase_collation_compares_case_folded():
    """Sanity test for the unicase collation helper — real Anki collections
    declare ``COLLATE unicase`` on ``decks.name`` etc., so any SELECT … WHERE
    name = ? against the real schema invokes the registered Python collation.
    Verify it case-folds correctly so e.g. 'SLOVENE' matches 'slovene'.
    """
    from app.srs.queue_stats import _register_unicase

    conn = sqlite3.connect(":memory:")
    _register_unicase(conn)
    conn.execute("CREATE TABLE t (name TEXT COLLATE unicase)")
    conn.execute("INSERT INTO t VALUES ('Slovene')")
    row = conn.execute("SELECT name FROM t WHERE name = 'SLOVENE'").fetchone()
    assert row is not None and row[0] == "Slovene"
    conn.close()


def test_no_bury_returns_raw_pool(tmp_path):
    """Pool of 10 cards across 5 notes, bury_reviews=false → returns 10."""
    db_path = build_review_test_db(tmp_path, num_notes=5, cards_per_note=2, bury_reviews=False)
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    assert result == 10


def test_bury_reviews_collapses_to_distinct_nid(tmp_path):
    """Same fixture with bury_reviews=true → returns 5 (distinct notes)."""
    db_path = build_review_test_db(tmp_path, num_notes=5, cards_per_note=2, bury_reviews=True)
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    assert result == 5


def test_cap_applied_when_review_studied_high(tmp_path):
    """Pool=200 distinct notes, reviews_per_day=50, review_studied=10 → returns 40."""
    db_path = build_review_test_db(
        tmp_path,
        num_notes=200,
        cards_per_note=1,
        bury_reviews=True,
        reviews_per_day=50,
        review_studied=10,
        new_studied=0,
        new_cards_ignore_review_limit=True,
        today_col_day=100,
        last_day_studied=100,
    )
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    # pool_count = 200, review_limit = 50 - 10 = 40, returns min(200, 40) = 40
    assert result == 40


def test_cap_subtracts_new_studied_when_not_ignoring_review_limit(tmp_path):
    """Pool=200 notes, reviews_per_day=50, review_studied=10, new_studied=5 → returns 35."""
    db_path = build_review_test_db(
        tmp_path,
        num_notes=200,
        cards_per_note=1,
        bury_reviews=True,
        reviews_per_day=50,
        review_studied=10,
        new_studied=5,
        new_cards_ignore_review_limit=False,
        today_col_day=100,
        last_day_studied=100,
    )
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    # pool_count = 200, review_limit = 50 - 10 - 5 = 35
    assert result == 35


def test_ignore_review_limit_skips_new_studied(tmp_path):
    """When new_cards_ignore_review_limit=True, new_studied not subtracted."""
    db_path = build_review_test_db(
        tmp_path,
        num_notes=200,
        cards_per_note=1,
        bury_reviews=True,
        reviews_per_day=50,
        review_studied=10,
        new_studied=5,
        new_cards_ignore_review_limit=True,
        today_col_day=100,
        last_day_studied=100,
    )
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    # review_limit = 50 - 10 = 40 (new_studied ignored)
    assert result == 40


def test_pool_count_returns_when_no_caps(tmp_path):
    """No conf_id → returns raw pool count."""
    db_path = build_review_test_db(tmp_path, num_notes=5, cards_per_note=2, bury_reviews=False)
    # Patch to skip the conf_id lookup
    with (
        patch("app.srs.queue_stats._compute_today_col_day", return_value=100),
        patch("app.srs.queue_stats._read_conf_id_for_deck", return_value=None),
    ):
        result = count_anki_review_remaining_today(collection_path=db_path)
    assert result == 10


def test_deck_not_found_returns_none(tmp_path):
    """Deck not in collection → returns None."""
    db_path = build_review_test_db(tmp_path, num_notes=0)
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    # _read_did_for_deck returns None for non-existent deck
    # Actually the deck exists, but let's test with wrong name
    from app.srs.queue_stats import count_anki_review_remaining_today as count_fn

    result = count_fn(collection_path=db_path, deck_name="Non-existent Deck")
    assert result is None


def test_missing_col_table_returns_none(tmp_path):
    """Missing col table → returns None."""
    db_path = build_review_test_db(tmp_path, num_notes=0)
    conn = sqlite3.connect(str(db_path))
    conn.execute("DROP TABLE col")
    conn.commit()
    conn.close()
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    assert result is None


def test_missing_decks_table_returns_none(tmp_path):
    """Missing decks table → returns None."""
    db_path = build_review_test_db(tmp_path, num_notes=0)
    conn = sqlite3.connect(str(db_path))
    conn.execute("DROP TABLE decks")
    conn.commit()
    conn.close()
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    assert result is None


def test_bury_reviews_from_deck_config(tmp_path):
    """Test that bury_reviews is read from deck_config blob (field 28)."""
    db_path = build_review_test_db(tmp_path, num_notes=5, cards_per_note=2, bury_reviews=True)
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    # bury_reviews=True → COUNT(DISTINCT nid)
    assert result == 5


def test_bury_reviews_subtracts_cross_queue_learning_siblings(tmp_path):
    """Anki gathers intraday-learning before reviews, adding their note IDs to
    seen_note_ids. When add_due_card hits a review whose note is already seen
    AND bury_reviews=true, it pre-buries the review and never adds it to the
    pool (rslib/.../queue/builder/gathering.rs:136-154).

    Setup: 5 notes × 2 cards = 10 cards.
      • 4 notes: both cards in queue=2 (queue=review).
      • 1 note: ord=0 in queue=2, ord=1 in queue=1 (learning sibling).
    Expected with bury_reviews=true: 5 distinct queue=2 nids minus the 1 with
    a learning sibling → 4.
    """
    today = 100
    deck_id = 12345
    cards = []
    # First 4 notes: both cards in queue=2
    for i in range(4):
        nid = 1000 + i
        for ord_ in range(2):
            cid = nid * 10 + ord_
            cards.append((cid, nid, deck_id, ord_, 0, 0, 2, 2, today, 21, 2500, 5, 0, 0, 0, 0, 0, ""))
    # 5th note: ord=0 in queue=2, ord=1 in queue=1 (learning)
    nid = 1004
    cards.append((nid * 10 + 0, nid, deck_id, 0, 0, 0, 2, 2, today, 21, 2500, 5, 0, 0, 0, 0, 0, ""))
    # queue=1 learning: due is a unix timestamp (any value)
    cards.append((nid * 10 + 1, nid, deck_id, 1, 0, 0, 1, 1, 1700000000, 0, 0, 1, 0, 1001, 0, 0, 0, ""))

    db_path = build_review_test_db(
        tmp_path,
        num_notes=5,
        cards_per_note=2,
        bury_reviews=True,
        deck_id=deck_id,
        review_cards=cards,
    )
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=today):
        result = count_anki_review_remaining_today(collection_path=db_path)
    assert result == 4, f"Expected 4 (5 distinct review-due nids minus 1 with learning sibling), got {result}"


def test_no_bury_does_not_subtract_learning_siblings(tmp_path):
    """When bury_reviews=false, the learning-sibling subtraction must NOT apply.
    Same fixture as above but bury_reviews=false → all 9 review-state cards
    are served (4 notes × 2 cards + 1 note × 1 card)."""
    today = 100
    deck_id = 12345
    cards = []
    for i in range(4):
        nid = 1000 + i
        for ord_ in range(2):
            cid = nid * 10 + ord_
            cards.append((cid, nid, deck_id, ord_, 0, 0, 2, 2, today, 21, 2500, 5, 0, 0, 0, 0, 0, ""))
    nid = 1004
    cards.append((nid * 10 + 0, nid, deck_id, 0, 0, 0, 2, 2, today, 21, 2500, 5, 0, 0, 0, 0, 0, ""))
    cards.append((nid * 10 + 1, nid, deck_id, 1, 0, 0, 1, 1, 1700000000, 0, 0, 1, 0, 1001, 0, 0, 0, ""))

    db_path = build_review_test_db(
        tmp_path,
        num_notes=5,
        cards_per_note=2,
        bury_reviews=False,
        deck_id=deck_id,
        review_cards=cards,
    )
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=today):
        result = count_anki_review_remaining_today(collection_path=db_path)
    assert result == 9, f"Expected 9 (raw queue=2 due count), got {result}"


def test_deck_config_no_bury_field(tmp_path):
    """When deck_config has no field 28, bury_reviews defaults to False."""
    db_path = build_review_test_db(tmp_path, num_notes=5, cards_per_note=2, bury_reviews=False)
    # Overwrite config blob without field 28
    conn = sqlite3.connect(str(db_path))
    conf_id = 999
    # Build config without bury_reviews field
    blob = b""
    blob += pb_varint_field(7, 0)  # new_cards_ignore_review_limit=False
    blob += pb_varint_field(9, 20)  # new_per_day=20
    blob += pb_varint_field(10, 9999)  # reviews_per_day=9999
    conn.execute("UPDATE deck_config SET config=? WHERE id=?", (blob, conf_id))
    conn.commit()
    conn.close()
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    # No bury_reviews field → defaults False → COUNT(*)
    assert result == 10


def test_deck_config_with_bury_field(tmp_path):
    """When deck_config has field 28=1, bury_reviews=True."""
    db_path = build_review_test_db(tmp_path, num_notes=5, cards_per_note=2, bury_reviews=False)
    # Overwrite config blob WITH field 28=1
    conn = sqlite3.connect(str(db_path))
    conf_id = 999
    blob = b""
    blob += pb_varint_field(7, 0)
    blob += pb_varint_field(9, 20)
    blob += pb_varint_field(10, 9999)
    blob += pb_varint_field(28, 1)  # bury_reviews=True
    conn.execute("UPDATE deck_config SET config=? WHERE id=?", (blob, conf_id))
    conn.commit()
    conn.close()
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    assert result == 5


def test_no_conf_id_returns_raw_pool(tmp_path):
    """When deck has no conf_id, return raw pool without cap."""
    db_path = build_review_test_db(tmp_path, num_notes=5, cards_per_note=2)
    # Remove conf_id from deck's kind blob
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE decks SET kind=NULL")
    conn.commit()
    conn.close()
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    # No conf_id → no cap, returns pool_count (10)
    assert result == 10


def test_studied_counters_zero_when_last_day_mismatch(tmp_path):
    """When last_day_studied != today_col_day, studied counts are 0."""
    db_path = build_review_test_db(
        tmp_path,
        num_notes=200,
        cards_per_note=1,
        bury_reviews=True,
        reviews_per_day=50,
        review_studied=10,
        new_studied=5,
        today_col_day=100,
        last_day_studied=99,  # Different from today_col_day
    )
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    # last_day mismatch → studied counts = 0 → review_limit = 50 - 0 = 50
    assert result == 50


def test_compute_today_col_day_actual_formula(tmp_path):
    """Test that _compute_today_col_day matches Anki's actual algorithm
    (scheduler/timing.rs::sched_timing_today_v2_new): local-date diff minus 1
    when local rollover hour hasn't been passed.
    """
    from datetime import UTC, datetime

    from app.srs.queue_stats import _compute_today_col_day

    crt = 1704067200  # Jan 1 2024 00:00 UTC
    db_path = build_review_test_db(tmp_path, num_notes=0, col_crt=crt)
    conn = sqlite3.connect(str(db_path))

    # 100 days + 1 hour after crt, EDT (-240 min west), rollover defaults to 4 in test DB?
    # The test DB doesn't set rollover; default fallback is 4 AM. Use UTC offset 0 so
    # the rollover hour comparison is straightforward against UTC midnight (4 AM UTC).
    fixed_now = datetime.fromtimestamp(crt + (100 * 86400) + 3600 * 5, tz=UTC)  # 5 AM UTC, past 4 AM rollover
    result = _compute_today_col_day(conn, now=fixed_now, local_offset_minutes_west=0)

    assert result == 100
    conn.close()


def test_count_returns_none_when_no_cards(tmp_path):
    """Empty cards table → returns 0."""
    db_path = build_review_test_db(tmp_path, num_notes=0)
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    assert result == 0


def test_compute_today_col_day_empty_col_table(tmp_path):
    """Line 47: _compute_today_col_day returns 0 when col table is empty."""
    from app.srs.queue_stats import _compute_today_col_day

    db_path = build_review_test_db(tmp_path, num_notes=0)
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM col")
    conn.commit()
    assert _compute_today_col_day(conn) == 0
    conn.close()


def test_returns_none_when_no_deck_configured(tmp_path):
    """Line 153: deck_name=None and settings.anki_deck_name=None → None."""
    from app.config import settings

    db_path = build_review_test_db(tmp_path)
    with patch.object(settings, "anki_deck_name", None):
        assert count_anki_review_remaining_today(collection_path=db_path) is None


def test_missing_deck_config_row_returns_pool(tmp_path):
    """Lines 85, 168→175, 190→201: conf_id resolves but deck_config row missing → raw pool."""
    db_path = build_review_test_db(tmp_path, num_notes=5, cards_per_note=2)
    conn = sqlite3.connect(str(db_path))
    conn.execute("DELETE FROM deck_config")
    conn.commit()
    conn.close()
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    # No deck_config row → no bury detection, no cap → COUNT(*) = 10
    assert result == 10


def test_reviews_per_day_defaults_when_field_missing(tmp_path):
    """Line 91: deck_config blob without field 10 → reviews_per_day defaults to 9999."""
    db_path = build_review_test_db(tmp_path, num_notes=5, cards_per_note=1)
    conn = sqlite3.connect(str(db_path))
    # Build config blob WITHOUT field 10 (reviews_per_day)
    blob = pb_varint_field(7, 0) + pb_varint_field(9, 20) + pb_varint_field(28, 0)
    conn.execute("UPDATE deck_config SET config = ?", (blob,))
    conn.commit()
    conn.close()
    with patch("app.srs.queue_stats._compute_today_col_day", return_value=100):
        result = count_anki_review_remaining_today(collection_path=db_path)
    # Default cap (9999) >> 5 pool, so result == 5
    assert result == 5


def test_read_today_studied_counts_missing_deck(tmp_path):
    """Line 111: _read_today_studied_counts returns (0,0) when deck row missing."""
    from app.srs.queue_stats import _read_today_studied_counts

    db_path = build_review_test_db(tmp_path, num_notes=0)
    conn = sqlite3.connect(str(db_path))
    assert _read_today_studied_counts(conn, did=99999, today_col_day=100) == (0, 0)
    conn.close()


def test_sqlite_error_during_query_returns_none(tmp_path):
    """Lines 203-205: sqlite3.Error inside the try block → None."""
    db_path = build_review_test_db(tmp_path)
    with patch("app.srs.queue_stats.sqlite3.connect", side_effect=sqlite3.Error("mock failure")):
        assert count_anki_review_remaining_today(collection_path=db_path) is None
