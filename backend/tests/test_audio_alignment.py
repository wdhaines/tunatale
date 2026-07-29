"""Tests for CTC forced alignment (core, model-free).

Every test here runs on synthetic emission matrices, so the whole module is
exercised without torch, transformers, or a 1.2 GB model download. The pieces
that genuinely need the model live in the language plugin's thin adapter.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.audio.alignment import (
    MODEL_SAMPLE_RATE,
    ctc_align,
    derive_syllable_bounds,
    resample_to_model_rate,
    trim_silence,
)

# Toy vocabulary for the synthetic emission matrices: 0 = blank, 1 = "a", 2 = "b".
_BLANK, _A, _B = 0, 1, 2


def _emissions(frames: list[int], vocab_size: int = 3, confident: float = -0.01) -> np.ndarray:
    """Log-probs that put nearly all mass on ``frames[t]`` at each frame t."""
    log_probs = np.full((len(frames), vocab_size), -20.0, dtype=np.float64)
    for t, tok in enumerate(frames):
        log_probs[t, tok] = confident
    return log_probs


class TestCtcAlign:
    def test_assigns_each_token_the_frame_it_dominates(self):
        spans = ctc_align(_emissions([_A, _B]), [_A, _B], _BLANK)
        assert spans == [(0, 1), (1, 2)]

    def test_token_held_over_several_frames_gets_the_whole_run(self):
        spans = ctc_align(_emissions([_A, _A, _A, _B]), [_A, _B], _BLANK)
        assert spans == [(0, 3), (3, 4)]

    def test_geminate_halves_are_separated_by_a_blank(self):
        """The reason for the blank-interleaved form rather than the compact one.

        Bokmål is full of geminates (``hadde``, ``mannen``, ``snudde``, ``sett``)
        and the syllable boundary is exactly the doubled letter. The compact
        two-transition CTC form lets two identical adjacent tokens occupy
        adjacent frames, which collapses that boundary; the extended form
        requires a blank between equal neighbours, so the halves get genuinely
        separate frame spans.
        """
        spans = ctc_align(_emissions([_A, _BLANK, _A]), [_A, _A], _BLANK)
        assert spans == [(0, 1), (2, 3)]
        first_end, second_start = spans[0][1], spans[1][0]
        assert second_start > first_end, "geminate halves must not touch"

    def test_distinct_neighbours_may_touch(self):
        """The blank is only mandatory between EQUAL tokens — 'ab' needs none."""
        spans = ctc_align(_emissions([_A, _B]), [_A, _B], _BLANK)
        assert spans[0][1] == spans[1][0]

    def test_squeezed_out_token_falls_back_to_previous_end(self):
        """More tokens than frames: keep boundaries monotonic instead of raising."""
        spans = ctc_align(_emissions([_A]), [_A, _B, _A], _BLANK)
        assert len(spans) == 3
        ends = [e for _, e in spans]
        starts = [s for s, _ in spans]
        assert starts == sorted(starts)
        assert ends == sorted(ends)

    def test_single_token(self):
        assert ctc_align(_emissions([_A, _A]), [_A], _BLANK) == [(0, 2)]

    def test_all_frames_blank_still_returns_one_span_per_token(self):
        spans = ctc_align(_emissions([_BLANK, _BLANK, _BLANK]), [_A, _B], _BLANK)
        assert len(spans) == 2


class TestDeriveSyllableBounds:
    def _spans(self, per_char: list[tuple[int, int]]) -> list[tuple[int, int]]:
        return per_char

    def test_cuts_where_the_next_syllables_first_char_starts(self):
        # "haden" as ha|den, five chars, one frame each.
        char_spans = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)]
        result = derive_syllable_bounds(
            char_spans, n_frames=5, n_samples=500, syllables=["ha", "den"], vowels=frozenset("ae")
        )
        assert result is not None
        bounds, onset_ends = result
        # char index 2 ('d') starts at frame 2 → sample 200.
        assert bounds == [0, 200, 500]
        # The vowel of "den" is 'e' at char index 3 → frame 3 → sample 300.
        assert onset_ends == [300]

    def test_onset_end_uses_the_next_vowel_not_the_consonant_end(self):
        """CTC is peaky: a consonant's span END underestimates its real extent.

        Only the following vowel's span START is a trustworthy landmark, so a
        long onset consonant must not shorten the tail ceiling.
        """
        # "asba" as as|ba; 'b' is confidently detected for one frame only.
        char_spans = [(0, 1), (1, 2), (2, 3), (7, 8)]
        result = derive_syllable_bounds(
            char_spans, n_frames=10, n_samples=1000, syllables=["as", "ba"], vowels=frozenset("a")
        )
        assert result is not None
        _bounds, onset_ends = result
        # Vowel 'a' starts at frame 7 → 700, NOT the end of 'b' (frame 3 → 300).
        assert onset_ends == [700]

    def test_syllable_without_a_vowel_falls_back_to_its_end(self):
        char_spans = [(0, 1), (1, 2), (2, 4)]
        result = derive_syllable_bounds(
            char_spans, n_frames=4, n_samples=400, syllables=["a", "st"], vowels=frozenset("a")
        )
        assert result is not None
        _bounds, onset_ends = result
        assert onset_ends == [400]

    def test_returns_none_when_frames_are_degenerate(self):
        assert (
            derive_syllable_bounds([(0, 1)], n_frames=1, n_samples=100, syllables=["a", "b"], vowels=frozenset("a"))
            is None
        )

    def test_returns_none_when_syllables_overrun_char_spans(self):
        """Non-lossless syllabification must not raise IndexError."""
        char_spans = [(0, 1), (1, 2), (2, 3)]
        result = derive_syllable_bounds(
            char_spans, n_frames=3, n_samples=300, syllables=["ab", "cd"], vowels=frozenset("ae")
        )
        assert result is None

    def test_returns_none_when_syllables_undershoot_char_spans(self):
        """Silent wrong-audio is worse than a crash; undershoot must also None."""
        char_spans = [(0, 1), (1, 2), (2, 3)]
        result = derive_syllable_bounds(
            char_spans, n_frames=3, n_samples=300, syllables=["a", "b"], vowels=frozenset("ae")
        )
        assert result is None

    def test_returns_none_when_cuts_collapse(self):
        """Two boundaries landing on the same sample cannot both be honoured."""
        char_spans = [(0, 1), (1, 1), (1, 1), (1, 2)]
        result = derive_syllable_bounds(
            char_spans, n_frames=2, n_samples=100, syllables=["a", "b", "c", "d"], vowels=frozenset("a")
        )
        assert result is None

    def test_cuts_are_clamped_inside_the_buffer(self):
        char_spans = [(0, 1), (0, 1), (9, 10)]
        result = derive_syllable_bounds(
            char_spans, n_frames=10, n_samples=100, syllables=["ab", "c"], vowels=frozenset("a")
        )
        assert result is not None
        bounds, _ = result
        assert bounds[0] == 0
        assert bounds[-1] == 100
        assert all(0 < b < 100 for b in bounds[1:-1])


class TestResampleToModelRate:
    def test_passthrough_when_already_at_model_rate(self):
        samples = np.sin(np.linspace(0, 40, MODEL_SAMPLE_RATE, dtype=np.float32))
        out = resample_to_model_rate(samples, MODEL_SAMPLE_RATE)
        assert out is samples

    def test_downsamples_24k_to_16k(self):
        rate = 24_000
        t = np.arange(rate, dtype=np.float32) / rate
        samples = (0.5 * np.sin(2 * np.pi * 220.0 * t)).astype(np.float32)
        out = resample_to_model_rate(samples, rate)
        assert out.ndim == 1
        assert abs(len(out) - MODEL_SAMPLE_RATE) < MODEL_SAMPLE_RATE * 0.02
        # Still a signal, not silence — the resample did not zero the buffer.
        assert float(np.sqrt((out**2).mean())) > 0.1

    def test_raises_when_ffmpeg_fails(self, monkeypatch):
        import subprocess

        class _Failed:
            returncode = 1
            stderr = b"boom"
            stdout = b""

        monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Failed())
        with pytest.raises(RuntimeError, match="ffmpeg resample failed"):
            resample_to_model_rate(np.zeros(100, dtype=np.float32), 24_000)


class TestTrimSilence:
    def test_drops_leading_and_trailing_silence(self):
        rate = 16_000
        tone = np.sin(np.linspace(0, 200, rate // 2, dtype=np.float32)).astype(np.float32)
        padded = np.concatenate([np.zeros(rate // 4, dtype=np.float32), tone, np.zeros(rate // 4, dtype=np.float32)])
        out = trim_silence(padded, rate)
        assert len(out) < len(padded)
        assert len(out) >= len(tone) * 0.9

    def test_returns_input_when_shorter_than_one_window(self):
        samples = np.zeros(3, dtype=np.float32)
        assert trim_silence(samples, 16_000) is samples

    def test_digital_silence_trims_to_itself(self):
        """The floor is relative to the loudest frame, so nothing is ever fully
        trimmed away — a uniformly silent buffer comes back whole rather than
        empty, which is what keeps a downstream ``len()`` division safe."""
        samples = np.zeros(16_000, dtype=np.float32)
        out = trim_silence(samples, 16_000)
        assert len(out) == len(samples)
