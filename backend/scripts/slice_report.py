"""Per-chunk slicing diagnostic: report what the renderer actually cuts.

Every non-final breakdown chunk carries a tail of the following audio, ramped to
zero (``app.audio.slicing.raw_span``). This tool reports that tail per interior
chunk — how far it runs past the next vowel's onset, whether it is pinned at the
caller's floor, and whether it was measured at all — so ear-tuned constants get
a number to argue with.

    uv run python -m scripts.slice_report --cache-dir DIR [--language no] [--json]

Reads the alignment records ``slicer._store_bounds`` writes (``syllables``,
``n_samples``, ``bounds``, ``onset_ends``) from ``--cache-dir``. No audio, no
aligner, no TTS. The cache does not store the sample rate, so ``--rate``
(default 24000) supplies it.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from app.audio.slicer import _TAIL_PAD_MS
from app.audio.slicing import SlicedWord, tail_length
from app.config import settings
from app.languages import get_alignment


@dataclass(frozen=True)
class ChunkRow:
    """One interior chunk's tail report."""

    word: str
    index: int  # syllable index of the chunk this row describes
    syllable: str
    next_syllable: str
    next_onset: str  # LEADING consonants of next_syllable; "" if vowel-initial
    tail_ms: float
    to_vowel_ms: float  # (onset_ends[i] - bounds[i + 1]) in ms
    overshoot_ms: float  # tail_ms - to_vowel_ms
    at_floor: bool  # the tail came out equal to tail_pad
    degenerate: bool  # onset_ends[i] == bounds[i + 1] — no measurement was taken


def build_rows(sw: SlicedWord, vowels: frozenset[str], tail_pad: int) -> list[ChunkRow]:
    """One ``ChunkRow`` per interior chunk of *sw*.

    ``tail_ms`` MUST be ``app.audio.slicing.tail_length``'s answer — the whole
    point of the tool is that it reports exactly what ``raw_span`` cuts, and a
    re-implementation of the clip/cap arithmetic silently drifts. The two
    distance fields are derived from the alignment record, not from the formula.
    """
    rows: list[ChunkRow] = []
    for j in range(1, len(sw.syllables)):
        i = j - 1
        onset_end = sw.onset_ends[i] if i < len(sw.onset_ends) else sw.bounds[j]
        next_syllable = sw.syllables[j]
        n_onset = 0
        while n_onset < len(next_syllable) and next_syllable[n_onset] not in vowels:
            n_onset += 1
        tail = tail_length(sw, j, tail_pad)
        to_vowel = onset_end - sw.bounds[j]
        rows.append(
            ChunkRow(
                word=sw.word,
                index=i,
                syllable=sw.syllables[i],
                next_syllable=next_syllable,
                next_onset=next_syllable[:n_onset],
                tail_ms=round(tail / sw.rate * 1000.0, 1),
                to_vowel_ms=round(to_vowel / sw.rate * 1000.0, 1),
                overshoot_ms=round((tail - to_vowel) / sw.rate * 1000.0, 1),
                at_floor=tail == tail_pad,
                degenerate=onset_end == sw.bounds[j],
            )
        )
    return rows


def _load_records(cache_dir: Path, rate: int) -> list[SlicedWord]:
    """One ``SlicedWord`` per ``*.json`` alignment record; no samples needed.

    The record does not store the word — the cache is keyed by a hash of
    word+voice, so ``path.stem`` is that hash and useless to read. The syllables
    rejoin to the surface form by construction (``flat_syllables`` returns
    ``None`` rather than pieces that don't), so join them instead: this report
    is read by someone holding a list of words they mistrust the sound of.
    """
    words: list[SlicedWord] = []
    for path in sorted(cache_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        words.append(
            SlicedWord(
                word="".join(data["syllables"]),
                syllables=data["syllables"],
                samples=np.zeros(int(data["n_samples"]), dtype=np.float32),
                rate=rate,
                bounds=data["bounds"],
                onset_ends=data["onset_ends"],
            )
        )
    return words


def _print_table(rows: list[ChunkRow]) -> None:
    word_w = max((len(r.word) for r in rows), default=0)
    header = (
        f"{'word':<{word_w}} {'idx':>3} {'syllable':<12} {'next':<12} "
        f"{'tail_ms':>8} {'to_vowel_ms':>11} {'overshoot_ms':>12} {'floor':>5} {'deg':>4}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        print(
            f"{r.word:<{word_w}} {r.index:>3} {r.syllable:<12} {r.next_syllable:<12} "
            f"{r.tail_ms:>8.1f} {r.to_vowel_ms:>11.1f} {r.overshoot_ms:>12.1f} "
            f"{'yes' if r.at_floor else '':>5} {'yes' if r.degenerate else '':>4}"
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cache-dir", type=Path, default=settings.audio_alignment_cache_dir)
    ap.add_argument("--language", default="no", help="language code whose vowel set is used")
    ap.add_argument(
        "--rate",
        type=int,
        default=24000,
        help="sample rate of the cached renders (the cache does not store it; default: 24000)",
    )
    ap.add_argument("--json", action="store_true", help="emit the rows as JSON instead of a table")
    args = ap.parse_args(argv)

    alignment = get_alignment(args.language)
    if alignment is None:
        ap.error(f"language {args.language!r} has no alignment wiring")
    if not args.cache_dir.is_dir():
        ap.error(f"cache directory not found: {args.cache_dir}")

    tail_pad = int(_TAIL_PAD_MS / 1000.0 * args.rate)
    rows: list[ChunkRow] = []
    for sw in _load_records(args.cache_dir, args.rate):
        rows.extend(build_rows(sw, alignment.vowels, tail_pad))
    rows.sort(key=lambda r: r.overshoot_ms, reverse=True)

    if args.json:
        print(json.dumps([asdict(r) for r in rows], indent=2))
        return 0

    _print_table(rows)
    n = len(rows)
    print(
        f"{n} rows; {sum(r.overshoot_ms > 0 for r in rows)} overshooting; "
        f"{sum(r.at_floor for r in rows)} at floor; {sum(r.degenerate for r in rows)} degenerate"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
