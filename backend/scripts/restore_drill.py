"""Restore drill: prove a backup actually restores, and record the RTO.

A backup that has never been restored is not a backup. This project wiped its
curricula twice (2026-06-30, 2026-07-13), so the restore path is the half that
carries the real risk.

Runs against ANY directory holding ``{stem}.{YYYY-MM-DD}.db`` snapshots plus
optional media/output trees — the local rolling snapshots today, a
restic/rclone-restored tree once off-box backups exist. Phase 2 re-runs this
identical command against the remote restore; that is why it takes source
directories rather than hardcoding ``~/.tunatale/db-backups``.

Read-only with respect to every source it is pointed at: sources are opened
``mode=ro`` or copied from, never written. Everything lands under --scratch.

    uv run python scripts/restore_drill.py                       # local snapshots
    uv run python scripts/restore_drill.py --snapshot-dir /restored/db-backups

Exit code is non-zero if any check fails, so it can be a cron/CI gate.

⚠️ Opening a snapshot creates ``-wal``/``-shm`` sidecars beside it. That is an
inspection artifact, not snapshot content: ``rotate_db_backups`` writes each
snapshot with the SQLite online-backup API, which materialises everything into
the ``.db``. Verified 2026-08-12 — a ``.db`` copied alone, with no sidecars,
gives row-for-row identical counts. Do not "fix" a restore by chasing the WAL.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_SNAPSHOT_DIR = Path("~/.tunatale/db-backups").expanduser()
_BACKEND_DIR = Path(__file__).parent.parent


class Drill:
    """Accumulates pass/fail across checks so one run reports every problem."""

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.timings: dict[str, float] = {}

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}{f' — {detail}' if detail else ''}")
        if not ok:
            self.failures.append(label)
        return ok


def latest_snapshots(snapshot_dir: Path) -> dict[str, Path]:
    """Newest snapshot per DB stem. ISO dates sort chronologically."""
    newest: dict[str, Path] = {}
    for snap in sorted(snapshot_dir.glob("*.db")):
        stem = snap.name.rsplit(".", 2)[0]
        newest[stem] = snap
    return newest


def restore_dbs(snapshots: dict[str, Path], dest: Path, drill: Drill) -> dict[str, Path]:
    """Copy each snapshot's ``.db`` ONLY — no sidecars — and time it."""
    dest.mkdir(parents=True, exist_ok=True)
    restored: dict[str, Path] = {}
    start = time.monotonic()
    for stem, snap in snapshots.items():
        target = dest / f"{stem}.db"
        shutil.copy2(snap, target)
        restored[stem] = target
    drill.timings["restore_dbs"] = time.monotonic() - start
    print(f"  restored {len(restored)} DB(s) in {drill.timings['restore_dbs']:.2f}s")
    return restored


def verify_db(path: Path, drill: Drill) -> None:
    """Integrity, FK state, and row counts for one restored DB.

    Corruption arrives in two flavours and both must land as a recorded FAIL:
    mild damage makes ``integrity_check`` *return* a complaint, while damage bad
    enough to break the page structure makes it *raise* ``DatabaseError``. An
    uncaught raise would abort the whole drill on a traceback — losing the checks
    for every other DB behind it — which is the opposite of what a drill is for.
    """
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA integrity_check;").fetchone()[0]
        drill.check(integrity == "ok", f"{path.name} integrity_check", integrity)

        orphans = con.execute("PRAGMA foreign_key_check;").fetchall()
        # Reported, never failed: FK enforcement is off (SQLite's default) and
        # pre-existing orphans are a data-quality question, not a restore fault.
        # A restore is faithful when it reproduces the source exactly, warts included.
        if orphans:
            print(f"  [note] {path.name}: {len(orphans)} pre-existing FK orphan row(s)")

        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;"
            )
        ]
        counts = {t: con.execute(f'SELECT COUNT(*) FROM "{t}";').fetchone()[0] for t in tables}
        drill.check(
            any(counts.values()), f"{path.name} has rows", f"{sum(counts.values())} across {len(tables)} tables"
        )
        for table, n in counts.items():
            print(f"        {table:<26} {n}")
    except sqlite3.DatabaseError as exc:
        drill.check(False, f"{path.name} readable", str(exc))
    finally:
        con.close()


def verify_media(db: Path, media_root: Path, drill: Drill) -> None:
    """Checksum every media file the DB claims, against the restored tree.

    The ``media`` table records a sha256 per file, which makes this a real
    cryptographic verification rather than a file-count comparison.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT filename, sha256 FROM media WHERE sha256 IS NOT NULL AND sha256 != ''").fetchall()
    except sqlite3.OperationalError as exc:
        # No such table — a legitimate skip (not every DB has media).
        print(f"  [skip] {db.name}: no usable media table ({exc})")
        return
    except sqlite3.DatabaseError as exc:
        # Malformed. Must NOT be reported as a skip: a skip reads as a pass, and
        # "the drill quietly verified nothing" is the failure this bead exists for.
        drill.check(False, f"{db.name} media table readable", str(exc))
        return
    finally:
        con.close()

    if not rows:
        print(f"  [skip] {db.name}: no media rows with a recorded sha256")
        return

    start = time.monotonic()
    matched = missing = mismatched = 0
    examples: list[str] = []
    for filename, sha in rows:
        path = media_root / filename
        if not path.exists():
            missing += 1
            if len(examples) < 3:
                examples.append(f"missing {filename}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() == sha:
            matched += 1
        else:
            mismatched += 1
            if len(examples) < 3:
                examples.append(f"sha mismatch {filename}")
    drill.timings["verify_media"] = time.monotonic() - start
    drill.check(
        missing == 0 and mismatched == 0,
        f"{db.name} media sha256",
        f"{matched}/{len(rows)} matched, {missing} missing, {mismatched} mismatched"
        + (f" ({'; '.join(examples)})" if examples else ""),
    )


def verify_audio(db: Path, tree_root: Path, drill: Drill) -> None:
    """Full-decode each lesson's audio and check the caption timeline fits.

    ``ffmpeg -f null`` decodes every frame, so this proves playability rather
    than merely that a file exists and has a readable header.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT lesson_id, file_path, cues_json FROM audio_files").fetchall()
    except sqlite3.OperationalError as exc:
        print(f"  [skip] {db.name}: no usable audio_files table ({exc})")
        return
    except sqlite3.DatabaseError as exc:
        drill.check(False, f"{db.name} audio_files table readable", str(exc))
        return
    finally:
        con.close()

    if not rows:
        print(f"  [skip] {db.name}: no audio_files rows")
        return

    start = time.monotonic()
    lessons: dict[str, dict[str, float | int]] = {}
    failures: list[str] = []
    for lesson_id, file_path, cues_json in rows:
        stats = lessons.setdefault(lesson_id, {"files": 0, "seconds": 0.0, "errors": 0, "cue_overruns": 0})
        stats["files"] = int(stats["files"]) + 1
        path = tree_root / file_path
        if not path.exists():
            stats["errors"] = int(stats["errors"]) + 1
            failures.append(f"missing {file_path}")
            continue
        decode = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(path), "-f", "null", "-"],
            capture_output=True,
            text=True,
        )
        if decode.returncode != 0 or decode.stderr.strip():
            stats["errors"] = int(stats["errors"]) + 1
            failures.append(f"decode {file_path}: {decode.stderr.strip()[:60]}")
            continue
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True,
            text=True,
        )
        duration = float(probe.stdout.strip() or 0.0)
        stats["seconds"] = float(stats["seconds"]) + duration
        if cues_json:
            try:
                cue_end = max(c["end_ms"] for c in json.loads(cues_json)) / 1000.0
            except ValueError, KeyError, TypeError:
                cue_end = 0.0
            # Captions running past the audio mean the timeline and the render
            # disagree — the file plays but the lesson does not line up.
            if cue_end > duration + 0.5:
                stats["cue_overruns"] = int(stats["cue_overruns"]) + 1
                failures.append(f"cues overrun audio in {file_path}")

    drill.timings["verify_audio"] = time.monotonic() - start
    for lesson_id, stats in lessons.items():
        print(
            f"        {lesson_id:<48} {stats['files']} files, "
            f"{float(stats['seconds']) / 60:.1f} min, {stats['errors']} decode err, "
            f"{stats['cue_overruns']} cue overrun"
        )
    drill.check(
        not failures,
        f"{db.name} audio decodes end to end",
        f"{len(rows)} files across {len(lessons)} lessons"
        + (f"; first failures: {'; '.join(failures[:3])}" if failures else ""),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--snapshot-dir", type=Path, default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--media-src", type=Path, default=_BACKEND_DIR / "media")
    parser.add_argument("--output-src", type=Path, default=_BACKEND_DIR / "output")
    parser.add_argument("--scratch", type=Path, required=True, help="scratch dir; WIPED on each run")
    parser.add_argument("--skip-trees", action="store_true", help="DBs only (fast)")
    args = parser.parse_args(argv)

    drill = Drill()
    overall_start = time.monotonic()

    scratch = args.scratch.expanduser()
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)

    print(f"\n=== RESTORE DRILL — source: {args.snapshot_dir} ===\n")

    snapshots = latest_snapshots(args.snapshot_dir)
    if not drill.check(bool(snapshots), "snapshots found", f"{len(snapshots)}: {', '.join(sorted(snapshots))}"):
        return 1

    print("\n--- databases ---")
    restored = restore_dbs(snapshots, scratch / "db", drill)
    for path in restored.values():
        verify_db(path, drill)

    if not args.skip_trees:
        print("\n--- media + output trees ---")
        tree = scratch / "tree"
        tree.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        for src, name in ((args.media_src, "media"), (args.output_src, "output")):
            if src.exists():
                shutil.copytree(src, tree / name, symlinks=True)
        drill.timings["restore_trees"] = time.monotonic() - start
        print(f"  restored trees in {drill.timings['restore_trees']:.2f}s")
        for path in restored.values():
            verify_media(path, tree / "media", drill)
            verify_audio(path, tree, drill)

    total = time.monotonic() - overall_start
    print("\n=== RTO ===")
    for label, seconds in drill.timings.items():
        print(f"  {label:<18} {seconds:7.2f}s")
    print(f"  {'TOTAL':<18} {total:7.2f}s")
    print(
        "\nBoot the app against the restored copy to finish the drill:\n"
        f'  DATABASE_URLS=\'{{"no": "sqlite:///{scratch / "db" / "tunatale_no.db"}"}}\' \\\n'
        f"    DB_BACKUP_DIR={scratch / 'db-backups'} SYNC_ENABLED=false LLM_MODE=mock \\\n"
        "    uv run uvicorn app.main:app --port 8099\n"
        "  then compare /api/srs/stats and /api/srs/queue-stats against the live server."
    )

    if drill.failures:
        print(f"\n=== DRILL FAILED ({len(drill.failures)}): {', '.join(drill.failures)} ===")
        return 1
    print("\n=== DRILL PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
