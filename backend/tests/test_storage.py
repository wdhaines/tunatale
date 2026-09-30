"""ContentStore unit tests."""

from pathlib import Path

import pytest

from app.config import settings
from app.models.curriculum import Curriculum, CurriculumDay
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.storage.store import ContentStore


@pytest.fixture
def store():
    s = ContentStore(":memory:")
    yield s
    s.close()


def _make_curriculum(id: str = "c1") -> Curriculum:
    return Curriculum(
        id=id,
        topic="ordering coffee",
        language_code="sl",
        cefr_level="A2",
        days=[
            CurriculumDay(
                day=1,
                title="Day 1",
                focus="greetings",
                learning_objective="say hello",
                story_guidance="use dober dan",
                collocations=["dober dan"],
            )
        ],
    )


def _make_lesson() -> Lesson:
    return Lesson(
        title="Day 1",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.KEY_PHRASES,
                phrases=[
                    Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl", role="female-1"),
                ],
            )
        ],
    )


class TestCurriculumStorage:
    """Tests for curriculum save/get/list operations."""

    def test_save_and_get_curriculum(self, store):
        curriculum = _make_curriculum("c1")
        store.save_curriculum("c1", curriculum)
        restored = store.get_curriculum("c1")
        assert restored is not None
        assert restored.topic == "ordering coffee"
        assert restored.language_code == "sl"
        assert restored.cefr_level == "A2"
        assert len(restored.days) == 1
        assert restored.days[0].collocations == ["dober dan"]

    def test_get_curriculum_returns_none_when_missing(self, store):
        assert store.get_curriculum("nonexistent") is None

    def test_list_curricula_returns_all(self, store):
        store.save_curriculum("c1", _make_curriculum("c1"))
        store.save_curriculum("c2", Curriculum(id="c2", topic="shopping", language_code="sl", cefr_level="B1"))
        result = store.list_curricula()
        assert len(result) == 2
        topics = {r["topic"] for r in result}
        assert topics == {"ordering coffee", "shopping"}
        assert all("created_at" in r for r in result)

    def test_list_curricula_empty(self, store):
        assert store.list_curricula() == []


class TestLessonStorage:
    """Tests for lesson save/get operations."""

    def test_save_and_get_lesson(self, store):
        lesson = _make_lesson()
        store.save_lesson("l1", "c1", 1, lesson)
        restored = store.get_lesson("l1")
        assert restored is not None
        assert restored.title == "Day 1"
        assert len(restored.sections) == 1
        assert restored.sections[0].section_type == SectionType.KEY_PHRASES
        assert restored.sections[0].phrases[0].text == "dober dan"
        assert restored.sections[0].phrases[0].role == "female-1"

    def test_get_lesson_returns_none_when_missing(self, store):
        assert store.get_lesson("nonexistent") is None

    def test_get_lesson_row_returns_full_row(self, store):
        """get_lesson_row returns dict with id, curriculum_id, day, and data_json."""
        lesson = _make_lesson()
        store.save_lesson("l1", "c1", 3, lesson)
        row = store.get_lesson_row("l1")
        assert row is not None
        assert row["id"] == "l1"
        assert row["curriculum_id"] == "c1"
        assert row["day"] == 3
        assert "data_json" in row

    def test_get_lesson_row_returns_none_when_missing(self, store):
        """get_lesson_row returns None for unknown lesson_id."""
        assert store.get_lesson_row("nonexistent") is None

    def test_get_all_token_glosses_merges_lessons(self, store):
        """Merges token_glosses from all lessons; later rows win on duplicate lemmas."""
        lesson1 = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            generation_metadata={"token_glosses": {"banka": "bank", "hiša": "house"}},
        )
        lesson2 = Lesson(
            title="Day 2",
            language_code="sl",
            sections=[],
            generation_metadata={"token_glosses": {"banka": "bank (updated)", "miza": "table"}},
        )
        store.save_lesson("l1", "c1", 1, lesson1)
        store.save_lesson("l2", "c1", 2, lesson2)
        glosses = store.get_all_token_glosses()
        assert glosses["hiša"] == "house"
        assert glosses["miza"] == "table"
        assert glosses["banka"] == "bank (updated)"  # later lesson wins

    def test_get_all_token_glosses_empty_store(self, store):
        """Returns empty dict when no lessons exist."""
        assert store.get_all_token_glosses() == {}

    def test_get_all_token_glosses_skips_lessons_without_glosses(self, store):
        """Lessons without token_glosses in generation_metadata are safely skipped."""
        lesson = _make_lesson()  # no generation_metadata
        store.save_lesson("l1", "c1", 1, lesson)
        assert store.get_all_token_glosses() == {}

    def test_list_lessons_returns_all_with_ids(self, store):
        """list_lessons yields (lesson_id, curriculum_id, day, Lesson) for every row."""
        store.save_lesson("l1", "c1", 1, _make_lesson())
        store.save_lesson("l2", "c1", 2, _make_lesson())
        rows = store.list_lessons()
        assert {(lid, cid, day) for lid, cid, day, _ in rows} == {("l1", "c1", 1), ("l2", "c1", 2)}
        assert all(isinstance(lesson, Lesson) for _, _, _, lesson in rows)

    def test_list_lessons_empty(self, store):
        assert store.list_lessons() == []


class TestAudioFileStorage:
    """Tests for audio file path save/get operations."""

    def test_save_and_get_audio_file(self, store):
        store.save_audio_file("a1", "l1", "/output/audio/a1.wav")
        result = store.get_audio_file_row("a1")
        assert result["file_path"] == "a1.wav"

    def test_get_audio_file_returns_none_when_missing(self, store):
        assert store.get_audio_file_row("nonexistent") is None

    def test_a_path_is_stored_as_its_BASENAME(self, store):
        """kbb.15 step 3. The recorded string must not name a machine.

        `resolve_audio_path` already looks a row up by basename under
        `settings.audio_dir`, and the render tree is flat (`<uuid>.opus`), so the
        directory part carries no information and is exactly the part that does
        not survive leaving this laptop. 100 of 104 rows named
        /Users/<author>/… and 404'd on the box.

        ⚠️ This REVERSES what c7tx did. That commit normalised toward absolute to
        remove an ambiguity for the ffmpeg concat demuxer; the concat now reads
        through `resolve_audio_path`, so the absolute form buys nothing and costs
        portability.
        """
        store.save_audio_file("a1", "l1", "/Users/someone/tunatale/backend/output/audio/a1.opus")
        assert store.get_audio_file_row("a1")["file_path"] == "a1.opus"

    def test_a_relative_path_is_also_reduced_to_its_basename(self, store):
        store.save_audio_file("a2", "l1", "output/audio/a2.opus")
        assert store.get_audio_file_row("a2")["file_path"] == "a2.opus"

    def test_a_bare_basename_is_stored_unchanged(self, store):
        """The control: the already-correct shape must be a no-op."""
        store.save_audio_file("a3", "l1", "a3.opus")
        assert store.get_audio_file_row("a3")["file_path"] == "a3.opus"

    def test_a_symlinked_root_is_not_resolved_on_the_way_to_the_basename(self, store):
        """Basename-only means symlink resolution never enters the picture.

        This used to assert an absolute path was preserved byte-for-byte, which
        kbb.15 step 3 deliberately falsified. What still matters is the reason
        that test existed: ``/tmp`` is a symlink to ``/private/tmp`` on macOS, and
        a ``resolve()``-based normalisation would silently rewrite the directory.
        Taking the basename cannot, because it never looks at the directory.
        """
        store.save_audio_file("abs1", "l1", "/tmp/output/audio/abs1.opus")
        assert store.get_audio_file_row("abs1")["file_path"] == "abs1.opus"


class TestSectionAudioStorage:
    """Tests for per-section audio file save/list/get operations."""

    def test_save_audio_file_with_section_metadata(self, store):
        """save_audio_file accepts section_index and section_type kwargs."""
        store.save_audio_file("full1", "l1", "/output/full1.wav")
        store.save_audio_file("sec0", "l1", "/output/sec0.wav", section_index=0, section_type="key_phrases")
        store.save_audio_file("sec1", "l1", "/output/sec1.wav", section_index=1, section_type="natural_speed")

        assert store.get_audio_file_row("full1")["file_path"] == "full1.wav"
        assert store.get_audio_file_row("sec0")["file_path"] == "sec0.wav"

    def test_list_audio_files_for_lesson_ordering(self, store):
        """list_audio_files_for_lesson returns full row first, then sections in order."""
        store.save_audio_file("sec1", "l1", "/s1.wav", section_index=1, section_type="natural_speed")
        store.save_audio_file("full", "l1", "/full.wav")
        store.save_audio_file("sec0", "l1", "/s0.wav", section_index=0, section_type="key_phrases")

        rows = store.list_audio_files_for_lesson("l1")
        assert len(rows) == 3
        # Full row first (section_index is NULL)
        assert rows[0]["id"] == "full"
        assert rows[0]["section_index"] is None
        # Then sections in order
        assert rows[1]["section_index"] == 0
        assert rows[1]["section_type"] == "key_phrases"
        assert rows[2]["section_index"] == 1

    def test_list_audio_files_for_lesson_empty(self, store):
        """list_audio_files_for_lesson returns empty list when no audio exists."""
        assert store.list_audio_files_for_lesson("nonexistent") == []

    def test_get_audio_file_row(self, store):
        """get_audio_file_row returns dict with all fields including section metadata."""
        store.save_audio_file("full1", "l1", "/full.wav")
        store.save_audio_file("sec0", "l1", "/s0.wav", section_index=0, section_type="key_phrases")

        full_row = store.get_audio_file_row("full1")
        assert full_row is not None
        assert full_row["file_path"] == "full.wav"
        assert full_row["lesson_id"] == "l1"
        assert full_row["section_index"] is None
        assert full_row["section_type"] is None

        sec_row = store.get_audio_file_row("sec0")
        assert sec_row is not None
        assert sec_row["section_index"] == 0
        assert sec_row["section_type"] == "key_phrases"

    def test_get_audio_file_row_returns_none_when_missing(self, store):
        """get_audio_file_row returns None for unknown audio_id."""
        assert store.get_audio_file_row("nonexistent") is None

    def test_delete_audio_files_for_lesson(self, store):
        """delete_audio_files_for_lesson removes all rows for a given lesson."""
        store.save_audio_file("a1", "l1", "/a1.wav")
        store.save_audio_file("a2", "l1", "/a2.wav", section_index=0, section_type="key_phrases")
        store.save_audio_file("a3", "l2", "/a3.wav")  # different lesson — should survive

        store.delete_audio_files_for_lesson("l1")
        assert store.list_audio_files_for_lesson("l1") == []
        assert store.list_audio_files_for_lesson("l2") != []

    def test_delete_lessons_for_day(self, store, monkeypatch, tmp_path):
        """delete_lessons_for_day removes all lessons and their audio for a given day."""
        from app.models.lesson import Lesson

        audio_dir = tmp_path / "audio"
        monkeypatch.setattr(settings, "audio_dir", audio_dir)
        lesson = Lesson(
            title="Test",
            language_code="sl",
            generation_metadata={"source_prompt": "x", "model": "y"},
        )
        store.save_lesson("l1", "c1", 2, lesson)
        store.save_lesson("l2", "c1", 2, lesson)  # another version, same day
        store.save_lesson("l3", "c1", 3, lesson)  # different day — should survive
        store.save_audio_file("a1", "l1", "/a1.wav")
        store.save_audio_file("a2", "l2", "/a2.wav")
        store.save_audio_file("a3", "l3", "/a3.wav")  # different day — should survive

        paths = store.delete_lessons_for_day("c1", 2)
        assert store.get_lesson("l1") is None
        assert store.get_lesson("l2") is None
        assert store.get_lesson("l3") is not None
        assert store.get_audio_file_row("a1") is None
        assert store.get_audio_file_row("a2") is None
        assert store.get_audio_file_row("a3") is not None
        assert sorted(paths) == sorted([audio_dir / "a1.wav", audio_dir / "a2.wav"]), (
            "the caller cannot unlink what it is not told about — the returned "
            "paths must reach the files on THIS machine, not name a record "
            "written on the machine that rendered them"
        )

    def test_delete_lessons_for_day_returns_empty_for_an_unknown_day(self, store):
        assert store.delete_lessons_for_day("c1", 99) == []

    def test_schema_migration_adds_missing_columns(self, tmp_path):
        """ContentStore adds section_index/section_type columns when opening an old-schema DB."""
        import sqlite3

        db_file = str(tmp_path / "old.db")

        # Create DB with original schema (no section columns)
        conn = sqlite3.connect(db_file)
        conn.execute("""
            CREATE TABLE audio_files (
                id TEXT PRIMARY KEY,
                lesson_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("INSERT INTO audio_files (id, lesson_id, file_path) VALUES ('old1', 'l1', '/old.wav')")
        conn.commit()
        conn.close()

        # Opening with ContentStore should add the new columns
        with ContentStore(db_file) as store:
            row = store.get_audio_file_row("old1")
            assert row is not None
            assert row["section_index"] is None
            assert row["section_type"] is None

            # Can save new-style rows
            store.save_audio_file("new1", "l1", "/new.wav", section_index=0, section_type="key_phrases")
            new_row = store.get_audio_file_row("new1")
            assert new_row["section_index"] == 0

    def test_schema_migration_adds_cues_json_column(self, tmp_path):
        """ContentStore adds cues_json column when opening a pre-cues schema DB."""
        import sqlite3

        db_file = str(tmp_path / "pre-cues.db")

        # Create DB with schema that has section_index/section_type but no cues_json
        conn = sqlite3.connect(db_file)
        conn.execute("""
            CREATE TABLE audio_files (
                id TEXT PRIMARY KEY,
                lesson_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                section_index INTEGER,
                section_type TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute(
            "INSERT INTO audio_files (id, lesson_id, file_path, section_index, section_type) VALUES ('old1', 'l1', '/old.wav', 0, 'key_phrases')"
        )
        conn.commit()
        conn.close()

        # Opening with ContentStore should add cues_json
        ContentStore(db_file).close()
        conn2 = sqlite3.connect(db_file)
        cols = {r[1] for r in conn2.execute("PRAGMA table_info(audio_files)").fetchall()}
        conn2.close()
        assert "cues_json" in cols, "cues_json column should have been added"

        # Existing data is preserved via a new store instance
        with ContentStore(db_file) as store2:
            row = store2.get_audio_file_row("old1")
        assert row is not None
        assert row["section_index"] == 0
        assert row["section_type"] == "key_phrases"

    def test_schema_migration_relaxes_file_path_to_nullable(self, tmp_path):
        """An old NOT NULL file_path is rebuilt as nullable, keeping every row.

        A render produces ONLY the section files, so the full-lesson row keeps
        its timeline but has no file. SQLite cannot drop a NOT NULL in place, so
        the table is rebuilt — which is a rewrite of the whole table, and the
        thing that has to survive it is every value in every column.
        """
        import sqlite3

        db_file = str(tmp_path / "not-null.db")
        old_schema = [
            ("id", "TEXT", 0),
            ("lesson_id", "TEXT", 1),
            ("file_path", "TEXT", 1),  # notnull=1: the constraint being removed
            ("section_index", "INTEGER", 0),
            ("section_type", "TEXT", 0),
            ("created_at", "TEXT", 0),
            ("cues_json", "TEXT", 0),
        ]
        conn = sqlite3.connect(db_file)
        conn.execute("""
            CREATE TABLE audio_files (
                id TEXT PRIMARY KEY,
                lesson_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                section_index INTEGER,
                section_type TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                cues_json TEXT
            )
        """)
        rows = [
            ("full1", "l1", "full1.wav", None, None, "2026-01-01 00:00:00", '[{"index": 0}]'),
            ("sec0", "l1", "sec0.wav", 0, "key_phrases", "2026-01-01 00:00:01", "[]"),
            ("sec1", "l1", "sec1.wav", 1, "natural_speed", "2026-01-01 00:00:02", None),
        ]
        conn.executemany("INSERT INTO audio_files VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
        conn.close()

        with ContentStore(db_file):
            pass

        def _dump() -> tuple[list[tuple], list[tuple]]:
            c = sqlite3.connect(db_file)
            try:
                info = c.execute("PRAGMA table_info(audio_files)").fetchall()
                kept = c.execute(
                    "SELECT id, lesson_id, file_path, section_index, section_type,"
                    " created_at, cues_json FROM audio_files ORDER BY id"
                ).fetchall()
            finally:
                c.close()
            return info, kept

        info, kept = _dump()
        notnull = {row[1]: row[3] for row in info}
        assert notnull["file_path"] == 0, f"file_path is still NOT NULL: {info}"
        # Every other column keeps its own declared constraint.
        assert notnull["lesson_id"] == 1
        assert {row[1] for row in info} == {name for name, _t, _n in old_schema}
        # The rebuild must not quietly drop the key: save_audio_file writes with
        # INSERT OR REPLACE, and without it a re-save appends a duplicate row.
        pk = {row[1] for row in info if row[5]}
        assert pk == {"id"}, f"primary key lost in the rebuild: {pk}"
        # The rebuild re-declares every column, so a lost DEFAULT would show up
        # as a silently different schema for every row written after it.
        dflt = {row[1]: row[4] for row in info}
        assert dflt["created_at"] == "datetime('now')", f"created_at default lost: {dflt}"

        assert kept == sorted(rows, key=lambda r: r[0]), f"rows changed across the rebuild: {kept}"

        # Idempotent: a second open changes nothing.
        with ContentStore(db_file):
            pass
        assert _dump() == (info, kept)

        # And the rebuilt table is actually writable with a NULL file_path.
        with ContentStore(db_file) as store:
            store.save_audio_file("full2", "l2", None, cues_json="[]")
            store.save_audio_file("sec0", "l1", "sec0-again.wav", section_index=0, section_type="key_phrases")
        c = sqlite3.connect(db_file)
        try:
            assert c.execute("SELECT file_path FROM audio_files WHERE id = 'full2'").fetchone() == (None,)
            replaced = c.execute("SELECT count(*) FROM audio_files WHERE id = 'sec0'").fetchone()
        finally:
            c.close()
        assert replaced == (1,), f"re-saving an existing id duplicated it: {replaced}"

    def test_relax_rebuild_is_atomic_when_a_step_fails(self, tmp_path):
        """A failure part-way through the rebuild must leave the ORIGINAL table.

        The rebuild is four statements — rename, create, copy, drop — and it
        runs against the user's live database and the production one. Python's
        sqlite3 opens an implicit transaction only before INSERT/UPDATE/DELETE/
        REPLACE, so under the default isolation the ALTER TABLE and the CREATE
        TABLE each commit themselves. A process killed between them leaves no
        ``audio_files`` table at all, and the NEXT open's
        ``CREATE TABLE IF NOT EXISTS`` then makes an empty one: every lesson in
        the database silently loses its audio rows, with no error anywhere.

        The failure is injected through a connection wrapper passed as an
        ARGUMENT — a plain test double, not a patch — which raises on the
        ``CREATE TABLE audio_files`` statement and delegates everything else.
        """
        import sqlite3

        db_file = str(tmp_path / "atomic.db")
        conn = sqlite3.connect(db_file)
        conn.execute("""
            CREATE TABLE audio_files (
                id TEXT PRIMARY KEY,
                lesson_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                section_index INTEGER,
                section_type TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                cues_json TEXT
            )
        """)
        conn.executemany(
            "INSERT INTO audio_files VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("full1", "l1", "full1.wav", None, None, "2026-01-01 00:00:00", '[{"index": 0}]'),
                ("sec0", "l1", "sec0.wav", 0, "key_phrases", "2026-01-01 00:00:01", "[]"),
            ],
        )
        conn.commit()

        class _FailOnCreate:
            """Delegates to a real connection, but explodes on the CREATE TABLE."""

            def __init__(self, real):
                self._real = real

            def execute(self, sql, *args):
                if sql.strip().startswith("CREATE TABLE audio_files"):
                    raise sqlite3.OperationalError("injected failure after the rename")
                return self._real.execute(sql, *args)

            def __getattr__(self, name):
                return getattr(self._real, name)

        store = ContentStore(":memory:")
        try:
            with pytest.raises(sqlite3.OperationalError, match="injected failure"):
                store._relax_audio_files_file_path(_FailOnCreate(conn))
        finally:
            conn.close()

        # Re-open independently: what survived is what the next process sees.
        check = sqlite3.connect(db_file)
        try:
            tables = {r[0] for r in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "audio_files" in tables, (
                f"the table was renamed away and never rebuilt — every lesson in this "
                f"database has just lost its audio rows: {tables}"
            )
            notnull = {r[1]: r[3] for r in check.execute("PRAGMA table_info(audio_files)").fetchall()}
            assert notnull.get("file_path") == 1, f"the original NOT NULL table was not restored: {notnull}"
            rows = check.execute("SELECT id, file_path, cues_json FROM audio_files ORDER BY id").fetchall()
        finally:
            check.close()
        assert rows == [("full1", "full1.wav", '[{"index": 0}]'), ("sec0", "sec0.wav", "[]")], (
            f"the rows did not survive the failed rebuild: {rows}"
        )

    def test_schema_migration_cues_json_read_write(self, tmp_path):
        """cues_json can be read/written via save/get_audio_file_row."""
        from app.storage.store import ContentStore

        db_file = str(tmp_path / "cues-rw.db")
        store = ContentStore(db_file)
        store.save_audio_file("test1", "l1", "/test.wav", section_index=0, section_type="natural_speed")

        # Access cues_json through raw SQL to verify column exists
        with store._file_conn() as conn:
            conn.execute(
                "UPDATE audio_files SET cues_json = ? WHERE id = ?",
                ('[{"start_ms": 0, "end_ms": 1000}]', "test1"),
            )
        row = store.get_audio_file_row("test1")
        assert row["cues_json"] == '[{"start_ms": 0, "end_ms": 1000}]'


class TestDeleteResolvesRecordedPaths:
    """The delete sweep hands the caller paths that reach the file on THIS machine.

    A recorded ``file_path`` is where a render was WRITTEN, and that string does
    not survive leaving the machine that wrote it. Post-migration the rows hold
    bare basenames that live in ``settings.audio_dir``; the delete methods must
    resolve them before returning, or the caller's ``Path(p).unlink()`` resolves
    against the process CWD and silently orphans the render instead of removing
    it — a soft failure worse than a 500, because nothing ever surfaces.
    """

    def _lesson(self):
        return Lesson(
            title="Test",
            language_code="sl",
            generation_metadata={"source_prompt": "x", "model": "y"},
        )

    def test_bare_basename_row_is_unlinked_from_audio_dir(self, store, monkeypatch, tmp_path):
        """Scenario (a): the post-migration shape. A row records ``<name>.opus``
        and the real file sits in ``settings.audio_dir``; the delete returns a
        path that actually reaches it. Before the sweep this returned the raw
        basename and the file survived the caller's unlink."""
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        monkeypatch.setattr(settings, "audio_dir", audio_dir)
        store.save_lesson("l1", "c1", 2, self._lesson())
        real = audio_dir / "a1.wav"
        real.write_bytes(b"audio")
        store.save_audio_file("a1", "l1", "a1.wav")

        paths = store.delete_lessons_for_day("c1", 2)

        assert paths == [audio_dir / "a1.wav"]
        paths[0].unlink(missing_ok=True)
        assert not real.exists(), "the caller could not reach the recorded render"

    def test_a_LEGACY_absolute_row_is_still_unlinkable(self, store, tmp_path):
        """Scenario (b): no regression on data written before kbb.15 step 3.

        Seeded with raw SQL on purpose — `save_audio_file` now reduces every
        path to its basename, so this shape can no longer be created through the
        API and only exists as rows an older build already wrote. Those still
        have to delete cleanly, which `resolve_audio_path`'s
        absolute-and-exists branch is what provides.
        """
        store.save_lesson("l1", "c1", 2, self._lesson())
        real = tmp_path / "existing.wav"
        real.write_bytes(b"audio")
        with store._get_conn() as conn:
            conn.execute(
                "INSERT INTO audio_files (id, lesson_id, file_path) VALUES ('a1', 'l1', ?)",
                (str(real),),
            )
            conn.commit()

        paths = store.delete_lessons_for_day("c1", 2)

        assert paths == [real]
        paths[0].unlink(missing_ok=True)
        assert not real.exists()

    def test_genuinely_absent_file_does_not_raise(self, store, monkeypatch, tmp_path):
        """Scenario (c), CONTROL: a delete that starts throwing is worse than one
        that orphans. missing_ok stays the caller's contract; resolution of a
        path no file backs must not change the delete's normal return."""
        audio_dir = tmp_path / "audio"
        monkeypatch.setattr(settings, "audio_dir", audio_dir)
        store.save_lesson("l1", "c1", 2, self._lesson())
        store.save_audio_file("a1", "l1", "ghost.wav")

        paths = store.delete_lessons_for_day("c1", 2)

        assert paths == [audio_dir / "ghost.wav"]
        paths[0].unlink(missing_ok=True)


class TestFileLessFullRowDeletesCleanly:
    """Every delete sweep survives a full-lesson row that has no file.

    A render now produces ONLY the section files (tunatale-guzo.3), so the
    full-lesson row keeps its timeline and has ``file_path`` NULL. Each of these
    methods reads EVERY row of a lesson / session / curriculum and hands the
    result to ``resolve_audio_path``, which does ``Path(value)`` — and
    ``Path(None)`` raises ``TypeError``. A row that is perfectly legal in the
    schema would turn every curriculum delete, every day delete and every
    review-session delete into a 500.

    The contract, identical for all six: the NULL row is still deleted, it
    contributes no path (there is no file for the caller to unlink), and the one
    section file that does exist is still reported. Guarding one of the six and
    not the other five is the failure this test is built to make impossible to
    ship.
    """

    @staticmethod
    def _seed(store, monkeypatch, tmp_path, *, owner, with_curriculum, with_session):
        """One NULL full row + one section row with a file, under a real owner.

        *owner* is the id the audio rows hang off, which is NOT always a lesson:
        the review-session deletes select ``audio_files WHERE lesson_id =
        session_id``, so those rows belong under the session's id. Seeding them
        under "l1" there would make the test pass for the wrong reason — an
        empty result set raises nothing.
        """
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        monkeypatch.setattr(settings, "audio_dir", audio_dir)
        lesson = Lesson(
            title="Test",
            language_code="no",
            generation_metadata={"source_prompt": "x", "model": "y"},
        )
        if with_curriculum:
            store.save_curriculum("c1", _make_curriculum("c1"))
            store.save_lesson("l1", "c1", 2, lesson)
        else:
            store.save_lesson("l1", "c1", 2, lesson)
        if with_session:
            store.save_review_session("s1", "no", "2026-09-29", lesson)
        store.save_audio_file("full1", owner, None, cues_json="[]")
        store.save_audio_file("sec0", owner, "sec0.wav", section_index=0, section_type="key_phrases")
        return audio_dir

    @pytest.mark.parametrize(
        ("method", "kwargs", "owner", "with_curriculum", "with_session", "survivor_check"),
        [
            ("delete_audio_files_for_lesson", {"lesson_id": "l1"}, "l1", False, False, "lesson"),
            ("delete_lesson", {"lesson_id": "l1"}, "l1", False, False, "lesson-gone"),
            ("delete_lessons_for_day", {"curriculum_id": "c1", "day": 2}, "l1", True, False, "lesson-gone"),
            ("delete_curriculum", {"curriculum_id": "c1"}, "l1", True, False, "lesson-gone"),
            ("delete_review_session_audio", {"session_id": "s1"}, "s1", False, True, "session-stays"),
            ("delete_review_session", {"session_id": "s1"}, "s1", False, True, "session-gone"),
        ],
        ids=[
            "delete_audio_files_for_lesson",
            "delete_lesson",
            "delete_lessons_for_day",
            "delete_curriculum",
            "delete_review_session_audio",
            "delete_review_session",
        ],
    )
    def test_delete_survives_a_file_less_full_row(
        self, store, monkeypatch, tmp_path, method, kwargs, owner, with_curriculum, with_session, survivor_check
    ):
        audio_dir = self._seed(
            store, monkeypatch, tmp_path, owner=owner, with_curriculum=with_curriculum, with_session=with_session
        )

        paths = getattr(store, method)(**kwargs)

        assert paths == [audio_dir / "sec0.wav"], (
            f"{method} must delete the file-less row without raising AND still report the "
            f"section file it really owns, so the caller can unlink it"
        )
        assert store.get_audio_file_row("full1") is None, f"{method} left the NULL row behind"
        assert store.get_audio_file_row("sec0") is None, f"{method} left a row with a real file behind"
        if survivor_check == "lesson":
            assert store.get_lesson("l1") is not None, "delete_audio_files_for_lesson must KEEP the lesson"
        elif survivor_check == "lesson-gone":
            assert store.get_lesson("l1") is None
        elif survivor_check == "session-stays":
            assert store.get_review_session("s1") is not None
        else:
            assert store.get_review_session("s1") is None


class TestLessonDays:
    """Tests for get_lesson_days bulk query."""

    def test_returns_all_days_with_lessons(self, store):
        lesson = _make_lesson()
        store.save_curriculum("c1", _make_curriculum("c1"))
        store.save_lesson("l1", "c1", 1, lesson)
        store.save_lesson("l3", "c1", 3, lesson)
        result = store.get_lesson_days("c1")
        days = [r["day"] for r in result]
        assert days == [1, 3]
        assert result[0]["lesson_id"] == "l1"
        assert result[1]["lesson_id"] == "l3"

    def test_returns_latest_lesson_per_day(self, store):
        lesson = _make_lesson()
        store.save_lesson("l1-old", "c1", 1, lesson)
        store.save_lesson("l1-new", "c1", 1, lesson)
        result = store.get_lesson_days("c1")
        assert len(result) == 1
        assert result[0]["lesson_id"] == "l1-new"

    def test_empty_for_unknown_curriculum(self, store):
        assert store.get_lesson_days("unknown") == []


class TestPersistence:
    """Tests for file-backed store persistence and multi-database coexistence."""

    def test_file_based_store_persists(self, tmp_path):
        db_file = str(tmp_path / "test.db")
        curriculum = _make_curriculum("c1")

        with ContentStore(db_file) as s1:
            s1.save_curriculum("c1", curriculum)

        with ContentStore(db_file) as s2:
            restored = s2.get_curriculum("c1")

        assert restored is not None
        assert restored.topic == "ordering coffee"

    def test_file_db_save_lesson(self, tmp_path):
        """save_lesson with file-backed store covers if self._in_memory: False branch (150->exit)."""
        db_file = str(tmp_path / "lessons.db")
        lesson = _make_lesson()
        with ContentStore(db_file) as s:
            s.save_lesson("l1", "c1", 1, lesson)
            restored = s.get_lesson("l1")
        assert restored is not None
        assert restored.title == "Day 1"

    def test_shared_db_with_srs_database(self, tmp_path):
        from app.srs.database import SRSDatabase

        db_file = str(tmp_path / "shared.db")

        srs = SRSDatabase(db_file)
        content = ContentStore(db_file)

        # All 5 tables should exist
        with content._get_conn() as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        table_names = {r[0] for r in rows}
        assert {"collocations", "violations", "curricula", "lessons", "audio_files"} <= table_names

        srs.close()
        content.close()


class TestAudioPathNormalisationOnOpen:
    """kbb.15 step 2 — heal rows written by an older build, on open.

    Self-healing rather than a one-shot migration on purpose: the failure this
    guards is a DB that has MOVED MACHINE, and the production box restores from
    a backup taken before the write side was fixed. A one-shot that already ran
    here would never run there.

    Idempotent, so it costs a scan and nothing else once the data is clean.
    """

    def _db_with(self, tmp_path, rows):
        """A real on-disk store seeded with pre-fix rows, then reopened."""
        import sqlite3

        db = tmp_path / "c.db"
        s = ContentStore(str(db))
        with s._get_conn() as conn:
            for i, fp in enumerate(rows):
                conn.execute(
                    "INSERT OR REPLACE INTO audio_files (id, lesson_id, file_path) VALUES (?, 'l1', ?)",
                    (f"a{i}", fp),
                )
            conn.commit()
        s.close()
        # Prove the pre-fix shape really landed, or the reopen below proves nothing.
        raw = sqlite3.connect(db)
        stored = [r[0] for r in raw.execute("SELECT file_path FROM audio_files ORDER BY id")]
        raw.close()
        assert stored == list(rows), stored
        return db

    def test_an_absolute_row_is_healed_on_reopen(self, tmp_path):
        db = self._db_with(tmp_path, ["/Users/someone/tunatale/backend/output/audio/x.opus"])
        reopened = ContentStore(str(db))
        assert reopened.get_audio_file_row("a0")["file_path"] == "x.opus"
        reopened.close()

    def test_a_legacy_relative_row_is_healed_too(self, tmp_path):
        """The 4 rows that caused the c7tx truncation had this exact shape."""
        db = self._db_with(tmp_path, ["output/audio/y.opus"])
        reopened = ContentStore(str(db))
        assert reopened.get_audio_file_row("a0")["file_path"] == "y.opus"
        reopened.close()

    def test_it_is_idempotent(self, tmp_path):
        db = self._db_with(tmp_path, ["/abs/z.opus"])
        ContentStore(str(db)).close()
        again = ContentStore(str(db))
        assert again.get_audio_file_row("a0")["file_path"] == "z.opus"
        again.close()

    def test_a_clean_row_is_left_alone(self, tmp_path):
        """The control: nothing to heal must mean nothing written."""
        db = self._db_with(tmp_path, ["already.opus"])
        reopened = ContentStore(str(db))
        assert reopened.get_audio_file_row("a0")["file_path"] == "already.opus"
        reopened.close()


class TestBasenameStorageInvariant:
    """kbb.15 step 4 — what makes basename storage safe, stated and pinned.

    A row records only ``<uuid>.opus``, and ``resolve_audio_path`` looks it up
    under ``settings.audio_dir``. That is correct if and only if EVERY render
    lands in that one directory. Today it does — ``main.py`` sets
    ``app.state.audio_dir = settings.audio_dir`` and every render entry point
    takes ``audio_dir`` from there — but nothing said so, and a future caller
    passing its own directory would produce rows that resolve to nothing, with
    no error at write time and a silent 404 later.

    The original bead wanted a check that "no row starts with '/'". That is now
    guaranteed by construction in ``save_audio_file``, so it would be a vacuous
    gate. This is the invariant actually worth defending.
    """

    def test_save_audio_file_cannot_record_a_directory(self, store):
        """The structural half: whatever a caller passes, no separator survives."""
        for given in (
            "/Users/someone/backend/output/audio/x.opus",
            "output/audio/x.opus",
            "../x.opus",
            "x.opus",
        ):
            store.save_audio_file("a", "l1", given)
            stored = store.get_audio_file_row("a")["file_path"]
            assert "/" not in stored, f"{given!r} -> {stored!r}"

    def test_every_render_entry_point_takes_audio_dir_from_the_setting(self):
        """The wiring half, read from the source rather than assumed.

        If this goes red, someone has introduced a second audio directory — and
        basename storage silently stops working for whatever writes there. The
        fix is NOT to relax this test; it is to decide whether rows should carry
        a directory again.
        """
        main_py = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()
        assert "app.state.audio_dir = settings.audio_dir" in main_py, (
            "main.py no longer ties app.state.audio_dir to settings.audio_dir"
        )

        api_audio = (Path(__file__).resolve().parents[1] / "app" / "api" / "audio.py").read_text()
        reviews = (Path(__file__).resolve().parents[1] / "app" / "api" / "review_sessions.py").read_text()
        for name, src in (("api/audio.py", api_audio), ("api/review_sessions.py", reviews)):
            assert "audio_dir=request.app.state.audio_dir" in src, (
                f"{name} passes an audio_dir that is not app.state.audio_dir"
            )
