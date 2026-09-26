#!/usr/bin/env python
"""Cebuano starter cards from the Tagalog you already know (tunatale-u8nz.7).

    uv run python scripts/seed_cognate_cards.py            # dry run: print the plan
    uv run python scripts/seed_cognate_cards.py --apply    # mint, or seed what is minted

Two passes with a sync between them, because only a card that exists in Anki can
be seeded (``SRSDatabase.seed_review_state`` refuses an unlinked one):

1. ``--apply`` adds every cognate and near-cognate as a NEW card.
2. Sync Cebuano in TunaTale. ``sync_create_new`` mints them into the language's
   mint deck with its vocab notetype.
3. ``--apply`` again seeds the well-known ones as review cards, due from tomorrow
   at no more than ``--per-day`` a day.
4. Sync again. The push writes the schedule and memory state into Anki; no
   revlog row is written.

Re-running is safe at every step: nothing already added is added again, and a
card with any history of its own is never seeded.

The review gate is the kaikki.org Cebuano extract (``--dictionary``, gitignored;
the download command is in ``.beads-tasks/briefs/design-philippine-morphology-2026-09.md``).
A word it cannot confirm with a compatible English gloss is not a starter. A
Tagalog word spelled the same with no compatible gloss is printed as a FALSE
FRIEND and never minted.

⚠️ Run it on the LIVE side (``./switch.sh status``), under that side's env, and
back up the target DB first. All the logic and tests live in
``app.srs.cognate_seed``; this file is the wiring.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings
from app.languages import resolve_language_context
from app.srs import cognate_seed
from app.srs.database import SRSDatabase

# Tagalog words a person reviewed one by one and judged to mean the same thing in
# Cebuano, though the automatic tests said otherwise (parse_accept syntax).
# Batch 2, 2026-09-26: the gloss test called these FALSE FRIENDS because it
# ignores pronouns and prepositions ("we" / "we; us") and cannot see synonyms
# (sige "all right" / "OK"). eroplano and buwan were left out: the dictionary
# lists them only as variants of ayroplano and bulan.
# Batch 3, same day, from a word-by-word check of the first Cebuano lesson:
# pero, lang, mga, ni, ka and pamilya have no headword in the extract, though
# its example sentences use them 71-341 times (MIN_USAGE). syete=siyete has no
# dictionary evidence at all; the user confirmed it is the spelling for 7.
REVIEWED_COGNATES = ",".join(
    [
        *("ako", "ko", "kami", "siya", "nila", "inyo", "para", "sa", "sige", "puwede", "babay", "kaopisina"),
        *("mainit", "anak"),
        *("pero", "lang", "mga", "ni", "ka", "pamilya", "syete=siyete"),
    ]
)

# Ruled out by the user in batch 2: the dictionary lists them only as variants
# (of ayroplano and bulan). Batch 3's classifier would otherwise reach bulan.
REVIEWED_REJECTS = "eroplano,buwan"

_BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_DICTIONARY = _BACKEND / "scripts/local/kaikki/Cebuano.jsonl"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="tl", help="language the learner already knows")
    parser.add_argument("--target", default="ceb", help="language to seed")
    parser.add_argument("--dictionary", type=Path, default=DEFAULT_DICTIONARY, help="kaikki.org JSONL for --target")
    parser.add_argument("--per-day", type=int, default=cognate_seed.DEFAULT_PER_DAY)
    parser.add_argument(
        "--accept",
        default=REVIEWED_COGNATES,
        help="comma-separated reviewed source words, or source=target pairs, to treat as cognates ('' for none)",
    )
    parser.add_argument("--reject", default=REVIEWED_REJECTS, help="comma-separated source words never to add")
    parser.add_argument("--apply", action="store_true", help="write to the target DB (default: dry run)")
    args = parser.parse_args(argv)

    source = resolve_language_context(args.source, settings)
    target = resolve_language_context(args.target, settings)
    source_name = source.language.name if source.language else args.source
    with args.dictionary.open(encoding="utf-8") as fh:
        entries = cognate_seed.load_dictionary(fh)
    with args.dictionary.open(encoding="utf-8") as fh:
        dictionary = cognate_seed.Dictionary(entries, usage=cognate_seed.load_usage(fh))

    db = SRSDatabase(target.db_url)
    now = datetime.now(UTC)
    plan = cognate_seed.plan(
        cognate_seed.known_words(SRSDatabase(source.db_url)),
        dictionary,
        per_day=args.per_day,
        accept=cognate_seed.parse_accept(args.accept),
        reject=frozenset(cognate_seed.parse_accept(args.reject)),
        started=cognate_seed.started_texts(db),
        occupied=cognate_seed.review_load(db, now=now),
    )
    _print_plan(plan)

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        return 0
    added = cognate_seed.mint(db, plan.starters, language_code=args.target, source_name=source_name)
    report = cognate_seed.seed(db, plan.starters, language_code=args.target, now=now)
    print(f"\nAdded {added} new card(s). Seeded {report.seeded} direction(s).")
    if report.not_minted:
        print(f"{report.not_minted} direction(s) wait for their card to be minted: sync, then run --apply again.")
    if report.already_started:
        print(f"{report.already_started} direction(s) already have a schedule and were left alone.")
    return 0


def _print_plan(plan: cognate_seed.Plan) -> None:
    load: dict[int, int] = {}
    for st in plan.starters:
        seeds = ", ".join(f"{d.value[:4]} s={s.stability:.1f} due+{s.offset_days}" for d, s in st.seeds.items())
        m = st.match
        print(
            f"{m.relation.value:13} {m.word.text:>16} → {m.target_text:16} {m.word.translation[:30]:30} {seeds or 'NEW'}"
        )
        for s in st.seeds.values():
            load[s.offset_days] = load.get(s.offset_days, 0) + 1
    seeded = sum(load.values())
    print(
        f"\n{len(plan.starters)} starter card(s), {seeded} direction(s) seeded, {plan.unplaced} left NEW for lack of a day."
    )
    print("Reviews per day: " + " ".join(f"+{d}:{n}" for d, n in sorted(load.items())))
    print(f"{plan.unrelated} word(s) with no counterpart; {len(plan.duplicates)} duplicate(s) of an earlier starter.")
    print(f"{plan.already_started} word(s) already started in an earlier batch, left alone.")
    print(f"{plan.rejected} word(s) rejected by review.")
    if plan.false_friends:
        print(f"\nFALSE FRIENDS ({len(plan.false_friends)}), never minted — same spelling, different meaning:")
        for m in plan.false_friends:
            print(
                f"  {m.word.text}: known as {m.word.translation!r}; dictionary says {'; '.join(m.target_glosses)[:80]!r}"
            )


if __name__ == "__main__":
    sys.exit(main())
