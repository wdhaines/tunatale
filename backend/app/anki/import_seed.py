"""Stage 2a orchestrator: read-only Anki → TunaTale import.

Usage:
    uv run python -m app.anki.import_seed --deck "0. Slovene" [--dry-run]

All TunaTale writes are wrapped in a single transaction: --dry-run issues
ROLLBACK, real mode issues COMMIT, any exception triggers ROLLBACK.
The Anki safety backup runs before the TunaTale transaction opens.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from app.anki.safety import safe_open
from app.anki.sqlite_reader import (
    AnkiCard,
    extract_disambig_from_fields,
    extract_gloss_from_fields,
    extract_inline_images,
    extract_l2,
    extract_l2_from_fields,
    extract_translation,
    extract_via_profile,
    fetch_cards_for_notes,
    fetch_notes_for_deck,
    find_deck_id,
    list_media_refs,
)
from app.common.guid import compute_guid
from app.config import settings
from app.languages import get_deck_name
from app.media.importer import compute_sha256, copy_media_file
from app.models.srs_item import Direction, DirectionState
from app.models.syntactic_unit import BackField, SyntacticUnit
from app.srs.database import SRSDatabase


def _refresh_media_for_collocation(
    anki_media_path: Path,
    note_fields: list[str],
    coll_id: int,
    media_dir: Path,
    db: SRSDatabase,
    results: dict[str, Any],
) -> None:
    """Copy media files for one collocation from Anki media dir to TT media dir.

    Handles SHA256 comparison for unchanged detection, copy for updates, and
    stale-file cleanup (collapse) per kind. Accumulates counters into `results`.

    Inline ``data:`` URI images are materialized into ``media_dir`` under a
    content-addressed ``inline_<sha[:16]>.<ext>`` filename — Anki stores the
    bytes inside the note itself, so there is no source file in
    ``collection.media/`` to copy. Identity for dedupe is the synthetic
    filename, which doubles as ``anki_filename`` for the cleanup pass.

    Cleanup iterates the union of kinds touched in this pass AND kinds
    already recorded on the collocation, so a kind that disappears from the
    note (e.g. its image field is removed entirely) collapses its stale rows
    instead of persisting forever — see the kratek incident (2026-05-21).
    """
    if not anki_media_path.exists():
        return
    import shutil

    current_by_kind: dict[str, set[str]] = {}
    for filename in list_media_refs(note_fields):
        src = anki_media_path / filename
        if not src.exists():
            continue
        existing_row = db.find_media_by_anki_filename(filename, collocation_id=coll_id)
        if existing_row is not None:
            current_sha = compute_sha256(src)
            if current_sha == existing_row["sha256"]:
                results["unchanged_media"] = results.get("unchanged_media", 0) + 1
                current_by_kind.setdefault(existing_row["kind"], set()).add(filename)
                continue
            dest_path = media_dir / filename
            shutil.copy2(src, dest_path)
            db.update_media_file(existing_row["id"], sha256=current_sha, size_bytes=src.stat().st_size)
            results["updated_media"] += 1
            current_by_kind.setdefault(existing_row["kind"], set()).add(filename)
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
        current_by_kind.setdefault(copy_result.kind, set()).add(filename)

    inline_images = extract_inline_images(note_fields)
    if inline_images:
        media_dir.mkdir(parents=True, exist_ok=True)
    for inline in inline_images:
        sha = hashlib.sha256(inline.data).hexdigest()
        fname = f"inline_{sha[:16]}.{inline.ext}"
        existing_row = db.find_media_by_sha256(coll_id, "image", sha)
        if existing_row is not None:
            results["unchanged_media"] = results.get("unchanged_media", 0) + 1
            current_by_kind.setdefault("image", set()).add(existing_row["anki_filename"])
            continue
        dest_path = media_dir / fname
        dest_path.write_bytes(inline.data)
        db.add_media(
            coll_id,
            kind="image",
            filename=fname,
            path=str(dest_path),
            anki_filename=fname,
            sha256=sha,
            size_bytes=len(inline.data),
        )
        results["new_media"] += 1
        current_by_kind.setdefault("image", set()).add(fname)

    existing_kinds = db.list_media_kinds_for_collocation(coll_id)
    for kind in set(current_by_kind) | existing_kinds:
        keep = current_by_kind.get(kind, set())
        if keep:
            removed = db.delete_stale_media_for_kind(coll_id, kind, keep)
        else:
            removed = db.delete_all_media_for_kind(coll_id, kind)
        if removed:
            results["collapsed_media"] = results.get("collapsed_media", 0) + removed


def refresh_media_from_conn(
    conn,
    *,
    deck_name: str,
    anki_media_path: Path,
    media_dir: Path,
    db: SRSDatabase,
) -> dict[str, Any]:
    """Refresh TT media for every linked note in `deck_name`, reading note fields
    from an already-open collection `conn`.

    The Anki→TT media-propagation engine, used by the peer-sync reconcile
    (``tt_collection``, Anki-open-safe). Copies from ``anki_media_path`` (where the
    collection's media lives) into ``media_dir`` (TT's frontend-served dir) and
    updates the TT ``media`` table — so an image swapped in Anki shows up in TT.
    """
    results: dict[str, Any] = {"new_media": 0, "updated_media": 0, "unchanged_media": 0, "collapsed_media": 0}

    deck_id = find_deck_id(conn, deck_name)
    if deck_id is None:
        return results
    notes = fetch_notes_for_deck(conn, deck_id)

    linked = db.list_linked_anki_note_ids()
    for note in notes:
        coll_id = linked.get(note.id)
        if coll_id is None:
            continue
        _refresh_media_for_collocation(anki_media_path, note.fields, coll_id, media_dir, db, results)

    return results


def _build_directions(
    cards: list[AnkiCard],
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
    language_code: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Import an Anki deck into TunaTale.

    ``language_code`` tags the imported collocations (and is folded into their
    GUIDs); defaults to ``settings.target_language``. One DB per language, so a
    Norwegian import (`language_code="no"`) lands in its own ``tunatale_no.db``.

    Returns a summary dict with counts: new_parents, new_directions,
    new_media, skipped_guid_collisions.
    """
    if deck_name is None:
        deck_name = settings.anki_deck_name
    if language_code is None:
        language_code = settings.target_language
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
            # Notetypes with a field-role profile (e.g. Norwegian's 17-field deck)
            # read L2/translation/disambig by field name; everything else (the
            # Slovene decks) falls back to the positional/HTML heuristics.
            article = ""
            extras: tuple[BackField, ...] = ()
            profile_result = extract_via_profile(note)
            if profile_result is not None:
                l2_text, translation, disambig, article, extras = profile_result
            else:
                l2_text = extract_l2_from_fields(note.fields)
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
            guid = compute_guid(l2_text, language_code, disambig)

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
                article=article,
                extras=extras,
                lemma=l2_text.lower() if word_count == 1 else None,
            )
            note_cards = card_map.get(note.id, [])
            directions = _build_directions(note_cards)

            is_new = existing is None
            coll_id = db.upsert_by_guid(unit, language_code, directions, anki_note_id=note.id)

            if is_new:
                results["new_parents"] += 1
                results["new_directions"] += len(directions)

            _refresh_media_for_collocation(anki_media_path, note.fields, coll_id, media_dir, db, results)

    return results


def _cli() -> None:
    parser = argparse.ArgumentParser(description="Import Anki deck into TunaTale (Stage 2a)")
    parser.add_argument("--deck", default=None, help="Anki deck name (default: the language's registered deck)")
    parser.add_argument("--language", default=None, help="language code (default: target_language setting)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse + backup but do not commit to TunaTale. Note: the fallback log is still written.",
    )
    args = parser.parse_args()

    language_code = args.language or settings.target_language
    deck_name = args.deck or get_deck_name(language_code)
    result = import_seed(deck_name=deck_name, language_code=language_code, dry_run=args.dry_run)

    mode = "DRY RUN" if args.dry_run else "IMPORTED"
    print(
        f"\n[{mode}] {result['new_parents']} parents, {result['new_directions']} directions, "
        f"{result['new_media']} media, {result['skipped_guid_collisions']} guid-skipped, "
        f"{result['skipped_non_vocab']} non-vocab-skipped"
    )


if __name__ == "__main__":  # pragma: no cover
    _cli()
