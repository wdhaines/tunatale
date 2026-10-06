#!/usr/bin/env python
"""Put a base list's rows in frequency order: most used first, themes spread out.

    uv run python scripts/order_base_list.py --list app/plugins/languages/ceb/data/base625.tsv \\
        --frequency app/plugins/languages/ceb/data/cebuano_frequency.tsv.gz             # dry run
    ... --apply                                                                        # rewrite the file

The rule is ``app.srs.base_list.spread_by_frequency``. Only the data rows move:
comment lines and the column header stay where they are, and each row keeps
every column it had. A second run moves nothing.

This changes the FILE. Cards already minted keep their old queue positions until
``scripts/seed_base_list.py --position --apply`` runs on the live side, followed
by a sync.
"""

from __future__ import annotations

import argparse
import gzip
import sys
from pathlib import Path

from app.srs import base_list
from app.srs.cognate_seed import normalize


def _neighbours(words: list[base_list.BaseWord]) -> int:
    return sum(1 for a, b in zip(words, words[1:], strict=False) if a.category == b.category)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", type=Path, required=True, help="base-list TSV to order")
    parser.add_argument("--frequency", type=Path, required=True, help="corpus frequency TSV (word, count), gzipped")
    parser.add_argument("--apply", action="store_true", help="rewrite the list (default: dry run)")
    args = parser.parse_args(argv)

    lines = args.list.read_text(encoding="utf-8").splitlines(keepends=True)
    header = next(i for i, line in enumerate(lines) if line.strip() and not line.startswith("#"))
    rows = lines[header + 1 :]
    if any(not row.strip() or row.startswith("#") for row in rows):
        print(f"REFUSING: {args.list} has a blank line or a comment among the rows", file=sys.stderr)
        return 2
    words = base_list.load_base_list(lines)
    with gzip.open(args.frequency, "rt", encoding="utf-8") as fh:
        counts = base_list.load_counts(fh)
    ordered = base_list.spread_by_frequency(words, counts)
    row_of = dict(zip(words, rows, strict=True))
    moved = sum(1 for was, now in zip(words, ordered, strict=True) if was is not now)
    absent = [word.text for word in words if normalize(word.text) not in counts]

    print(f"{len(words)} rows, {moved} move.")
    print(f"same-theme neighbours: {_neighbours(words)} -> {_neighbours(ordered)}")
    print(f"not in the corpus: {len(absent)}" + (f" ({', '.join(absent)})" if absent else ""))
    print("first: " + ", ".join(f"{word.text} ({word.english})" for word in ordered[:12]))
    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        return 0
    args.list.write_text("".join(lines[: header + 1] + [row_of[word] for word in ordered]), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
