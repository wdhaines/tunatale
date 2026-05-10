"""Bit-exact regression tests for the Anki RNG port.

These pin known seed → output pairs so any drift (rand/rand_chacha changing
algorithm, our port introducing a bug) is caught.

Verification status by layer:
  - SplitMix64: `state=0 → z0=0xE220A8397B1DCDAF, z1=0x6E789E6AA1B965F4` are the
    canonical reference outputs (well-documented in the SplitMix64 paper / ref
    impl). PASS.
  - `seed_from_u64`: derived directly from SplitMix64. PASS by construction.
  - ChaCha12 block / `ChaCha12Rng` / `random_range_u32` / fuzz: regression-pinned
    against this port's own output. NOT YET cross-verified against Rust's
    `rand 0.9.2 / rand_chacha 0.9.0`. The user is expected to validate by
    grading a card in lockstep with Anki and confirming `due_at` matches. If a
    mismatch surfaces, refresh the baselines after pinning the right Rust value.

If Anki ever upgrades `rand` and changes `Uniform<u32>::sample_single` (Canon →
Lemire) or `rand_chacha::ChaCha12Rng` semantics, these tests fail and we
refresh the values + retest against Anki output.
"""

from __future__ import annotations

import pytest

from app.srs._anki_rng import (
    ChaCha12Rng,
    _chacha12_block,
    _seed_from_u64,
    _split_mix_64,
    random_range_u32,
)


class TestSplitMix64:
    """SplitMix64 is the seed-stretching helper in `rand_core::SeedableRng::seed_from_u64`."""

    def test_known_first_outputs_seed_zero(self):
        """SplitMix64 from state=0 produces a fixed sequence (well-known constants)."""
        # Reference: standard SplitMix64 reference implementation, seed = 0.
        state, z0 = _split_mix_64(0)
        state, z1 = _split_mix_64(state)
        state, z2 = _split_mix_64(state)
        assert z0 == 0xE220A8397B1DCDAF
        assert z1 == 0x6E789E6AA1B965F4
        assert z2 == 0x06C45D188009454F


class TestSeedFromU64:
    """`seed_from_u64` expands a u64 seed into a 32-byte key for ChaCha."""

    def test_seed_zero_produces_known_key_bytes(self):
        """First 32 bytes of SplitMix64(0..3) concatenated little-endian."""
        key = _seed_from_u64(0)
        assert len(key) == 32
        # z0 = 0xE220A8397B1DCDAF -> LE bytes start with AF CD 1D 7B 39 A8 20 E2
        assert key[:8] == bytes.fromhex("AFCD1D7B39A820E2")
        assert key[8:16] == bytes.fromhex("F465B9A16A9E786E")
        assert key.hex().upper() == ("AFCD1D7B39A820E2F465B9A16A9E786E4F450980185DC406EC814C72A8B88BF8")


class TestChaCha12Block:
    """The ChaCha12 block function — the core of `ChaCha12Rng`'s keystream."""

    def test_zero_key_zero_nonce_counter_zero(self):
        """ChaCha12 with all-zero key and nonce, counter=0 → first u32 pinned."""
        block = _chacha12_block(b"\x00" * 32, 0, b"\x00" * 12)
        assert len(block) == 64
        first_u32 = int.from_bytes(block[:4], "little")
        # Regression-pinned (port self-consistency); cross-verify vs Rust if drift suspected.
        assert first_u32 == 0x6A9AF49B


class TestChaCha12Rng:
    """End-to-end: same RNG path as `StdRng::seed_from_u64`."""

    def test_seed_zero_first_u32_outputs(self):
        """First few u32 outputs from ChaCha12Rng seeded with u64 = 0."""
        rng = ChaCha12Rng(0)
        # Regression-pinned (port self-consistency); cross-verify vs Rust if drift suspected.
        assert rng.next_u32() == 0x82B67BCA
        assert rng.next_u32() == 0xD18C9D7B
        assert rng.next_u32() == 0xDD8C2EB1
        assert rng.next_u32() == 0x73F1688A

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
        # Regression-pinned (port self-consistency); cross-verify vs Rust if drift suspected.
        assert v == 7

    def test_range_size_one_returns_low_no_rng_consumed(self):
        """range_size=1 → return low directly, with one rng consumption."""
        rng = ChaCha12Rng(0)
        v = random_range_u32(rng, 100, 101)
        # Canon's branch on range_size==0 (after wrapping_sub of identical values
        # plus +1) returns rng.next_u32(); but high-low=1 → range_size=1, valid.
        # We multiply rng.next_u32() * 1 (= rng output), which is huge, then add low.
        # Expected: low + rng.next_u32() mod 2^32; but the wmul puts the full value
        # in lo_order, so result=0 always for range=1. So we always return `low`.
        assert v == 100

    def test_empty_range_raises(self):
        rng = ChaCha12Rng(0)
        with pytest.raises(ValueError):
            random_range_u32(rng, 5, 5)
        with pytest.raises(ValueError):
            random_range_u32(rng, 10, 5)

    def test_canons_resample_branch_runs_for_wide_range(self):
        """Canon's correction step (`if lo_order > range.wrapping_neg()`) is rare
        for the small ranges TT uses (≤ 300), but trips for wide ranges. seed=0 +
        range=2^31+1 hits it, exercising the second `next_u32()` call without overflow.
        """
        rng = ChaCha12Rng(0)
        v = random_range_u32(rng, 0, 0x80000001)
        assert 0 <= v < 0x80000001

    def test_canons_resample_with_is_overflow_true(self):
        """Cover the overflow-add path inside Canon's correction (`result + 1`).
        seed=2 + range=2^31+1 produces lo_order + new_hi_order > 2^32 - 1.
        """
        rng = ChaCha12Rng(2)
        v = random_range_u32(rng, 0, 0x80000001)
        assert 0 <= v < 0x80000001


class TestLearningStepFuzzBitExact:
    """The user-facing function in `fsrs.py` — bit-exact via the port above."""

    def test_known_seed_known_fuzz_60s_step(self):
        """For (anki_card_id=0, reps=0, step=60), seed=0 → fuzz=+7s (port baseline)."""
        from app.srs.fsrs import _learning_step_fuzz_seconds

        assert _learning_step_fuzz_seconds(0, 0, 60) == 60 + 7

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
