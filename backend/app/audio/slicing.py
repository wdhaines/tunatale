"""Cut syllable-sized chunks out of a whole-word audio render.

Why this exists
---------------
A Pimsleur breakdown chunk used to be its own TTS utterance, which made the voice
run *word-level* grapheme-to-phoneme on a syllable fragment: any fragment that
happens to spell a real word is read as that word. Measured on nb-NO, comparing
the isolated render against the same syllable cut from its parent word:

    gen  (hagen, ingen)   /gən/  ->  /geːn/ ("gene")   spectral centroid -361 Hz
    ret  (sporet)         /rət/  ->  /reːt/                              -335 Hz
    nen  (mannen)         /nən/  ->  /neːn/                              -333 Hz

Those are the ``-en``/``-et`` definite-article endings, i.e. the most common
syllable shape in the language, not a tail case. Respelling the fragment (the old
``de`` -> ``deh`` trick) cannot generalise: there is no spelling of ``gen`` the
voice reads as a schwa. So the word is synthesised ONCE and the chunks are cut
out of it, which also collapses ~5 TTS calls per word to 1.

This module is deliberately language- and model-agnostic: it consumes
already-computed syllable boundaries and does the signal work. Producing those
boundaries (forced alignment) belongs behind a language plugin.

Every constant below was tuned by ear over four rounds of listening feedback, and
the reasoning is recorded at its use site. They look arbitrary because they are
empirical, not because they are unconsidered. Before changing one, read the
comment explaining what it was traded against.
"""

from __future__ import annotations

import io
import subprocess
from dataclasses import dataclass, field

import numpy as np
import soundfile as sf

# Search radius for moving a boundary onto a good splice point.
_SPLICE_SEARCH_MS = 30.0
# Analysis frame for the energy envelope used by that search.
_ENV_HOP_MS = 5.0
_ENV_WIN_MS = 10.0
# Radius for snapping a splice onto a zero crossing.
_ZERO_SNAP_MS = 8.0
# Fade applied to a chunk edge, and the ceiling the adaptive rule may raise it to.
_FADE_MS = 12.0
_MAX_FADE_MS = 40.0
# How far a chunk's tail may run PAST the following vowel's onset.
_VOWEL_OVERLAP_MS = 40.0
# Ceiling on the measured distance to that vowel onset, applied before the
# overlap is added.
_MAX_HEADROOM_MS = 100.0
# Absolute ceiling on a tail. NOTE: given the two constants above, a computed
# tail never exceeds 140 ms, so this only ever binds against a caller-supplied
# ``tail_pad`` floor larger than itself. It guards the caller, not the
# measurement.
_MAX_TAIL_MS = 220.0
# Ceiling on make-up gain, so a near-silent chunk is not amplified into hiss.
_MAX_GAIN_DB = 12.0
# Duration a short chunk is stretched toward.
_TARGET_MS = 400.0
# Slowest WSOLA rate accepted; below this, atempo artifacts become obvious.
_MIN_ATEMPO = 0.5


@dataclass
class SlicedWord:
    """A whole-word render plus where its syllables start and end.

    ``bounds`` has ``len(syllables) + 1`` entries in samples: 0, each interior
    boundary, then the end. ``onset_ends`` has one entry per INTERIOR boundary,
    giving the sample at which the next syllable's vowel begins — the ceiling for
    how far that chunk's tail may overlap. It is optional so a caller without
    alignment data can still slice on ``bounds`` alone.
    """

    word: str
    syllables: list[str]
    samples: np.ndarray
    rate: int
    bounds: list[int]
    onset_ends: list[int] = field(default_factory=list)


def _energy_envelope(samples: np.ndarray, rate: int) -> tuple[np.ndarray, int]:
    """Short-window RMS envelope; returns (envelope, hop_in_samples)."""
    hop = max(1, int(_ENV_HOP_MS / 1000.0 * rate))
    win = max(hop * 2, int(_ENV_WIN_MS / 1000.0 * rate))
    if len(samples) < win:
        return np.zeros(1, dtype=np.float32), hop
    n = 1 + (len(samples) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return np.sqrt((samples[idx] ** 2).mean(axis=1)), hop


def snap_negative_zero(samples: np.ndarray, idx: int, rate: int) -> int:
    """Snap *idx* to the nearest NEGATIVE-going zero crossing within +/-8 ms.

    Direction-consistency is the point, not merely landing on a zero. Two chunk
    edges spliced at crossings of opposite slope still leave a step
    discontinuity — both samples are zero, but the waveform arrives from one
    direction and leaves in the other — and that step is audible as a tick.
    Constraining both edges to the same slope removes it.

    Returns *idx* unchanged when no negative-going crossing is in range.
    """
    span = int(_ZERO_SNAP_MS / 1000.0 * rate)
    lo, hi = max(1, idx - span), min(len(samples) - 1, idx + span)
    if hi <= lo:
        return idx
    seg = samples[lo:hi]
    downs = np.flatnonzero((seg[:-1] >= 0) & (seg[1:] < 0))
    if len(downs) == 0:
        return idx
    return int(lo + downs[np.argmin(np.abs(downs + lo - idx))])


def refine_splice(samples: np.ndarray, rate: int, idx: int) -> int:
    """Move *idx* to the quietest point within +/-30 ms, then zero-snap it.

    Alignment says where the syllable boundary IS; it says nothing about where it
    is safe to cut. Cutting mid-vowel at full amplitude clicks however gently it
    is faded, and fading across a plosive release smears the burst that
    identifies the consonant. Both problems disappear when the splice lands in a
    local energy minimum, so the boundary is nudged onto one.

    The +/-30 ms window is what stops this re-introducing placement error: it is
    far too small to reach a competing syllable boundary, so it can only
    fine-tune the alignment's answer, never overrule it.
    """
    env, hop = _energy_envelope(samples, rate)
    if len(env) < 2:
        return idx
    span = int(_SPLICE_SEARCH_MS / 1000.0 * rate)
    lo, hi = max(0, idx - span), min(len(samples), idx + span)
    f_lo, f_hi = lo // hop, max(lo // hop + 1, min(len(env), hi // hop))
    quietest = int(f_lo + np.argmin(env[f_lo:f_hi]))
    return snap_negative_zero(samples, quietest * hop, rate)


def raw_span(sw: SlicedWord, i: int, j: int, head_pad: int, tail_pad: int) -> np.ndarray:
    """Unfaded audio for ``syllables[i:j]``, padded asymmetrically at interior cuts.

    Padding is deliberately lopsided. A non-final syllable cut exactly at its
    boundary ends while the vowel is still at full amplitude — ``spo`` stops the
    instant /r/ begins — and that is heard as the chunk being lopped off, not as
    a click. Carrying the tail through the following consonant fixes it. The head
    stays short: a chunk's start usually sits in a closure already, and padding
    backwards just imports the previous vowel's tail and muddies the onset.

    How far the tail may run is measured, not guessed, because the distance from
    the boundary to the next vowel varies ~3.5x across ordinary words (34 ms at
    ``no|e``, 122 ms at ``ha|gen``) — any fixed pad is simultaneously too short
    for one and too long for another.

    The tail is then RAMPED TO ZERO across its whole length, and that is
    load-bearing. ``spo|ret`` and ``fulg|te`` have a short onset consonant
    followed immediately by a schwa, so any tail long enough to carry the
    consonant is already inside a voiced vowel — and a voiced vowel is heard as a
    syllable however brief it is. ``ha|gen`` escapes this only because /g/ gives a
    silent closure first. Decaying keeps the consonant and its formant
    transition, the cue that stops the chunk sounding truncated, while the
    following vowel dies away as a natural offset. Shortening the tail instead
    was tried twice and rejected by ear both times: it only trades "I can hear
    the next syllable" back for "it cuts off awkwardly".
    """
    start = sw.bounds[i] - (head_pad if i > 0 else 0)
    end = sw.bounds[j]
    tail = 0
    if j < len(sw.syllables):
        limit = sw.onset_ends[j - 1] if j - 1 < len(sw.onset_ends) else end
        headroom = min(max(0, limit - end), int(_MAX_HEADROOM_MS / 1000.0 * sw.rate))
        headroom += int(_VOWEL_OVERLAP_MS / 1000.0 * sw.rate)
        tail = int(np.clip(headroom, tail_pad, _MAX_TAIL_MS / 1000.0 * sw.rate))
        end += tail

    span = sw.samples[max(0, start) : min(len(sw.samples), end)].copy()

    if tail > 1:
        n = min(tail, len(span))
        if n > 1:
            ramp = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, n, dtype=np.float32)))
            span[-n:] *= ramp
    return span


def _edge_fade_ms(chunk: np.ndarray, rate: int, *, head: bool) -> float:
    """Fade length for ONE edge, chosen from how loud that edge actually is.

    A fixed fade is wrong in both directions. Where a splice landed in a closure
    or a pause the edge is already near-silent, and a long fade there would eat
    the plosive release. Where no quiet point existed to find — hiatus
    (``no|e``), long nasals (``man|nen``), fricatives (``hu|set``) — the edge
    starts at near-full amplitude and a short fade ticks. Measuring the edge
    selects the right case automatically, so plosives keep their burst and
    sonorants stop clicking.

    Maps -18 dB (relative to the chunk's own RMS) and below to 12 ms, rising
    linearly to 40 ms at 0 dB.
    """
    n = max(1, int(0.010 * rate))
    if len(chunk) < 3 * n:
        return _FADE_MS
    edge = chunk[:n] if head else chunk[-n:]
    edge_rms = float(np.sqrt((edge**2).mean()))
    full_rms = float(np.sqrt((chunk**2).mean()))
    if full_rms <= 1e-9:
        return _FADE_MS
    ratio_db = 20.0 * np.log10(max(edge_rms, 1e-9) / full_rms)
    if ratio_db <= -18.0:
        return _FADE_MS
    return float(np.clip(_FADE_MS + (ratio_db + 18.0) / 18.0 * (_MAX_FADE_MS - _FADE_MS), _FADE_MS, _MAX_FADE_MS))


def _fade(chunk: np.ndarray, rate: int) -> np.ndarray:
    """Raised-cosine taper on BOTH edges, each with its own length."""
    out = chunk.copy()
    for head in (True, False):
        n = min(int(_edge_fade_ms(chunk, rate, head=head) / 1000.0 * rate), len(chunk) // 2)
        if n <= 0:
            continue
        ramp = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, n, dtype=np.float32)))
        if head:
            out[:n] *= ramp
        else:
            out[-n:] *= ramp[::-1]
    return out


def normalize_rms(chunk: np.ndarray, target_rms: float) -> np.ndarray:
    """Scale *chunk* toward *target_rms*, gain-capped and peak-limited.

    An unstressed syllable genuinely is quieter than its stressed neighbour. That
    is correct inside the word and wrong in a drill, where the learner has to
    hear every chunk equally well — the measured spread before normalising was
    -4.5 to +1.2 dB, and the quietest chunk was the one users complained about.

    The +12 dB cap stops a near-silent span being amplified into hiss; the 0.99
    peak limit preserves the headroom the fades assume.
    """
    rms = float(np.sqrt((chunk**2).mean())) if len(chunk) else 0.0
    if rms <= 1e-6 or target_rms <= 0:
        return chunk
    gain = min(target_rms / rms, 10.0 ** (_MAX_GAIN_DB / 20.0))
    out = chunk * gain
    peak = float(np.abs(out).max())
    if peak > 0.99:
        out = out * (0.99 / peak)
    return out.astype(np.float32)


def time_stretch(chunk: np.ndarray, rate: int, target_ms: float) -> np.ndarray:
    """Lengthen *chunk* toward *target_ms* with ffmpeg atempo (WSOLA).

    Pitch-preserving stretching is required rather than incidental: pitch carries
    the tone accent, which is the whole reason for cutting from a connected word
    instead of re-synthesising the syllable alone. Resampling would destroy the
    property being protected.

    Only closes the gap to *target_ms* — never compresses a chunk that is already
    long enough — and returns the input unchanged if ffmpeg fails, so a missing
    or broken encoder degrades to un-stretched audio rather than raising
    mid-render.
    """
    dur_ms = len(chunk) / rate * 1000.0
    if dur_ms <= 0 or dur_ms >= target_ms:
        return chunk
    tempo = max(_MIN_ATEMPO, dur_ms / target_ms)
    buf = io.BytesIO()
    sf.write(buf, chunk, rate, format="WAV", subtype="PCM_16")
    proc = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-filter:a",
            f"atempo={tempo:.4f}",
            "-f",
            "wav",
            "-",
        ],
        input=buf.getvalue(),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        return chunk
    out, _ = sf.read(io.BytesIO(proc.stdout), dtype="float32", always_2d=True)
    return out.mean(axis=1)


def polish(
    chunk: np.ndarray,
    rate: int,
    target_rms: float,
    target_ms: float,
    *,
    stretch: bool,
    normalize: bool,
) -> np.ndarray:
    """Condition a raw span for use as a drill chunk.

    Order matters: DC first (an offset thumps when faded), stretch before
    normalising (WSOLA changes the RMS), and fade LAST so the edges finish at
    true zero rather than at whatever the gain stage left behind.
    """
    out = chunk - float(chunk.mean()) if len(chunk) else chunk
    if stretch:
        out = time_stretch(out, rate, target_ms)
    if normalize:
        out = normalize_rms(out, target_rms)
    return _fade(out, rate)
