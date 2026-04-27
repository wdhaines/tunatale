"""Anki integration endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.anki.anki_connect import AnkiConnectClient, AnkiConnectUnavailable
from app.anki.media.pipeline import fetch_card_media
from app.anki.sync import AnkiSync, OnlineWriter

router = APIRouter(prefix="/api/anki", tags=["anki"])


def _derive_media_dir(collection_path) -> Path:
    return Path(collection_path).parent / "collection.media"


@router.post("/sync-create-new", status_code=200)
async def trigger_sync_create_new(request: Request, dry_run: bool = False):
    """Push new SRS items (no anki_note_id) to Anki as new notes."""
    from app.anki import model_discovery
    from app.config import settings

    db = request.app.state.srs_db
    client = AnkiConnectClient(url=settings.anki_connect_url)

    try:
        client.ping()
    except AnkiConnectUnavailable as exc:
        raise HTTPException(status_code=503, detail="AnkiConnect is not available") from exc

    model_name = settings.anki_model_name
    if not model_name:
        model_name = model_discovery.get_or_discover_model_name(client)
    if not model_name:
        raise HTTPException(
            status_code=409,
            detail=(
                "Anki model name is not configured and could not be discovered. "
                "Set anki_model_name in settings, or ensure the target deck has at "
                "least one existing note to discover from."
            ),
        )

    writer = OnlineWriter(client, db)
    sync = AnkiSync(db=db, _reader=object(), _writer=writer)

    async def _media_fn(word, english, *, used_image_urls):
        return await fetch_card_media(
            word,
            english,
            pixabay_key=settings.pixabay_api_key,
            used_image_urls=used_image_urls,
        )

    report = await sync.sync_create_new(
        deck_name=settings.anki_deck_name,
        model_name=model_name,
        dry_run=dry_run,
        _media_fn=_media_fn,
    )
    return {
        "count": report.count,
        "created": report.created,
        "linked": report.linked,
        "skipped": report.skipped,
        "dry_run": dry_run,
    }


@router.post("/sync", status_code=200)
async def trigger_sync(request: Request, dry_run: bool = False):
    """Unified create-new + push + drain + pull sync using direct sqlite access.

    Requires Anki to be closed (safe_open acquires an exclusive lock).
    Returns 409 with a user-facing message if Anki is running.
    """
    from app.anki import model_discovery
    from app.anki.safety import AnkiRunningError, safe_open
    from app.anki.sync import AnkiSync, OfflineReader, OfflineWriter, drain_pending_revlog_to_writer
    from app.config import settings

    db = request.app.state.srs_db

    try:
        with safe_open(settings.anki_collection_path, mode="rw") as ctx:
            col_ver = ctx.conn.execute("SELECT ver FROM col").fetchone()[0]
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

            sync = AnkiSync(db=db, _reader=reader, _writer=writer, _anki_col_ver=col_ver)

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
            drained = 0 if dry_run else drain_pending_revlog_to_writer(db, writer)
            pull_report = sync.sync_pull(dry_run=dry_run)

    except AnkiRunningError as exc:
        raise HTTPException(
            status_code=409,
            detail="Close Anki to sync — TunaTale needs exclusive access to collection.anki2.",
        ) from exc

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
        "revlog_drained": drained,
        "dry_run": dry_run,
    }


@router.get("/status", status_code=200)
def get_anki_status(request: Request):
    """Return whether Anki is currently running (i.e. collection.anki2 is locked)."""
    from app.anki.safety import probe_lock
    from app.config import settings

    locked = probe_lock(settings.anki_collection_path)
    return {"anki_running": locked, "lock_acquirable": not locked}
