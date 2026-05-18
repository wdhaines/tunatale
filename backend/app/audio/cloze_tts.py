"""TTS audio synthesis for cloze cards — sentence + word audio."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.anki.media.tts import generate_tts_audio
from app.anki.sync import _safe_stem

logger = logging.getLogger(__name__)

_SENTENCE_PREFIX = "tts_sentence_"

_MEDIA_DIR = Path(__file__).parent.parent.parent / "media"


async def synthesize_cloze_audios(
    db,
    collocation_id: int,
    sentence: str,
    word: str,
    *,
    voice: str = "sl-SI-PetraNeural",
    media_dir: Path | None = None,
) -> None:
    """Idempotently synthesize sentence + word audio for a cloze collocation.

    - Sentence file: tts_sentence_{sha256(sentence)[:16]}.mp3 (shared across cards)
    - Word file:     tts_{safe_stem(word)}.mp3                 (matches vocab convention)
    - Writes both files to media_dir, then inserts media rows via db.add_media().
    - Skips synthesis if the file already exists on disk.
    - Skips media row insert if a row with (collocation_id, kind) already exists.
    """
    media_root = media_dir or _MEDIA_DIR
    media_root.mkdir(parents=True, exist_ok=True)

    # ── Sentence audio ──────────────────────────────────────────────────
    sentence_hash = hashlib.sha256(sentence.encode("utf-8")).hexdigest()[:16]
    sentence_filename = f"{_SENTENCE_PREFIX}{sentence_hash}.mp3"
    sentence_path = media_root / sentence_filename

    if not sentence_path.exists():
        audio = await generate_tts_audio(sentence, voice=voice)
        if audio is not None:
            sentence_path.write_bytes(audio)
            logger.info("Wrote sentence audio: %s", sentence_filename)
        else:
            logger.warning("Failed to synthesize sentence audio for %r", sentence[:60])

    if _missing_media_row(db, collocation_id, "audio_tts_sentence") and sentence_path.exists():
        size_bytes = sentence_path.stat().st_size
        sha = hashlib.sha256(sentence_path.read_bytes()).hexdigest()
        db.add_media(
            collocation_id=collocation_id,
            kind="audio_tts_sentence",
            filename=sentence_filename,
            path=str(sentence_path),
            anki_filename="",
            sha256=sha,
            size_bytes=size_bytes,
        )

    # ── Word audio ──────────────────────────────────────────────────────
    stem = _safe_stem(word, "tts")
    word_filename = f"{stem}.mp3"
    word_path = media_root / word_filename

    if not word_path.exists():
        audio = await generate_tts_audio(word, voice=voice)
        if audio is not None:
            word_path.write_bytes(audio)
            logger.info("Wrote word audio: %s", word_filename)
        else:
            logger.warning("Failed to synthesize word audio for %r", word)

    if _missing_media_row(db, collocation_id, "audio_tts") and word_path.exists():
        size_bytes = word_path.stat().st_size
        sha = hashlib.sha256(word_path.read_bytes()).hexdigest()
        db.add_media(
            collocation_id=collocation_id,
            kind="audio_tts",
            filename=word_filename,
            path=str(word_path),
            anki_filename="",
            sha256=sha,
            size_bytes=size_bytes,
        )


def _missing_media_row(db, collocation_id: int, kind: str) -> bool:
    """Return True if no media row exists for (collocation_id, kind)."""
    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM media WHERE collocation_id = ? AND kind = ? LIMIT 1",
            (collocation_id, kind),
        ).fetchone()
    return row is None
