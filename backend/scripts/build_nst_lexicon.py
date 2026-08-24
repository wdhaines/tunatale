#!/usr/bin/env python3
"""Build tooling for the NST pronunciation lexicon (Norwegian plugin).

Two subcommands, normally run from ``backend/``::

    uv run python scripts/build_nst_lexicon.py extract   # raw .pron -> committed gz
    uv run python scripts/build_nst_lexicon.py build     # committed gz -> gitignored DB

``extract`` turns the raw CC0 NST dump (~170 MB, latin-1, semicolon-separated,
51 fields) into the 4-column gzipped TSV the plugin commits. ``build`` turns
that extract into the indexed SQLite database via
``app.plugins.languages.no.lexicon.build_lexicon_db`` — never a reimplementation.
All defaults resolve against the repository, not the caller's cwd.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND.parent

sys.path.insert(0, str(_BACKEND))

from app.plugins.languages.no.lexicon import DB_PATH as DEFAULT_DB_PATH  # noqa: E402
from app.plugins.languages.no.lexicon import build_lexicon_db  # noqa: E402

DEFAULT_PRON_PATH = _REPO_ROOT / "backend/scripts/local/nst_source/nor030224NST.pron"
DEFAULT_EXTRACT_PATH = _REPO_ROOT / "backend/app/plugins/languages/no/data/nst_lexicon.tsv.gz"

# Rows whose certainty field is empty (all 972 also lack a POS tag) normalise to
# 9 — a min-wins sentinel that keeps them reachable without letting them beat a
# real reading (certainty 1 and 2 are the only values the source uses).
EMPTY_CERTAINTY_SENTINEL = "9"


def _extract(input_path: Path, output_path: Path) -> None:
    """Read the raw .pron dump and write the committed gzipped 4-column TSV."""
    lines_read = 0
    rows: set[tuple[str, str, str, str]] = set()
    with open(input_path, encoding="latin-1") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if not line:
                continue
            lines_read += 1
            fields = line.split(";")
            if len(fields) < 13:
                raise ValueError(f"Malformed line in {input_path}: {line!r}")
            word, pos, transcription, certainty = fields[0], fields[1], fields[11], fields[12]
            rows.add((word, pos, transcription, certainty or EMPTY_CERTAINTY_SENTINEL))

    payload = "".join(
        f"{word}\t{pos}\t{transcription}\t{certainty}\n" for word, pos, transcription, certainty in sorted(rows)
    ).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(buf.getvalue())

    print(f"lines read: {lines_read}")
    print(f"rows after dedupe: {len(rows)}")
    print(f"output: {output_path}")
    print(f"size: {output_path.stat().st_size} bytes")
    print(f"sha256: {hashlib.sha256(buf.getvalue()).hexdigest()}")


def _build(input_path: Path, output_path: Path) -> None:
    """Build the indexed SQLite database from the committed gzipped extract."""
    build_lexicon_db(input_path, output_path)
    conn = sqlite3.connect(f"file:{output_path}?mode=ro", uri=True)
    try:
        (rows_inserted,) = conn.execute("SELECT COUNT(*) FROM entries").fetchone()
    finally:
        conn.close()
    print(f"rows inserted: {rows_inserted}")
    print(f"output: {output_path}")
    print(f"size: {output_path.stat().st_size} bytes")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    parser_extract = subparsers.add_parser("extract", help="raw NST .pron -> committed gzipped TSV")
    parser_extract.add_argument("--input", type=Path, default=DEFAULT_PRON_PATH)
    parser_extract.add_argument("--output", type=Path, default=DEFAULT_EXTRACT_PATH)

    parser_build = subparsers.add_parser("build", help="committed gzipped TSV -> gitignored lexicon DB")
    parser_build.add_argument("--input", type=Path, default=DEFAULT_EXTRACT_PATH)
    parser_build.add_argument("--output", type=Path, default=DEFAULT_DB_PATH)

    args = parser.parse_args(argv)
    if args.command == "extract":
        _extract(args.input, args.output)
    else:
        _build(args.input, args.output)


if __name__ == "__main__":
    main()
