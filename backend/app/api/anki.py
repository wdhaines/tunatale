"""Anki integration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.anki.anki_connect import AnkiConnectClient, AnkiConnectUnavailable
from app.anki.sync import AnkiSync, OnlineWriter

router = APIRouter(prefix="/api/anki", tags=["anki"])


@router.post("/sync-create-new", status_code=200)
async def trigger_sync_create_new(request: Request, dry_run: bool = False):
    """Push new SRS items (no anki_note_id) to Anki as new notes."""
    from app.config import settings

    db = request.app.state.srs_db
    client = AnkiConnectClient(url=settings.anki_connect_url)

    try:
        client.ping()
    except AnkiConnectUnavailable as exc:
        raise HTTPException(status_code=503, detail="AnkiConnect is not available") from exc

    writer = OnlineWriter(client, db)
    sync = AnkiSync(db=db, _reader=object(), _writer=writer)

    count = await sync.sync_create_new(
        deck_name=settings.anki_deck_name,
        model_name=settings.anki_model_name,
        dry_run=dry_run,
    )
    return {"count": count, "dry_run": dry_run}
