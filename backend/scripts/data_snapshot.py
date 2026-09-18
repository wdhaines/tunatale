#!/usr/bin/env python3
"""Consistent SQLite snapshots and a row-count comparison, for data-transfer.sh.

    python3 data_snapshot.py snapshot SRC DST
    python3 data_snapshot.py stats --db no=/data/tunatale_no.db --dir media=/data/media [--out F]
    python3 data_snapshot.py compare A.json B.json

⚠️ STDLIB ONLY, and Python 3.12-compatible. It runs on the Mac under uv AND on
the box's system python3 (3.12), which has no sqlite3 CLI and no project venv.
tests/test_data_snapshot.py parses this file as 3.12 syntax, because ruff
targets 3.14 and would otherwise rewrite exception tuples into a form 3.12
rejects — green here, a SyntaxError on the box.

Why the backup API and never `cp`: the databases run in WAL mode, so committed
rows can live in `-wal` until a checkpoint. A byte copy of the main file drops
them silently. The backup API reads through SQLite and produces one standalone
file that holds everything committed at the moment it runs.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _unicase(a: str, b: str) -> int:
    """Anki's `unicase` collation: Unicode SIMPLE case folding, which lower()
    matches (casefold() is FULL folding — "ß" becomes "ss" — and would disagree
    with the index order Anki wrote, making integrity_check report false errors)."""
    x, y = a.lower(), b.lower()
    return (x > y) - (x < y)


def _connect(target: str, **kwargs) -> sqlite3.Connection:
    """Every connection knows `unicase`: tt_collection.anki2 is an Anki
    collection, and its indexes cannot be read or checked without it."""
    conn = sqlite3.connect(target, **kwargs)
    conn.create_collation("unicase", _unicase)
    return conn


def _open_ro(path: Path) -> sqlite3.Connection:
    """Read-only by URI, so a mistyped path raises instead of creating a file."""
    if not path.is_file():
        raise FileNotFoundError(path)
    return _connect(f"file:{path}?mode=ro", uri=True)


def snapshot_db(src: Path, dst: Path) -> None:
    """Copy *src* to *dst* with the online backup API, then integrity-check it."""
    source = _open_ro(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    target = _connect(str(dst))
    try:
        source.backup(target)
        # A copy of a WAL database inherits WAL mode; ship it as one plain file.
        target.execute("PRAGMA journal_mode=DELETE")
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        target.close()
        source.close()
    if result != "ok":
        raise RuntimeError(f"snapshot of {src} failed integrity_check: {result}")


def db_stats(path: Path) -> dict:
    """Row count of every user table, plus the integrity_check verdict.

    A missing file is reported, not raised: `status` against a fresh box must
    show the gap. `snapshot_db` still raises on a missing source.
    """
    if not path.is_file():
        return {"missing": True}
    conn = _open_ro(path)
    try:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        tables = {name: conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in names}
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    return {"integrity": integrity, "tables": tables}


def dir_stats(path: Path) -> dict:
    """File count and total bytes under *path*; a missing dir is reported as such."""
    if not path.is_dir():
        return {"missing": True}
    files = [p for p in path.rglob("*") if p.is_file()]
    return {"files": len(files), "bytes": sum(p.stat().st_size for p in files)}


def collect(dbs: dict[str, Path], dirs: dict[str, Path]) -> dict:
    return {
        "dbs": {name: db_stats(path) for name, path in dbs.items()},
        "dirs": {name: dir_stats(path) for name, path in dirs.items()},
    }


def compare(a: dict, b: dict) -> list[str]:
    """Every difference between two ``collect`` results, as readable lines."""
    problems: list[str] = []
    for name in sorted(set(a["dbs"]) | set(b["dbs"])):
        left, right = a["dbs"].get(name), b["dbs"].get(name)
        if left is None or right is None:
            problems.append(f"db {name}: {'present' if left else '(absent)'} != {'present' if right else '(absent)'}")
            continue
        if "missing" in left or "missing" in right:
            if left != right:
                problems.append(
                    f"db {name}: {'(missing)' if 'missing' in left else 'present'}"
                    f" != {'(missing)' if 'missing' in right else 'present'}"
                )
            continue
        if left["integrity"] != right["integrity"]:
            problems.append(f"{name} integrity: {left['integrity']!r} != {right['integrity']!r}")
        for table in sorted(set(left["tables"]) | set(right["tables"])):
            lc = left["tables"].get(table, "(absent)")
            rc = right["tables"].get(table, "(absent)")
            if lc != rc:
                problems.append(f"{name}.{table}: {lc} != {rc}")
    for name in sorted(set(a["dirs"]) | set(b["dirs"])):
        left, right = a["dirs"].get(name), b["dirs"].get(name)
        if left != right:
            problems.append(f"dir {name}: {left} != {right}")
    return problems


def _pairs(values: list[str]) -> dict[str, Path]:
    out = {}
    for value in values:
        name, _, path = value.partition("=")
        out[name] = Path(path)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("src", type=Path)
    snap.add_argument("dst", type=Path)
    stats = sub.add_parser("stats")
    stats.add_argument("--db", action="append", default=[])
    stats.add_argument("--dir", action="append", default=[])
    stats.add_argument("--out", type=Path)
    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("a", type=Path)
    cmp_.add_argument("b", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "snapshot":
        snapshot_db(args.src, args.dst)
        print(f"snapshot ok: {args.dst}")
        return 0
    if args.cmd == "stats":
        text = json.dumps(collect(_pairs(args.db), _pairs(args.dir)), indent=1, sort_keys=True)
        if args.out:
            args.out.write_text(text + "\n", encoding="utf-8")
        else:
            print(text)
        return 0
    a = json.loads(args.a.read_text(encoding="utf-8"))
    b = json.loads(args.b.read_text(encoding="utf-8"))
    for name in sorted(a["dbs"]):
        stat = a["dbs"][name]
        if "missing" in stat:
            print(f"  {name}: (missing)")
            continue
        tables = stat["tables"]
        print(f"  {name}: {len(tables)} tables, {sum(tables.values())} rows, integrity {stat['integrity']}")
    for name, stat in sorted(a["dirs"].items()):
        print(f"  {name}: {stat}")
    problems = compare(a, b)
    for line in problems:
        print(f"MISMATCH {line}")
    print("VERIFIED: source and destination match" if not problems else f"FAILED: {len(problems)} mismatches")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
