"""The FK-orphan cleaner (tunatale-vpn): deletes only dead children, refuses the rest.

Built on the real TT schema, with orphans made the way the live ones were: a
collocation deleted over a connection with ``foreign_keys`` off.
"""

from __future__ import annotations

import sqlite3

from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from scripts.clean_fk_orphans import main


def _db_with_orphans(path) -> None:
    """Two cards, each reviewed and pictured; then 'gone' is deleted with FKs OFF."""
    db = SRSDatabase(f"sqlite:///{path}")
    for text in ("gone", "kept"):
        db.add_collocation(
            SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test"),
            language_code="ceb",
        )
    with db._get_conn() as conn:
        ids = {r[1]: r[0] for r in conn.execute("SELECT id, text FROM collocations")}
        for n, cid in enumerate(ids.values()):
            conn.execute(
                "INSERT INTO tt_revlog (id, collocation_id, direction, button_chosen, interval, last_interval,"
                " factor, taken_millis, review_kind) VALUES (?, ?, 'recognition', 3, 1, 0, 0, 1000, 1)",
                (1790000000000 + n, cid),
            )
        conn.commit()
    for text, cid in ids.items():
        db.add_media(cid, "image", f"img_{text}.jpg", f"img_{text}.jpg", f"img_{text}.jpg", "0" * 64, 1)
    raw = sqlite3.connect(path)  # foreign_keys OFF: SQLite's default, and the bug
    raw.execute("DELETE FROM collocation_directions WHERE collocation_id = ?", (ids["gone"],))
    raw.execute("DELETE FROM collocations WHERE id = ?", (ids["gone"],))
    raw.commit()
    raw.close()


def _violations(path) -> list[tuple]:
    return sqlite3.connect(path).execute("PRAGMA foreign_key_check").fetchall()


def test_the_fixture_really_has_orphans(tmp_path):
    """Control: without this, every test below could pass on a clean DB."""
    db = tmp_path / "t.db"
    _db_with_orphans(db)
    assert sorted((t, p) for t, _r, p, _f in _violations(db)) == [
        ("media", "collocations"),
        ("tt_revlog", "collocation_directions"),
    ]


def test_dry_run_lists_and_writes_nothing(tmp_path, capsys):
    db = tmp_path / "t.db"
    _db_with_orphans(db)
    assert main(["--db", str(db)]) == 0
    out = capsys.readouterr().out
    assert "media -> collocations: 1" in out
    assert "tt_revlog -> collocation_directions: 1" in out
    assert "2 orphan row(s)." in out
    assert "DRY RUN" in out
    assert len(_violations(db)) == 2


def test_apply_deletes_exactly_the_orphans(tmp_path, capsys):
    db = tmp_path / "t.db"
    _db_with_orphans(db)
    assert main(["--db", str(db), "--apply"]) == 0
    assert "Deleted 2 row(s); 0 violation(s) left." in capsys.readouterr().out
    assert _violations(db) == []
    conn = sqlite3.connect(db)
    # The live card's history and picture survive.
    assert conn.execute("SELECT count(*) FROM tt_revlog").fetchone()[0] == 1
    assert [r[0] for r in conn.execute("SELECT filename FROM media")] == ["img_kept.jpg"]


def test_a_clean_db_is_a_no_op(tmp_path, capsys):
    db = tmp_path / "t.db"
    SRSDatabase(f"sqlite:///{db}")
    assert main(["--db", str(db), "--apply"]) == 0
    out = capsys.readouterr().out
    assert "0 orphan row(s)." in out
    assert "DRY RUN" not in out


def test_a_violation_outside_the_cleanable_tables_refuses(tmp_path, capsys):
    db = tmp_path / "t.db"
    _db_with_orphans(db)
    raw = sqlite3.connect(db)
    parent = raw.execute("SELECT id FROM collocations").fetchone()[0]
    raw.execute("DELETE FROM collocations WHERE id = ?", (parent,))  # orphans its directions
    raw.commit()
    raw.close()
    assert main(["--db", str(db), "--apply"]) == 2
    assert "REFUSING: violations in collocation_directions" in capsys.readouterr().err
    assert any(t == "tt_revlog" for t, *_ in _violations(db)), "nothing may be deleted when it refuses"


def test_a_missing_db_fails_loudly(tmp_path, capsys):
    assert main(["--db", str(tmp_path / "absent.db")]) == 1
    assert "No database" in capsys.readouterr().err
