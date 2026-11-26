#!/usr/bin/env python
"""Collapse two cards for one word into one (tunatale-h0q3.71). Logic: ``app.plugins.anki_sync.merge_pair_cards``.

    .venv/bin/python scripts/merge_pair_cards.py --language ceb \\
        --merge 796:928:nako: --merge 797:790:nimo: --merge 844:879        # dry run
    ... --apply                                                          # write

``--merge SURVIVOR:LOSER[:TEXT:KEY]`` keeps SURVIVOR, removes LOSER, and renames the
survivor to TEXT keyed KEY when given (an empty KEY is the word's own sense).

⚠️ The graves go to ``settings.tt_collection_path``, the collection the live
peer-sync uses, never the desktop collection. Run it on the LIVE side
(``./switch.sh status``) under that side's env, back the TT DB up first, then one
peer-sync carries the graves and the renamed notes to AnkiWeb.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.config import settings
from app.languages import resolve_language_context
from app.plugins.anki_sync.merge_pair_cards import Merge, apply_anki, apply_tt, plan_merges
from app.plugins.anki_sync.safety import safe_open
from app.srs.database import SRSDatabase


def _merge(spec: str) -> Merge:
    parts = spec.split(":")
    if len(parts) == 2:
        return Merge(int(parts[0]), int(parts[1]))
    if len(parts) == 4:
        return Merge(int(parts[0]), int(parts[1]), text=parts[2], disambig=parts[3])
    raise argparse.ArgumentTypeError(f"--merge wants SURVIVOR:LOSER or SURVIVOR:LOSER:TEXT:KEY, not {spec!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--language", required=True)
    parser.add_argument("--merge", type=_merge, action="append", required=True)
    parser.add_argument("--db", default=None, help="database URL (default: the language's configured DB)")
    parser.add_argument("--collection", type=Path, default=None, help="default: settings.tt_collection_path")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args(argv)
    collection = args.collection or Path(settings.tt_collection_path)

    db = SRSDatabase(args.db or resolve_language_context(args.language, settings).db_url)
    try:
        with db._get_conn() as conn:
            steps = plan_merges(conn, args.language, args.merge)
        for s in steps:
            rename = f" renamed to {s.text!r} key {s.disambig!r}" if s.renamed else ""
            print(f"  KEEP id={s.survivor_id} note={s.survivor_note_id}{rename}")
            print(f"  DROP id={s.loser_id} note={s.loser_note_id} (graved in {collection.name})")
        if not args.apply:
            print("\nDRY RUN: nothing written. Re-run with --apply.")
            return 0
        with safe_open(collection, mode="rw") as ctx:
            apply_anki(ctx.conn, steps, args.language)
        apply_tt(db, steps, args.language)
        print(f"\nMerged {len(steps)}. Peer-sync to carry the graves and renamed notes.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
