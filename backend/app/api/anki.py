"""Anki integration endpoints."""

from __future__ import annotations

import logging
import time
from dataclasses import replace

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from app.api import app_state
from app.api.models import PeerSyncResponse, PresetChangeResponse
from app.cards.media.pipeline import fetch_card_media
from app.cards.media.query_llm import generate_image_query
from app.srs.anki_mirror.preset_watch import PRESET_CHANGE_KEY, PresetChangeAlert

router = APIRouter(prefix="/api/anki", tags=["anki"])

_log = logging.getLogger(__name__)


# ── the unrescheduled-preset-change banner (tunatale-c649) ────────────────────
#
# ``watch_preset`` records a preset change Anki did NOT reschedule; these two
# routes are the only way the app learns about one. They read the same
# ``request.state.srs_db`` the sync wrote — the language DB ``X-TT-Language``
# resolved — because the record is per-language: a Norwegian Optimize must not
# banner a Slovene deck, and dismissing one must not silence the other.


def _undismissed_alert(db) -> PresetChangeAlert | None:
    """The stored alert, or None if there is none or the user dismissed it.

    A dismissed record is deliberately still IN the cache rather than deleted:
    it is the evidence that this change was already seen, and the next
    unrescheduled change overwrites it anyway.
    """
    row = db.get_anki_state_cache(PRESET_CHANGE_KEY)
    if row is None:
        return None
    alert = PresetChangeAlert.from_json(row[0])
    return None if alert.dismissed_at_ms is not None else alert


@router.get("/preset-change", response_model=PresetChangeResponse)
async def get_preset_change(request: Request):
    """The unrescheduled preset change to tell the user about, if any.

    ``{"change": null}`` covers both "no change was ever recorded" and "the one
    that was has been dismissed" — the banner renders nothing for either.
    """
    return {"change": _undismissed_alert(request.state.srs_db)}


@router.post("/preset-change/dismiss", response_model=PresetChangeResponse)
async def dismiss_preset_change(request: Request):
    """Mark the stored alert seen. Idempotent, and a no-op with nothing recorded.

    Always answers ``{"change": null}``: the only success here is "the banner is
    gone", and an error would put a second thing on screen at the moment the user
    asked for the first to leave.
    """
    db = request.state.srs_db
    row = db.get_anki_state_cache(PRESET_CHANGE_KEY)
    if row is not None:
        alert = PresetChangeAlert.from_json(row[0])
        db.set_anki_state_cache(PRESET_CHANGE_KEY, replace(alert, dismissed_at_ms=int(time.time() * 1000)).to_json())
    return {"change": None}


def _build_media_fn(llm, db):
    """Build the create-time media generator (LLM image query → Pixabay/TTS fetch).

    Called by peer-sync so TT-added cards get audio + images.
    """
    from app.config import settings

    async def _media_fn(word, english, *, used_image_urls, source_sentence="", grammar="", skip_image=False):
        # `skip_image`: the caller already has the picture (a drawn number), so
        # spend neither the LLM image query nor a Pixabay search. "" is
        # `fetch_card_media`'s documented skip sentinel.
        image_query = (
            ""
            if skip_image
            else await generate_image_query(
                word,
                english,
                llm=llm,
                db=db,
                source_sentence=source_sentence,
                grammar=grammar,
            )
        )
        return await fetch_card_media(
            word,
            english,
            pixabay_key=settings.pixabay_api_key,
            # settings.target_language is the language being synced (peer_sync's
            # _tt_settings sets it per request) — resolve the TTS voice / Forvo
            # section from it so a Norwegian card isn't voiced in Slovene.
            language_code=settings.target_language,
            used_image_urls=used_image_urls,
            image_query=image_query,
            llm=llm,
        )

    return _media_fn


@router.post("/peer-sync", status_code=200, response_model=PeerSyncResponse)
async def trigger_peer_sync(request: Request, background_tasks: BackgroundTasks, dry_run: bool = False):
    """Sync TT's own collection to AnkiWeb (or a self-host server) as a peer.

    Touches TT's own ``tt_collection`` and works with Anki open. Returns
    409 with a user-facing message if peer-sync isn't configured (e.g. no credential in
    the macOS Keychain) or if the server demands a full sync.

    Generates media (audio/images) for any TT-added cards via the media generator,
    so they reach AnkiWeb with media attached.
    """
    from fastapi.concurrency import run_in_threadpool

    from app.cards.cloze_prestage import prestage_cloze_sentences
    from app.cards.media.prestage import prestage_production_images
    from app.config import settings
    from app.plugins.anki_sync.sync_orchestrator import PeerSyncError, peer_sync

    db = request.state.srs_db
    llm = app_state.llm(request)
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
        # Log it too: the detail reaches only the UI, so two failed Tagalog syncs
        # (2026-09-23) left no trace in any server log.
        _log.exception("PEER_SYNC_FAILED language=%s", language_code)
        raise HTTPException(status_code=500, detail=f"Sync failed: {type(e).__name__}: {e}") from e

    # Pre-stage the NEXT sync's production images off the critical path. Promotion
    # fetches inline for any candidate lacking an image (an LLM call plus a Pixabay
    # round trip, serially, while the user waits) and 1487 Norwegian words await
    # production with none — so this is what makes a sync stop being slow. It writes
    # only TT media, never the Anki collection, which is why it is safe off-sync.
    if not dry_run and settings.prestage_images_limit > 0:
        background_tasks.add_task(
            app_state.background_work(request.app).track("prestage_images", prestage_production_images),
            db,
            media_fn,
            language_code=language_code or settings.target_language,
            limit=settings.prestage_images_limit,
        )

    # Same reason, different tier: a word whose own note carries no clozable
    # example gets NO production card at all today (`unservable`), and it is
    # closed-class so there is no image path either. The sentence is written
    # here rather than at mint time because the mint makes no network call
    # (tunatale-keb0).
    if not dry_run and llm is not None and settings.prestage_cloze_limit > 0:
        background_tasks.add_task(
            app_state.background_work(request.app).track("prestage_cloze", prestage_cloze_sentences),
            db,
            llm,
            language_code=language_code or settings.target_language,
            limit=settings.prestage_cloze_limit,
        )

    return {
        "auth_success": report.auth_success,
        "pull_required": report.pull_required,
        "push_required": report.push_required,
        "tt_push_pull_exit": report.tt_push_pull_exit,
        "dry_run": report.dry_run,
    }
