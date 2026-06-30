"""Anki integration endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.anki.media.pipeline import fetch_card_media
from app.anki.media.query_llm import generate_image_query

router = APIRouter(prefix="/api/anki", tags=["anki"])


def _build_media_fn(llm, db):
    """Build the create-time media generator (LLM image query → Pixabay/TTS fetch).

    Called by peer-sync so TT-added cards get audio + images.
    """
    from app.config import settings

    async def _media_fn(word, english, *, used_image_urls, source_sentence="", grammar=""):
        image_query = await generate_image_query(
            word,
            english,
            llm=llm,
            db=db,
            source_sentence=source_sentence,
            grammar=grammar,
        )
        return await fetch_card_media(
            word,
            english,
            pixabay_key=settings.pixabay_api_key,
            used_image_urls=used_image_urls,
            image_query=image_query,
        )

    return _media_fn


@router.post("/peer-sync", status_code=200)
async def trigger_peer_sync(request: Request, dry_run: bool = False):
    """Sync TT's own collection to AnkiWeb (or a self-host server) as a peer.

    Touches TT's own ``tt_collection`` and works with Anki open. Returns
    409 with a user-facing message if peer-sync isn't configured (e.g. no credential in
    the macOS Keychain) or if the server demands a full sync.

    Generates media (audio/images) for any TT-added cards via the media generator,
    so they reach AnkiWeb with media attached.
    """
    from fastapi.concurrency import run_in_threadpool

    from app.anki.sync_orchestrator import PeerSyncError, peer_sync

    db = request.state.srs_db
    llm = getattr(request.app.state, "llm", None)
    media_fn = _build_media_fn(llm, db)
    # Sync the language the UI is on (X-TT-Language, resolved by the middleware),
    # not the .env default — otherwise a Slovene grade pushes the Norwegian deck.
    language_code = getattr(request.state, "language_code", None)

    try:
        report = await run_in_threadpool(lambda: peer_sync(dry_run, media_fn=media_fn, language_code=language_code))
    except PeerSyncError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except Exception as e:
        # Surface the real failure to the UI instead of a bare "Internal Server
        # Error". An unhandled exception here (e.g. a sqlite IntegrityError mid-
        # reconcile) otherwise reaches the user as an opaque 500 with no reason.
        raise HTTPException(status_code=500, detail=f"Sync failed: {type(e).__name__}: {e}") from e

    return {
        "auth_success": report.auth_success,
        "pull_required": report.pull_required,
        "push_required": report.push_required,
        "tt_push_pull_exit": report.tt_push_pull_exit,
        "dry_run": report.dry_run,
    }
