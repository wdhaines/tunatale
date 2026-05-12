"""Stage 2a orchestrator: read-only Anki → TunaTale import.

Usage:
    uv run python -m app.anki.import_seed --deck "0. Slovene" [--dry-run]

All TunaTale writes are wrapped in a single transaction: --dry-run issues
ROLLBACK, real mode issues COMMIT, any exception triggers ROLLBACK.
The Anki safety backup runs before the TunaTale transaction opens.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from app.anki.safety import safe_open
from app.anki.sqlite_reader import (
    AnkiCard,
    extract_disambig_from_fields,
    extract_gloss_from_fields,
    extract_l2,
    extract_l2_from_fields,
    extract_translation,
    fetch_cards_for_notes,
    fetch_notes_for_deck,
    find_deck_id,
    list_media_refs,
)
from app.common.guid import compute_guid
from app.config import settings
from app.media.importer import copy_media_file
from app.models.srs_item import Direction, DirectionState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase


def _build_directions(
    cards: list[AnkiCard],
    note_id: int,
) -> dict[Direction, DirectionState]:
    """Build a {Direction: DirectionState} map from the cards for one note.

    Only directions that have an actual card in Anki are returned. Single-
    template notetypes (e.g. Anki's "Basic" used for phonics) only ever have
    a recognition card, and TT used to invent a phantom production direction
    with `anki_card_id=None` for them — polluting the learning count and
    leaving orphan rows the sync layer couldn't reconcile.
    """
    directions: dict[Direction, DirectionState] = {}
    for card in cards:
        directions[card.direction] = card.fsrs_state
    return directions


def import_seed(
    deck_name: str | None = None,
    anki_collection_path: Path | None = None,
    anki_media_path: Path | None = None,
    anki_backup_dir: Path | None = None,
    tunatale_db_path: str | None = None,
    media_dir: Path | None = None,
    fallback_log_path: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Import an Anki deck into TunaTale.

    Returns a summary dict with counts: new_parents, new_directions,
    new_media, skipped_guid_collisions.
    """
    if deck_name is None:
        deck_name = settings.anki_deck_name
    if anki_collection_path is None:
        anki_collection_path = settings.anki_collection_path
    if anki_media_path is None:
        anki_media_path = settings.anki_media_path
    if anki_backup_dir is None:
        anki_backup_dir = settings.anki_backup_dir
    if tunatale_db_path is None:
        tunatale_db_path = settings.database_url.replace("sqlite:///", "")
    if media_dir is None:
        media_dir = settings.media_dir
    if fallback_log_path is None:
        fallback_log_path = settings.anki_fallback_log

    results: dict[str, Any] = {
        "new_parents": 0,
        "new_directions": 0,
        "new_media": 0,
        "updated_media": 0,
        "unchanged_media": 0,
        "skipped_guid_collisions": 0,
        "skipped_non_vocab": 0,
    }

    # Safety envelope: backup + read-only open (before TunaTale transaction)
    with safe_open(anki_collection_path, backup_dir=anki_backup_dir) as ctx:
        deck_id = find_deck_id(ctx.conn, deck_name)
        if deck_id is None:
            raise RuntimeError(f"Deck '{deck_name}' not found in {anki_collection_path}")

        notes = fetch_notes_for_deck(ctx.conn, deck_id)
        note_ids = [n.id for n in notes]
        cards = fetch_cards_for_notes(ctx.conn, note_ids, fallback_log_path=fallback_log_path)

    # Build lookup: note_id -> cards list
    card_map: dict[int, list[AnkiCard]] = {}
    for card in cards:
        card_map.setdefault(card.note_id, []).append(card)

    # All TunaTale writes in one transaction
    db = SRSDatabase(tunatale_db_path)
    with db.begin_transaction(dry_run=dry_run):
        for note in notes:
            l2_text = extract_l2_from_fields(note.fields)
            word_count = len(l2_text.split())
            if word_count < 1:
                # Extractor returned empty/whitespace — nothing to import.
                # Reference/Q&A notes with long English questions are now
                # imported as-is (no upper bound); only genuinely empty L2
                # text falls into the skipped-non-vocab bucket.
                print(
                    f"SKIP non-vocab: nid={note.id} words={word_count} text={l2_text[:60]!r}",
                    flush=True,
                )
                results["skipped_non_vocab"] += 1
                continue
            disambig = extract_disambig_from_fields(note.fields)
            # Layer 31: if a field uses the `<b>L2</b><br><i>EN</i>` pattern
            # (Pronunciation/Basic notetype), the English gloss lives inside
            # the same field as the L2 word — recover it directly. Otherwise
            # translation lives in the field that isn't the L2 field. For
            # inverse-layout notes the L2 text is in fields[1], so read
            # translation from fields[0].
            gloss = extract_gloss_from_fields(note.fields)
            if gloss is not None:
                translation = gloss
            else:
                l2_idx = next(
                    (i for i, f in enumerate(note.fields) if extract_l2(f) == l2_text and l2_text),
                    0,
                )
                other_idx = 1 - l2_idx if len(note.fields) > 1 else 0
                translation = extract_translation(note.fields[other_idx]) if len(note.fields) > 1 else ""
            guid = compute_guid(l2_text, "sl", disambig)

            # GUID collision check: if existing row has different (text, disambig_key), skip
            existing = db.get_collocation_by_guid(guid)
            if existing is not None and (
                existing.syntactic_unit.text != l2_text or existing.syntactic_unit.disambig_key != disambig
            ):
                print(
                    f"SKIP guid collision: guid={guid} existing={existing.syntactic_unit.text!r} incoming={l2_text!r}",
                    flush=True,
                )
                results["skipped_guid_collisions"] += 1
                continue

            # Fallback: if guid lookup misses, check by anki_note_id to prevent creating a
            # duplicate when DisambigKey was cleared or the Slovene field was edited after import.
            if existing is None:
                existing = db.get_collocation_by_anki_note_id(note.id)
                if existing is not None:
                    print(
                        f"SKIP anki_note_id match: nid={note.id} existing guid={existing.guid!r} incoming guid={guid!r}",
                        flush=True,
                    )
                    results["skipped_guid_collisions"] += 1
                    continue

            unit = SyntacticUnit(
                text=l2_text,
                translation=translation,
                word_count=word_count,
                difficulty=1,
                source="anki",
                frequency=0,
                disambig_key=disambig,
                lemma=l2_text.lower() if word_count == 1 else None,
            )
            note_cards = card_map.get(note.id, [])
            directions = _build_directions(note_cards, note.id)

            is_new = existing is None
            coll_id = db.upsert_by_guid(unit, "sl", directions, anki_note_id=note.id)

            if is_new:
                results["new_parents"] += 1
                results["new_directions"] += len(directions)

            # Media: copy referenced files from Anki media dir
            if anki_media_path.exists():
                for filename in list_media_refs(note.fields):
                    src = anki_media_path / filename
                    if not src.exists():
                        continue
                    existing_row = db.find_media_by_anki_filename(filename)
                    if existing_row is not None:
                        # SHA-aware: only skip if content unchanged
                        from app.media.importer import compute_sha256

                        current_sha = compute_sha256(src)
                        if current_sha == existing_row["sha256"]:
                            results["unchanged_media"] = results.get("unchanged_media", 0) + 1
                            continue  # no-op: same content
                        # Content changed: overwrite file and update DB
                        dest_dir = Path(media_dir)
                        dest_path = dest_dir / filename
                        import shutil

                        shutil.copy2(src, dest_path)
                        db.update_media_file(existing_row["id"], sha256=current_sha, size_bytes=src.stat().st_size)
                        results["updated_media"] += 1
                        continue
                    copy_result = copy_media_file(src, media_dir)
                    db.add_media(
                        coll_id,
                        kind=copy_result.kind,
                        filename=copy_result.dest_path.name,
                        path=str(copy_result.dest_path),
                        anki_filename=filename,
                        sha256=copy_result.sha256,
                        size_bytes=copy_result.size_bytes,
                    )
                    results["new_media"] += 1

    return results


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Import Anki deck into TunaTale (Stage 2a)")
    parser.add_argument("--deck", default=None, help="Anki deck name")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse + backup but do not commit to TunaTale. Note: the fallback log is still written.",
    )
    args = parser.parse_args()

    result = import_seed(deck_name=args.deck, dry_run=args.dry_run)

    mode = "DRY RUN" if args.dry_run else "IMPORTED"
    print(
        f"\n[{mode}] {result['new_parents']} parents, {result['new_directions']} directions, "
        f"{result['new_media']} media, {result['skipped_guid_collisions']} guid-skipped, "
        f"{result['skipped_non_vocab']} non-vocab-skipped"
    )


if __name__ == "__main__":  # pragma: no cover
    _cli()
