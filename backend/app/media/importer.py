"""Copy-from-Anki media importer (Stage 2a: read-only path only).

Copies files from collection.media/ into settings.media_dir, computing
SHA256 and inferring the media kind from the filename prefix.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class MediaCopyResult:
    anki_filename: str
    dest_path: Path
    kind: str
    sha256: str
    size_bytes: int


_AUDIO_EXTS = {".mp3", ".ogg", ".oga", ".opus", ".wav", ".m4a", ".aac", ".flac"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif", ".bmp", ".tif", ".tiff"}


def infer_kind(filename: str) -> str:
    """Infer media kind for a media file.

    Audio vs image is decided by file **extension** — the only reliable signal
    across decks. The audio *sub-kind* (Forvo vs TTS) is refined by a source
    marker in the name: Slovene uses ``sl_*`` (Forvo) / ``tts_*`` (TTS); Norwegian
    uses ``forvo-*`` (Forvo) / ``azure-*`` (Azure TTS). A prefix-only rule (the old
    behaviour) mislabelled every ``forvo-*``/``azure-*`` ``.mp3`` as an image, so
    ``get_image_filename`` returned an audio file and the card rendered a broken
    ``<img>`` on every Norwegian card. Unknown extensions fall back to the legacy
    prefix heuristic (keeps ``some_file.webm`` → image).
    """
    name = Path(filename).name
    ext = Path(name).suffix.lower()
    if ext in _AUDIO_EXTS:
        # Sentence-level TTS (cloze Back audio) is its own kind — don't fold it
        # into plain audio_tts (get_sentence_audio_filename queries it).
        if name.startswith("tts_sentence"):
            return "audio_tts_sentence"
        return "audio_forvo" if name.startswith(("sl_", "forvo")) else "audio_tts"
    if ext in _IMAGE_EXTS:
        return "image"
    if name.startswith("sl_"):
        return "audio_forvo"
    if name.startswith("tts_"):
        return "audio_tts"
    return "image"


def compute_sha256(path: Path) -> str:
    """Compute SHA256 hex digest of a file without copying it."""
    sha256_hash = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()


def copy_media_file(src: Path, dest_dir: Path) -> MediaCopyResult:
    """Copy a media file from Anki's collection.media/ into dest_dir.

    Computes SHA256 of the source, creates dest_dir if needed, and writes
    a byte-identical copy. Returns a MediaCopyResult with all metadata.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / src.name

    sha256_hash = hashlib.sha256()
    size = 0
    with src.open("rb") as f_in, dest_path.open("wb") as f_out:
        for chunk in iter(lambda: f_in.read(65536), b""):
            sha256_hash.update(chunk)
            size += len(chunk)
            f_out.write(chunk)

    return MediaCopyResult(
        anki_filename=src.name,
        dest_path=dest_path,
        kind=infer_kind(src.name),
        sha256=sha256_hash.hexdigest(),
        size_bytes=size,
    )
