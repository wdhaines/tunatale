"""Tests for the restore drill (scripts/restore_drill.py).

A drill that reports PASS on a damaged backup is worse than no drill: it
converts an unknown into a false assurance, and the whole point of the bead
behind this script is that this project has already lost data twice. So these
tests are written negative-first — each one damages exactly one thing and
asserts the drill NOTICES. The happy path is covered too, but it is the cheap
half; a drill that cannot fail is the failure mode that matters.

Outside the coverage gate (`source = ["app"]`), tested anyway for that reason.

Real SQLite files and real ffmpeg-encoded audio throughout — no mocks. A drill
verified against mocked checksums and mocked decoders would prove nothing about
whether it can detect a corrupt restore, which is the only question worth asking.
"""
# ruff: noqa: I001 — import from scripts/ needs sys.path.insert before it

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# Allow importing from scripts/ one level up.
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

from restore_drill import (  # noqa: E402
    Drill,
    latest_snapshots,
    main,
    restore_dbs,
    verify_audio,
    verify_db,
    verify_media,
)

# Shells out to a real ffmpeg binary. CI's two hostile-timezone jobs deselect
# these with -m "not ffmpeg" so they need no ffmpeg install; see
# pyproject.toml [tool.pytest.ini_options] markers.
pytestmark = pytest.mark.ffmpeg


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_db(
    path: Path, *, media: list[tuple[str, str]] | None = None, audio: list[tuple[str, str, str]] | None = None
):
    """Minimal DB with the two tables the drill reads."""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE media (id INTEGER PRIMARY KEY, filename TEXT, sha256 TEXT)")
    con.execute("CREATE TABLE audio_files (id INTEGER PRIMARY KEY, lesson_id TEXT, file_path TEXT, cues_json TEXT)")
    for i, (filename, sha) in enumerate(media or []):
        con.execute("INSERT INTO media VALUES (?, ?, ?)", (i + 1, filename, sha))
    for i, (lesson, file_path, cues) in enumerate(audio or []):
        con.execute("INSERT INTO audio_files VALUES (?, ?, ?, ?)", (i + 1, lesson, file_path, cues))
    con.commit()
    con.close()


def _write_media(root: Path, filename: str, content: bytes) -> str:
    root.mkdir(parents=True, exist_ok=True)
    (root / filename).write_bytes(content)
    return hashlib.sha256(content).hexdigest()


@pytest.fixture(scope="module")
def silent_opus(tmp_path_factory) -> Path:
    """A real 2-second opus file. Module-scoped: encoding is the slow part."""
    out = tmp_path_factory.mktemp("audio") / "tone.opus"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=mono",
            "-t",
            "2",
            "-c:a",
            "libopus",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


# ── latest_snapshots ──────────────────────────────────────────────────────────


def test_latest_snapshots_picks_newest_per_stem(tmp_path: Path):
    for name in (
        "tunatale_no.2026-08-10.db",
        "tunatale_no.2026-08-12.db",
        "tunatale_no.2026-08-11.db",
        "tunatale_sl.2026-08-09.db",
    ):
        (tmp_path / name).touch()

    found = latest_snapshots(tmp_path)

    assert found["tunatale_no"].name == "tunatale_no.2026-08-12.db"
    assert found["tunatale_sl"].name == "tunatale_sl.2026-08-09.db"


def test_latest_snapshots_ignores_sidecars(tmp_path: Path):
    """A ``-wal``/``-shm`` beside a snapshot must not be mistaken for a snapshot.

    They appear the moment anything opens a snapshot, so the backup dir in
    practice always has some.
    """
    (tmp_path / "tunatale_no.2026-08-12.db").touch()
    (tmp_path / "tunatale_no.2026-08-12.db-wal").touch()
    (tmp_path / "tunatale_no.2026-08-12.db-shm").touch()

    assert list(latest_snapshots(tmp_path)) == ["tunatale_no"]


def test_latest_snapshots_empty_dir(tmp_path: Path):
    assert latest_snapshots(tmp_path) == {}


# ── restore_dbs ───────────────────────────────────────────────────────────────


def test_restore_dbs_copies_db_only_not_sidecars(tmp_path: Path):
    """The restore takes the ``.db`` alone — the sidecars are not part of it."""
    src = tmp_path / "src"
    src.mkdir()
    _make_db(src / "tunatale_no.2026-08-12.db")
    (src / "tunatale_no.2026-08-12.db-wal").write_bytes(b"")
    (src / "tunatale_no.2026-08-12.db-shm").write_bytes(b"\0" * 32768)

    dest = tmp_path / "restored"
    restored = restore_dbs(latest_snapshots(src), dest, Drill())

    assert set(p.name for p in dest.iterdir()) == {"tunatale_no.db"}
    assert restored["tunatale_no"].exists()


def test_restored_db_alone_reads_identically(tmp_path: Path):
    """The claim the whole procedure rests on: a ``.db`` with no sidecars is complete.

    Regression guard for the tempting "the restore dropped the WAL" diagnosis —
    it is wrong for snapshots written by the online-backup API, and chasing it
    would send someone rewriting a correct restore path.
    """
    src = tmp_path / "src"
    src.mkdir()
    original = src / "tunatale_no.2026-08-12.db"
    _make_db(original, media=[("a.mp3", "deadbeef"), ("b.mp3", "cafebabe")])
    # Opening it creates sidecars, exactly as inspection does in the real dir.
    con = sqlite3.connect(original)
    con.execute("PRAGMA journal_mode=wal")
    con.execute("SELECT COUNT(*) FROM media").fetchone()
    con.close()

    before = sqlite3.connect(original).execute("SELECT filename, sha256 FROM media ORDER BY id").fetchall()
    restored = restore_dbs(latest_snapshots(src), tmp_path / "restored", Drill())
    after = (
        sqlite3.connect(restored["tunatale_no"]).execute("SELECT filename, sha256 FROM media ORDER BY id").fetchall()
    )

    assert before == after


# ── verify_db ─────────────────────────────────────────────────────────────────


def test_verify_db_passes_on_healthy_db(tmp_path: Path):
    db = tmp_path / "ok.db"
    _make_db(db, media=[("a.mp3", "x")])
    drill = Drill()

    verify_db(db, drill)

    assert drill.failures == []


def test_verify_db_fails_on_empty_db(tmp_path: Path):
    """An empty restore is the clean negative a drill must never call success."""
    db = tmp_path / "empty.db"
    _make_db(db)
    drill = Drill()

    verify_db(db, drill)

    assert any("has rows" in f for f in drill.failures)


def test_verify_db_fails_on_corruption(tmp_path: Path):
    """Byte-level corruption must be recorded as a failure, however it surfaces.

    Two flavours, deliberately not distinguished here: mild damage makes
    ``integrity_check`` return a complaint, severe damage makes it raise. The
    property under test is that neither escapes as a pass — asserting on the
    specific label would just pin today's flavour of this fixture's corruption.
    """
    db = tmp_path / "corrupt.db"
    _make_db(db, media=[(f"f{i}.mp3", "x") for i in range(200)])
    raw = bytearray(db.read_bytes())
    # Scribble well past the header so the file still opens and only the
    # page checksums / b-tree structure give it away.
    for offset in range(4096, min(len(raw), 16384), 7):
        raw[offset] = (raw[offset] + 137) % 256
    db.write_bytes(bytes(raw))
    drill = Drill()

    verify_db(db, drill)

    assert drill.failures != []


# ── verify_media ──────────────────────────────────────────────────────────────


def test_verify_media_passes_when_all_checksums_match(tmp_path: Path):
    tree = tmp_path / "media"
    sha_a = _write_media(tree, "a.mp3", b"aaa")
    sha_b = _write_media(tree, "b.mp3", b"bbb")
    db = tmp_path / "db.db"
    _make_db(db, media=[("a.mp3", sha_a), ("b.mp3", sha_b)])
    drill = Drill()

    verify_media(db, tree, drill)

    assert drill.failures == []


def test_verify_media_fails_on_missing_file(tmp_path: Path):
    """The img_cup.jpg case: the DB references a file the tree does not have."""
    tree = tmp_path / "media"
    sha_a = _write_media(tree, "a.mp3", b"aaa")
    db = tmp_path / "db.db"
    _make_db(db, media=[("a.mp3", sha_a), ("gone.jpg", "0" * 64)])
    drill = Drill()

    verify_media(db, tree, drill)

    assert any("media sha256" in f for f in drill.failures)


def test_verify_media_fails_on_silent_corruption(tmp_path: Path):
    """Right name, right size, wrong bytes — what a file-count check misses."""
    tree = tmp_path / "media"
    sha_a = _write_media(tree, "a.mp3", b"aaa")
    (tree / "a.mp3").write_bytes(b"zzz")  # same length, different content
    db = tmp_path / "db.db"
    _make_db(db, media=[("a.mp3", sha_a)])
    drill = Drill()

    verify_media(db, tree, drill)

    assert any("media sha256" in f for f in drill.failures)


def test_verify_media_fails_loudly_on_malformed_db(tmp_path: Path):
    """A corrupt DB must not be reported as a skip — a skip reads as a pass."""
    db = tmp_path / "corrupt.db"
    db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4096)
    drill = Drill()

    verify_media(db, tmp_path, drill)

    assert drill.failures != []


def test_verify_audio_fails_loudly_on_malformed_db(tmp_path: Path):
    db = tmp_path / "corrupt.db"
    db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 4096)
    drill = Drill()

    verify_audio(db, tmp_path, drill)

    assert drill.failures != []


def test_verify_media_skips_db_without_media_table(tmp_path: Path):
    db = tmp_path / "bare.db"
    sqlite3.connect(db).close()
    drill = Drill()

    verify_media(db, tmp_path, drill)

    assert drill.failures == []


# ── verify_audio ──────────────────────────────────────────────────────────────


def test_verify_audio_passes_on_decodable_file(tmp_path: Path, silent_opus: Path):
    tree = tmp_path / "tree"
    (tree / "output/audio").mkdir(parents=True)
    shutil.copy2(silent_opus, tree / "output/audio/x.opus")
    cues = json.dumps([{"end_ms": 1500}])
    db = tmp_path / "db.db"
    _make_db(db, audio=[("lesson-1", "output/audio/x.opus", cues)])
    drill = Drill()

    verify_audio(db, tree, drill)

    assert drill.failures == []


def test_verify_audio_fails_on_truncated_file(tmp_path: Path, silent_opus: Path):
    """Header-only checks pass on a truncated file; a full decode does not.

    This is the difference between "the file exists" and "the lesson plays".
    """
    tree = tmp_path / "tree"
    (tree / "output/audio").mkdir(parents=True)
    target = tree / "output/audio/x.opus"
    target.write_bytes(silent_opus.read_bytes()[: len(silent_opus.read_bytes()) // 2])
    db = tmp_path / "db.db"
    _make_db(db, audio=[("lesson-1", "output/audio/x.opus", None)])
    drill = Drill()

    verify_audio(db, tree, drill)

    assert any("audio decodes" in f for f in drill.failures)


def test_verify_audio_fails_on_missing_file(tmp_path: Path):
    db = tmp_path / "db.db"
    _make_db(db, audio=[("lesson-1", "output/audio/gone.opus", None)])
    drill = Drill()

    verify_audio(db, tmp_path / "tree", drill)

    assert any("audio decodes" in f for f in drill.failures)


def test_verify_audio_fails_when_captions_outrun_audio(tmp_path: Path, silent_opus: Path):
    """A 2-second file whose cues claim 30 seconds: plays, but does not line up."""
    tree = tmp_path / "tree"
    (tree / "output/audio").mkdir(parents=True)
    shutil.copy2(silent_opus, tree / "output/audio/x.opus")
    db = tmp_path / "db.db"
    _make_db(db, audio=[("lesson-1", "output/audio/x.opus", json.dumps([{"end_ms": 30_000}]))])
    drill = Drill()

    verify_audio(db, tree, drill)

    assert any("audio decodes" in f for f in drill.failures)


def test_verify_audio_tolerates_unparseable_cues(tmp_path: Path, silent_opus: Path):
    """Malformed cue JSON is not a restore fault — the audio is what was restored."""
    tree = tmp_path / "tree"
    (tree / "output/audio").mkdir(parents=True)
    shutil.copy2(silent_opus, tree / "output/audio/x.opus")
    db = tmp_path / "db.db"
    _make_db(db, audio=[("lesson-1", "output/audio/x.opus", '{"not": "a list"}')])
    drill = Drill()

    verify_audio(db, tree, drill)

    assert drill.failures == []


# ── main ──────────────────────────────────────────────────────────────────────


def test_main_exits_nonzero_when_no_snapshots(tmp_path: Path):
    """An empty backup dir must be loud. It looks identical to a healthy one."""
    rc = main(["--snapshot-dir", str(tmp_path / "empty"), "--scratch", str(tmp_path / "scratch"), "--skip-trees"])

    assert rc == 1


def test_main_exits_nonzero_when_a_check_fails(tmp_path: Path):
    snaps = tmp_path / "snaps"
    snaps.mkdir()
    _make_db(snaps / "tunatale_no.2026-08-12.db")  # no rows

    rc = main(["--snapshot-dir", str(snaps), "--scratch", str(tmp_path / "scratch"), "--skip-trees"])

    assert rc == 1


def test_main_exits_zero_on_healthy_backup(tmp_path: Path):
    snaps = tmp_path / "snaps"
    snaps.mkdir()
    _make_db(snaps / "tunatale_no.2026-08-12.db", media=[("a.mp3", "x")])

    rc = main(["--snapshot-dir", str(snaps), "--scratch", str(tmp_path / "scratch"), "--skip-trees"])

    assert rc == 0


def test_main_wipes_scratch_between_runs(tmp_path: Path):
    """Stale files from a previous run must not be mistaken for restored data."""
    snaps = tmp_path / "snaps"
    snaps.mkdir()
    _make_db(snaps / "tunatale_no.2026-08-12.db", media=[("a.mp3", "x")])
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "leftover.txt").write_text("from a previous run")

    main(["--snapshot-dir", str(snaps), "--scratch", str(scratch), "--skip-trees"])

    assert not (scratch / "leftover.txt").exists()
