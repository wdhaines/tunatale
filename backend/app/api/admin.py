"""Admin endpoints — refresh-media and other operations."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/refresh-media", status_code=200)
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
