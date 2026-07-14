"""Image-update endpoints: replace a card's TT-side image via Pixabay pick,
pasted URL, file upload, or remove."""

from __future__ import annotations

import logging
from functools import partial
from typing import Annotated
from urllib.parse import urlparse

import anyio
import httpx
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from app.anki.media import pixabay as _pixabay_mod
from app.anki.media.pixabay import PixabaySearch
from app.anki.media.vocab_media import replace_item_image
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/srs", tags=["srs-images"])

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
_DOWNLOAD_TIMEOUT = 15.0

_IMAGE_MAGIC = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"RIFF": "webp",  # RIFF....WEBP
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}


def _sniff_ext(data: bytes) -> str | None:
    """Return file extension from magic bytes, or None if not a recognised image."""
    for sig, ext in _IMAGE_MAGIC.items():
        if data[: len(sig)] == sig:
            if ext == "webp" and data[8:12] != b"WEBP":
                continue
            return ext
    return None


def _validate_image_bytes(data: bytes) -> str:
    """Validate image bytes via magic-byte sniff, return ext. Raises ValueError."""
    ext = _sniff_ext(data)
    if ext is None:
        raise ValueError("not a recognised image")
    return ext


def _resolve_item(db, item_id: int):
    """Resolve item or raise 404/409."""
    result = db.get_collocation_by_id(item_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Item not found")
    coll_id, item, lang = result
    if item.syntactic_unit.card_type == "cloze":
        raise HTTPException(status_code=409, detail="Cloze cards have no image field")
    return coll_id, item, lang


def _item_response(db, coll_id: int, item) -> dict:
    img = db.get_image_filename(coll_id)
    return {
        "id": coll_id,
        "text": item.syntactic_unit.text,
        "translation": item.syntactic_unit.translation,
        "card_type": item.syntactic_unit.card_type,
        "image_url": f"/api/srs/media/{img}" if img else None,
    }


@router.get("/items/{item_id}/image/candidates")
async def get_image_candidates(
    item_id: int,
    request: Request,
    q: Annotated[str | None, Query()] = None,
):
    db = request.state.srs_db
    coll_id, item, _lang = _resolve_item(db, item_id)

    if not settings.pixabay_api_key:
        raise HTTPException(status_code=409, detail="Pixabay API key not configured")

    query = q
    if query is None:
        cached = db.get_image_query(
            item.syntactic_unit.text,
            item.syntactic_unit.translation,
            "v1",
        )
        query = cached if cached is not None else item.syntactic_unit.translation

    search_result: PixabaySearch = await anyio.to_thread.run_sync(
        partial(_pixabay_mod.search_pixabay, query, api_key=settings.pixabay_api_key),
    )

    candidates = []
    for hit in search_result.hits[:24]:
        candidates.append(
            {
                "preview_url": hit.get("previewURL", ""),
                "webformat_url": hit.get("webformatURL", ""),
                "tags": hit.get("tags", ""),
                "width": hit.get("imageWidth", 0),
                "height": hit.get("imageHeight", 0),
                "likes": hit.get("likes", 0),
            }
        )

    return {
        "query": query,
        "status": search_result.status,
        "candidates": candidates,
    }


class _UrlBody(BaseModel):
    url: str


@router.put("/items/{item_id}/image")
async def put_image_from_url(
    item_id: int,
    body: _UrlBody,
    request: Request,
):
    db = request.state.srs_db
    coll_id, item, _lang = _resolve_item(db, item_id)

    parsed = urlparse(body.url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=422, detail="URL scheme must be http or https")

    # Trust boundary: the URL is user-controlled and fetched server-side.
    # follow_redirects=False blocks redirect pivots, but the initial request
    # can still reach localhost / internal IPs.  Safe today (single-user
    # localhost); revisit if the server is ever exposed.
    async with httpx.AsyncClient(follow_redirects=False, timeout=_DOWNLOAD_TIMEOUT) as client:
        resp = await client.get(body.url)

    if resp.is_redirect or (300 <= resp.status_code < 400):
        raise HTTPException(status_code=422, detail="URL redirected; provide a direct image link")
    if resp.status_code >= 400:
        raise HTTPException(
            status_code=422,
            detail=f"Could not fetch image (HTTP {resp.status_code})",
        )

    data = resp.content

    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=422, detail="Image exceeds 10 MB limit")

    ext = _sniff_ext(data)
    if ext is None:
        raise HTTPException(status_code=422, detail="Content is not an image")

    replace_item_image(db, coll_id, item.syntactic_unit.translation, data, ext)

    return _item_response(db, coll_id, item)


@router.put("/items/{item_id}/image/upload")
async def put_image_upload(
    item_id: int,
    request: Request,
    file: UploadFile,
):
    db = request.state.srs_db
    coll_id, item, _lang = _resolve_item(db, item_id)

    contents = await file.read()
    if len(contents) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=422, detail="Image exceeds 10 MB limit")

    try:
        ext = _validate_image_bytes(contents)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Content is not an image") from exc
    replace_item_image(db, coll_id, item.syntactic_unit.translation, contents, ext)

    return _item_response(db, coll_id, item)


@router.delete("/items/{item_id}/image")
async def delete_image(
    item_id: int,
    request: Request,
):
    db = request.state.srs_db
    coll_id, item, _lang = _resolve_item(db, item_id)

    db.delete_all_media_for_kind(coll_id, "image")
    db.add_dirty_field_by_id(coll_id, "image")

    return _item_response(db, coll_id, item)
