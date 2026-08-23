"""Admin endpoints — refresh-media and other operations."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.models import RefreshMediaResponse, TtsCacheStatsResponse
from app.config import settings

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/tts-cache", status_code=200, response_model=TtsCacheStatsResponse)
async def tts_cache_stats() -> dict:
    """Report TTS cache size: ``*.mp3`` count and total bytes.

    Readout only — no eviction lives here (tunatale-rm6v). A missing or
    non-directory cache path is the fresh-install case and reports as absent,
    never an error.
    """
    cache_dir = settings.tts_cache_dir
    if not cache_dir.is_dir():
        return {"present": False, "file_count": 0, "total_bytes": 0}
    clips = list(cache_dir.glob("*.mp3"))
    return {
        "present": True,
        "file_count": len(clips),
        "total_bytes": sum(clip.stat().st_size for clip in clips),
    }


@router.post("/refresh-media", status_code=200, response_model=RefreshMediaResponse)
async def refresh_media() -> dict:
    """Re-import media from Anki, updating changed files (SHA-aware).

    Returns counts: {updated, unchanged, new, errors}.
    """
    try:
        from app.plugins.anki_sync.import_seed import import_seed
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="Anki sync plugin not available") from exc

    try:
        result = import_seed()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "updated": result.get("updated_media", 0),
        "unchanged": result.get("unchanged_media", 0),
        "new": result.get("new_media", 0),
        "errors": result.get("skipped_guid_collisions", 0),
    }
