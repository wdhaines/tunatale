#!/usr/bin/env python3
"""Lesson audio by LAST USE — the pruning instrument (tunatale-al6). READ-ONLY.

Retention policy (docs/deployment.md § Disk and log hygiene): nothing is deleted
now; if pruning is ever needed it goes by when a lesson was last listened to or
reviewed, not by when it was rendered. This prints what that policy would see.

Stdlib only and 3.12-compatible (catch one exception type per clause): scripts/
is not in the api image and `uv run` inside the container is forbidden, so on
the box it runs as

    sudo docker exec -i tunatale-api-1 python - --db /data/tunatale_no.db \\
        --db /data/tunatale_sl.db --audio-dir /data/output/audio \\
        < backend/scripts/report_audio_retention.py

and locally as `uv run python scripts/report_audio_retention.py --db tunatale_no.db
--db tunatale_sl.db --audio-dir output/audio`.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class LessonAudio:
    lesson_id: str
    db: str
    day: int | None
    files: int = 0
    bytes: int = 0
    missing: int = 0
    orphaned: bool = False
    days_since_use: int | None = None


@dataclass
class Report:
    lessons: list[LessonAudio] = field(default_factory=list)
    unreferenced: dict[str, int] = field(default_factory=dict)


def _parse(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace(" ", "T"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def collect(db_paths: list[Path], audio_dir: Path, now: datetime) -> Report:
    report = Report()
    referenced: set[str] = set()
    for db_path in db_paths:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            days = dict(conn.execute("SELECT id, day FROM lessons"))
            last: dict[str, datetime] = {}
            for table, col in (("lesson_listens", "listened_at"), ("lesson_reviews", "reviewed_at")):
                for lesson_id, ts in conn.execute(f"SELECT lesson_id, max({col}) FROM {table} GROUP BY lesson_id"):
                    t = _parse(ts)
                    if lesson_id not in last or t > last[lesson_id]:
                        last[lesson_id] = t
            rows: dict[str, LessonAudio] = {}
            for lesson_id, file_path in conn.execute("SELECT lesson_id, file_path FROM audio_files"):
                row = rows.get(lesson_id)
                if row is None:
                    row = rows[lesson_id] = LessonAudio(
                        lesson_id=lesson_id,
                        db=db_path.name,
                        day=days.get(lesson_id),
                        orphaned=lesson_id not in days,
                        days_since_use=(now - last[lesson_id]).days if lesson_id in last else None,
                    )
                name = Path(file_path).name
                referenced.add(name)
                f = audio_dir / name
                if f.is_file():
                    row.files += 1
                    row.bytes += f.stat().st_size
                else:
                    row.missing += 1
            report.lessons.extend(rows.values())
        finally:
            conn.close()
    for f in sorted(audio_dir.iterdir()):
        if f.is_file() and f.name not in referenced:
            report.unreferenced[f.name] = f.stat().st_size
    return report


def _bucket(row: LessonAudio) -> str:
    if row.orphaned:
        return "orphaned (lesson gone)"
    if row.days_since_use is None:
        return "never listened"
    if row.days_since_use > 90:
        return "> 90 days"
    if row.days_since_use > 30:
        return "31-90 days"
    return "<= 30 days"


BUCKETS = ["<= 30 days", "31-90 days", "> 90 days", "never listened", "orphaned (lesson gone)"]


def render(report: Report) -> str:
    mb = 1e6
    out = ["Lesson audio by last use (listen or review). Read-only; nothing was deleted.", ""]
    out.append(f"{'last use':<24}{'lessons':>8}{'files':>7}{'MB':>9}")
    for b in BUCKETS:
        rows = [r for r in report.lessons if _bucket(r) == b]
        out.append(f"{b:<24}{len(rows):>8}{sum(r.files for r in rows):>7}{sum(r.bytes for r in rows) / mb:>9.1f}")
    un = report.unreferenced
    out.append(f"{'unreferenced files':<24}{'':>8}{len(un):>7}{sum(un.values()) / mb:>9.1f}")
    missing = sum(r.missing for r in report.lessons)
    if missing:
        out.append(f"\n{missing} audio_files rows point at files that are not on disk.")
    out += ["", f"{'lesson':<52}{'db':<16}{'day':>4}{'MB':>7}{'days':>6}  bucket"]
    order = sorted(report.lessons, key=lambda r: (r.days_since_use is None, -(r.days_since_use or 0)))
    for r in order:
        day = "" if r.day is None else str(r.day)
        since = "" if r.days_since_use is None else str(r.days_since_use)
        out.append(f"{r.lesson_id[:51]:<52}{r.db:<16}{day:>4}{r.bytes / mb:>7.1f}{since:>6}  {_bucket(r)}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", type=Path, action="append", required=True, help="a language DB; repeat for each")
    p.add_argument("--audio-dir", type=Path, required=True)
    args = p.parse_args(argv)
    print(render(collect(args.db, args.audio_dir, datetime.now(UTC))))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
