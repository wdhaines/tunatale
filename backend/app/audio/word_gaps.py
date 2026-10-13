"""An Enunciated take with its pauses rewritten to lengths of ours.

A voice that takes no pause markup can only be ASKED to pause between words, and
what comes back is uneven: the pauses are there but run long, and some fall
inside a word. This module does not ask again. It takes the audio as it came
and rewrites the silences in it:

- a line of N words has N - 1 word gaps, so the N - 1 LONGEST pauses are taken
  to be those gaps and each is set to one length;
- every other pause is a break inside a word, and is shut;
- the speech between the pauses can be sped, with its pitch kept, so the line's
  whole length is ours to set as well;
- the silence at either end is taken off.

"The longest pauses are the word gaps" is a bet, not a measurement: a pause
inside a word that outlasts a real word gap would be kept and the gap shut.
Heard on 51 Cebuano lines (2026-10-13) it held.

One refusal. A take with FEWER stretches of speech than words ran two words
together: there is no pause where a gap belongs, and cutting one in would split
a word at a guess. That is ``None``, which the caller reads as "take the line
again".

ffmpeg does the work, as in ``speech_length``, whose detector decides what a
pause is here too.
"""

from __future__ import annotations

import subprocess

from app.audio.speech_length import speech_stretches

# Kept on either side of a stretch of speech when it is cut out, so that a
# quiet onset or a trailing consonant under the detector's floor is not lost.
_EDGE_SECONDS = 0.02

# The rewritten clip is an MP3 because the take it replaces is one, and every
# reader of the cache expects that. Mono at the provider's own sample rate.
_OUTPUT = ["-c:a", "libmp3lame", "-b:a", "64k", "-ac", "1", "-ar", "24000", "-f", "mp3", "pipe:1"]


def rewrite_word_gaps(
    audio: bytes, words: int, *, gap_seconds: float, shut_seconds: float, speed: float
) -> bytes | None:
    """*audio* with its word gaps set to *gap_seconds*, or ``None`` if a gap is missing.

    *words* is how many words the line has. *shut_seconds* is what is left of a
    pause that is not a word gap (under the detector's floor, so the mended word
    reads as one stretch). *speed* multiplies the pace of the speech only: the
    gaps come out at the length asked for whatever it is.

    Raises ``RuntimeError`` when ffmpeg cannot read or write the audio.
    """
    stretches = speech_stretches(audio)
    if len(stretches) < words:
        return None
    pauses = [stretches[i + 1][0] - stretches[i][1] for i in range(len(stretches) - 1)]
    longest = sorted(range(len(pauses)), key=lambda i: pauses[i], reverse=True)
    word_gaps = set(longest[: max(0, words - 1)])

    count = len(stretches)
    graph = [f"[0:a]asplit={count}" + "".join(f"[in{i}]" for i in range(count))]
    for i, (start, end) in enumerate(stretches):
        cut = f"atrim=start={max(0.0, start - _EDGE_SECONDS):.3f}:end={end + _EDGE_SECONDS:.3f},asetpts=PTS-STARTPTS"
        tempo = f",atempo={speed}" if speed != 1.0 else ""
        # The silence is added AFTER the speed-up, so it is not sped with the speech.
        after = "" if i == count - 1 else f",apad=pad_dur={gap_seconds if i in word_gaps else shut_seconds}"
        graph.append(f"[in{i}]{cut}{tempo}{after}[out{i}]")
    graph.append("".join(f"[out{i}]" for i in range(count)) + f"concat=n={count}:v=0:a=1[line]")
    proc = subprocess.run(  # noqa: S603
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            "pipe:0",
            "-filter_complex",
            ";".join(graph),
            "-map",
            "[line]",
            *_OUTPUT,
        ],  # noqa: S607
        input=audio,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not rewrite the clip: {proc.stderr.decode(errors='replace')[-300:]}")
    return proc.stdout
