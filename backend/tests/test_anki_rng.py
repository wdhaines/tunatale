"""Bit-exact regression tests for the Anki RNG port.

These pin known seed → output pairs so any drift (rand/rand_chacha changing
algorithm, our port introducing a bug) is caught.

Verification status by layer:
  - `_pcg32`: verified against `rand_core` 0.9.5 source (`lib.rs:seed_from_u64`),
    confirmed by value-breakage test: `seed_from_u64(0)` → `5029875928683246316`
    for `[u8; 8]` seed. PASS.
  - `_seed_from_u64`: derived directly from PCG32 expansion (8 calls × u32).
    PASS by construction above.
  - ChaCha12 block / `ChaCha12Rng` / `random_range_u32` / fuzz: cross-verified
    against Rust `rand 0.9.2 / rand_chacha 0.9.0` for seeds 0 and
    1775264032847 (see `test_ground_truth_regression`). PASS.

If Anki ever upgrades `rand` and changes `Uniform<u32>::sample_single` (Canon →
Lemire) or `rand_chacha::ChaCha12Rng` semantics, these tests fail and we
refresh the values + retest against Anki output.
"""

from __future__ import annotations

import pytest

from app.srs._anki_rng import (
    ChaCha12Rng,
    _chacha12_block,
    _pcg32,
    _seed_from_u64,
    random_range_u32,
)


class TestPcg32:
    """PCG32 (xsh rr) is the seed-stretching in `rand_core::SeedableRng::seed_from_u64`."""

    def test_known_first_outputs_seed_zero(self):
        """PCG32 from seed=0 produces well-known outputs (rand_core 0.9.x)."""
        state, v0 = _pcg32(0)
        state, v1 = _pcg32(state)
        state, v2 = _pcg32(state)
        state, v3 = _pcg32(state)
        assert v0 == 0xF973F2EC
        assert v1 == 0x45CDB581
        assert v2 == 0x7346F087
        assert v3 == 0xAD6CAD06

    def test_seed_from_u64_value_breakage_8byte_seed(self):
        """Reproduce rand_core's own value-breakage test: seed_from_u64(0) for [u8; 8] seed
        must equal 5029875928683246316."""
        state, v0 = _pcg32(0)
        state, v1 = _pcg32(state)
        result = v0 | (v1 << 32)
        assert result == 5029875928683246316


class TestSeedFromU64:
    """`seed_from_u64` expands a u64 seed into a 32-byte key for ChaCha."""

    def test_seed_zero_produces_known_key_bytes(self):
        """8 PCG32 outputs for seed=0 concatenated little-endian = 32-byte key."""
        key = _seed_from_u64(0)
        assert len(key) == 32
        assert key.hex().upper() == ("ECF273F981B5CD4587F0467306AD6CADD0D0A3E33317E767F29BEA72D78A7DFE")


class TestChaCha12Block:
    """The ChaCha12 block function — the core of `ChaCha12Rng`'s keystream."""

    def test_zero_key_zero_nonce_counter_zero(self):
        """ChaCha12 with all-zero key and nonce, counter=0 → first u32 pinned."""
        block = _chacha12_block(b"\x00" * 32, 0, b"\x00" * 12)
        assert len(block) == 64
        first_u32 = int.from_bytes(block[:4], "little")
        # This is unchanged from the SplitMix64 era — ChaCha12 itself is correct.
        assert first_u32 == 0x6A9AF49B


class TestChaCha12Rng:
    """End-to-end: same RNG path as `StdRng::seed_from_u64`."""

    def test_seed_zero_first_u32_outputs(self):
        """First few u32 outputs from ChaCha12Rng seeded with u64 = 0."""
        rng = ChaCha12Rng(0)
        # Cross-verified against Rust rand 0.9.2 / rand_chacha 0.9.0.
        assert rng.next_u32() == 0xCD2C6F7F
        assert rng.next_u32() == 0xBB2A3FB2
        assert rng.next_u32() == 0x8E27697B
        assert rng.next_u32() == 0xC6017C94

    def test_consumes_buffer_in_4_byte_chunks(self):
        """16 successive next_u32 calls span exactly one block (64 bytes)."""
        rng = ChaCha12Rng(42)
        first = [rng.next_u32() for _ in range(16)]
        # Should not have refilled twice — counter should still be 1.
        assert rng._counter == 1  # noqa: SLF001
        # The 17th call triggers a refill.
        rng.next_u32()
        assert rng._counter == 2  # noqa: SLF001
        # Sanity: outputs are non-trivially distinct.
        assert len(set(first)) >= 12


class TestRandomRangeU32:
    """`Uniform<u32>::sample_single` via Canon's biased method."""

    def test_known_outputs_seed_zero_step_60(self):
        """For seed=0 and a 60-second learning step, fuzz upper=15 → range [0, 15)."""
        rng = ChaCha12Rng(0)
        v = random_range_u32(rng, 0, 15)
        # Cross-verified against Rust rand 0.9.2 / rand_chacha 0.9.0.
        assert v == 12

    def test_range_size_one_returns_low(self):
        """range_size=1 → wmul puts full value in lo_order, result=0 → low."""
        rng = ChaCha12Rng(0)
        v = random_range_u32(rng, 100, 101)
        assert v == 100

    def test_empty_range_raises(self):
        rng = ChaCha12Rng(0)
        with pytest.raises(ValueError):
            random_range_u32(rng, 5, 5)
        with pytest.raises(ValueError):
            random_range_u32(rng, 10, 5)

    def test_canons_resample_branch_runs_for_wide_range(self):
        """Canon's resample without overflow. seed=2 + range=2^31+1."""
        rng = ChaCha12Rng(2)
        v = random_range_u32(rng, 0, 0x80000001)
        assert 0 <= v < 0x80000001

    def test_canons_resample_with_is_overflow_true(self):
        """Cover the overflow-add path inside Canon's correction (`result + 1`).
        seed=12 + range=2^31+1 produces lo_order + new_hi_order > 2^32 - 1.
        """
        rng = ChaCha12Rng(12)
        v = random_range_u32(rng, 0, 0x80000001)
        assert 0 <= v < 0x80000001


class TestLearningStepFuzzBitExact:
    """The user-facing function in `fsrs.py` — bit-exact via the port above."""

    def test_known_seed_known_fuzz_60s_step(self):
        """For (anki_card_id=0, reps=0, step=60), seed=0 → fuzz=+12s."""
        from app.srs.fsrs import _learning_step_fuzz_seconds

        assert _learning_step_fuzz_seconds(0, 0, 60) == 60 + 12

    def test_seed_combines_card_id_and_reps(self):
        """seed = (card_id + reps) — same value across (50, 5) and (52, 3)."""
        from app.srs.fsrs import _learning_step_fuzz_seconds

        a = _learning_step_fuzz_seconds(50, 5, 60)
        b = _learning_step_fuzz_seconds(52, 3, 60)
        assert a == b, f"identical seed (card_id+reps=55) must produce identical fuzz; got {a} vs {b}"

    def test_no_fuzz_when_step_too_small(self):
        """For step < 4s, upper_offset = floor(0.25 * step) = 0 → no fuzz applied."""
        from app.srs.fsrs import _learning_step_fuzz_seconds

        assert _learning_step_fuzz_seconds(123, 0, 3) == 3
        assert _learning_step_fuzz_seconds(123, 0, 1) == 1

    def test_fuzz_caps_at_300s_for_long_steps(self):
        """For step ≥ 1200s, upper_offset is capped at 300."""
        from app.srs.fsrs import _learning_step_fuzz_seconds

        # step=2000 → 0.25*2000=500, capped at 300 → range [2000, 2300).
        v = _learning_step_fuzz_seconds(0, 0, 2000)
        assert 2000 <= v < 2300


class TestGroundTruthRegression:
    """Cross-verified seed→output pairs captured from Rust `rand 0.9.2 / rand_chacha 0.9.0`.

    These are the ground-truth references for the entire RNG chain, not just
    our own port's output.  If any of these fail, either our port diverged from
    Rust's `StdRng::seed_from_u64` or `rand` upgraded its algorithm.
    """

    @pytest.mark.parametrize(
        ("seed", "expected_u32_0", "expected_u32_1", "expected_fuzz_60s"),
        [
            # fmt: off
            (0, 0xCD2C6F7F, 0xBB2A3FB2, 12),
            (1, 0xD3301861, 0xF9681A64, 12),
            (1775264032847, 0xC1B92DED, 0x1B785743, 11),
            (0xFFFFFFFFFFFFFFFF, 0x2E3D5FB8, 0x0FA79848, 2),
            # fmt: on
        ],
    )
    def test_known_seeds(
        self,
        seed: int,
        expected_u32_0: int,
        expected_u32_1: int,
        expected_fuzz_60s: int,
    ):
        rng = ChaCha12Rng(seed)
        assert rng.next_u32() == expected_u32_0, f"seed={seed}, first u32"
        assert rng.next_u32() == expected_u32_1, f"seed={seed}, second u32"
        # Also verify the learning-step fuzz for a 60-second step (range [0, 15)).
        rng2 = ChaCha12Rng(seed)
        assert random_range_u32(rng2, 0, 15) == expected_fuzz_60s, f"seed={seed}, fuzz for [0, 15)"
