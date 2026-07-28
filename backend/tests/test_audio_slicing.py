from __future__ import annotations

from unittest import mock

import numpy as np
import pytest

from app.audio.slicing import (
    _FADE_MS,
    _MAX_FADE_MS,
    _MAX_GAIN_DB,
    _TARGET_MS,
    SlicedWord,
    normalize_rms,
    polish,
    raw_span,
    refine_splice,
    snap_negative_zero,
    time_stretch,
)

_RATE = 16000


def _sine(freq: float = 440.0, dur_ms: float = 500.0, rate: int = _RATE) -> np.ndarray:
    n = round(dur_ms / 1000.0 * rate)
    t = np.arange(n, dtype=np.float32) / rate
    return (np.sin(2.0 * np.pi * freq * t) * 0.5).astype(np.float32)


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
    def test_snaps_to_negative_going_crossing(self):
        samples = np.array([1.0, 0.5, 0.0, -0.3, -0.1, 0.2, 0.0, -0.2], dtype=np.float32)
        # Negative-going crossing at index 2 (0.0→-0.3) and index 6 (0.0→-0.2)
        result = snap_negative_zero(samples, 2, _RATE)
        assert result == 2  # already at a negative-going crossing

    def test_finds_negative_going_not_positive(self):
        rate = 1000
        samples = np.array([0.1, 0.0, 0.05, -0.1, 0.0, -0.2, 0.02], dtype=np.float32)
        # negative-going crossing at idx 2 (0.05→-0.1) and idx 4 (0.0→-0.2)
        result = snap_negative_zero(samples, 4, rate)
        # idx 4 is the closest negative-going crossing to idx 4 (distance 0)
        assert result == 4

    def test_no_crossing_returns_original(self):
        samples = np.array([1.0, 0.5, 0.3, 0.1, 0.0, 0.0], dtype=np.float32)
        result = snap_negative_zero(samples, 2, _RATE)
        assert result == 2

    def test_edge_returns_clamped(self):
        samples = np.array([1.0, 0.5], dtype=np.float32)
        result = snap_negative_zero(samples, 0, _RATE)
        assert result == 0


class TestRefineSplice:
    def test_moves_to_quietest_region(self):
        # Two sine tones: quiet middle, loud on either side
        n = round(100.0 / 1000.0 * _RATE)
        loud = _sine(440.0, 100.0, _RATE) * 0.5
        quiet = _sine(440.0, 100.0, _RATE) * 0.01
        samples = np.concatenate([loud[:n], quiet[:n], loud[:n]])
        # Boundary at the loud/quiet transition (idx n), refine should move into quiet
        result = refine_splice(samples, _RATE, n)
        assert result >= n  # should stay at or move into the quiet region

    def test_short_buffer_returns_original(self):
        samples = np.zeros(10, dtype=np.float32)
        result = refine_splice(samples, _RATE, 5)
        assert result == 5


class TestRawSpan:
    def test_tail_decay_ends_at_near_zero_amplitude(
        self,
    ):
        # Three-syllable word: request middle chunk (i=1, j=2), which gets a tail
        samples = _sine(220.0, 600.0, _RATE)  # full word audio
        n = len(samples)
        thirds = [n // 3, 2 * n // 3]
        onset_ends = [min(t + n // 8, n) for t in thirds]
        sw = SlicedWord(
            word="testordet",
            syllables=["test", "or", "det"],
            samples=samples,
            rate=_RATE,
            bounds=[0, *thirds, n],
            onset_ends=onset_ends,
        )
        tail_pad = int(50.0 / 1000.0 * _RATE)
        raw = raw_span(sw, 1, 2, head_pad=0, tail_pad=tail_pad)
        assert len(raw) > 0
        # The last few samples of the tail extension should be at ≈0
        tail_end = raw[-5:]
        assert np.abs(tail_end).max() < 0.001

    def test_preserves_pre_tail_region_sample_for_sample(self):
        sw = _word("testord", ["test", "ord"])
        chunk = sw.samples[sw.bounds[1] : sw.bounds[2]].copy()
        raw = raw_span(sw, 1, 2, head_pad=0, tail_pad=int(50.0 / 1000.0 * _RATE))
        pre_tail = raw[: len(chunk)]
        np.testing.assert_array_equal(pre_tail, chunk)

    def test_word_initial_chunk_no_head_pad(self):
        sw = _word("testord", ["test", "ord"])
        raw = raw_span(sw, 0, 1, head_pad=int(100.0 / 1000.0 * _RATE), tail_pad=0)
        assert raw[0] == sw.samples[0]

    def test_word_final_chunk_no_tail(self):
        sw = _word("testord", ["test", "ord"])
        raw = raw_span(sw, 1, 2, head_pad=0, tail_pad=int(100.0 / 1000.0 * _RATE))
        assert raw[-1] == sw.samples[sw.bounds[2] - 1]

    def test_tail_decay_short_span_no_error(self):
        samples = np.ones(10, dtype=np.float32)
        sw = SlicedWord(
            word="ab",
            syllables=["a", "b"],
            samples=samples,
            rate=_RATE,
            bounds=[0, 5, 10],
            onset_ends=[8],
        )
        tail_pad = int(50.0 / 1000.0 * _RATE)
        raw = raw_span(sw, 0, 1, head_pad=0, tail_pad=tail_pad)
        assert len(raw) > 0

    def test_tail_decay_n_leq_one(self):
        samples = np.array([1.0], dtype=np.float32)
        sw = SlicedWord(
            word="a",
            syllables=["a", "a"],
            samples=samples,
            rate=_RATE,
            bounds=[0, 0, 1],
        )
        tail_pad = int(50.0 / 1000.0 * _RATE)
        raw = raw_span(sw, 0, 1, head_pad=0, tail_pad=tail_pad)
        assert len(raw) == 1


class TestFade:
    def test_quiet_edge_returns_12ms(
        self,
    ):
        # A chunk starting with near-silence followed by loud signal
        n = round(50.0 / 1000.0 * _RATE)
        quiet = np.full(n, 1e-6, dtype=np.float32)
        loud = _sine(440.0, 50.0, _RATE) * 0.5
        chunk = np.concatenate([quiet, loud])
        # The head edge should be very quiet relative to full chunk
        from app.audio.slicing import _edge_fade_ms

        fade_len = _edge_fade_ms(chunk, _RATE, head=True)
        assert fade_len == pytest.approx(_FADE_MS, abs=0.1), f"expected {_FADE_MS} ms, got {fade_len}"

    def test_loud_edge_returns_40ms(
        self,
    ):
        # Full-amplitude chunk with no quiet start
        chunk = _sine(440.0, 100.0, _RATE) * 0.5
        from app.audio.slicing import _edge_fade_ms

        fade_len = _edge_fade_ms(chunk, _RATE, head=True)
        assert fade_len == pytest.approx(_MAX_FADE_MS, abs=0.1), f"expected {_MAX_FADE_MS} ms, got {fade_len}"

    def test_fade_output_same_length(self):
        chunk = _sine(440.0, 100.0, _RATE)
        result = polish(chunk.copy(), _RATE, target_rms=0.1, target_ms=_TARGET_MS, stretch=False, normalize=False)
        assert len(result) == len(chunk)

    def test_short_chunk_returns_12ms(self):
        from app.audio.slicing import _edge_fade_ms

        chunk = np.zeros(5, dtype=np.float32)
        result = _edge_fade_ms(chunk, _RATE, head=True)
        assert result == _FADE_MS

    def test_zero_length_chunk_in_fade(self):
        chunk = np.array([0.5], dtype=np.float32)
        result = polish(chunk, _RATE, target_rms=0.1, target_ms=_TARGET_MS, stretch=False, normalize=False)
        assert len(result) == 1


class TestNormalizeRms:
    def test_caps_gain_at_12_db(self):
        # Very quiet chunk: target_rms / rms would exceed +12 dB
        chunk = np.full(1000, 1e-8, dtype=np.float32)
        result = normalize_rms(chunk, target_rms=0.1)
        rms_before = float(np.sqrt((chunk**2).mean()))
        rms_after = float(np.sqrt((result**2).mean()))
        actual_gain_db = 20.0 * np.log10(rms_after / max(rms_before, 1e-12))
        assert actual_gain_db <= _MAX_GAIN_DB + 0.1, f"Gain {actual_gain_db:.1f} dB exceeds cap"

    def test_peak_limited_to_099(self):
        # Chunk at full scale that would exceed 0.99 after gain
        chunk = np.full(1000, 0.5, dtype=np.float32)
        result = normalize_rms(chunk, target_rms=0.1)
        assert np.abs(result).max() <= 0.99 + 1e-6

    def test_silent_chunk_unchanged(self):
        chunk = np.zeros(1000, dtype=np.float32)
        result = normalize_rms(chunk, target_rms=0.1)
        np.testing.assert_array_equal(result, chunk)

    def test_peak_limiting_triggers(self):
        rng = np.random.default_rng(42)
        chunk = rng.uniform(-1.0, 1.0, 1000).astype(np.float32)
        result = normalize_rms(chunk, target_rms=10.0)
        assert np.abs(result).max() <= 0.99 + 1e-6


class TestTimeStretch:
    def test_lengthens_toward_target(self):
        chunk = _sine(440.0, 100.0, _RATE)
        result = time_stretch(chunk, _RATE, _TARGET_MS)
        assert len(result) > len(chunk)

    def test_noop_above_target(self):
        # 500 ms chunk is already above 400 ms target → no change
        chunk = _sine(440.0, 500.0, _RATE)
        result = time_stretch(chunk, _RATE, _TARGET_MS)
        assert len(result) == len(chunk)
        np.testing.assert_array_equal(result, chunk)

    def test_noop_on_empty(self):
        chunk = np.array([], dtype=np.float32)
        result = time_stretch(chunk, _RATE, _TARGET_MS)
        assert len(result) == 0

    def test_ffmpeg_failure_returns_original_chunk(self):
        chunk = _sine(440.0, 50.0, _RATE)
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1, stdout=b"", stderr=b"")):
            result = time_stretch(chunk, _RATE, _TARGET_MS)
        np.testing.assert_array_equal(result, chunk)

    def test_noop_at_exact_target(self):
        chunk = _sine(440.0, _TARGET_MS, _RATE)
        result = time_stretch(chunk, _RATE, _TARGET_MS)
        assert len(result) == len(chunk)
        np.testing.assert_array_equal(result, chunk)


class TestPolish:
    def test_dc_removes_offset(self):
        chunk = np.full(1000, 0.5, dtype=np.float32)
        result = polish(chunk, _RATE, target_rms=0.1, target_ms=_TARGET_MS, stretch=False, normalize=False)
        assert abs(float(result.mean())) < 0.001

    def test_stretch_and_normalize(self):
        chunk = _sine(440.0, 100.0, _RATE)  # 100 ms
        result = polish(chunk, _RATE, target_rms=0.1, target_ms=_TARGET_MS, stretch=True, normalize=True)
        # Should be longer than input
        assert len(result) > len(chunk)
        # Should be float32
        assert result.dtype == np.float32
