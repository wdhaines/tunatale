"""Lesson audio by LAST USE (tunatale-al6).

The user's decision (2026-09-22): nothing is deleted now, but pruning — when it
is ever needed — goes by when a lesson was last listened to, not by when it was
rendered. This report is that instrument. It is READ-ONLY and stdlib-only,
because on the box it runs through `docker exec -i api python - < script`
(scripts/ is not in the image, and `uv run` in the container is forbidden).
"""

import sqlite3
from datetime import UTC, datetime

from scripts.report_audio_retention import collect, render

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def make_db(path, audio_dir):
    c = sqlite3.connect(path)
    c.executescript(
        """
        CREATE TABLE lessons (id TEXT PRIMARY KEY, curriculum_id TEXT, day INTEGER, data_json TEXT,
                              created_at TEXT);
        CREATE TABLE audio_files (id TEXT PRIMARY KEY, lesson_id TEXT, file_path TEXT, section_index INTEGER,
                                  section_type TEXT, created_at TEXT, cues_json TEXT);
        CREATE TABLE lesson_listens (id INTEGER PRIMARY KEY, lesson_id TEXT, listened_at TEXT, source TEXT);
        CREATE TABLE lesson_reviews (id INTEGER PRIMARY KEY, lesson_id TEXT, reviewed_at TEXT);
        """
    )
    rows = [
        # (lesson, day, file, bytes)
        ("fresh", 3, "f1.opus", 1000),
        ("fresh", 3, "f2.opus", 500),
        ("stale", 1, "s1.opus", 4000),
        ("unheard", 2, "u1.opus", 300),
    ]
    c.executemany("INSERT INTO lessons VALUES (?, 'c', ?, '{}', '2026-06-01 00:00:00')", {(r[0], r[1]) for r in rows})
    for i, (lesson, _day, name, size) in enumerate(rows):
        (audio_dir / name).write_bytes(b"x" * size)
        c.execute(
            "INSERT INTO audio_files VALUES (?, ?, ?, 0, 'natural_speed', '2026-06-01 00:00:00', NULL)",
            (f"a{i}", lesson, name),
        )
    # A regenerated lesson: its audio row survives, the lesson row does not.
    (audio_dir / "o1.opus").write_bytes(b"x" * 2000)
    c.execute("INSERT INTO audio_files VALUES ('ao', 'gone', 'o1.opus', 0, 'x', '2026-05-01 00:00:00', NULL)")
    # A row whose file is not on disk.
    c.execute("INSERT INTO audio_files VALUES ('am', 'fresh', 'missing.opus', 1, 'x', '2026-06-01 00:00:00', NULL)")
    c.executemany(
        "INSERT INTO lesson_listens (lesson_id, listened_at, source) VALUES (?, ?, 'listen')",
        [("fresh", "2026-09-20T10:00:00+00:00"), ("stale", "2026-05-01T10:00:00+00:00")],
    )
    # A review is a use too, and here it is the most recent one.
    c.execute("INSERT INTO lesson_reviews (lesson_id, reviewed_at) VALUES ('stale', '2026-06-10T10:00:00+00:00')")
    c.commit()
    c.close()


def by_id(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    make_db(tmp_path / "no.db", audio)
    (audio / "stray.opus").write_bytes(b"x" * 700)  # on disk, in no DB
    report = collect([tmp_path / "no.db"], audio, NOW)
    return report, {r.lesson_id: r for r in report.lessons}


def test_last_use_is_the_latest_listen_or_review(tmp_path):
    _, rows = by_id(tmp_path)
    assert rows["fresh"].days_since_use == 2
    assert rows["stale"].days_since_use == 104  # the review on 06-10, not the listen on 05-01
    assert rows["unheard"].days_since_use is None


def test_bytes_are_measured_on_disk_and_missing_files_are_counted(tmp_path):
    _, rows = by_id(tmp_path)
    assert rows["fresh"].bytes == 1500
    assert rows["fresh"].missing == 1


def test_audio_of_a_deleted_lesson_is_flagged_as_orphaned(tmp_path):
    _, rows = by_id(tmp_path)
    assert rows["gone"].orphaned is True
    assert rows["fresh"].orphaned is False


def test_files_no_database_references_are_reported(tmp_path):
    report, _ = by_id(tmp_path)
    assert report.unreferenced == {"stray.opus": 700}


def test_render_buckets_by_last_use_and_totals(tmp_path):
    report, _ = by_id(tmp_path)
    lines = render(report).splitlines()

    def row(label):
        # Every bucket is printed even when empty, so assert its COUNTS, not
        # that its label exists.
        (line,) = [ln for ln in lines if ln.startswith(label)]
        return line[len(label) :].split()

    assert row("<= 30 days") == ["1", "2", "0.0"]  # fresh: 2 files on disk, 1 missing
    assert row("31-90 days") == ["0", "0", "0.0"]
    assert row("> 90 days") == ["1", "1", "0.0"]  # stale
    assert row("never listened") == ["1", "1", "0.0"]  # unheard
    assert row("orphaned (lesson gone)") == ["1", "1", "0.0"]
    assert row("unreferenced files") == ["1", "0.0"]
    assert any("1 audio_files rows point at files that are not on disk" in ln for ln in lines)


def test_the_database_is_opened_read_only(tmp_path):
    report, _ = by_id(tmp_path)
    before = (tmp_path / "no.db").read_bytes()
    collect([tmp_path / "no.db"], tmp_path / "audio", NOW)
    assert (tmp_path / "no.db").read_bytes() == before


def test_the_script_parses_as_python_3_12(tmp_path):
    """It runs in the api container today, but the host's python3 is 3.12 and
    ruff's PEP 758 rewrite would break it there. Same guard as disk_alert."""
    import ast
    from pathlib import Path

    import scripts.report_audio_retention as mod

    ast.parse(Path(mod.__file__).read_text(), feature_version=(3, 12))
