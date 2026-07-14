"""Generate + store vocab-card media (image + word audio) into TunaTale at
card-creation time.

The card-adding endpoints (``POST /items``, ``/listen``, the base-card and
key-phrase paths) create a collocation and then call ``generate_vocab_media`` so
the card is complete in ``/review`` immediately — image and word audio — without
waiting for a sync. Historically this media was fetched only inside
``sync_create_new``; a freshly-added vocab card therefore rendered blank until
its first sync (the nasvidenje report).

``sync_create_new`` now *reuses* whatever this stores (it attaches the existing
TT media to the Anki note instead of re-fetching), so a card ends up with
exactly one Pixabay image whether it was completed at add time or at sync. The
filename conventions here are kept byte-identical to the sync fetch path so the
two never diverge.

This module writes only to TT's media table + the frontend media dir
(``backend/media``, served at ``/api/srs/media/{filename}``). The Anki note is
populated later by sync from these same bytes.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from app.config import settings

from .pipeline import fetch_card_media
from .query_llm import generate_image_query

logger = logging.getLogger(__name__)

# backend/media — the frontend serves this dir at /api/srs/media/{filename}.
# Mirrors app.anki.sync._MEDIA_DIR (vocab_media.py is one level deeper).
_MEDIA_DIR = Path(__file__).parent.parent.parent.parent / "media"


def safe_stem(word: str, prefix: str) -> str:
    """Sanitize word for use as a media filename stem: keep letters/digits/underscores."""
    sanitized = re.sub(r"[^\w\s]", "", word).replace(" ", "_")
    return f"{prefix}_{sanitized}"


def _unlink_orphaned_images(
    db: Any, coll_id: int, kind: str, media_dir: Path, *, skip_filename: str | None = None
) -> None:
    """Delete media rows for (coll_id, kind) and unlink files no longer referenced.

    Captures filenames *before* deleting the rows, then for each old filename
    that is (a) not *skip_filename* and (b) not referenced by any other media
    row, removes the file from *media_dir*. Errors are logged and swallowed —
    a missing or locked file must not propagate.
    """
    with db._get_conn() as conn:
        rows = conn.execute(
            "SELECT filename FROM media WHERE collocation_id = ? AND kind = ?",
            (coll_id, kind),
        ).fetchall()
    old_filenames = [r["filename"] for r in rows]

    db.delete_all_media_for_kind(coll_id, kind)

    for fname in old_filenames:
        if fname == skip_filename:
            continue
        if db.is_media_filename_referenced(fname):
            continue
        fpath = media_dir / fname
        if fpath.exists():
            try:
                fpath.unlink()
            except OSError:
                logger.warning("Could not unlink orphaned image %s", fpath)


def store_tt_media(db: Any, coll_id: int, kind: str, filename: str, data: bytes) -> None:
    """Write media bytes to TT's canonical media dir and record the media row.

    The single place vocab/word media lands in TunaTale, used by both the
    add-time path here and ``sync_create_new`` (via the ``_store_tt_media``
    alias). Served by the frontend at ``/api/srs/media/{filename}``.
    """
    _MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    (_MEDIA_DIR / filename).write_bytes(data)
    db.add_media(
        coll_id,
        kind,
        filename,
        f"media/{filename}",
        filename,
        hashlib.sha256(data).hexdigest(),
        len(data),
    )


def replace_item_image(db: Any, coll_id: int, english: str, data: bytes, ext: str) -> str:
    """Replace a card's TT image: delete old media row, store new, flag dirty.

    Returns the new filename.
    """
    filename = f"{safe_stem(english, 'img')}_{hashlib.sha256(data).hexdigest()[:8]}.{ext}"
    _unlink_orphaned_images(db, coll_id, "image", _MEDIA_DIR, skip_filename=filename)
    store_tt_media(db, coll_id, "image", filename, data)
    db.add_dirty_field_by_id(coll_id, "image")
    return filename


async def generate_vocab_media(
    db: Any,
    coll_id: int,
    word: str,
    english: str,
    *,
    llm: Any,
    pixabay_key: str,
    language_code: str = "sl",
    source_sentence: str = "",
    grammar: str = "",
    used_image_urls: set[str] | None = None,
    _query_fn: Any = None,
    _fetch_fn: Any = None,
) -> dict[str, str]:
    """Fetch and store image + word audio for a freshly-created vocab card.

    Returns ``{"image": filename, "audio": filename}`` for whatever was stored
    (either key may be absent). Best-effort: never raises and never blocks card
    creation on an LLM/Pixabay/network hiccup — a missing image is recoverable,
    a failed POST is not.

    No-ops when ``pixabay_key`` is unset (media isn't configured), which also
    keeps the test suite free of outbound HTTP.
    """
    if not pixabay_key:
        return {}

    query_fn = _query_fn or generate_image_query
    fetch_fn = _fetch_fn or fetch_card_media
    stored: dict[str, str] = {}
    try:
        image_query = await query_fn(
            word,
            english,
            llm=llm,
            db=db,
            source_sentence=source_sentence,
            grammar=grammar,
        )
        media = await fetch_fn(
            word,
            english,
            pixabay_key=pixabay_key,
            language_code=language_code,
            used_image_urls=used_image_urls,
            image_query=image_query,
            llm=llm,
        )
    except Exception as exc:  # noqa: BLE001 — media is best-effort; never block card creation
        logger.warning("vocab media generation failed for %r: %s", word, exc)
        return stored

    if media is None:
        return stored

    if media.audio_bytes is not None:
        # Forvo audio gets the active language's code as filename prefix (matches
        # the sync fetch path in sync_engine); TTS audio uses "tts".
        prefix = settings.target_language if media.audio_source == "forvo" else "tts"
        audio_filename = f"{safe_stem(word, prefix)}.mp3"
        store_tt_media(db, coll_id, f"audio_{media.audio_source or 'tts'}", audio_filename, media.audio_bytes)
        stored["audio"] = audio_filename

    if media.image_bytes is not None:
        ext = media.image_ext or "jpg"
        img_filename = f"{safe_stem(english, 'img')}.{ext}"
        store_tt_media(db, coll_id, "image", img_filename, media.image_bytes)
        stored["image"] = img_filename

    img_status = getattr(media, "image_status", None)
    if img_status is not None:
        stored["image_status"] = img_status
        if media.image_bytes is None and img_status not in (None, "skipped"):
            logger.warning(
                "image fetch failed for %r: status=%s query=%s",
                word,
                img_status,
                getattr(media, "image_query_used", None),
            )

    return stored
