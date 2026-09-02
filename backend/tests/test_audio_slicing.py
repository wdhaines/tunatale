"""Guardrails for app.audio.slicing.

Each test here is written to FAIL against a specific plausible-but-wrong
implementation, and every one was sabotage-drilled by mutating the module and
confirming it went red. A mutation sweep of 16 non-equivalent mutants kills
16/16 with this file in place. If you weaken an assertion, re-run that sweep —
the original version of this file passed 28/28 while leaving 9 behaviours
completely unguarded, including the negative-going zero snap and the entire body
of ``refine_splice``.
"""

from __future__ import annotations

from unittest import mock

import numpy as np
import pytest

from app.audio.slicer import _TARGET_MS as _SLICER_TARGET_MS
from app.audio.slicing import (
    _FADE_MS,
    _MAX_FADE_MS,
    _MAX_GAIN_DB,
    SlicedWord,
    normalize_rms,
    polish,
    raw_span,
    refine_splice,
    snap_negative_zero,
    tail_length,
    time_stretch,
)

# Shells out to a real ffmpeg binary. CI's two hostile-timezone jobs deselect
# these with -m "not ffmpeg" so they need no ffmpeg install; see
# pyproject.toml [tool.pytest.ini_options] markers.
pytestmark = pytest.mark.ffmpeg

_RATE = 16000
_TARGET_MS = _SLICER_TARGET_MS


def _ms(milliseconds: float, rate: int = _RATE) -> int:
    return int(milliseconds / 1000.0 * rate)


def _sine(freq: float = 440.0, dur_ms: float = 500.0, rate: int = _RATE) -> np.ndarray:
    n = round(dur_ms / 1000.0 * rate)
    t = np.arange(n, dtype=np.float32) / rate
    return (np.sin(2.0 * np.pi * freq * t) * 0.5).astype(np.float32)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt((x**2).mean())) if len(x) else 0.0


def _two_syllable(headroom_ms: float, boundary_ms: float = 400.0, total_ms: float = 1000.0) -> SlicedWord:
    """A 2-syllable word whose vowel-onset headroom is exactly *headroom_ms*."""
    n = _ms(total_ms)
    boundary = _ms(boundary_ms)
    return SlicedWord(
        word="testord",
        syllables=["test", "ord"],
        samples=_sine(220.0, total_ms),
        rate=_RATE,
        bounds=[0, boundary, n],
        onset_ends=[boundary + _ms(headroom_ms)],
    )


def _word(word: str, syllables: list[str], samples: np.ndarray | None = None, rate: int = _RATE) -> SlicedWord:
    if samples is None:
        samples = _sine(dur_ms=600.0, rate=rate)
    n = len(samples)
    cuts = [n * (i + 1) // len(syllables) for i in range(len(syllables) - 1)]
    onset_ends = [min(c + n // 8, n) for c in cuts]
    return SlicedWord(
        word=word,
        syllables=syllables,
        samples=samples,
        rate=rate,
        bounds=[0, *cuts, n],
        onset_ends=onset_ends,
    )


class TestSnapNegativeZero:
    def test_prefers_negative_going_over_a_NEARER_positive_going(self):
        """The direction constraint, not merely 'lands on a zero'.

        Kills: snapping to any sign change, and not snapping at all. idx=2 sits
        one sample from a POSITIVE-going crossing and two from a negative-going
        one, so an any-crossing implementation returns 1 and a no-op returns 2.
        """
        samples = np.array([-0.5, -0.2, 0.1, 0.4, 0.2, -0.3, -0.1, 0.2], dtype=np.float32)
        result = snap_negative_zero(samples, 2, _RATE)
        assert result == 4, "must pick the negative-going crossing at 4, not the nearer positive-going one at 1"

    def test_moves_when_idx_is_not_already_on_a_crossing(self):
        """Kills a no-op implementation that returns idx unchanged."""
        samples = np.array([-0.5, -0.2, 0.1, 0.4, 0.2, -0.3, -0.1, 0.2], dtype=np.float32)
        assert snap_negative_zero(samples, 0, _RATE) != 0

    def test_no_crossing_returns_original(self):
        samples = np.array([1.0, 0.5, 0.3, 0.1, 0.0, 0.0], dtype=np.float32)
        assert snap_negative_zero(samples, 2, _RATE) == 2

    def test_edge_returns_clamped(self):
        samples = np.array([1.0, 0.5], dtype=np.float32)
        assert snap_negative_zero(samples, 0, _RATE) == 0


class TestRefineSplice:
    def test_moves_boundary_out_of_loud_region_into_quiet_one(self):
        """Kills a no-op body: idx starts 10 ms INSIDE the loud region.

        The previous version of this test passed idx == n and asserted
        `result >= n`, which `return idx` satisfies by construction.
        """
        seg = _ms(100.0)
        loud = _sine(440.0, 100.0) * 0.5
        quiet = _sine(440.0, 100.0) * 0.01
        samples = np.concatenate([loud[:seg], quiet[:seg], loud[:seg]])
        idx = seg - _ms(10.0)
        result = refine_splice(samples, _RATE, idx)
        assert result != idx, "must not return the input index unchanged"
        assert result >= seg, f"must move into the quiet region (>= {seg}), got {result}"
        assert result < 2 * seg, "must not run past the quiet region"

    def test_picks_quietest_not_loudest(self):
        seg = _ms(100.0)
        quiet = _sine(440.0, 100.0) * 0.01
        loud = _sine(440.0, 100.0) * 0.5
        samples = np.concatenate([quiet[:seg], loud[:seg]])
        result = refine_splice(samples, _RATE, seg)
        assert result < seg, "argmin, not argmax"

    def test_short_buffer_returns_original(self):
        assert refine_splice(np.zeros(10, dtype=np.float32), _RATE, 5) == 5


class TestRawSpanTailLength:
    """The tail formula: clip(min(headroom, 100ms) + 40ms, tail_pad, 220ms)."""

    def test_vowel_overlap_is_added(self):
        """Kills dropping the +40 ms overlap (would give 50 ms)."""
        sw = _two_syllable(headroom_ms=50.0)
        span = raw_span(sw, 0, 1, head_pad=0, tail_pad=_ms(20.0))
        assert len(span) - sw.bounds[1] == _ms(90.0)

    def test_headroom_is_capped_at_100ms_before_overlap(self):
        """Kills dropping the _MAX_HEADROOM_MS cap (would give 190 ms)."""
        sw = _two_syllable(headroom_ms=150.0)
        span = raw_span(sw, 0, 1, head_pad=0, tail_pad=_ms(20.0))
        assert len(span) - sw.bounds[1] == _ms(140.0)

    def test_max_tail_clamps_an_oversized_caller_floor(self):
        """Kills dropping the _MAX_TAIL_MS ceiling (would give the 300 ms floor)."""
        sw = _two_syllable(headroom_ms=50.0)
        span = raw_span(sw, 0, 1, head_pad=0, tail_pad=_ms(300.0))
        assert len(span) - sw.bounds[1] == _ms(220.0)

    def test_tail_pad_acts_as_the_floor(self):
        sw = _two_syllable(headroom_ms=0.0)
        span = raw_span(sw, 0, 1, head_pad=0, tail_pad=_ms(150.0))
        assert len(span) - sw.bounds[1] == _ms(150.0)


class TestTailLength:
    """``tail_length`` is what the diagnostics read; it must BE what raw_span cuts.

    The tail formula previously lived inline in ``raw_span``, so any tool that
    wanted to report it had to re-implement the arithmetic — and a re-implementation
    silently drifts. Extracting it makes drift impossible, but only if these tests
    hold the two implementations together.
    """

    def test_reports_exactly_the_tail_raw_span_cuts(self):
        """The anti-drift guard: the reported number IS the audio that gets carried."""
        pad = _ms(20.0)
        for headroom_ms in (0.0, 50.0, 150.0):
            sw = _two_syllable(headroom_ms=headroom_ms)
            span = raw_span(sw, 0, 1, head_pad=0, tail_pad=pad)
            assert len(span) - sw.bounds[1] == tail_length(sw, 1, pad), (
                f"tail_length disagrees with raw_span at headroom={headroom_ms}ms"
            )

    def test_final_chunk_carries_no_tail(self):
        sw = _two_syllable(headroom_ms=50.0)
        assert tail_length(sw, len(sw.syllables), _ms(20.0)) == 0

    def test_a_zero_measurement_falls_through_to_the_floor(self):
        """A vowel-initial next syllable puts onset_ends ON the cut.

        derive_syllable_bounds sets the ceiling to the next VOWEL's start, so when
        the next syllable has no onset consonant that ceiling is the cut itself —
        the headroom is 0 by construction, not by observation, and the caller's
        floor decides the whole tail.
        """
        sw = _two_syllable(headroom_ms=0.0)
        assert tail_length(sw, 1, _ms(80.0)) == _ms(80.0)


class TestRawSpan:
    def test_tail_decay_ends_at_near_zero_amplitude(self):
        samples = _sine(220.0, 600.0, _RATE)
        n = len(samples)
        thirds = [n // 3, 2 * n // 3]
        sw = SlicedWord(
            word="testordet",
            syllables=["test", "or", "det"],
            samples=samples,
            rate=_RATE,
            bounds=[0, *thirds, n],
            onset_ends=[min(t + n // 8, n) for t in thirds],
        )
        raw = raw_span(sw, 1, 2, head_pad=0, tail_pad=_ms(50.0))
        assert len(raw) > 0
        assert np.abs(raw[-5:]).max() < 0.001

    def test_tail_decays_monotonically_not_the_reverse(self):
        """Kills an inverted ramp (fade-IN across the tail)."""
        sw = _two_syllable(headroom_ms=50.0)
        span = raw_span(sw, 0, 1, head_pad=0, tail_pad=_ms(20.0))
        tail = span[sw.bounds[1] :]
        first_quarter = _rms(tail[: len(tail) // 4])
        last_quarter = _rms(tail[-len(tail) // 4 :])
        assert last_quarter < first_quarter * 0.2, "tail must decay toward zero, not grow"

    def test_preserves_pre_tail_region_sample_for_sample(self):
        sw = _word("testord", ["test", "ord"])
        chunk = sw.samples[sw.bounds[1] : sw.bounds[2]].copy()
        raw = raw_span(sw, 1, 2, head_pad=0, tail_pad=_ms(50.0))
        np.testing.assert_array_equal(raw[: len(chunk)], chunk)

    def test_word_initial_chunk_no_head_pad(self):
        sw = _word("testord", ["test", "ord"])
        raw = raw_span(sw, 0, 1, head_pad=_ms(100.0), tail_pad=0)
        assert raw[0] == sw.samples[0]

    def test_interior_chunk_does_get_head_pad(self):
        """Kills applying the head pad unconditionally OR never applying it."""
        sw = _word("testordet", ["test", "or", "det"])
        head = _ms(20.0)
        raw = raw_span(sw, 1, 2, head_pad=head, tail_pad=0)
        assert raw[0] == sw.samples[sw.bounds[1] - head]

    def test_word_final_chunk_no_tail(self):
        sw = _word("testord", ["test", "ord"])
        raw = raw_span(sw, 1, 2, head_pad=0, tail_pad=_ms(100.0))
        assert raw[-1] == sw.samples[sw.bounds[2] - 1]

    def test_tail_decay_short_span_no_error(self):
        sw = SlicedWord(
            word="ab",
            syllables=["a", "b"],
            samples=np.ones(10, dtype=np.float32),
            rate=_RATE,
            bounds=[0, 5, 10],
            onset_ends=[8],
        )
        assert len(raw_span(sw, 0, 1, head_pad=0, tail_pad=_ms(50.0))) > 0

    def test_tail_decay_n_leq_one(self):
        sw = SlicedWord(
            word="a",
            syllables=["a", "a"],
            samples=np.array([1.0], dtype=np.float32),
            rate=_RATE,
            bounds=[0, 0, 1],
        )
        assert len(raw_span(sw, 0, 1, head_pad=0, tail_pad=_ms(50.0))) == 1


class TestFade:
    def test_quiet_edge_returns_12ms(self):
        from app.audio.slicing import _edge_fade_ms

        quiet = np.full(_ms(50.0), 1e-6, dtype=np.float32)
        chunk = np.concatenate([quiet, _sine(440.0, 50.0) * 0.5])
        assert _edge_fade_ms(chunk, _RATE, head=True) == pytest.approx(_FADE_MS, abs=0.1)

    def test_loud_edge_returns_40ms(self):
        from app.audio.slicing import _edge_fade_ms

        assert _edge_fade_ms(_sine(440.0, 100.0) * 0.5, _RATE, head=True) == pytest.approx(_MAX_FADE_MS, abs=0.1)

    def test_both_edges_are_faded(self):
        """Kills fading only the head, and kills skipping the fade entirely."""
        chunk = _sine(440.0, 200.0)
        result = polish(chunk.copy(), _RATE, target_rms=0.1, target_ms=_TARGET_MS, stretch=False, normalize=False)
        edge = _ms(5.0)
        mid = _rms(result[len(result) // 2 - edge : len(result) // 2 + edge])
        assert _rms(result[:edge]) < mid * 0.2, "head edge not faded"
        assert _rms(result[-edge:]) < mid * 0.2, "tail edge not faded"

    def test_fade_output_same_length(self):
        chunk = _sine(440.0, 100.0)
        result = polish(chunk.copy(), _RATE, target_rms=0.1, target_ms=_TARGET_MS, stretch=False, normalize=False)
        assert len(result) == len(chunk)

    def test_short_chunk_returns_12ms(self):
        from app.audio.slicing import _edge_fade_ms

        assert _edge_fade_ms(np.zeros(5, dtype=np.float32), _RATE, head=True) == _FADE_MS

    def test_zero_length_chunk_in_fade(self):
        chunk = np.array([0.5], dtype=np.float32)
        result = polish(chunk, _RATE, target_rms=0.1, target_ms=_TARGET_MS, stretch=False, normalize=False)
        assert len(result) == 1


class TestNormalizeRms:
    def test_gain_is_capped_at_exactly_12_db(self):
        """Kills removing the cap.

        The chunk must sit ABOVE the 1e-6 silence guard or normalize_rms returns
        early and the cap line is never reached — that is exactly how the
        previous version of this test passed while measuring 0 dB of gain.
        """
        chunk = np.full(1000, 1e-3, dtype=np.float32)
        result = normalize_rms(chunk, target_rms=0.1)
        gain_db = 20.0 * np.log10(_rms(result) / _rms(chunk))
        assert gain_db == pytest.approx(_MAX_GAIN_DB, abs=0.05)

    def test_gain_applied_when_under_the_cap(self):
        chunk = np.full(1000, 0.05, dtype=np.float32)
        result = normalize_rms(chunk, target_rms=0.1)
        assert _rms(result) == pytest.approx(0.1, rel=1e-3)

    def test_peak_limited_to_099(self):
        rng = np.random.default_rng(42)
        chunk = rng.uniform(-1.0, 1.0, 1000).astype(np.float32)
        result = normalize_rms(chunk, target_rms=10.0)
        assert np.abs(result).max() <= 0.99 + 1e-6
        assert np.abs(result).max() == pytest.approx(0.99, abs=1e-3), "limiter must engage, not merely not-exceed"

    def test_silent_chunk_unchanged(self):
        chunk = np.zeros(1000, dtype=np.float32)
        np.testing.assert_array_equal(normalize_rms(chunk, target_rms=0.1), chunk)

    def test_nonpositive_target_unchanged(self):
        chunk = np.full(1000, 0.05, dtype=np.float32)
        np.testing.assert_array_equal(normalize_rms(chunk, target_rms=0.0), chunk)


class TestTimeStretch:
    def test_lengthens_toward_target(self):
        chunk = _sine(440.0, 100.0)
        assert len(time_stretch(chunk, _RATE, _TARGET_MS)) > len(chunk)

    def test_noop_above_target(self):
        chunk = _sine(440.0, 500.0)
        np.testing.assert_array_equal(time_stretch(chunk, _RATE, _TARGET_MS), chunk)

    def test_noop_on_empty(self):
        assert len(time_stretch(np.array([], dtype=np.float32), _RATE, _TARGET_MS)) == 0

    def test_ffmpeg_failure_returns_original_chunk(self):
        chunk = _sine(440.0, 50.0)
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1, stdout=b"", stderr=b"")):
            np.testing.assert_array_equal(time_stretch(chunk, _RATE, _TARGET_MS), chunk)

    def test_noop_at_exact_target(self):
        chunk = _sine(440.0, _TARGET_MS)
        np.testing.assert_array_equal(time_stretch(chunk, _RATE, _TARGET_MS), chunk)

    def test_min_atempo_floor_bounds_the_stretch(self):
        """Kills removing the _MIN_ATEMPO floor: a 10 ms chunk would otherwise be
        stretched 40x rather than the permitted 2x."""
        chunk = _sine(440.0, 10.0)
        result = time_stretch(chunk, _RATE, _TARGET_MS)
        assert len(result) < len(chunk) * 3


class TestPolish:
    def test_dc_removes_offset(self):
        chunk = np.full(1000, 0.5, dtype=np.float32)
        result = polish(chunk, _RATE, target_rms=0.1, target_ms=_TARGET_MS, stretch=False, normalize=False)
        assert abs(float(result.mean())) < 0.001

    def test_stretch_and_normalize(self):
        chunk = _sine(440.0, 100.0)
        result = polish(chunk, _RATE, target_rms=0.1, target_ms=_TARGET_MS, stretch=True, normalize=True)
        assert len(result) > len(chunk)
        assert result.dtype == np.float32
