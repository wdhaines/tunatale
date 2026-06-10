"""Anki integration endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.anki.media.pipeline import fetch_card_media
from app.anki.media.query_llm import generate_image_query

router = APIRouter(prefix="/api/anki", tags=["anki"])


def _derive_media_dir(collection_path) -> Path:
    return Path(collection_path).parent / "collection.media"


def _refresh_media() -> dict:
    from app.anki.import_seed import refresh_media_for_deck

    return refresh_media_for_deck()


def _build_media_fn(llm, db):
    """Build the create-time media generator (LLM image query → Pixabay/TTS fetch).

    Shared by both sync entry points (legacy /sync and peer-sync) so TT-added
    cards get audio + images regardless of which path mints them.
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


@router.post("/sync", status_code=200)
async def trigger_sync(request: Request, dry_run: bool = False):
    """Unified create-new + push + drain + pull sync using direct sqlite access.

    Requires Anki to be closed (safe_open acquires an exclusive lock).
    Returns 409 with a user-facing message if Anki is running.
    """
    from app.anki import model_discovery
    from app.anki.safety import AnkiRunningError, safe_open
    from app.anki.sync import (
        AnkiSync,
        OfflineReader,
        OfflineWriter,
        OrphanThresholdExceededError,
        run_full_sync,
    )
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

            llm = getattr(request.app.state, "llm", None)

            # The single canonical sync sequence (orphans → create → push → pull
            # → refresh-all → soak), shared with the peer-sync reconcile so the
            # two entry points can never diverge. See run_full_sync.
            create_report, push_report, pull_report = await run_full_sync(
                sync,
                ctx.conn,
                db,
                deck_name=settings.anki_deck_name,
                model_name=model_name,
                sync_log_path=settings.sync_log,
                media_fn=_build_media_fn(llm, db),
                dry_run=dry_run,
            )

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
        "recompute_divergences": len(pull_report.recompute_divergences),
        "notes_pushed": push_report.notes_pushed,
        "directions_pushed": push_report.directions_pushed,
        "notes_created_from_anki": create_report.notes_created_from_anki,
        "dry_run": dry_run,
        "media_updated": media_updated,
        "media_unchanged": media_unchanged,
        "media_new": media_new,
    }


@router.post("/peer-sync", status_code=200)
async def trigger_peer_sync(request: Request, dry_run: bool = False):
    """Sync TT's own collection to AnkiWeb (or a self-host server) as a peer.

    Unlike ``/sync`` (which writes the user's local collection.anki2 and needs Anki
    closed), this touches TT's own ``tt_collection`` and works with Anki open. Returns
    409 with a user-facing message if peer-sync isn't configured (e.g. no credential in
    the macOS Keychain) or if the server demands a full sync.

    Generates media (audio/images) for any TT-added cards via the same media
    generator the legacy /sync uses, so they reach AnkiWeb with media attached.
    """
    from fastapi.concurrency import run_in_threadpool

    from app.anki.sync_orchestrator import PeerSyncError, peer_sync

    db = request.app.state.srs_db
    llm = getattr(request.app.state, "llm", None)
    media_fn = _build_media_fn(llm, db)

    try:
        report = await run_in_threadpool(lambda: peer_sync(dry_run, media_fn=media_fn))
    except PeerSyncError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None

    return {
        "auth_success": report.auth_success,
        "pull_required": report.pull_required,
        "push_required": report.push_required,
        "tt_push_pull_exit": report.tt_push_pull_exit,
        "dry_run": report.dry_run,
    }


@router.get("/status", status_code=200)
def get_anki_status(request: Request):
    """Return whether Anki is currently running (i.e. collection.anki2 is locked)."""
    from app.anki.safety import probe_lock
    from app.config import settings

    locked = probe_lock(settings.anki_collection_path)
    return {"anki_running": locked, "lock_acquirable": not locked}
