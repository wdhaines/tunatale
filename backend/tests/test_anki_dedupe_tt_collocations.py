"""Tests for app.anki.dedupe_tt_collocations.

Merges 3 known pairs of TT collocations that both link to the same Anki note.
sync_pull only refreshes the first cid it finds per anki_note_id, leaving the
duplicate with stale anki_due=NULL — which Layer 33's phantom-direction logic
(_merge_directions in srs.py) then sinks to the bottom of the new queue. The
user-reported symptom: ulica disappears from TT's new-queue head even though
Anki shows it next.

Merge rule (per direction with conflict): take max(reps), max(stability),
max(lapses), and the field set (state/due_date/last_review/difficulty/
anki_card_id/anki_due) from the row with the later last_review (preferring
non-NULL anki_card_id/anki_due if mixed).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.anki.dedupe_tt_collocations import DEDUPE_PAIRS, DedupePair, apply_dedupe, main


def _make_tt_db(tmp_path: Path) -> Path:
    db = tmp_path / "tt.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE collocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT UNIQUE NOT NULL,
            translation TEXT NOT NULL DEFAULT '',
            language_code TEXT NOT NULL DEFAULT 'sl',
            guid TEXT,
            disambig_key TEXT NOT NULL DEFAULT '',
            anki_note_id INTEGER,
            card_type TEXT DEFAULT 'vocab',
            source_sentence TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE collocation_directions (
            collocation_id INTEGER NOT NULL,
            direction TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'new',
            due_date TEXT NOT NULL DEFAULT '2026-01-01',
            stability REAL NOT NULL DEFAULT 1.0,
            fsrs_difficulty REAL NOT NULL DEFAULT 5.0,
            reps INTEGER NOT NULL DEFAULT 0,
            lapses INTEGER NOT NULL DEFAULT 0,
            anki_card_id INTEGER,
            anki_due INTEGER,
            last_review TEXT,
            dirty_fsrs INTEGER DEFAULT 0,
            PRIMARY KEY (collocation_id, direction)
        );
        CREATE TABLE media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collocation_id INTEGER,
            kind TEXT NOT NULL,
            filename TEXT NOT NULL,
            anki_filename TEXT
        );
        CREATE TABLE collocation_tags (
            collocation_id INTEGER NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (collocation_id, tag)
        );
    """)
    conn.commit()
    conn.close()
    return db


def _add_coll(
    tt_path: Path,
    *,
    cid: int,
    text: str,
    anki_note_id: int,
    translation: str = "",
    card_type: str = "vocab",
) -> None:
    conn = sqlite3.connect(str(tt_path))
    conn.execute(
        "INSERT INTO collocations (id, text, translation, anki_note_id, card_type, guid) VALUES (?, ?, ?, ?, ?, ?)",
        (cid, text, translation, anki_note_id, card_type, f"g_{cid}"),
    )
    conn.commit()
    conn.close()


def _add_dir(
    tt_path: Path,
    *,
    cid: int,
    direction: str,
    state: str = "review",
    reps: int = 0,
    lapses: int = 0,
    stability: float = 1.0,
    difficulty: float = 5.0,
    due_date: str = "2026-05-13",
    last_review: str | None = None,
    anki_card_id: int | None = None,
    anki_due: int | None = None,
) -> None:
    conn = sqlite3.connect(str(tt_path))
    conn.execute(
        "INSERT INTO collocation_directions (collocation_id, direction, state, reps, lapses, "
        "stability, fsrs_difficulty, due_date, last_review, anki_card_id, anki_due) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, direction, state, reps, lapses, stability, difficulty, due_date, last_review, anki_card_id, anki_due),
    )
    conn.commit()
    conn.close()


def _add_media(tt_path: Path, *, cid: int, kind: str, filename: str) -> None:
    conn = sqlite3.connect(str(tt_path))
    conn.execute(
        "INSERT INTO media (collocation_id, kind, filename, anki_filename) VALUES (?, ?, ?, ?)",
        (cid, kind, filename, filename),
    )
    conn.commit()
    conn.close()


# ── Constants ────────────────────────────────────────────────────────────────


def test_dedupe_pairs_targets_three_known_pairs():
    assert len(DEDUPE_PAIRS) == 3
    pairs = {(p.winner_cid, p.loser_cid) for p in DEDUPE_PAIRS}
    assert pairs == {(626, 263), (363, 707), (409, 802)}


# ── apply_dedupe ─────────────────────────────────────────────────────────────


def test_loser_direction_not_in_winner_is_copied(tmp_path):
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=10, text="winner", anki_note_id=1)
    _add_coll(tt_path, cid=11, text="loser", anki_note_id=1)
    # Winner has only production; loser has only recognition.
    _add_dir(tt_path, cid=10, direction="production", state="new", reps=0)
    _add_dir(tt_path, cid=11, direction="recognition", state="review", reps=5, last_review="2026-04-15T00:00:00+00:00")

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_dedupe(conn, DedupePair(winner_cid=10, loser_cid=11))

    rows = sorted(
        tuple(r)
        for r in conn.execute(
            "SELECT direction, reps, state FROM collocation_directions WHERE collocation_id=10"
        ).fetchall()
    )
    assert rows == [("production", 0, "new"), ("recognition", 5, "review")]
    # Loser gone
    assert conn.execute("SELECT COUNT(*) FROM collocations WHERE id=11").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM collocation_directions WHERE collocation_id=11").fetchone()[0] == 0


def test_conflicting_direction_picks_later_last_review_with_max_metrics(tmp_path):
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=10, text="winner", anki_note_id=1)
    _add_coll(tt_path, cid=11, text="loser", anki_note_id=1)
    # Both have recognition; loser was reviewed later.
    _add_dir(
        tt_path,
        cid=10,
        direction="recognition",
        state="review",
        reps=4,
        stability=13.78,
        difficulty=5.0,
        last_review="2026-04-07T00:00:00+00:00",
        due_date="2026-05-28",
        anki_card_id=99,
        anki_due=None,
    )
    _add_dir(
        tt_path,
        cid=11,
        direction="recognition",
        state="review",
        reps=3,
        stability=30.03,
        difficulty=4.5,
        last_review="2026-04-15T00:00:00+00:00",
        due_date="2026-07-04",
        anki_card_id=99,
        anki_due=4564,
    )

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_dedupe(conn, DedupePair(winner_cid=10, loser_cid=11))

    row = conn.execute(
        "SELECT reps, stability, due_date, last_review, anki_due FROM collocation_directions "
        "WHERE collocation_id=10 AND direction='recognition'"
    ).fetchone()
    # max(reps), max(stability)
    assert row[0] == 4
    assert row[1] == 30.03
    # due_date + last_review from loser (later)
    assert row[2] == "2026-07-04"
    assert row[3] == "2026-04-15T00:00:00+00:00"
    # anki_due populated (winner had NULL, loser had 4564)
    assert row[4] == 4564


def test_winner_kept_when_later_last_review_is_winners(tmp_path):
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=10, text="winner", anki_note_id=1)
    _add_coll(tt_path, cid=11, text="loser", anki_note_id=1)
    _add_dir(
        tt_path,
        cid=10,
        direction="recognition",
        state="review",
        reps=5,
        stability=96.0,
        last_review="2026-04-20T00:00:00+00:00",
        due_date="2026-09-15",
        anki_card_id=99,
        anki_due=8500,
    )
    _add_dir(
        tt_path,
        cid=11,
        direction="recognition",
        state="review",
        reps=1,
        stability=3.9,
        last_review="2026-04-04T00:00:00+00:00",
        due_date="2026-09-09",
        anki_card_id=99,
        anki_due=4564,
    )

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_dedupe(conn, DedupePair(winner_cid=10, loser_cid=11))

    row = conn.execute(
        "SELECT reps, stability, due_date, last_review FROM collocation_directions "
        "WHERE collocation_id=10 AND direction='recognition'"
    ).fetchone()
    assert row == (5, 96.0, "2026-09-15", "2026-04-20T00:00:00+00:00")


def test_loser_media_deleted_winner_untouched(tmp_path):
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=10, text="winner", anki_note_id=1)
    _add_coll(tt_path, cid=11, text="loser", anki_note_id=1)
    _add_media(tt_path, cid=10, kind="image", filename="img.jpg")
    _add_media(tt_path, cid=11, kind="image", filename="img.jpg")
    _add_dir(tt_path, cid=10, direction="recognition")
    _add_dir(tt_path, cid=11, direction="recognition")

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_dedupe(conn, DedupePair(winner_cid=10, loser_cid=11))

    winner_media = conn.execute("SELECT filename FROM media WHERE collocation_id=10").fetchall()
    loser_media = conn.execute("SELECT filename FROM media WHERE collocation_id=11").fetchall()
    assert winner_media == [("img.jpg",)]
    assert loser_media == []


def test_loser_with_no_media_or_tags_still_merges_clean(tmp_path):
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=10, text="winner", anki_note_id=1)
    _add_coll(tt_path, cid=11, text="loser", anki_note_id=1)
    _add_dir(tt_path, cid=10, direction="recognition", reps=2)
    _add_dir(tt_path, cid=11, direction="recognition", reps=1)
    # No media, no tags on either.
    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_dedupe(conn, DedupePair(winner_cid=10, loser_cid=11))
    assert conn.execute("SELECT COUNT(*) FROM collocations WHERE id=11").fetchone()[0] == 0


def test_loser_tags_deleted(tmp_path):
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=10, text="winner", anki_note_id=1)
    _add_coll(tt_path, cid=11, text="loser", anki_note_id=1)
    _add_dir(tt_path, cid=10, direction="recognition")
    _add_dir(tt_path, cid=11, direction="recognition")
    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    conn.execute("INSERT INTO collocation_tags (collocation_id, tag) VALUES (11, 'old')")
    conn.commit()
    apply_dedupe(conn, DedupePair(winner_cid=10, loser_cid=11))
    assert conn.execute("SELECT COUNT(*) FROM collocation_tags WHERE collocation_id=11").fetchone()[0] == 0


def test_apply_skips_when_winner_missing(tmp_path):
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=11, text="loser", anki_note_id=1)
    _add_dir(tt_path, cid=11, direction="recognition")
    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    result = apply_dedupe(conn, DedupePair(winner_cid=10, loser_cid=11))
    assert result is False
    # Loser untouched
    assert conn.execute("SELECT COUNT(*) FROM collocations WHERE id=11").fetchone()[0] == 1


def test_apply_skips_when_loser_missing(tmp_path):
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=10, text="winner", anki_note_id=1)
    _add_dir(tt_path, cid=10, direction="recognition")
    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    result = apply_dedupe(conn, DedupePair(winner_cid=10, loser_cid=11))
    assert result is False
    # Winner untouched
    assert conn.execute("SELECT COUNT(*) FROM collocation_directions WHERE collocation_id=10").fetchone()[0] == 1


# ── max metrics when winner is later but loser has higher reps ──────────────


def test_anki_card_id_and_due_pulled_from_other_row_when_later_row_has_nulls(tmp_path):
    """When the 'later' row has NULL anki_card_id/due but the other row has values,
    take the populated ones (so we don't lose Anki linkage)."""
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=10, text="winner", anki_note_id=1)
    _add_coll(tt_path, cid=11, text="loser", anki_note_id=1)
    # Winner is the later row but has NULL anki_card_id + anki_due.
    _add_dir(
        tt_path,
        cid=10,
        direction="recognition",
        reps=2,
        stability=5.0,
        last_review="2026-05-01T00:00:00+00:00",
        anki_card_id=None,
        anki_due=None,
    )
    # Loser is earlier but has populated Anki linkage.
    _add_dir(
        tt_path,
        cid=11,
        direction="recognition",
        reps=1,
        stability=3.0,
        last_review="2026-04-01T00:00:00+00:00",
        anki_card_id=42,
        anki_due=1234,
    )
    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_dedupe(conn, DedupePair(winner_cid=10, loser_cid=11))
    row = conn.execute(
        "SELECT anki_card_id, anki_due FROM collocation_directions WHERE collocation_id=10 AND direction='recognition'"
    ).fetchone()
    assert tuple(row) == (42, 1234)


def test_max_metrics_taken_from_either_row(tmp_path):
    """reps and stability are maxed across both rows even when the later last_review picks a row with lower values."""
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=10, text="winner", anki_note_id=1)
    _add_coll(tt_path, cid=11, text="loser", anki_note_id=1)
    # Winner: later last_review, fewer reps.
    _add_dir(
        tt_path,
        cid=10,
        direction="recognition",
        reps=2,
        stability=5.0,
        lapses=0,
        last_review="2026-05-01T00:00:00+00:00",
    )
    # Loser: earlier last_review, more reps.
    _add_dir(
        tt_path,
        cid=11,
        direction="recognition",
        reps=7,
        stability=20.0,
        lapses=2,
        last_review="2026-04-01T00:00:00+00:00",
    )
    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    apply_dedupe(conn, DedupePair(winner_cid=10, loser_cid=11))
    row = conn.execute(
        "SELECT reps, stability, lapses, last_review FROM collocation_directions WHERE collocation_id=10"
    ).fetchone()
    assert row[0] == 7  # max reps
    assert row[1] == 20.0  # max stability
    assert row[2] == 2  # max lapses
    assert row[3] == "2026-05-01T00:00:00+00:00"  # latest last_review


# ── main / CLI ──────────────────────────────────────────────────────────────


def test_main_returns_1_when_tt_db_missing(tmp_path):
    rc = main(["--tt-db", str(tmp_path / "nope.db")])
    assert rc == 1


def test_main_dry_run_does_not_write(tmp_path, capsys):
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=626, text="ulica", anki_note_id=1)
    _add_coll(tt_path, cid=263, text="[street/road]", anki_note_id=1)
    _add_dir(tt_path, cid=626, direction="recognition")
    _add_dir(tt_path, cid=263, direction="recognition")

    rc = main(["--dry-run", "--tt-db", str(tt_path)])
    assert rc == 0
    assert "Plan:" in capsys.readouterr().out
    conn = sqlite3.connect(str(tt_path))
    assert conn.execute("SELECT COUNT(*) FROM collocations WHERE id=263").fetchone()[0] == 1


def test_main_apply_writes(tmp_path, capsys):
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=626, text="ulica", anki_note_id=1)
    _add_coll(tt_path, cid=263, text="[street/road]", anki_note_id=1)
    _add_dir(tt_path, cid=626, direction="recognition", reps=4)
    _add_dir(tt_path, cid=263, direction="recognition", reps=3, last_review="2026-04-15T00:00:00+00:00")

    rc = main(["--tt-db", str(tt_path)])
    assert rc == 0
    assert "Applied:" in capsys.readouterr().out
    conn = sqlite3.connect(str(tt_path))
    assert conn.execute("SELECT COUNT(*) FROM collocations WHERE id=263").fetchone()[0] == 0
    # max(reps) = 4
    reps = conn.execute(
        "SELECT reps FROM collocation_directions WHERE collocation_id=626 AND direction='recognition'"
    ).fetchone()[0]
    assert reps == 4


def test_main_no_targets_present_returns_0(tmp_path, capsys):
    tt_path = _make_tt_db(tmp_path)
    rc = main(["--tt-db", str(tt_path)])
    assert rc == 0
    assert "Applied: {'merged': 0}" in capsys.readouterr().out
