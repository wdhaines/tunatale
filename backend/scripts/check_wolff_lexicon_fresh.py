#!/usr/bin/env python
"""Check the committed Wolff lexicon matches a rebuild (tunatale-h0q3.36).

The lexicon is a committed build artifact whose inputs live outside the repo, so
CI cannot rebuild it. This script rebuilds in memory and compares; it writes
nothing to disk. Run from deploy.sh on the laptop where the inputs exist.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

from app.plugins.languages.ceb.wolff_cards import LEXICON_PATH
from scripts.build_cebuano_wolff_lexicon import build as default_build
from scripts.wolff_handbook_oracle import DEFAULT_DICTIONARY, DEFAULT_EN_SOUNDS


def check(lexicon: Path, dictionary: Path, en_sounds: Path, build=default_build) -> int:
    for path in (dictionary, en_sounds):
        if not path.exists():
            print(f"check_wolff_lexicon_fresh: SKIPPED, {path} not found (the build's inputs live off-repo)")
            return 0

    with gzip.open(lexicon, "rt", encoding="utf-8") as fh:
        committed = json.load(fh)

    records = build(dictionary.read_text(encoding="utf-8"), en_sounds)
    committed_entries = committed.get("entries", [])
    # Ids repeat (one per homograph paragraph: 21,740 entries, 21,372 ids on
    # 2026-11-25), so the verdict compares the whole lists, order included, and
    # ids only group the report. A dict keyed by id kept the last entry of each
    # id and let a change to an earlier one through.
    committed_by_id: dict[str, list[dict]] = {}
    for e in committed_entries:
        committed_by_id.setdefault(e["id"], []).append(e)
    rebuilt_by_id: dict[str, list[dict]] = {}
    for e in records:
        rebuilt_by_id.setdefault(e["id"], []).append(e)

    committed_sha = committed.get("meta", {}).get("source_sha256")
    actual_sha = hashlib.sha256(dictionary.read_bytes()).hexdigest()
    sha_mismatch = committed_sha != actual_sha

    only_committed = sorted(set(committed_by_id) - set(rebuilt_by_id))
    only_rebuilt = sorted(set(rebuilt_by_id) - set(committed_by_id))
    both = sorted(set(committed_by_id) & set(rebuilt_by_id))
    different = [i for i in both if committed_by_id[i] != rebuilt_by_id[i]]

    if not sha_mismatch and committed_entries == records:
        print(f"check_wolff_lexicon_fresh: OK, {len(records)} entries match a rebuild")
        return 0

    print("check_wolff_lexicon_fresh: STALE")
    if sha_mismatch:
        print(f"source sha mismatch: committed {committed_sha}, rebuilt {actual_sha}")
    print(f"ids only in the committed file: {len(only_committed)}")
    print(f"ids only in the rebuild: {len(only_rebuilt)}")
    print(f"ids present in both but different: {len(different)}")
    if not (only_committed or only_rebuilt or different) and committed_entries != records:
        print("entries are the same but in a different order")
    for i in different[:10]:
        print(i)
    print("cd backend && uv run python scripts/build_cebuano_wolff_lexicon.py")
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lexicon", type=Path, default=LEXICON_PATH)
    ap.add_argument("--dictionary", type=Path, default=DEFAULT_DICTIONARY)
    ap.add_argument("--en-sounds", type=Path, default=DEFAULT_EN_SOUNDS)
    args = ap.parse_args(argv)
    return check(args.lexicon, args.dictionary, args.en_sounds)


if __name__ == "__main__":
    sys.exit(main())
