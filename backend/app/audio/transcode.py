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

import contextlib
import subprocess
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path

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


def encode_audio_stream(
    chunks: Iterable[np.ndarray],
    rate: int,
    codec: str,
    bitrate: str,
    out_path: Path,
) -> None:
    """Encode *chunks* to *out_path* as ONE continuous stream, holding none of it.

    The full-lesson export used to concatenate every piece into a single float32
    buffer (~340 MB for a 58-minute lesson), render it to a WAV ``BytesIO``, and
    copy that again for ffmpeg's stdin. This writes the pieces to ffmpeg as they
    arrive, so peak memory is one piece rather than the lesson (bd
    tunatale-rwkz.2).

    ⚠️ ONE ENCODE, not a join of encoded parts. Concatenating separately-encoded
    Opus segments adds a frame of padding per seam — measured at +20 ms each,
    cumulative — which would drift every cue after the first, and produces
    chained Ogg streams that ``soundfile`` refuses to open at all. Streaming the
    PCM keeps the encoder's timeline identical to the old one-shot encode.

    Raw ``f32le`` on stdin rather than a WAV wrapper: a WAV header must declare
    its length up front, which is precisely what a streaming writer does not
    know. It also drops the old path's intermediate 16-bit quantisation — the
    samples now reach the encoder at the precision they were assembled in.
    """
    channels = None
    proc = subprocess.Popen(  # noqa: S603
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "f32le",
            "-ar",
            str(rate),
            "-ac",
            "1",  # replaced below once the first chunk says how many channels
            "-i",
            "pipe:0",
            *_FFMPEG_ARGS[codec],
            "-b:a",
            bitrate,
            "-y",
            str(out_path),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stderr = b""
    try:
        for chunk in chunks:
            if channels is None:
                channels = chunk.shape[1]
                if channels != 1:
                    # Guarded rather than supported: every renderer path is mono
                    # today, and a silent mis-declaration to ffmpeg would halve
                    # or double the playback rate instead of failing.
                    proc.kill()
                    msg = f"encode_audio_stream is mono-only, got {channels} channels"
                    raise ValueError(msg)
            proc.stdin.write(np.ascontiguousarray(chunk, dtype="float32").tobytes())
    except BrokenPipeError:  # pragma: no cover - ffmpeg died early; its stderr is the real error
        pass
    finally:
        # suppress(), not an `if closed` guard: closing a pipe ffmpeg has
        # already dropped raises again, and the branch for "already closed" is
        # unreachable in any test that does not also kill ffmpeg mid-write.
        with contextlib.suppress(BrokenPipeError):
            proc.stdin.close()
        stderr = proc.stderr.read()
        proc.stderr.close()
        proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}): {stderr.decode(errors='replace')}")


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
