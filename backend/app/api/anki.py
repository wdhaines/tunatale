"""Anki integration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.anki.anki_connect import AnkiConnectClient, AnkiConnectUnavailable
from app.anki.sync import AnkiSync, OnlineWriter

router = APIRouter(prefix="/api/anki", tags=["anki"])


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

    count = await sync.sync_create_new(
        deck_name=settings.anki_deck_name,
        model_name=model_name,
        dry_run=dry_run,
    )
    return {"count": count, "dry_run": dry_run}
