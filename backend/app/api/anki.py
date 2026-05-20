"""Anki integration endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.anki.media.pipeline import fetch_card_media

router = APIRouter(prefix="/api/anki", tags=["anki"])


def _derive_media_dir(collection_path) -> Path:
    return Path(collection_path).parent / "collection.media"


def _refresh_media() -> dict:
    from app.anki.import_seed import refresh_media_for_deck

    return refresh_media_for_deck()


@router.post("/sync", status_code=200)
async def trigger_sync(request: Request, dry_run: bool = False):
    """Unified create-new + push + drain + pull sync using direct sqlite access.

    Requires Anki to be closed (safe_open acquires an exclusive lock).
    Returns 409 with a user-facing message if Anki is running.
    """
    from app.anki import model_discovery
    from app.anki.safety import AnkiRunningError, safe_open
    from app.anki.sync import AnkiSync, OfflineReader, OfflineWriter, OrphanThresholdExceededError
    from app.config import settings

    db = request.app.state.srs_db

    try:
        with safe_open(settings.anki_collection_path, mode="rw") as ctx:
            col_row = ctx.conn.execute("SELECT ver, crt FROM col").fetchone()
            col_ver, col_crt = col_row[0], col_row[1]
            reader = OfflineReader(ctx.conn, settings.anki_deck_name)
            writer = OfflineWriter(ctx.conn, media_dir=_derive_media_dir(settings.anki_collection_path))

            model_name = settings.anki_model_name
            if not model_name:
                model_name = model_discovery.get_or_discover_model_name_offline(ctx.conn, settings.anki_deck_name)
            if not model_name:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Anki model not configured and no notes found to discover from. "
                        "Set anki_model_name in settings."
                    ),
                )

            sync = AnkiSync(
                db=db,
                _reader=reader,
                _writer=writer,
                _anki_col_ver=col_ver,
                _anki_col_crt=col_crt,
            )

            # Self-healing: detect TT rows that point at Anki cards/notes that
            # no longer exist (deleted from Anki, lost to a force-full-download,
            # etc). Reset those pointers so sync_create_new recreates them, and
            # arm `_recovered_directions` so sync_push force_fsrs the rebuild.
            # Must run BEFORE create_new and push for the recovery to land in
            # this same sync invocation.
            sync.detect_and_reset_orphans()

            async def _media_fn(word, english, *, used_image_urls):
                return await fetch_card_media(
                    word,
                    english,
                    pixabay_key=settings.pixabay_api_key,
                    used_image_urls=used_image_urls,
                )

            create_report = await sync.sync_create_new(
                deck_name=settings.anki_deck_name,
                model_name=model_name,
                dry_run=dry_run,
                _media_fn=_media_fn,
            )
            push_report = sync.sync_push(dry_run=dry_run)
            pull_report = sync.sync_pull(dry_run=dry_run)

            if not dry_run:
                from app.srs.queue_stats import (
                    refresh_col_crt,
                    refresh_daily_new_cap,
                    refresh_daily_review_cap,
                    refresh_desired_retention,
                    refresh_fsrs_params,
                    refresh_fsrs_short_term_flag,
                    refresh_learning_steps,
                    refresh_review_settings,
                )

                refresh_col_crt(db, ctx.conn)
                refresh_daily_new_cap(db, ctx.conn, settings.anki_deck_name)
                refresh_daily_review_cap(db, ctx.conn, settings.anki_deck_name)
                refresh_desired_retention(db, ctx.conn, settings.anki_deck_name)
                refresh_fsrs_params(db, ctx.conn, settings.anki_deck_name)
                refresh_fsrs_short_term_flag(db, ctx.conn)
                refresh_review_settings(db, ctx.conn, settings.anki_deck_name)
                refresh_learning_steps(db, ctx.conn, settings.anki_deck_name)

    except AnkiRunningError as exc:
        raise HTTPException(
            status_code=409,
            detail="Close Anki to sync — TunaTale needs exclusive access to collection.anki2.",
        ) from exc
    except OrphanThresholdExceededError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if not dry_run:
        media_result = _refresh_media()
        media_updated = media_result.get("updated_media", 0)
        media_unchanged = media_result.get("unchanged_media", 0)
        media_new = media_result.get("new_media", 0)
    else:
        media_updated = 0
        media_unchanged = 0
        media_new = 0

    return {
        "mode": "offline",
        "created": create_report.created,
        "linked": create_report.linked,
        "skipped": create_report.skipped,
        "notes_pulled": pull_report.notes_updated,
        "directions_pulled": pull_report.directions_updated,
        "conflicts": len(pull_report.conflicts),
        "notes_pushed": push_report.notes_pushed,
        "directions_pushed": push_report.directions_pushed,
        "notes_created_from_anki": create_report.notes_created_from_anki,
        "dry_run": dry_run,
        "media_updated": media_updated,
        "media_unchanged": media_unchanged,
        "media_new": media_new,
    }


@router.get("/status", status_code=200)
def get_anki_status(request: Request):
    """Return whether Anki is currently running (i.e. collection.anki2 is locked)."""
    from app.anki.safety import probe_lock
    from app.config import settings

    locked = probe_lock(settings.anki_collection_path)
    return {"anki_running": locked, "lock_acquirable": not locked}
