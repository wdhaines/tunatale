"""One-off: backfill media into already-linked TT cards that synced before the
peer-sync media wiring existed (oprostiti / trg / glaven — collocations 882/883/884,
created with anki_note_id set but 0 media rows).

Operates on tt_collection (Anki may stay open). Generates media via the same
generator the /peer-sync endpoint uses, stores the files into the resolved media
dir (the real collection.media, symlinked), updates the existing notes' Audio/Image
fields (usn=-1, mod, col bump via OfflineWriter), and records TT media rows.

Does NOT sync — run a peer-sync afterward to push the field updates + media to
AnkiWeb. Idempotent-ish: re-running regenerates + overwrites the same fields.

    cd backend && uv run python -m app.anki.archive.backfill_media_for_cards
"""

from __future__ import annotations

import asyncio
import hashlib

from app.anki.safety import safe_open
from app.anki.sync import OfflineWriter, _safe_stem
from app.anki.sync_orchestrator import _ensure_tt_media_linked, _resolve_media_dir
from app.api.anki import _build_media_fn
from app.config import settings
from app.llm.client import LLMClient
from app.srs.database import SRSDatabase

CIDS = [882, 883, 884]


async def main() -> None:
    _ensure_tt_media_linked()
    media_dir = _resolve_media_dir()
    print(f"media_dir = {media_dir}  (exists={media_dir.exists()})")

    db = SRSDatabase(settings.database_url.removeprefix("sqlite:///"))
    llm = LLMClient(groq_api_key=settings.groq_api_key, groq_model=settings.llm_model)
    media_fn = _build_media_fn(llm, db)
    used_image_urls: set[str] = set()

    with safe_open(settings.tt_collection_path, mode="rw") as ctx:
        writer = OfflineWriter(ctx.conn, media_dir=media_dir)
        for cid in CIDS:
            rec = db.get_collocation_by_id(cid)
            if rec is None:
                print(f"cid={cid}: NOT FOUND, skipping")
                continue
            _row_id, item, _guid = rec
            su = item.syntactic_unit
            note_id = item.anki_note_id
            if note_id is None:
                print(f"cid={cid} ({su.text}): no anki_note_id (not linked), skipping")
                continue

            media = await media_fn(
                su.text,
                su.translation,
                used_image_urls=used_image_urls,
                source_sentence=su.source_sentence,
                grammar=su.grammar,
            )
            audio_tag = ""
            image_tag = ""
            if media is not None and media.audio_bytes is not None:
                prefix = "sl" if media.audio_source == "forvo" else "tts"
                audio_filename = f"{_safe_stem(su.text, prefix)}.mp3"
                writer.store_media_file(audio_filename, media.audio_bytes)
                audio_tag = f"[sound:{audio_filename}]"
                db.add_media(
                    cid,
                    f"audio_{media.audio_source or 'tts'}",
                    audio_filename,
                    str(media_dir / audio_filename),
                    audio_filename,
                    hashlib.sha256(media.audio_bytes).hexdigest(),
                    len(media.audio_bytes),
                )
            if media is not None and media.image_bytes is not None:
                ext = media.image_ext or "jpg"
                img_filename = f"{_safe_stem(su.translation, 'img')}.{ext}"
                writer.store_media_file(img_filename, media.image_bytes)
                image_tag = f'<img src="{img_filename}">'
                db.add_media(
                    cid,
                    "image",
                    img_filename,
                    str(media_dir / img_filename),
                    img_filename,
                    hashlib.sha256(media.image_bytes).hexdigest(),
                    len(media.image_bytes),
                )

            writer.update_note_fields(note_id, {"Audio": audio_tag, "Image": image_tag})
            print(f"cid={cid} ({su.text}): audio={bool(audio_tag)} image={bool(image_tag)} note_id={note_id}")

    print("Local backfill complete. Run a peer-sync to push to AnkiWeb.")


if __name__ == "__main__":
    asyncio.run(main())
