"""TTS audio synthesis for cloze cards — sentence + word audio."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from app.cards.media.tts import generate_tts_audio
from app.cards.media.vocab_media import safe_stem
from app.config import settings

logger = logging.getLogger(__name__)

_SENTENCE_PREFIX = "tts_sentence_"

# The fourth copy of this constant. Now the same setting the other three read —
# see Settings.media_dir. Note conftest.py pins the other three by name and never
# pinned this one, so before Deploy P0.1 this module could write to the real
# backend/media during a test run.
_MEDIA_DIR = settings.media_dir


def sentence_filename(sentence: str, voice: str) -> str:
    """A cloze's sentence clip: one per voice and text, shared across cards."""
    return f"{_SENTENCE_PREFIX}{hashlib.sha256(f'{voice}\n{sentence}'.encode()).hexdigest()[:16]}.mp3"


def word_filename(word: str, voice: str) -> str:
    """A cloze's word clip: one per voice and word, shared across cards."""
    return f"{safe_stem(word, 'tts')}_v{hashlib.sha256(voice.encode()).hexdigest()[:6]}.mp3"


async def synthesize_cloze_audios(
    db,
    collocation_id: int,
    sentence: str,
    word: str,
    *,
    voice: str | None = None,
    media_dir: Path | None = None,
) -> None:
    """Idempotently synthesize sentence + word audio for a cloze collocation.

    - Sentence file: tts_sentence_{sha256(voice + sentence)[:16]}.mp3 (shared across cards)
    - Word file:     tts_{safe_stem(word)}_v{sha256(voice)[:6]}.mp3
    Both are named by the voice as well as the text: one media dir serves every
    language and learner, and named by the text alone a Cebuano cloze for 'gusto'
    took Tagalog's tts_gusto.mp3 off the disk (tunatale-xj83). Within a voice a
    clip is still shared, so a function word blanked on many cards renders once.
    - Writes both files to media_dir, then inserts media rows via db.add_media().
    - Skips synthesis if the file already exists on disk.
    - Skips media row insert if a row with (collocation_id, kind) already exists.
    """
    media_root = media_dir or _MEDIA_DIR
    media_root.mkdir(parents=True, exist_ok=True)
    if voice is None:
        from app.config import settings
        from app.languages import get_tts_voice

        voice = get_tts_voice(settings.target_language)

    # ── Sentence audio ──────────────────────────────────────────────────
    sentence_name = sentence_filename(sentence, voice)
    sentence_path = media_root / sentence_name

    if not sentence_path.exists():
        audio = await generate_tts_audio(sentence, voice=voice)
        if audio is not None:
            sentence_path.write_bytes(audio)
            logger.info("Wrote sentence audio: %s", sentence_name)
        else:
            logger.warning("Failed to synthesize sentence audio for %r", sentence[:60])

    wrote_sentence = False
    if not db.has_media_row(collocation_id, "audio_tts_sentence") and sentence_path.exists():
        size_bytes = sentence_path.stat().st_size
        sha = hashlib.sha256(sentence_path.read_bytes()).hexdigest()
        db.add_media(
            collocation_id=collocation_id,
            kind="audio_tts_sentence",
            filename=sentence_name,
            # Relative media/<filename>, like every other writer (vocab_media
            # builds f"media/{filename}") — an absolute path never matches Anki
            # media names (tunatale-zyw9).
            path=f"media/{sentence_name}",
            # anki_filename must equal the filename Anki references in the cloze
            # Back Extra ([sound:tts_sentence_…]). The sync's refresh_media
            # reconciliation matches rows by anki_filename; an empty value never
            # matches, so the row is collapsed on every sync (silent TTS loss).
            anki_filename=sentence_name,
            sha256=sha,
            size_bytes=size_bytes,
        )
        wrote_sentence = True

    # ── Word audio ──────────────────────────────────────────────────────
    word_name = word_filename(word, voice)
    word_path = media_root / word_name

    if not word_path.exists():
        audio = await generate_tts_audio(word, voice=voice)
        if audio is not None:
            word_path.write_bytes(audio)
            logger.info("Wrote word audio: %s", word_name)
        else:
            logger.warning("Failed to synthesize word audio for %r", word)

    if not db.has_media_row(collocation_id, "audio_tts") and word_path.exists():
        size_bytes = word_path.stat().st_size
        sha = hashlib.sha256(word_path.read_bytes()).hexdigest()
        db.add_media(
            collocation_id=collocation_id,
            kind="audio_tts",
            filename=word_name,
            path=f"media/{word_name}",
            anki_filename=word_name,
            sha256=sha,
            size_bytes=size_bytes,
        )

    if wrote_sentence:
        guid = db.get_guid_by_collocation_id(collocation_id)
        if guid:
            db.add_dirty_field(guid, "audio")
