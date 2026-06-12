"""WAV → compressed delivery encoding via ffmpeg.

The lesson renderer assembles a float32 buffer and historically wrote it as
uncompressed WAV. WAV is ~10-20× larger than speech-tuned Opus, which matters
when a phone streams lessons over mobile data. This module turns an assembled
buffer into compressed bytes for delivery.

ffmpeg is already a system dependency (root CLAUDE.md: "CI requires ffmpeg as
system dependency"); we shell out to it rather than rely on libsndfile's codec
support, which varies by build and can't set a speech bitrate.
"""

from __future__ import annotations

import subprocess
from io import BytesIO

import numpy as np
import soundfile as sf

# Delivery codec → file extension (no leading dot).
CODEC_EXT: dict[str, str] = {
    "opus": "opus",
    "aac": "m4a",
    "mp3": "mp3",
    "wav": "wav",
}

# File extension (with dot) → HTTP media type, for serving stored files. Keyed by
# the actual on-disk suffix so old WAV files and new Opus files both serve right.
EXT_MEDIA_TYPE: dict[str, str] = {
    ".opus": "audio/ogg",
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}

# Per-codec ffmpeg output args (everything except -b:a, appended generically).
# Each pins the output container format explicitly because the pipe target has
# no extension for ffmpeg to infer from. AAC uses fragmented MP4 so the moov
# atom can be written to a non-seekable pipe.
_FFMPEG_ARGS: dict[str, list[str]] = {
    "opus": ["-c:a", "libopus", "-f", "ogg"],
    "mp3": ["-c:a", "libmp3lame", "-f", "mp3"],
    "aac": ["-c:a", "aac", "-movflags", "frag_keyframe+empty_moov", "-f", "mp4"],
}


def encode_audio(samples: np.ndarray, rate: int, codec: str, bitrate: str) -> bytes:
    """Encode a float32 ``(frames, channels)`` buffer to *codec* at *bitrate*.

    Pipes a WAV rendering of the buffer through ffmpeg (stdin → stdout) and
    returns the compressed bytes. Raises ``RuntimeError`` if ffmpeg exits
    non-zero (e.g. an invalid bitrate) so a bad config fails loudly instead of
    writing a corrupt/empty file.
    """
    wav_buf = BytesIO()
    sf.write(wav_buf, samples, rate, format="WAV", subtype="PCM_16")

    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            *_FFMPEG_ARGS[codec],
            "-b:a",
            bitrate,
            "pipe:1",
        ],
        input=wav_buf.getvalue(),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {proc.stderr.decode(errors='replace')}")
    return proc.stdout
