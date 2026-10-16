#!/usr/bin/env python
"""Turn pronoun clozes into picture cards, keeping their history (tunatale-37c1).

Three steps, each a dry run until --apply, each followed by a sync:

    uv run python scripts/convert_pronoun_clozes.py --language ceb --retire [--apply]
    # sync: the graves reach AnkiWeb
    uv run python scripts/convert_pronoun_clozes.py --language ceb --add [--apply]
    # sync twice: the pre-stage draws, the mint creates the notes
    uv run python scripts/convert_pronoun_clozes.py --language ceb --inherit [--apply]
    # sync: the history reaches Anki

Why three, and why in this order: ``app.plugins.anki_sync.convert_pronoun_clozes``.

⚠️ **The graves go to ``settings.tt_collection_path``**, the collection the live
peer-sync writes, NOT ``anki_collection_path`` (the desktop collection, which the
number-cloze script defaulted to). Pass ``--collection`` only to override it.

⚠️ **Anki is graved BEFORE the TT row is deleted**, as in
``migrate_number_clozes.py``: a failure between the two leaves a graved note
whose TT row the next sync's ``detect_and_reset_orphans`` deletes, which is
self-repairing; the reverse order would strand a cloze note for re-import.

``--add`` also redraws every existing pronoun card (``picture_redraw``), so the
cards that were already pictures get their sentence in the same sync.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.cards.picture_redraw import apply_redraws, plan_redraws
from app.config import settings
from app.languages import resolve_language_context
from app.plugins.anki_sync.convert_pronoun_clozes import (
    PronounCloze,
    gloss_updates,
    inheritance_target,
    plan_new_cards,
    plan_pronoun_clozes,
)
from app.plugins.anki_sync.migrate_number_clozes import (
    InheritedState,
    apply_inheritance,
    bump_col_mod,
    delete_tt_clozes,
    grave_cloze_note,
)
from app.plugins.anki_sync.safety import safe_open
from app.srs.database import SRSDatabase

DEFAULT_CAPTURE = Path("~/.tunatale/pronoun-cloze-conversion.json").expanduser()


def _save(path: Path, plan: list[PronounCloze]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "cloze_id": p.cloze_id,
            "text": p.text,
            "cloze_note_id": p.cloze_note_id,
            "inherited": p.inherited.values if p.inherited else None,
        }
        for p in plan
    ]
    path.write_text(json.dumps(payload, indent=2))


def _load(path: Path) -> list[PronounCloze]:
    return [
        PronounCloze(
            d["cloze_id"], d["text"], d["cloze_note_id"], InheritedState(d["inherited"]) if d["inherited"] else None
        )
        for d in json.loads(path.read_text())
    ]


def _retire(args, lang: str, db: SRSDatabase) -> int:
    with db._get_conn() as conn:
        plan = plan_pronoun_clozes(conn, lang)
    if not plan:
        print("Nothing to retire: no cloze's word is a drawn pronoun.")
        return 0
    for p in plan:
        carries = f"INHERITS {p.inherited.state} reps={p.inherited.reps}" if p.inherited else "no history"
        print(f"  {p.text:6} cloze={p.cloze_id:<6} anki_note={p.cloze_note_id!s:16} {carries}")
    if not args.apply:
        print("\nDRY RUN: nothing written. Re-run with --apply.")
        return 0
    if args.capture_file.exists():
        print(f"REFUSING: {args.capture_file} exists, so a retire already ran. Move it aside to start over.")
        return 2
    _save(args.capture_file, plan)
    print(f"\nCaptured -> {args.capture_file}")
    with safe_open(args.collection, mode="rw") as ctx:
        usn_before = ctx.conn.execute("SELECT usn FROM col").fetchone()[0]
        cards = sum(grave_cloze_note(ctx.conn, p.cloze_note_id) for p in plan if p.cloze_note_id is not None)
        bump_col_mod(ctx.conn)
        ctx.conn.commit()
        assert ctx.conn.execute("SELECT usn FROM col").fetchone()[0] == usn_before, "col.usn must not move"
    print(f"Anki ({args.collection}): graved {cards} card(s); col.mod bumped, col.usn and col.scm untouched.")
    with db._get_conn() as conn:
        deleted = delete_tt_clozes(conn, [p.cloze_id for p in plan])
        conn.commit()
    print(f"TunaTale: deleted {deleted} cloze row(s).\n\nNEXT: sync once, then run --add.")
    return 0


def _add(args, lang: str, db: SRSDatabase) -> int:
    converted = [p.text for p in _load(args.capture_file)] if args.capture_file.exists() else []
    with db._get_conn() as conn:
        units = plan_new_cards(conn, lang, converted=converted)
        glosses = gloss_updates(conn, lang)
    redraws = [r for r in plan_redraws(db, lang) if r.new_filename.startswith("pronoun_")]
    for u in units:
        print(f"  ADD     {u.text:6} key={u.disambig_key or '-':9} {u.translation!r}")
    for coll_id, text, old, new in glosses:
        print(f"  GLOSS   {text:6} id={coll_id:<6} {old!r} -> {new!r}")
    for r in redraws:
        print(f"  REDRAW  {r.text:6} id={r.collocation_id:<6} {r.old_filename} -> {r.new_filename}")
    if not (units or glosses or redraws):
        print("Nothing to add, re-gloss or redraw.")
        return 0
    if not args.apply:
        print("\nDRY RUN: nothing written. Re-run with --apply.")
        return 0
    for u in units:
        db.add_collocation(u, lang)
    for coll_id, text, _old, new in glosses:
        db.update_collocation_fields(coll_id, text=text, translation=new)
    apply_redraws(db, redraws, lang)
    print(f"\nAdded {len(units)}, re-glossed {len(glosses)}, redrew {len(redraws)}.")
    print("NEXT: sync twice (the pre-stage draws, then the mint), then run --inherit.")
    return 0


def _inherit(args, lang: str, db: SRSDatabase) -> int:
    if not args.capture_file.exists():
        print(f"REFUSING: no capture file at {args.capture_file}. Run --retire --apply first.")
        return 2
    carriers = [p for p in _load(args.capture_file) if p.inherited]
    if not carriers:
        print("Nothing to inherit: no retired cloze carried history.")
        return 0
    with db._get_conn() as conn:
        ready = [(p, inheritance_target(conn, lang, p.text)) for p in carriers]
        for p, target in ready:
            where = f"-> id={target}" if target else "-- not minted yet; sync again"
            print(f"  {p.text:6} {p.inherited.state} reps={p.inherited.reps} {where}")
        if not args.apply:
            print("\nDRY RUN: nothing written. Re-run with --apply.")
            return 0
        applied = sum(apply_inheritance(conn, target, p.inherited) for p, target in ready if target)
        conn.commit()
    print(f"\nApplied history to {applied} card(s), each dirty_fsrs=1. NEXT: sync once.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--language", required=True)
    parser.add_argument("--capture-file", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--collection", type=Path, default=None, help="default: settings.tt_collection_path")
    parser.add_argument("--apply", action="store_true")
    step = parser.add_mutually_exclusive_group(required=True)
    step.add_argument("--retire", action="store_true")
    step.add_argument("--add", action="store_true")
    step.add_argument("--inherit", action="store_true")
    args = parser.parse_args(argv)
    args.collection = args.collection or Path(settings.tt_collection_path)
    context = resolve_language_context(args.language, settings)
    db = SRSDatabase(context.db_url)
    lang = context.target_language
    if args.retire:
        return _retire(args, lang, db)
    return _add(args, lang, db) if args.add else _inherit(args, lang, db)


if __name__ == "__main__":
    sys.exit(main())
