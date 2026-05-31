"""Tests for app.srs.relemmatize_collocations.

Re-lemmatizes single-word collocations with the configured lemmatizer.
Merges inflections into their base-form collocations or re-keys the lemma.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.srs.relemmatize_collocations import relemmatize


def _make_tt_db(tmp_path: Path) -> Path:
    db = tmp_path / "tt.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE collocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            translation TEXT NOT NULL DEFAULT '',
            language_code TEXT NOT NULL DEFAULT 'sl',
            word_count INTEGER NOT NULL DEFAULT 1,
            unit_difficulty INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'corpus',
            corpus_frequency INTEGER NOT NULL DEFAULT 0,
            lemma TEXT,
            guid TEXT UNIQUE,
            disambig_key TEXT NOT NULL DEFAULT '',
            anki_note_id INTEGER,
            dirty_fields TEXT NOT NULL DEFAULT '',
            last_synced_at TEXT,
            card_type TEXT DEFAULT 'vocab',
            source_sentence TEXT NOT NULL DEFAULT '',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(text, disambig_key)
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
    lemma: str | None = None,
    anki_note_id: int | None = None,
    word_count: int = 1,
) -> None:
    conn = sqlite3.connect(str(tt_path))
    conn.execute(
        "INSERT INTO collocations (id, text, lemma, anki_note_id, word_count, guid) VALUES (?, ?, ?, ?, ?, ?)",
        (cid, text, lemma, anki_note_id, word_count, f"g_{cid}"),
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


# ── Helpers ──────────────────────────────────────────────────────────────────


def _build_stub_lemmatizer(monkeypatch, mapping: dict[str, str]) -> None:
    """Monkeypatch get_lemmatizer to return a StubLemmatizer with the given mapping."""
    from tests._helpers.lemmatizer import StubLemmatizer

    stub = StubLemmatizer()
    for word, lemma in mapping.items():
        stub.set_lemma(word, lemma)
    from app.srs.lemmatizer import get_lemmatizer

    get_lemmatizer.cache_clear()
    monkeypatch.setattr("app.srs.lemmatizer.get_lemmatizer", lambda: stub)
    return stub


# ── Dry run ──────────────────────────────────────────────────────────────────


def test_dry_run_does_not_write(tmp_path, monkeypatch):
    _build_stub_lemmatizer(monkeypatch, {"hotelu": "hotel"})
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=1, text="hotelu", lemma="hotelu", anki_note_id=10)
    _add_dir(tt_path, cid=1, direction="recognition", reps=3)

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    audit = relemmatize(conn, dry_run=True)
    assert audit["rekeyed"] == 0
    assert audit["merged"] == 0
    # Lemma unchanged
    lemma = conn.execute("SELECT lemma FROM collocations WHERE id=1").fetchone()[0]
    assert lemma == "hotelu"


# ── Re-key ───────────────────────────────────────────────────────────────────


def test_rekey_when_target_lemma_not_present(tmp_path, monkeypatch):
    _build_stub_lemmatizer(monkeypatch, {"hotelu": "hotel"})
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=1, text="hotelu", lemma="hotelu")
    _add_dir(tt_path, cid=1, direction="recognition", reps=3)

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    audit = relemmatize(conn, dry_run=False)
    assert audit["rekeyed"] == 1
    assert audit["merged"] == 0
    lemma = conn.execute("SELECT lemma FROM collocations WHERE id=1").fetchone()[0]
    assert lemma == "hotel"


def test_rekey_skip_when_lemma_unchanged(tmp_path, monkeypatch):
    _build_stub_lemmatizer(monkeypatch, {"hotel": "hotel"})
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=1, text="hotel", lemma="hotel")
    _add_dir(tt_path, cid=1, direction="recognition")

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    audit = relemmatize(conn, dry_run=False)
    assert audit["rekeyed"] == 0
    assert audit["merged"] == 0


# ── Merge ────────────────────────────────────────────────────────────────────


def test_merge_inflection_into_base(tmp_path, monkeypatch):
    _build_stub_lemmatizer(monkeypatch, {"hotelu": "hotel"})
    tt_path = _make_tt_db(tmp_path)
    # Base: hotel (already correct lemma)
    _add_coll(tt_path, cid=1, text="hotel", lemma="hotel", anki_note_id=10)
    _add_dir(tt_path, cid=1, direction="recognition", reps=5, stability=10.0)
    # Inflection: hotelu (stored lemma is "hotelu", should map to "hotel")
    _add_coll(tt_path, cid=2, text="hotelu", lemma="hotelu", anki_note_id=10)
    _add_dir(tt_path, cid=2, direction="recognition", reps=3, stability=7.0, last_review="2026-05-01T00:00:00+00:00")

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    audit = relemmatize(conn, dry_run=False)
    assert audit["merged"] == 1
    assert audit["rekeyed"] == 0
    # Inflection row gone
    assert conn.execute("SELECT COUNT(*) FROM collocations WHERE id=2").fetchone()[0] == 0
    # Base still has both directions (only one direction in this test)
    rows = conn.execute(
        "SELECT reps, stability FROM collocation_directions WHERE collocation_id=1 AND direction='recognition'"
    ).fetchone()
    assert rows is not None
    # max(reps) = 5, max(stability) = 10.0
    assert rows[0] == 5
    assert rows[1] == 10.0


def test_merge_unions_directions(tmp_path, monkeypatch):
    _build_stub_lemmatizer(monkeypatch, {"zdravo": "zdrav"})
    tt_path = _make_tt_db(tmp_path)
    # Base: zdrav has recognition only
    _add_coll(tt_path, cid=1, text="zdrav", lemma="zdrav", anki_note_id=20)
    _add_dir(tt_path, cid=1, direction="recognition", reps=2)
    # Inflection: zdravo has production only
    _add_coll(tt_path, cid=2, text="zdravo", lemma="zdravo", anki_note_id=20)
    _add_dir(tt_path, cid=2, direction="production", reps=1)

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    audit = relemmatize(conn, dry_run=False)
    assert audit["merged"] == 1
    # Base now has both directions
    dirs = sorted(
        r[0] for r in conn.execute("SELECT direction FROM collocation_directions WHERE collocation_id=1").fetchall()
    )
    assert dirs == ["production", "recognition"]
    # Inflection gone
    assert conn.execute("SELECT COUNT(*) FROM collocations WHERE id=2").fetchone()[0] == 0


def test_merge_no_duplicate_anki_note_id(tmp_path, monkeypatch):
    _build_stub_lemmatizer(monkeypatch, {"vratu": "vrat", "vrat": "vrat"})
    tt_path = _make_tt_db(tmp_path)
    # Base: vrat with anki_note_id
    _add_coll(tt_path, cid=1, text="vrat", lemma="vrat", anki_note_id=30)
    _add_dir(tt_path, cid=1, direction="recognition", reps=2)
    # Inflection: vratu with SAME anki_note_id (safe merge)
    _add_coll(tt_path, cid=2, text="vratu", lemma="vratu", anki_note_id=30)
    _add_dir(tt_path, cid=2, direction="recognition", reps=1)

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    audit = relemmatize(conn, dry_run=False)
    assert audit["merged"] == 1
    # Verify no duplicate anki_note_id
    dups = conn.execute(
        "SELECT anki_note_id, COUNT(*) FROM collocations "
        "WHERE anki_note_id IS NOT NULL "
        "GROUP BY anki_note_id HAVING COUNT(*) > 1"
    ).fetchall()
    assert dups == []


def test_merge_with_media_deleted(tmp_path, monkeypatch):
    _build_stub_lemmatizer(monkeypatch, {"dobro": "dober"})
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=1, text="dober", lemma="dober", anki_note_id=40)
    _add_dir(tt_path, cid=1, direction="recognition", reps=2)
    _add_coll(tt_path, cid=2, text="dobro", lemma="dobro", anki_note_id=40)
    _add_dir(tt_path, cid=2, direction="recognition", reps=1)
    _add_media(tt_path, cid=2, kind="image", filename="dobro.jpg")

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    audit = relemmatize(conn, dry_run=False)
    assert audit["merged"] == 1
    # Inflection media deleted
    assert conn.execute("SELECT COUNT(*) FROM media WHERE collocation_id=2").fetchone()[0] == 0
    # Inflection row gone
    assert conn.execute("SELECT COUNT(*) FROM collocations WHERE id=2").fetchone()[0] == 0


# ── Main / CLI ───────────────────────────────────────────────────────────────


def test_main_returns_1_when_tt_db_missing(monkeypatch):
    from app.srs.relemmatize_collocations import main

    rc = main(["--apply", "--tt-db", "/tmp/nope.db"])
    assert rc == 1


def test_main_dry_run_does_not_write(tmp_path, capsys, monkeypatch):
    _build_stub_lemmatizer(monkeypatch, {"hotelu": "hotel"})
    from app.srs.relemmatize_collocations import main

    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=1, text="hotelu", lemma="hotelu")
    _add_dir(tt_path, cid=1, direction="recognition")

    rc = main(["--tt-db", str(tt_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dry-run" in out
    conn = sqlite3.connect(str(tt_path))
    lemma = conn.execute("SELECT lemma FROM collocations WHERE id=1").fetchone()[0]
    assert lemma == "hotelu"


def test_main_apply_writes(tmp_path, capsys, monkeypatch):
    _build_stub_lemmatizer(monkeypatch, {"hotelu": "hotel"})
    from app.srs.relemmatize_collocations import main

    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=1, text="hotelu", lemma="hotelu")
    _add_dir(tt_path, cid=1, direction="recognition")

    rc = main(["--apply", "--tt-db", str(tt_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Re-keyed: 1" in out
    conn = sqlite3.connect(str(tt_path))
    lemma = conn.execute("SELECT lemma FROM collocations WHERE id=1").fetchone()[0]
    assert lemma == "hotel"


# ── Edge cases / error branches ─────────────────────────────────────────────


def test_merge_with_null_anki_fields(tmp_path, monkeypatch):
    """Merge direction where later row has NULL anki_card_id/due, other has values."""
    _build_stub_lemmatizer(monkeypatch, {"hotelu": "hotel"})
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=1, text="hotel", lemma="hotel", anki_note_id=10)
    _add_dir(tt_path, cid=1, direction="recognition", reps=2, anki_card_id=42, anki_due=1234)
    _add_coll(tt_path, cid=2, text="hotelu", lemma="hotelu", anki_note_id=10)
    _add_dir(
        tt_path,
        cid=2,
        direction="recognition",
        reps=1,
        anki_card_id=None,
        anki_due=None,
        last_review="2026-04-01T00:00:00+00:00",
    )

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    audit = relemmatize(conn, dry_run=False)
    assert audit["merged"] == 1
    # Later row (inflection) had NULL anki fields — winner's values preserved
    row = conn.execute(
        "SELECT anki_card_id, anki_due FROM collocation_directions WHERE collocation_id=1 AND direction='recognition'"
    ).fetchone()
    assert row[0] == 42
    assert row[1] == 1234


def test_merge_different_anki_note_ids_errors(tmp_path, monkeypatch):
    """Merge would create duplicate anki_note_id → error logged, skip."""
    _build_stub_lemmatizer(monkeypatch, {"hotelu": "hotel"})
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=1, text="hotel", lemma="hotel", anki_note_id=10)
    _add_dir(tt_path, cid=1, direction="recognition", reps=2)
    _add_coll(tt_path, cid=2, text="hotelu", lemma="hotelu", anki_note_id=20)
    _add_dir(tt_path, cid=2, direction="recognition", reps=1)

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    audit = relemmatize(conn, dry_run=False)
    assert audit["merged"] == 0
    assert len(audit["errors"]) == 1
    assert "duplicate anki_note_id" in audit["errors"][0]


def test_main_reports_errors(tmp_path, monkeypatch):
    """main() with conflicting anki_note_ids hits _print_audit error path."""
    _build_stub_lemmatizer(monkeypatch, {"hotelu": "hotel"})
    from app.srs.relemmatize_collocations import main

    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=1, text="hotel", lemma="hotel", anki_note_id=10)
    _add_dir(tt_path, cid=1, direction="recognition")
    _add_coll(tt_path, cid=2, text="hotelu", lemma="hotelu", anki_note_id=20)
    _add_dir(tt_path, cid=2, direction="recognition")

    rc = main(["--apply", "--tt-db", str(tt_path)])
    assert rc == 0
    # main() still exits 0 even with errors; errors are printed


def test_merge_failure_rolls_back(tmp_path, monkeypatch):
    """Exception during merge is caught, rolled back, error logged."""
    _build_stub_lemmatizer(monkeypatch, {"hotelu": "hotel"})
    tt_path = _make_tt_db(tmp_path)
    _add_coll(tt_path, cid=1, text="hotel", lemma="hotel", anki_note_id=10)
    _add_dir(tt_path, cid=1, direction="recognition")
    _add_coll(tt_path, cid=2, text="hotelu", lemma="hotelu", anki_note_id=10)

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    # Drop directions table to cause a DB error during merge
    conn.execute("DROP TABLE collocation_directions")
    conn.commit()
    audit = relemmatize(conn, dry_run=False)
    assert audit["merged"] == 0
    assert any("merge failed" in e for e in audit["errors"])
