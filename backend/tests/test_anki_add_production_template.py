"""Tests for app.plugins.anki_sync.add_production_template.

Schema migration: give a recognition-only imported notetype the capability to
carry production cards (an Image field + a Production template), WITHOUT
minting any cards. Cards arrive one at a time via just-in-time promotion —
see `.beads-tasks/briefs/design-no-production-cards-2026-08.md`.

The synthetic collection names its imported notetype after the real one so the
real `field_map` profile lookup runs — no internal function is faked.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.cards.field_map import BackFieldSpec, NotetypeProfile, get_profile
from app.plugins.anki_sync.add_production_template import (
    IMAGE_FIELD,
    add_production_template,
    build_production_template,
    find_recognition_only_notetypes,
    run,
)

SEED_MID = 1694414741634
TT_MID = 1782678240500
DECK_ID = 17

#: The imported notetype this migration exists for. Named exactly as in the
#: collection so `get_profile` resolves the real profile.
SEED_NOTETYPE = "6000 Most Frequent Norwegian Words"
PROFILED_NOTETYPE = "TT Vocabulary"  # deliberately has no field_map profile

SEED_PROFILE = get_profile(SEED_NOTETYPE)
assert SEED_PROFILE is not None, "the imported notetype must keep its field_map profile"

# A representative slice of the real 17-field layout: every field the profile
# names, plus one before them, so the L2 is not field 0 (as in the real deck).
SEED_FIELDS = (
    "Frequency index",
    "Norwegian word",
    "Word class",
    "Article",
    "IPA",
    "English translation",
    "General meaning",
)

_SCHEMA = """
    CREATE TABLE col (id INTEGER, crt INTEGER, mod INTEGER, scm INTEGER, ver INTEGER, dty INTEGER,
        usn INTEGER, ls INTEGER, conf TEXT, models TEXT, decks TEXT, dconf TEXT, tags TEXT);
    CREATE TABLE notes (id INTEGER PRIMARY KEY, guid TEXT, mid INTEGER, mod INTEGER, usn INTEGER,
        tags TEXT, flds TEXT, sfld TEXT, csum INTEGER, flags INTEGER, data TEXT);
    CREATE TABLE cards (id INTEGER PRIMARY KEY, nid INTEGER, did INTEGER, ord INTEGER, mod INTEGER,
        usn INTEGER, type INTEGER, queue INTEGER, due INTEGER, ivl INTEGER, factor INTEGER, reps INTEGER,
        lapses INTEGER, left INTEGER, odue INTEGER, odid INTEGER, flags INTEGER, data TEXT);
    CREATE TABLE revlog (id INTEGER PRIMARY KEY, cid INTEGER, usn INTEGER, ease INTEGER, ivl INTEGER,
        lastIvl INTEGER, factor INTEGER, time INTEGER, type INTEGER);
    CREATE TABLE notetypes (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB);
    CREATE TABLE fields (ntid INTEGER, ord INTEGER, name TEXT, config BLOB, PRIMARY KEY (ntid, ord));
    CREATE TABLE templates (ntid INTEGER, ord INTEGER, name TEXT, mtime_secs INTEGER, usn INTEGER, config BLOB,
        PRIMARY KEY (ntid, ord));
    CREATE TABLE decks (id INTEGER PRIMARY KEY, name TEXT, mtime_secs INTEGER, usn INTEGER, common BLOB);
"""


def _populate(conn: sqlite3.Connection, *, seed_notes: int = 3) -> None:
    """A deck holding a 1-template imported notetype plus a 2-template TT notetype."""
    conn.execute("INSERT INTO col VALUES (1, 0, 100, 1000, 18, 0, 5, 0, '{}', '{}', '{}', '{}', '{}')")
    conn.execute("INSERT INTO decks VALUES (?, 'Imported Deck', 50, 0, NULL)", (DECK_ID,))

    conn.execute("INSERT INTO notetypes VALUES (?, ?, 50, 0, X'')", (SEED_MID, SEED_NOTETYPE))
    for ord_, name in enumerate(SEED_FIELDS):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, X'')", (SEED_MID, ord_, name))
    conn.execute("INSERT INTO templates VALUES (?, 0, 'Card 1', 50, 0, X'')", (SEED_MID,))

    # Control: a two-template notetype in the same deck must be left alone.
    conn.execute("INSERT INTO notetypes VALUES (?, ?, 50, 0, X'')", (TT_MID, PROFILED_NOTETYPE))
    for ord_, name in enumerate(("L2", "English", "Audio", "Image")):
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, X'')", (TT_MID, ord_, name))
    conn.execute("INSERT INTO templates VALUES (?, 0, 'Recognition', 50, 0, X'')", (TT_MID,))
    conn.execute("INSERT INTO templates VALUES (?, 1, 'Production', 50, 0, X'')", (TT_MID,))

    for i in range(seed_notes):
        flds = "\x1f".join((str(i), f"ord{i}", "noun", "en", "/o/", f"word {i}", "a meaning"))
        conn.execute(
            "INSERT INTO notes VALUES (?, ?, ?, 100, 7, '', ?, ?, 0, 0, '')",
            (1000 + i, f"guid{i}", SEED_MID, flds, str(i)),
        )
        conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, 0, 100, 7, 0, 0, ?, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (2000 + i, 1000 + i, DECK_ID, i),
        )

    # One note on the TT notetype, with both its cards.
    conn.execute(
        "INSERT INTO notes VALUES (9000, 'guidtt', ?, 100, 7, '', ?, 'tt', 0, 0, '')",
        (TT_MID, "\x1f".join(("tt", "eng", "", "img.jpg"))),
    )
    for ord_ in (0, 1):
        conn.execute(
            "INSERT INTO cards VALUES (?, 9000, ?, ?, 100, 7, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, '')",
            (9100 + ord_, DECK_ID, ord_),
        )
    conn.commit()


def _make_conn(*, seed_notes: int = 3) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    _populate(conn, seed_notes=seed_notes)
    return conn


def _field_names(conn: sqlite3.Connection, mid: int) -> list[str]:
    return [r["name"] for r in conn.execute("SELECT name FROM fields WHERE ntid=? ORDER BY ord", (mid,))]


def _template_names(conn: sqlite3.Connection, mid: int) -> list[str]:
    return [r["name"] for r in conn.execute("SELECT name FROM templates WHERE ntid=? ORDER BY ord", (mid,))]


class TestBuildProductionTemplate:
    def test_fronts_on_the_image_field(self):
        qfmt, _ = build_production_template(SEED_PROFILE)
        assert qfmt.strip() == "{{" + IMAGE_FIELD + "}}"

    def test_back_reveals_l2_and_translation_from_the_profile(self):
        _, afmt = build_production_template(SEED_PROFILE)
        assert "{{FrontSide}}" in afmt
        assert f"{{{{{SEED_PROFILE.l2}}}}}" in afmt
        assert f"{{{{{SEED_PROFILE.translation}}}}}" in afmt

    def test_back_includes_summary_tier_back_fields(self):
        _, afmt = build_production_template(SEED_PROFILE)
        summary = [s.field_name for s in SEED_PROFILE.back_fields if s.tier == "summary"]
        assert summary, "the profile is expected to declare at least one summary field"
        for name in summary:
            assert f"{{{{{name}}}}}" in afmt

    def test_back_omits_non_summary_back_fields(self):
        profile = NotetypeProfile(
            l2="L2 word",
            translation="English translation",
            back_fields=(BackFieldSpec("Dictionary entry", "Dictionary", "deep"),),
        )
        _, afmt = build_production_template(profile)
        assert "Dictionary entry" not in afmt

    def test_back_wraps_l2_in_a_conditional_article_when_the_profile_declares_one(self):
        profile = NotetypeProfile(l2="L2 word", translation="English translation", article="Article")
        _, afmt = build_production_template(profile)
        # Conditional so non-nouns (blank Article) don't render a stray space.
        assert "{{#Article}}{{Article}} {{/Article}}{{L2 word}}" in afmt

    def test_back_has_no_article_markup_when_the_profile_declares_none(self):
        profile = NotetypeProfile(l2="L2 word", translation="English translation")
        _, afmt = build_production_template(profile)
        assert "Article" not in afmt


class TestFindRecognitionOnlyNotetypes:
    def test_reports_the_single_template_notetype_with_its_note_count(self):
        conn = _make_conn()
        assert find_recognition_only_notetypes(conn, "Imported Deck") == [(SEED_MID, SEED_NOTETYPE, 3)]

    def test_excludes_multi_template_notetypes(self):
        conn = _make_conn()
        names = [name for _, name, _ in find_recognition_only_notetypes(conn, "Imported Deck")]
        assert PROFILED_NOTETYPE not in names

    def test_returns_empty_for_unknown_deck(self):
        conn = _make_conn()
        assert find_recognition_only_notetypes(conn, "No Such Deck") == []


class TestAddProductionTemplateCore:
    def test_appends_image_field_and_production_template(self):
        conn = _make_conn()
        assert add_production_template(conn, SEED_NOTETYPE, SEED_PROFILE, now_ms=1700000000000) == "created"
        assert _field_names(conn, SEED_MID) == [*SEED_FIELDS, IMAGE_FIELD]
        assert _template_names(conn, SEED_MID) == ["Card 1", "Production"]

    def test_every_note_gains_exactly_one_separator(self):
        conn = _make_conn()
        before = [r["flds"] for r in conn.execute("SELECT flds FROM notes WHERE mid=?", (SEED_MID,))]
        add_production_template(conn, SEED_NOTETYPE, SEED_PROFILE, now_ms=1700000000000)
        after = [r["flds"] for r in conn.execute("SELECT flds FROM notes WHERE mid=?", (SEED_MID,))]
        assert [a.count("\x1f") for a in after] == [b.count("\x1f") + 1 for b in before]
        # The new trailing field is empty, and nothing before it moved.
        assert all(a == f"{b}\x1f" for a, b in zip(after, before, strict=True))

    def test_leaves_other_notetypes_notes_untouched(self):
        conn = _make_conn()
        add_production_template(conn, SEED_NOTETYPE, SEED_PROFILE, now_ms=1700000000000)
        row = conn.execute("SELECT flds, usn, mod FROM notes WHERE id=9000").fetchone()
        assert row["flds"].count("\x1f") == 3
        assert row["usn"] == 7
        assert row["mod"] == 100
        assert _field_names(conn, TT_MID) == ["L2", "English", "Audio", "Image"]

    def test_marks_touched_notes_dirty(self):
        conn = _make_conn()
        add_production_template(conn, SEED_NOTETYPE, SEED_PROFILE, now_ms=1700000000000)
        rows = conn.execute("SELECT usn, mod FROM notes WHERE mid=?", (SEED_MID,)).fetchall()
        assert all(r["usn"] == -1 for r in rows)
        assert all(r["mod"] == 1700000000 for r in rows)

    def test_bumps_notetype_mtime_and_usn(self):
        conn = _make_conn()
        add_production_template(conn, SEED_NOTETYPE, SEED_PROFILE, now_ms=1700000000000)
        row = conn.execute("SELECT mtime_secs, usn FROM notetypes WHERE id=?", (SEED_MID,)).fetchone()
        assert row["mtime_secs"] == 1700000000
        assert row["usn"] == -1

    def test_bumps_col_scm_and_mod(self):
        conn = _make_conn()
        add_production_template(conn, SEED_NOTETYPE, SEED_PROFILE, now_ms=1700000000000)
        row = conn.execute("SELECT scm, mod FROM col").fetchone()
        assert row["scm"] == 1700000000000
        assert row["mod"] == 1700000000000

    def test_does_not_touch_col_usn(self):
        conn = _make_conn()
        add_production_template(conn, SEED_NOTETYPE, SEED_PROFILE, now_ms=1700000000000)
        # Layer 61: col.usn is the sync anchor, never clobbered to -1.
        assert conn.execute("SELECT usn FROM col").fetchone()["usn"] == 5

    def test_mints_no_cards(self):
        """Capability, not cards — the ord=1 cards arrive via JIT promotion."""
        conn = _make_conn()
        before = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        add_production_template(conn, SEED_NOTETYPE, SEED_PROFILE, now_ms=1700000000000)
        assert conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == before
        assert conn.execute("SELECT COUNT(*) FROM cards WHERE ord=1 AND nid < 9000").fetchone()[0] == 0

    def test_idempotent(self):
        conn = _make_conn()
        add_production_template(conn, SEED_NOTETYPE, SEED_PROFILE, now_ms=1700000000000)
        flds_before = [r["flds"] for r in conn.execute("SELECT flds FROM notes WHERE mid=?", (SEED_MID,))]

        assert add_production_template(conn, SEED_NOTETYPE, SEED_PROFILE, now_ms=1800000000000) == "exists"

        assert conn.execute("SELECT scm FROM col").fetchone()["scm"] == 1700000000000  # no second bump
        assert _field_names(conn, SEED_MID) == [*SEED_FIELDS, IMAGE_FIELD]  # no duplicate Image
        assert _template_names(conn, SEED_MID) == ["Card 1", "Production"]
        after = [r["flds"] for r in conn.execute("SELECT flds FROM notes WHERE mid=?", (SEED_MID,))]
        assert after == flds_before  # no second separator

    def test_reuses_an_existing_image_field(self):
        conn = _make_conn()
        conn.execute("INSERT INTO fields VALUES (?, ?, ?, X'')", (SEED_MID, len(SEED_FIELDS), IMAGE_FIELD))
        conn.execute("UPDATE notes SET flds = flds || ? WHERE mid = ?", ("\x1f", SEED_MID))
        conn.commit()
        widths = [r["flds"].count("\x1f") for r in conn.execute("SELECT flds FROM notes WHERE mid=?", (SEED_MID,))]

        add_production_template(conn, SEED_NOTETYPE, SEED_PROFILE, now_ms=1700000000000)

        assert _field_names(conn, SEED_MID).count(IMAGE_FIELD) == 1
        after = [r["flds"].count("\x1f") for r in conn.execute("SELECT flds FROM notes WHERE mid=?", (SEED_MID,))]
        assert after == widths  # field already existed → no flds rewrite

    def test_raises_for_unknown_notetype(self):
        conn = _make_conn()
        with pytest.raises(ValueError, match="not found"):
            add_production_template(conn, "Nope", SEED_PROFILE, now_ms=1700000000000)

    def test_defaults_now_ms_to_wall_clock(self):
        conn = _make_conn()
        add_production_template(conn, SEED_NOTETYPE, SEED_PROFILE)
        assert conn.execute("SELECT scm FROM col").fetchone()["scm"] > 1000


def _build_collection_file(tmp_path: Path) -> Path:
    db_path = tmp_path / "collection.anki2"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    _populate(conn)
    conn.commit()
    conn.close()
    return db_path


class TestRun:
    def test_creates_capability_in_the_collection_file(self, tmp_path):
        db_path = _build_collection_file(tmp_path)

        result = run(
            notetype_name=SEED_NOTETYPE,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
        )

        assert result == "created"
        conn = sqlite3.connect(str(db_path))
        try:
            names = [r[0] for r in conn.execute("SELECT name FROM templates WHERE ntid=? ORDER BY ord", (SEED_MID,))]
            assert names == ["Card 1", "Production"]
            assert conn.execute("SELECT COUNT(*) FROM cards WHERE ord=1 AND nid < 9000").fetchone()[0] == 0
        finally:
            conn.close()

    def test_is_idempotent(self, tmp_path):
        db_path = _build_collection_file(tmp_path)
        run(notetype_name=SEED_NOTETYPE, anki_collection_path=db_path, anki_backup_dir=tmp_path / "b1")
        result = run(notetype_name=SEED_NOTETYPE, anki_collection_path=db_path, anki_backup_dir=tmp_path / "b2")
        assert result == "exists"

    def test_dry_run_makes_no_change(self, tmp_path):
        db_path = _build_collection_file(tmp_path)

        result = run(
            notetype_name=SEED_NOTETYPE,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "bak",
            dry_run=True,
        )

        assert result == "dry-run"
        conn = sqlite3.connect(str(db_path))
        try:
            assert conn.execute("SELECT COUNT(*) FROM templates WHERE ntid=?", (SEED_MID,)).fetchone()[0] == 1
            assert conn.execute("SELECT scm FROM col").fetchone()[0] == 1000
        finally:
            conn.close()

    def test_dry_run_reports_exists_when_already_migrated(self, tmp_path):
        db_path = _build_collection_file(tmp_path)
        run(notetype_name=SEED_NOTETYPE, anki_collection_path=db_path, anki_backup_dir=tmp_path / "b1")
        result = run(
            notetype_name=SEED_NOTETYPE,
            anki_collection_path=db_path,
            anki_backup_dir=tmp_path / "b2",
            dry_run=True,
        )
        assert result == "dry-run"

    def test_raises_when_notetype_has_no_profile(self, tmp_path):
        db_path = _build_collection_file(tmp_path)
        with pytest.raises(ValueError, match="no field-role profile"):
            run(
                notetype_name=PROFILED_NOTETYPE,
                anki_collection_path=db_path,
                anki_backup_dir=tmp_path / "bak",
            )

    def test_uses_settings_defaults_when_paths_none(self, tmp_path, monkeypatch):
        import app.plugins.anki_sync.add_production_template as mod

        db_path = _build_collection_file(tmp_path)

        class _FakeSettings:
            anki_collection_path = db_path
            anki_backup_dir = tmp_path / "bak_settings"

        monkeypatch.setattr(mod, "settings", _FakeSettings())
        assert run(notetype_name=SEED_NOTETYPE) == "created"
