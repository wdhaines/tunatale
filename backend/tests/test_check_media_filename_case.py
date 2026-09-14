"""Check that every ``media`` row's filename matches an on-disk file byte-for-byte.

The TunaTale ``media`` table names audio files that are served from the media
directory on the production box's case-SENSITIVE filesystem (ext4); macOS APFS
is case-insensitive by default, so a row whose spelling differs only in case
resolves fine on a developer's Mac and 404s in production. The checker
(``scripts/check_media_filename_case.py``) exists to catch the mismatch before
it ships — in both directions: a false negative is a hole, and a false positive
in a zero-tolerance gate is an outage.

These tests build a tiny temp sqlite DB + media directory per test — never the
real ``backend/tunatale_*.db`` databases or ``backend/media``.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_media_filename_case import check_db, do_check  # noqa: E402


def _make_db(db_path: Path, rows: dict[int, str]) -> None:
    """Create a writable temp DB with a minimal ``media`` table and the given rows."""
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE media (id INTEGER PRIMARY KEY, filename TEXT)")
    conn.executemany("INSERT INTO media (id, filename) VALUES (?, ?)", rows.items())
    conn.commit()
    conn.close()


def _check(tmp_path: Path, rows: dict[int, str], files: list[str]) -> tuple[int, list[str]]:
    """Build a temp DB + media dir and run the matcher; return (exit code, messages)."""
    db = tmp_path / "tunatale_test.db"
    media = tmp_path / "media"
    media.mkdir()
    for name in files:
        (media / name).write_bytes(b"x")
    _make_db(db, rows)
    return check_db(db, media)


class TestCheckDb:
    """The matcher itself, probed in both directions."""

    def test_clean_directory_passes(self, tmp_path):
        code, messages = _check(tmp_path, {1: "sl_foo.mp3"}, ["sl_foo.mp3"])
        assert code == 0
        assert messages == []

    def test_case_only_mismatch_fails_and_names_both_spellings(self, tmp_path):
        code, messages = _check(tmp_path, {1: "sl_foo.mp3"}, ["sl_Foo.mp3"])
        assert code == 1
        assert len(messages) == 1
        assert "sl_foo.mp3" in messages[0]
        assert "sl_Foo.mp3" in messages[0]
        assert "case-only" in messages[0]

    def test_absent_file_fails_and_is_distinct_from_case_mismatch(self, tmp_path):
        code, messages = _check(tmp_path, {1: "sl_missing.mp3"}, ["sl_other.mp3"])
        assert code == 1
        assert "sl_missing.mp3" in messages[0]
        assert "absent" in messages[0]
        assert "case-only" not in messages[0]

    def test_non_ascii_exact_match_passes(self, tmp_path):
        code, messages = _check(tmp_path, {1: "no_snømann.mp3"}, ["no_snømann.mp3"])
        assert code == 0
        assert messages == []

    def test_exact_match_that_contains_another_name_as_a_substring_passes(self, tmp_path):
        code, messages = _check(
            tmp_path,
            {1: "sl_foo.mp3", 2: "sl_foo_mix.mp3"},
            ["sl_foo.mp3", "sl_foo_mix.mp3"],
        )
        assert code == 0
        assert messages == []

    def test_name_differing_only_after_the_extension_passes(self, tmp_path):
        code, messages = _check(tmp_path, {1: "sl_foo.mp3"}, ["sl_foo.mp3", "sl_foo.mp3.orig"])
        assert code == 0
        assert messages == []

    @pytest.mark.parametrize(
        ("db_name", "disk_name"),
        [
            ("sl_foo.MP3", "sl_foo.mp3"),
            ("Sl_foo.mp3", "sl_foo.mp3"),
            ("no_snØmann.mp3", "no_snømann.mp3"),
        ],
    )
    def test_case_mismatch_is_caught(self, tmp_path, db_name, disk_name):
        code, messages = _check(tmp_path, {1: db_name}, [disk_name])
        assert code == 1
        assert db_name in messages[0]
        assert disk_name in messages[0]


class TestSabotageDrill:
    """The brief's sabotage drill, as a permanent test: one bad row goes red,
    restore goes green — in the same fixture, so it cannot rot."""

    def test_bad_row_goes_red_then_restore_goes_green(self, tmp_path):
        db = tmp_path / "tunatale_test.db"
        media = tmp_path / "media"
        media.mkdir()
        disk_file = media / "demo_Film.mp3"
        disk_file.write_bytes(b"x")
        _make_db(db, {1: "demo_film.mp3"})

        code, messages = check_db(db, media)
        assert code == 1, "a case-only mismatch must make the check red"
        assert "demo_film.mp3" in messages[0] and "demo_Film.mp3" in messages[0]

        os.rename(disk_file, media / "demo_film.mp3")

        code, messages = check_db(db, media)
        assert code == 0, "restoring the spelling must make the check green"
        assert messages == []


class TestDoCheck:
    """Discovery, skip semantics, and multi-DB reporting."""

    def test_missing_database_skips(self, tmp_path, capsys):
        media = tmp_path / "media"
        media.mkdir()
        assert do_check(tmp_path, media) == 0
        assert "SKIP" in capsys.readouterr().out

    def test_database_without_a_media_table_skips(self, tmp_path, capsys):
        """A tunatale_*.db that has no ``media`` table must SKIP, not crash.

        Regression: the first version ran ``SELECT ... FROM media`` unguarded and
        raised ``sqlite3.OperationalError``, which in a zero-tolerance gate is an
        outage rather than a finding. The glob finds every ``tunatale_*.db``, so
        any new language DB that exists before its first media row hits this.
        Found by an adversarial substrate probe, not by the original test set.
        """
        media = tmp_path / "media"
        media.mkdir()
        conn = sqlite3.connect(tmp_path / "tunatale_nomedia.db")
        conn.execute("CREATE TABLE collocations (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        assert do_check(tmp_path, media) == 0
        assert "SKIP" in capsys.readouterr().out

    def test_missing_media_directory_skips(self, tmp_path, capsys):
        _make_db(tmp_path / "tunatale_test.db", {1: "sl_foo.mp3"})
        assert do_check(tmp_path, tmp_path / "nope") == 0
        assert "SKIP" in capsys.readouterr().out

    def test_full_run_fails_on_any_mismatching_db_and_passes_clean_ones(self, tmp_path, capsys):
        media = tmp_path / "media"
        media.mkdir()
        (media / "sl_Foo.mp3").write_bytes(b"x")
        _make_db(tmp_path / "tunatale_clean.db", {1: "sl_Foo.mp3"})
        _make_db(tmp_path / "tunatale_bad.db", {2: "sl_foo.mp3"})

        assert do_check(tmp_path, media) == 1

        out = capsys.readouterr().out
        assert "tunatale_clean.db" not in out
        assert "tunatale_bad.db" in out
        assert "sl_foo.mp3" in out
