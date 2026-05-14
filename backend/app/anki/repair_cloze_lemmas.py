"""One-shot repair of broken `lemma` / `word_count` on single-word cloze rows.

After the cleanup_function_word_notes conversion (2026-05-12), a follow-on
sync_pull / import_seed pass re-extracted L2 text from the now-Cloze notetype
fields and wrote bogus values into both columns for the two converted rows:

| cid | text | lemma (broken) | word_count (broken) |
|-----|------|----------------|---------------------|
| 672 | sem  | NULL           | 2                   |
| 701 | vsak | 'vsakevery'    | 1                   |

The lemma column powers ``SRSDatabase.get_collocation_by_lemma_with_id``, which
the transcript extractor (``app/srs/transcript.py``) consults per-token. A broken
lemma surfaces as the word being labeled ``unknown`` in the lesson page even
though the SRS row exists. A wrong ``word_count`` makes the row turn up as a
phantom multi-word collocation.

Generic repair: for any cloze row whose ``text`` contains no whitespace, force
``lemma = lower(text)`` and ``word_count = 1``. TT-only write — no Anki tables
touched, no safe_open envelope needed.

Usage::

    uv run python -m app.anki.repair_cloze_lemmas --dry-run
    uv run python -m app.anki.repair_cloze_lemmas
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from app.config import settings


def find_broken_rows(conn: sqlite3.Connection) -> list[tuple[int, str, str | None, int]]:
    """Return (id, text, current_lemma, current_word_count) for broken cloze rows.

    A cloze row whose ``text`` has no whitespace counts as broken when either
    its ``lemma`` is missing/mismatched or its ``word_count`` isn't 1.
    """
    rows = conn.execute(
        """
        SELECT id, text, lemma, word_count
        FROM collocations
        WHERE card_type = 'cloze'
          AND text NOT LIKE '% %'
          AND (lemma IS NULL OR lemma = '' OR lemma != lower(text) OR word_count != 1)
        """,
    ).fetchall()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def apply_repair(conn: sqlite3.Connection) -> int:
    """Repair every broken cloze single-word row. Returns count."""
    broken = find_broken_rows(conn)
    for cid, text, _, _ in broken:
        conn.execute(
            "UPDATE collocations SET lemma = ?, word_count = 1 WHERE id = ?",
            (text.casefold(), cid),
        )
    conn.commit()
    return len(broken)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--dry-run", action="store_true", help="show plan without writing")
    parser.add_argument("--tt-db", type=Path, default=None, help="override TT database path")
    args = parser.parse_args(argv)

    tt_path = args.tt_db or Path(settings.database_url.removeprefix("sqlite:///"))
    if not tt_path.exists():
        print(f"TT database not found: {tt_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(tt_path), isolation_level=None)
    try:
        broken = find_broken_rows(conn)
        if not broken:
            print("No broken cloze lemmas found.")
            return 0
        print(f"Plan: repair {len(broken)} cloze single-word row(s)")
        for cid, text, current, wc in broken:
            print(
                f"  cid={cid} text={text!r} lemma={current!r} word_count={wc} -> lemma={text.casefold()!r} word_count=1"
            )
        if args.dry_run:
            print("--dry-run: no changes applied.")
            return 0
        count = apply_repair(conn)
        print(f"Applied: {count}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
