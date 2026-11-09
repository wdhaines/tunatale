"""Bring the live "TT Sentence" notetype's CSS up to date (tunatale-h0q3.55).

TT owns the whole notetype, so its CSS is replaced outright with the one a new
notetype gets (``sentence_notetype.CSS``). Data-only: the notetype row is dirty
(``usn = -1``, ``mtime_secs``) and ``col.mod`` moves; ``col.scm`` and ``col.usn``
never do, so the change reaches the phone by an ordinary sync.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.cards.sentence_notetype import CSS, NAME
from app.cards.vocab_notetype import build_notetype_config
from app.plugins.anki_sync.add_sentence_notetype import add_sentence_notetype
from app.plugins.anki_sync.refresh_sentence_css import main, refresh_sentence_css
from tests._helpers.anki_sync_create_new import _make_dual_collection_conn

NOW_MS = 1_800_000_000_000
OLD_CSS = CSS.replace(".tt-front .cloze {", '.tt-front .cloze::after { content: "?"; }\n.tt-front .cloze {')
#: What create_sentence_notetype writes: kind = cloze, then the notetype config.
CURRENT = bytes([0x08, 0x01]) + build_notetype_config(CSS)


def _conn(css: str = OLD_CSS) -> sqlite3.Connection:
    conn = _make_dual_collection_conn()
    assert add_sentence_notetype(conn, now_ms=NOW_MS - 10_000) == "created"
    conn.execute(
        "UPDATE notetypes SET config = ?, usn = 2754, mtime_secs = 1 WHERE name = ?",
        (bytes([0x08, 0x01]) + build_notetype_config(css), NAME),
    )
    conn.execute("UPDATE col SET usn = 2754, mod = 1, scm = 5")
    conn.commit()
    return conn


def _row(conn) -> tuple:
    return tuple(conn.execute("SELECT config, usn, mtime_secs FROM notetypes WHERE name = ?", (NAME,)).fetchone())


def test_the_live_notetype_becomes_what_a_new_one_is():
    conn = _conn()

    assert refresh_sentence_css(conn, now_ms=NOW_MS) == "updated"

    assert _row(conn) == (CURRENT, -1, NOW_MS // 1000)


def test_data_only_the_schema_and_the_sync_anchor_do_not_move():
    conn = _conn()

    refresh_sentence_css(conn, now_ms=NOW_MS)

    assert tuple(conn.execute("SELECT mod, usn, scm FROM col").fetchone()) == (NOW_MS, 2754, 5)


def test_no_other_notetype_is_touched():
    conn = _conn()
    before = conn.execute("SELECT * FROM notetypes WHERE name != ? ORDER BY id", (NAME,)).fetchall()

    refresh_sentence_css(conn, now_ms=NOW_MS)

    assert conn.execute("SELECT * FROM notetypes WHERE name != ? ORDER BY id", (NAME,)).fetchall() == before


def test_a_current_notetype_is_left_alone():
    conn = _conn(CSS)
    before = (_row(conn), tuple(conn.execute("SELECT mod FROM col").fetchone()))

    assert refresh_sentence_css(conn, now_ms=NOW_MS) == "current"

    assert (_row(conn), tuple(conn.execute("SELECT mod FROM col").fetchone())) == before


def test_a_collection_without_the_notetype_is_refused():
    conn = _make_dual_collection_conn()
    with pytest.raises(ValueError, match="add_sentence_notetype"):
        refresh_sentence_css(conn, now_ms=NOW_MS)


def _on_disk(tmp_path, css: str = OLD_CSS):
    path = tmp_path / "collection.anki2"
    mem = _conn(css)
    with sqlite3.connect(path) as disk:
        mem.backup(disk)
    mem.close()
    return path


def _disk_row(path) -> tuple:
    conn = sqlite3.connect(path)
    try:
        return _row(conn)
    finally:
        conn.close()


def test_main_dry_run_writes_nothing(tmp_path, capsys):
    path = _on_disk(tmp_path)
    before = _disk_row(path)

    assert main(["--collection", str(path), "--backup-dir", str(tmp_path / "b"), "--dry-run"]) == 0

    assert _disk_row(path) == before
    assert "would be: updated" in capsys.readouterr().out


def test_main_applies_through_the_safety_envelope(tmp_path, capsys):
    path = _on_disk(tmp_path)

    assert main(["--collection", str(path), "--backup-dir", str(tmp_path / "b")]) == 0

    assert _disk_row(path)[:2] == (CURRENT, -1)
    assert any((tmp_path / "b").iterdir()), "safe_open took a backup"
    assert "[DONE]" in capsys.readouterr().out


def test_main_on_a_current_notetype_says_so(tmp_path, capsys):
    path = _on_disk(tmp_path, CSS)

    assert main(["--collection", str(path), "--backup-dir", str(tmp_path / "b")]) == 0

    assert "[SKIP]" in capsys.readouterr().out
