"""Character-level CTC forced alignment: where each letter of a word is spoken.

Why forced alignment rather than signal analysis
------------------------------------------------
The first attempt derived syllable boundaries from a sonority envelope plus a
character prior. It was rejected by ear: the prior did most of the work and the
signal only nudged it 15–40 ms, which is not enough on the ``-en``/``-et``
endings that motivate the whole exercise. An acoustic model that emits *per
character* removes the guessing — our syllabification is orthographic, so
``ha|gen`` is literally characters ``[0:2]`` and ``[2:5]`` and the boundary is
looked up rather than estimated.

The trade is resolution: a wav2vec2-class model hops 20 ms against the envelope's
5 ms. Coarse and right beats fine and confidently wrong, and the slicer's splice
refinement absorbs the quantisation.

This module is model-agnostic. It owns the Viterbi, the frame→sample mapping and
the audio conditioning; a language plugin owns the model that produces the
emissions (see :class:`CharAligner`).
"""

from __future__ import annotations

import io
import subprocess
from typing import TYPE_CHECKING, Protocol

import numpy as np
import soundfile as sf

if TYPE_CHECKING:
    from collections.abc import Iterable

# Sample rate every wav2vec2-class CTC model expects.
MODEL_SAMPLE_RATE = 16_000
# A frame quieter than this, relative to the loudest frame, counts as silence
# for trimming. EdgeTTS pads every utterance with a little of it.
_TRIM_FLOOR_DB = -45.0
_TRIM_WIN_MS = 10.0


class CharAligner(Protocol):
    """A model that reports which frames each character of a word occupies.

    Implemented behind a language plugin, because the model is a language
    literal. Core only ever sees this shape.
    """

    def supports(self, word: str) -> bool:
        """Whether every character of *word* is in the model's vocabulary."""
        ...

    def char_spans(self, samples: np.ndarray, word: str) -> tuple[list[tuple[int, int]], int]:
        """Per-character ``(start_frame, end_frame)`` plus the total frame count.

        *samples* is mono float32 at :data:`MODEL_SAMPLE_RATE`.
        """
        ...


def ctc_align(log_probs: np.ndarray, tokens: list[int], blank: int) -> list[tuple[int, int]]:
    """Viterbi-align *tokens* to *log_probs* ``[T, V]``; per-token (start, end) frames.

    This is the full **blank-interleaved** CTC alignment, not the compact
    two-transition form from the torchaudio tutorial, and the difference is
    load-bearing here. The compact form lets two identical consecutive
    characters occupy adjacent frames; Bokmål is full of geminates (``hadde``,
    ``mannen``, ``snudde``, ``sett``, ``etterforskningsteam``) whose syllable
    boundary is exactly that doubled letter, so collapsing them destroys the one
    boundary we came for. The extended form requires a blank between equal
    neighbours, giving the two halves genuinely separate frame spans.

    States are ``[blank, t0, blank, t1, …, t_{L-1}, blank]``, length ``2L+1``;
    the odd states are the real tokens.
    """
    n_frames, _ = log_probs.shape
    ext: list[int] = [blank]
    for tok in tokens:
        ext += [tok, blank]
    n_states = len(ext)

    neg_inf = -1e30
    dp = np.full((n_frames, n_states), neg_inf, dtype=np.float64)
    back = np.zeros((n_frames, n_states), dtype=np.int32)

    # Frame 0 may start on the leading blank or on the first real token.
    for s in range(min(2, n_states)):
        dp[0, s] = log_probs[0, ext[s]]

    for t in range(1, n_frames):
        for s in range(n_states):
            best, arg = dp[t - 1, s], s
            if s > 0 and dp[t - 1, s - 1] > best:
                best, arg = dp[t - 1, s - 1], s - 1
            # Skip over a blank only when the two real tokens differ — this is
            # the clause that keeps a geminate's halves apart.
            if s > 1 and ext[s] != blank and ext[s] != ext[s - 2] and dp[t - 1, s - 2] > best:
                best, arg = dp[t - 1, s - 2], s - 2
            dp[t, s] = best + log_probs[t, ext[s]]
            back[t, s] = arg

    s = n_states - 1 if dp[n_frames - 1, n_states - 1] >= dp[n_frames - 1, n_states - 2] else n_states - 2
    path = [0] * n_frames
    for t in range(n_frames - 1, -1, -1):
        path[t] = s
        s = int(back[t, s])

    spans: list[tuple[int, int]] = []
    for i in range(len(tokens)):
        state = 2 * i + 1
        frames = [t for t, st in enumerate(path) if st == state]
        if frames:
            spans.append((frames[0], frames[-1] + 1))
        else:
            # Token squeezed out entirely; pin it to the previous token's end so
            # boundaries stay monotonic instead of raising mid-render.
            prev = spans[-1][1] if spans else 0
            spans.append((prev, prev))
    return spans


def derive_syllable_bounds(
    char_spans: list[tuple[int, int]],
    n_frames: int,
    n_samples: int,
    syllables: list[str],
    vowels: Iterable[str],
) -> tuple[list[int], list[int]] | None:
    """Map per-character frames onto syllable cut points and tail ceilings.

    Returns ``(bounds, onset_ends)`` — ``bounds`` has ``len(syllables) + 1``
    sample offsets, ``onset_ends`` one entry per interior boundary — or ``None``
    when the alignment is too degenerate to honour (fewer distinct cuts than
    boundaries, or not enough frames to align against). ``None`` means "fall back
    to TTS", never "cut anyway".

    ``onset_ends`` is deliberately the *next vowel's start*, not the end of the
    intervening consonant. **CTC alignment is peaky**: a character occupies only
    the two or three frames where the model is confident, so a consonant's token
    span badly underestimates its acoustic extent (measured 30–60 ms for onsets
    that plainly last longer). The next vowel's span start is the first landmark
    that is a real acoustic event rather than an artifact of where the model
    peaked, so it is the only trustworthy ceiling this alignment offers.
    """
    if n_frames < 2:
        return None
    vowel_set = frozenset(vowels)

    # Frames map linearly back onto the original-rate samples; deriving the ratio
    # from the arrays avoids hardcoding any model's stride.
    per_frame = n_samples / n_frames

    cuts: list[int] = []
    onset_ends: list[int] = []
    char_idx = 0
    for k, syl in enumerate(syllables[:-1]):
        char_idx += len(syl)
        cuts.append(int(round(char_spans[char_idx][0] * per_frame)))

        nxt = syllables[k + 1]
        n_onset = 0
        while n_onset < len(nxt) and nxt[n_onset] not in vowel_set:
            n_onset += 1
        if n_onset >= len(nxt):
            # No vowel in the next syllable at all; fall back to its end.
            limit_frame = char_spans[char_idx + len(nxt) - 1][1]
        else:
            limit_frame = char_spans[char_idx + n_onset][0]
        onset_ends.append(int(round(limit_frame * per_frame)))

    cuts = sorted({min(max(c, 1), n_samples - 1) for c in cuts})
    if len(cuts) != len(syllables) - 1:
        return None
    return [0, *cuts, n_samples], onset_ends


def resample_to_model_rate(samples: np.ndarray, rate: int) -> np.ndarray:
    """Resample mono float32 to :data:`MODEL_SAMPLE_RATE` via ffmpeg.

    ffmpeg rather than a hand-rolled polyphase filter: it is already a hard
    dependency (``app.audio.transcode``) and its resampler is not the thing
    under test.
    """
    if rate == MODEL_SAMPLE_RATE:
        return samples
    buf = io.BytesIO()
    sf.write(buf, samples, rate, format="WAV", subtype="PCM_16")
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-ar",
            str(MODEL_SAMPLE_RATE),
            "-f",
            "wav",
            "-",
        ],
        input=buf.getvalue(),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg resample failed: {proc.stderr.decode()[:300]}")
    out, _ = sf.read(io.BytesIO(proc.stdout), dtype="float32", always_2d=True)
    return out.mean(axis=1)


def trim_silence(samples: np.ndarray, rate: int) -> np.ndarray:
    """Drop leading/trailing near-silence, which EdgeTTS pads every utterance with.

    The padding is not merely wasted audio: it shifts every frame→sample mapping,
    so aligning against an untrimmed buffer skews all boundaries toward the end.
    """
    win = max(1, int(_TRIM_WIN_MS / 1000.0 * rate))
    n = len(samples) // win
    if n == 0:
        return samples
    frames = samples[: n * win].reshape(n, win)
    db = 20.0 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-10)
    # The threshold is relative to the loudest frame, so that frame always
    # clears it and ``loud`` is never empty — no "nothing survived" branch is
    # reachable here. Digital silence trims to itself, which is correct.
    loud = np.flatnonzero(db > db.max() + _TRIM_FLOOR_DB)
    return samples[loud[0] * win : min(len(samples), (loud[-1] + 1) * win)]
