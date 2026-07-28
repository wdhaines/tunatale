from __future__ import annotations

import io
import subprocess
from dataclasses import dataclass, field

import numpy as np
import soundfile as sf

_SPLICE_SEARCH_MS = 30.0
_ENV_HOP_MS = 5.0
_ENV_WIN_MS = 10.0
_ZERO_SNAP_MS = 8.0
_FADE_MS = 12.0
_MAX_FADE_MS = 40.0
_VOWEL_OVERLAP_MS = 40.0
_MAX_HEADROOM_MS = 100.0
_MAX_TAIL_MS = 220.0
_MAX_GAIN_DB = 12.0
_TARGET_MS = 400.0
_MIN_ATEMPO = 0.5


@dataclass
class SlicedWord:
    word: str
    syllables: list[str]
    samples: np.ndarray
    rate: int
    bounds: list[int]
    onset_ends: list[int] = field(default_factory=list)


def _energy_envelope(samples: np.ndarray, rate: int) -> tuple[np.ndarray, int]:
    hop = max(1, int(_ENV_HOP_MS / 1000.0 * rate))
    win = max(hop * 2, int(_ENV_WIN_MS / 1000.0 * rate))
    if len(samples) < win:
        return np.zeros(1, dtype=np.float32), hop
    n = 1 + (len(samples) - win) // hop
    idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
    return np.sqrt((samples[idx] ** 2).mean(axis=1)), hop


def snap_negative_zero(samples: np.ndarray, idx: int, rate: int) -> int:
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
    env, hop = _energy_envelope(samples, rate)
    if len(env) < 2:
        return idx
    span = int(_SPLICE_SEARCH_MS / 1000.0 * rate)
    lo, hi = max(0, idx - span), min(len(samples), idx + span)
    f_lo, f_hi = lo // hop, max(lo // hop + 1, min(len(env), hi // hop))
    quietest = int(f_lo + np.argmin(env[f_lo:f_hi]))
    return snap_negative_zero(samples, quietest * hop, rate)


def raw_span(sw: SlicedWord, i: int, j: int, head_pad: int, tail_pad: int) -> np.ndarray:
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
    out = chunk - float(chunk.mean()) if len(chunk) else chunk
    if stretch:
        out = time_stretch(out, rate, target_ms)
    if normalize:
        out = normalize_rms(out, target_rms)
    return _fade(out, rate)
