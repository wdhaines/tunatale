"""Tests for per-voice loudness gain at assembly (tunatale-rag.5)."""

import numpy as np
import pytest

from app.audio.renderer import (
    _apply_voice_gain,
    _Audio,
)
from app.languages import get_tts_voice_gain_db

# ---------------------------------------------------------------------------
# Unit tests for _apply_voice_gain
# ---------------------------------------------------------------------------


class TestApplyVoiceGain:
    """The gain + clamp helper operates on _Audio buffers."""

    def test_gain_1_6_db_multiplies_by_literal(self):
        """+1.6 dB multiplies every sample by 10 ** (1.6 / 20).

        Expected factor is a LITERAL, not re-derived from the formula in the
        assertion — per the brief's measured-literal requirement.
        """
        samples = np.full((100, 1), 0.3, dtype="float32")
        audio = _Audio(samples, rate=24000)
        result = _apply_voice_gain(audio, 1.6)
        # 10 ** (1.6 / 20) = 1.2022643717...
        expected_factor = np.float32(1.2022643717)
        np.testing.assert_allclose(
            result.samples[:, 0],
            np.float32(0.3) * expected_factor,
            rtol=1e-5,
        )

    def test_zero_db_returns_bit_identical(self):
        """0.0 dB returns the same object — no copy, no mutation."""
        samples = np.array([[0.1], [-0.2], [0.3]], dtype="float32")
        audio = _Audio(samples, rate=24000)
        result = _apply_voice_gain(audio, 0.0)
        assert result is audio

    def test_unknown_voice_gets_zero_gain_is_bit_identical(self):
        """A voice absent from the table (→ 0.0 dB) returns bit-identical audio.

        Regression guard for English and every unmeasured voice.
        """
        samples = np.array([[0.5], [-0.5]], dtype="float32")
        audio = _Audio(samples, rate=24000)
        result = _apply_voice_gain(audio, 0.0)
        assert result is audio
        np.testing.assert_array_equal(result.samples, audio.samples)

    def test_clamp_engages_at_minus_1_dbfs(self):
        """Input peak −0.5 dBFS with +1.6 dB → output peak exactly −1.0 dBFS.

        10 ** (-0.5 / 20) ≈ 0.94406087 (literal input peak linear).
        Post-gain: 0.944 × 1.202 ≈ 1.135 > 0.891 → clamp to 0.891.
        """
        peak_before = np.float32(0.94406087)
        samples = np.full((100, 1), peak_before, dtype="float32")
        audio = _Audio(samples, rate=24000)
        result = _apply_voice_gain(audio, 1.6)
        # 10 ** (-1.0 / 20) ≈ 0.89125094 — literal ceiling.
        ceiling = 0.8912509381337456
        actual_peak = float(np.max(np.abs(result.samples)))
        np.testing.assert_allclose(actual_peak, ceiling, rtol=1e-5)
        # Gain was reduced: output < input × 1.20226
        assert actual_peak < float(peak_before) * 1.2022643717

    def test_clamp_never_raises(self):
        """Quiet clip with negative gain_db is not pushed toward the ceiling."""
        samples = np.full((100, 1), 0.1, dtype="float32")
        audio = _Audio(samples, rate=24000)
        result = _apply_voice_gain(audio, -2.4)
        assert float(np.max(np.abs(result.samples))) < 0.1

    def test_preserves_sample_count(self):
        """Gain changes amplitude only — never adds or removes samples."""
        rng = np.random.default_rng(42)
        samples = rng.standard_normal((500, 1)).astype("float32") * 0.5
        audio = _Audio(samples, rate=24000)
        result = _apply_voice_gain(audio, 0.6)
        assert len(result.samples) == len(samples)

    def test_preserves_sample_rate(self):
        """Sample rate is unchanged through gain application."""
        samples = np.array([[0.5]], dtype="float32")
        audio = _Audio(samples, rate=24000)
        result = _apply_voice_gain(audio, 1.6)
        assert result.rate == 24000


# ---------------------------------------------------------------------------
# Registry accessor: get_tts_voice_gain_db
# ---------------------------------------------------------------------------


class TestGetVoiceGainDb:
    """Per-voice dB gain from the language registry."""

    @pytest.mark.parametrize(
        "code,voice_id,expected",
        [
            # Norwegian — target −20.0 LUFS, measured on one sentence set.
            ("no", "nb-NO-PernilleNeural", 1.6),
            ("no", "nb-NO-IselinNeural", -0.2),
            ("no", "nb-NO-FinnNeural", 0.6),
            ("no", "en-AU-WilliamMultilingualNeural", 0.6),
            # Slovene — from rag.4's final comment, one sentence set.
            ("sl", "sl-SI-PetraNeural", -2.4),
            ("sl", "sl-SI-RokNeural", -1.2),
            ("sl", "en-US-EmmaMultilingualNeural", -1.9),
            ("sl", "de-DE-FlorianMultilingualNeural", -0.6),
        ],
    )
    def test_measured_gain_for_each_voice(self, code, voice_id, expected):
        assert get_tts_voice_gain_db(code, voice_id) == expected

    def test_unknown_voice_returns_zero(self):
        assert get_tts_voice_gain_db("no", "some-unknown-voice") == 0.0

    def test_english_narrator_returns_zero(self):
        """English / narrator: unmeasured, must default to 0.0."""
        assert get_tts_voice_gain_db("en", "en-US-GuyNeural") == 0.0

    def test_unknown_language_returns_zero(self):
        assert get_tts_voice_gain_db("zz", "some-voice") == 0.0


# ---------------------------------------------------------------------------
# Pacing integration: gain does not affect the pace_audio path
# ---------------------------------------------------------------------------


class TestPacingUnchangedByGain:
    """Pacing is identical with and without gain.

    The pace_audio site in _assemble_section_audio was left alone; gain is
    applied only to phrase_audio.  These tests prove the structural property.
    """

    def test_gain_preserves_duration(self):
        """Duration_ms is unchanged — gain multiplies amplitude, not time."""
        samples = np.full((480, 1), 0.5, dtype="float32")
        audio = _Audio(samples, rate=24000)  # 20 ms
        gained = _apply_voice_gain(audio, 1.6)
        assert gained.duration_ms == audio.duration_ms

    def test_negative_gain_preserves_duration(self):
        """Negative gain also preserves duration."""
        samples = np.full((480, 1), 0.5, dtype="float32")
        audio = _Audio(samples, rate=24000)
        gained = _apply_voice_gain(audio, -2.4)
        assert gained.duration_ms == audio.duration_ms
