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


def infer_kind(filename: str) -> str:
    """Infer media kind from filename prefix. sl_ → audio_forvo, tts_ → audio_tts, else → image."""
    name = Path(filename).name
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
