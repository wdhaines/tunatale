"""How long a clip SPEAKS for: its length with the silences taken out.

One function, for one question an adapter cannot answer from the text it sent:
did the voice say the line once? A provider that repeats a line returns a clip
whose total length says little, because an Enunciated line is mostly pause by
design — the pauses are asked for. What a repeat cannot hide is the time spent
speaking, so that is what is measured.

ffmpeg does the measuring, as it does every other piece of audio arithmetic in
this package: it is already a hard dependency (root AGENTS.md), and
``silencedetect`` is the instrument the threshold in ``gemini_tts`` was
calibrated with. A different detector would need its own calibration.
"""

from __future__ import annotations

import re
import subprocess

# The two knobs the calibration was run with (2026-10-09 and 2026-10-13, the
# cached Cebuano Enunciated clips). They are part of the measurement: changing
# either moves every ratio in ``gemini_tts.REPEAT_SPEECH_FACTOR``'s table. On
# natural-speed clips this pair finds 147 pauses against 144 punctuation breaks,
# so it is not reading stop closures as pauses at any real rate.
_NOISE_FLOOR = "-38dB"
_MIN_SILENCE_SECONDS = 0.12

# Decoded only to be counted. Mono 8 kHz is the smallest stream that still has
# the clip's length in it; the detector runs on the input, before the resample.
_SAMPLE_RATE = 8000
_BYTES_PER_SECOND = _SAMPLE_RATE * 2  # s16le, one channel

_SILENCE_RE = re.compile(r"silence_duration: ([0-9.]+)")


def speech_seconds(audio: bytes) -> float:
    """Seconds of *audio* that are not silence. *audio* is any container ffmpeg reads.

    Total length minus every silence of ``_MIN_SILENCE_SECONDS`` or more under
    ``_NOISE_FLOOR``, the leading and trailing ones included. Raises
    ``RuntimeError`` when ffmpeg cannot decode the bytes: a clip that cannot be
    measured cannot be vouched for, and saying "0.0 seconds" would pass it.
    """
    proc = subprocess.run(  # noqa: S603
        [  # noqa: S607
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            "pipe:0",
            "-af",
            f"silencedetect=noise={_NOISE_FLOOR}:d={_MIN_SILENCE_SECONDS}",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(_SAMPLE_RATE),
            "pipe:1",
        ],
        input=audio,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not measure the clip: {proc.stderr.decode(errors='replace')[-300:]}")
    total = len(proc.stdout) / _BYTES_PER_SECOND
    silent = sum(float(seconds) for seconds in _SILENCE_RE.findall(proc.stderr.decode(errors="replace")))
    return max(0.0, total - silent)
