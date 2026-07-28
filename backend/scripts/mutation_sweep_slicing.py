#!/usr/bin/env python3
"""Mutation sweep for app/audio/slicing.py.

Usage::

    uv run python scripts/mutation_sweep_slicing.py

Runs ``pytest tests/test_audio_slicing.py`` once per mutant.  Exits non-zero
when any non-equivalent mutant survives or any equivalent mutant is killed.

Each mutant is a ``(label, old_source, new_source)`` triple — no AST or random
mutation.  The module is restored byte-for-byte on every exit path.

The 18 mutants (16 non‑equivalent + 2 equivalent) encode every behaviour the
Fable audit found an unguarded 9d7fae4:e7fd16a.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import NoReturn

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SLICING = _REPO_ROOT / "app" / "audio" / "slicing.py"

_PYTEST = [
    "uv",
    "run",
    "pytest",
    "tests/test_audio_slicing.py",
    "-q",
    "--no-cov",
    "-x",
    "-p",
    "no:cacheprovider",
]

MUTANTS: list[tuple[str, str, str]] = [
    (
        "snap: negative-going → positive-going only",
        "    downs = np.flatnonzero((seg[:-1] >= 0) & (seg[1:] < 0))",
        "    downs = np.flatnonzero((seg[:-1] <= 0) & (seg[1:] > 0))",
    ),
    (
        "snap: no-op (always return idx)",
        "    return int(lo + downs[np.argmin(np.abs(downs + lo - idx))])",
        "    return idx",
    ),
    (
        "refine_splice: no-op (return idx)",
        "    return snap_negative_zero(samples, quietest * hop, rate)",
        "    return idx",
    ),
    (
        "refine_splice: argmax instead of argmin",
        "    quietest = int(f_lo + np.argmin(env[f_lo:f_hi]))",
        "    quietest = int(f_lo + np.argmax(env[f_lo:f_hi]))",
    ),
    (
        "raw_span: drop vowel overlap (+0 not +40ms)",
        "        headroom += int(_VOWEL_OVERLAP_MS / 1000.0 * sw.rate)",
        "        headroom += 0",
    ),
    (
        "raw_span: drop headroom cap (100ms)",
        '        headroom = min(max(0, limit - end), int(_MAX_HEADROOM_MS / 1000.0 * sw.rate))',
        "        headroom = max(0, limit - end)",
    ),
    (
        "raw_span: drop tail ceiling (220ms)",
        '        tail = int(np.clip(headroom, tail_pad, _MAX_TAIL_MS / 1000.0 * sw.rate))',
        "        tail = int(max(headroom, tail_pad))",
    ),
    (
        "raw_span: invert tail ramp (fade-in not fade-out)",
        "            ramp = 0.5 * (1.0 + np.cos(np.linspace(0.0, np.pi, n, dtype=np.float32)))",
        "            ramp = 0.5 * (1.0 - np.cos(np.linspace(0.0, np.pi, n, dtype=np.float32)))",
    ),
    (
        "raw_span: no head pad on interior chunks",
        "    start = sw.bounds[i] - (head_pad if i > 0 else 0)",
        "    start = sw.bounds[i]",
    ),
    (
        "_edge_fade_ms: always 12ms (return _FADE_MS)",
        '    return float(np.clip(_FADE_MS + (ratio_db + 18.0) / 18.0 * (_MAX_FADE_MS - _FADE_MS), _FADE_MS, _MAX_FADE_MS))',
        "    return _FADE_MS",
    ),
    (
        "_fade: skip head fade only",
        "        if head:\n"
        "            out[:n] *= ramp\n"
        "        else:\n"
        "            out[-n:] *= ramp[::-1]",
        "        if head:\n"
        "            pass\n"
        "        else:\n"
        "            out[-n:] *= ramp[::-1]",
    ),
    (
        "_fade: skip tail fade only",
        "        if head:\n"
        "            out[:n] *= ramp\n"
        "        else:\n"
        "            out[-n:] *= ramp[::-1]",
        "        if head:\n"
        "            out[:n] *= ramp\n"
        "        else:\n"
        "            pass",
    ),
    (
        "normalize_rms: remove +12dB gain cap",
        "    gain = min(target_rms / rms, 10.0 ** (_MAX_GAIN_DB / 20.0))",
        "    gain = target_rms / rms",
    ),
    (
        "normalize_rms: remove peak limiter",
        "    if peak > 0.99:\n"
        "        out = out * (0.99 / peak)",
        "    if False:\n"
        "        out = out * (0.99 / peak)",
    ),
    (
        "normalize_rms: remove silence guard",
        "    if rms <= 1e-6 or target_rms <= 0:\n"
        "        return chunk",
        "    if False:\n"
        "        return chunk",
    ),
    (
        "time_stretch: remove _MIN_ATEMPO floor",
        "    tempo = max(_MIN_ATEMPO, dur_ms / target_ms)",
        "    tempo = dur_ms / target_ms",
    ),
]

EQUIVALENT: list[tuple[str, str, str]] = [
    (
        "raw_span: head pad also at word start (equivalent — max(0, start) erases)",
        "    start = sw.bounds[i] - (head_pad if i > 0 else 0)",
        "    start = sw.bounds[i] - head_pad",
    ),
    (
        "fade: skip the -18dB early return (equivalent — clip's lower bound erases)",
        "    if ratio_db <= -18.0:\n"
        "        return _FADE_MS\n"
        '    return float(np.clip(_FADE_MS + (ratio_db + 18.0) / 18.0 * (_MAX_FADE_MS - _FADE_MS), _FADE_MS, _MAX_FADE_MS))',
        '    return float(np.clip(_FADE_MS + (ratio_db + 18.0) / 18.0 * (_MAX_FADE_MS - _FADE_MS), _FADE_MS, _MAX_FADE_MS))',
    ),
]


def _fail(msg: str) -> NoReturn:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _pytest() -> bool:
    """Run the pytest command.  Returns True when all tests pass."""
    result = subprocess.run(_PYTEST, capture_output=True, text=True, cwd=_REPO_ROOT)
    return result.returncode == 0


def _check_old_strings(source: str, mutants: list[tuple[str, str, str]], tag: str) -> None:
    """Assert every old string is found in *source*."""
    for label, old, _new in mutants:
        if old not in source:
            _fail(
                f"[{tag}] old_string not found: {label!r}\n"
                f"  The module may have changed underneath this tool."
            )


def _mutate(source: str, old: str, new: str) -> str:
    """Replace *old* with *new* in *source*."""
    assert old in source, f"old_string not found in module source"
    return source.replace(old, new, 1)


def _restore(source: str) -> None:
    """Restore the module.  Assert byte-for-byte match on every exit path."""
    current = _SLICING.read_text("utf-8")
    if current != source:
        _SLICING.write_text(source)
    restored = _SLICING.read_text("utf-8")
    assert restored == source, "RESTORE FAILED — module left in mutated state!"


def main() -> None:
    source = _SLICING.read_text("utf-8")

    # Pre-flight: every old_string must be found.
    _check_old_strings(source, MUTANTS, "MUTANTS")
    _check_old_strings(source, EQUIVALENT, "EQUIVALENT")

    header = f"mutation_sweep_slicing.py — {len(MUTANTS)} non-equivalent + {len(EQUIVALENT)} equivalent"
    print(header)
    print("━" * len(header))

    killed = 0
    survived: list[str] = []

    for label, old, new in MUTANTS:
        _SLICING.write_text(_mutate(source, old, new))
        ok = _pytest()
        _restore(source)

        if ok:
            survived.append(label)
            print(f"  ✗ {label}  ... SURVIVED")
        else:
            killed += 1
            print(f"  ✓ {label}  ... KILLED")

    eq_ok = 0
    eq_broken: list[str] = []

    for label, old, new in EQUIVALENT:
        _SLICING.write_text(_mutate(source, old, new))
        ok = _pytest()
        _restore(source)

        if ok:
            eq_ok += 1
            print(f"  ✓ {label}  ... SURVIVED (equivalent)")
        else:
            eq_broken.append(label)
            print(f"  ✗ {label}  ... KILLED (equivalent!?)")

    print("━" * len(header))
    score = f"Score: {killed}/{len(MUTANTS)} non-equivalent killed."
    if len(EQUIVALENT) > 0:
        score += f" Equivalents: {eq_ok}/{len(EQUIVALENT)} survived."
    print(score)

    errors: list[str] = []
    if survived:
        errors.append(f"Non-equivalent survivors ({len(survived)}): {', '.join(survived)}")
    if eq_broken:
        errors.append(f"Equivalent mutants KILLED ({len(eq_broken)}): {', '.join(eq_broken)}")

    if errors:
        print("\n".join(errors))
        sys.exit(1)

    print("Exit: 0")


if __name__ == "__main__":
    main()
