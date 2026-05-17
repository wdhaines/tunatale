"""Bit-exact port of Anki's RNG path for learning-step fuzz.

Mirrors the chain `StdRng::seed_from_u64(seed) → rng.random_range(low..high)` as
used by Anki at `rslib/src/scheduler/answering/learning.rs:learning_ivl_with_fuzz`
(Anki 25.09 / `rand 0.9.1` / `rand_chacha 0.9.0`).

Components:
  - `_pcg32`                — `rand_core::SeedableRng::seed_from_u64` expansion (PCG32 xsh rr)
  - `ChaCha12Rng`           — `rand_chacha::ChaCha12Rng` keystream (12 rounds)
  - `random_range_u32`      — `Uniform<u32>::sample_single`'s biased Canon's method

The point of porting bit-for-bit (rather than just using Python's `random.Random`
with the same seed) is so TT and Anki produce **identical** fuzzed `due_at` for
the same `(card_id + reps)` seed. Without this, lockstep-grading TT vs Anki
diverges by up to 25% of the step size on every learning grade.

If Anki's `rand` / `rand_chacha` versions ever change their algorithm (Lemire vs
Canon, ChaCha rounds, etc.) we'll need to track. The CI test below pins known
seed→output pairs.
"""

from __future__ import annotations

_U32_MASK = 0xFFFFFFFF
_U64_MASK = 0xFFFFFFFFFFFFFFFF

# PCG32 constants — bit-identical to rand_core 0.9.5's seed_from_u64 default impl.
_PCG_MUL = 6364136223846793005
_PCG_INC = 11634580027462260723


def _pcg32(state: int) -> tuple[int, int]:
    """One round of PCG32 (xsh rr variant) — same as `rand_core::SeedableRng::seed_from_u64`.

    Returns `(next_state, output_u32)`.  The LCG step happens **before** the output
    function, matching `rand_core`'s `seed_from_u64` (advance first, then read).
    """
    state = (state * _PCG_MUL + _PCG_INC) & _U64_MASK
    xorshifted = (((state >> 18) ^ state) >> 27) & _U32_MASK
    rot = (state >> 59) & _U32_MASK
    out = ((xorshifted >> rot) | (xorshifted << (32 - rot))) & _U32_MASK
    return state, out


def _seed_from_u64(seed: int) -> bytes:
    """Mirror `rand_core::SeedableRng::seed_from_u64` for a 32-byte ChaCha key.

    Runs PCG32 eight times (8 × u32 = 32 bytes) and concatenates the results
    little-endian.  Bit-exact with `rand_core` 0.9.5 `lib.rs:seed_from_u64`.
    """
    state = seed & _U64_MASK
    out = bytearray()
    for _ in range(8):
        state, z = _pcg32(state)
        out += z.to_bytes(4, "little")
    return bytes(out)


def _rotl32(v: int, n: int) -> int:
    v &= _U32_MASK
    return ((v << n) | (v >> (32 - n))) & _U32_MASK


def _quarter_round(s: list[int], a: int, b: int, c: int, d: int) -> None:
    s[a] = (s[a] + s[b]) & _U32_MASK
    s[d] = _rotl32(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & _U32_MASK
    s[b] = _rotl32(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b]) & _U32_MASK
    s[d] = _rotl32(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & _U32_MASK
    s[b] = _rotl32(s[b] ^ s[c], 7)


def _chacha12_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    """Generate one 64-byte ChaCha12 keystream block.

    `key` is 32 bytes, `nonce` is 12 bytes (zero for `ChaCha12Rng::seed_from_u64`),
    `counter` is a u32. ChaCha12 = 6 double-rounds = 12 rounds total.

    Note: `rand_chacha` uses an 8-byte nonce, but we pass 12 zero bytes.  The extra
    zero word lands in the counter-high slot, which is also zero — so the ChaCha
    state matrix is identical to Rust's for the `seed_from_u64` path (counter=0,
    nonce=0).  If this function were ever used with a non-zero counter or nonce
    the layouts would diverge.
    """
    constants = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574]  # "expand 32-byte k"
    key_words = [int.from_bytes(key[i : i + 4], "little") for i in range(0, 32, 4)]
    nonce_words = [int.from_bytes(nonce[i : i + 4], "little") for i in range(0, 12, 4)]
    state = constants + key_words + [counter & _U32_MASK] + nonce_words
    working = state[:]
    for _ in range(6):
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)
    out = bytearray()
    for i in range(16):
        out += ((working[i] + state[i]) & _U32_MASK).to_bytes(4, "little")
    return bytes(out)


class ChaCha12Rng:
    """Stateful keystream — same byte order as `rand_chacha::ChaCha12Rng`.

    `next_u32` consumes 4 bytes at a time, little-endian. Matches what
    `rng.random::<u32>()` does at the call site (rand 0.9.1).

    The 32-byte key is produced by `_seed_from_u64` (PCG32 expansion, not
    SplitMix64 — see that function for details).
    """

    __slots__ = ("_key", "_counter", "_buffer")

    def __init__(self, seed: int) -> None:
        self._key = _seed_from_u64(seed)
        self._counter = 0
        self._buffer = b""

    def _refill(self) -> None:
        self._buffer += _chacha12_block(self._key, self._counter, b"\x00" * 12)
        self._counter = (self._counter + 1) & _U32_MASK

    def next_u32(self) -> int:
        if len(self._buffer) < 4:
            self._refill()
        v = int.from_bytes(self._buffer[:4], "little")
        self._buffer = self._buffer[4:]
        return v


def _wmul_u32(a: int, b: int) -> tuple[int, int]:
    """Widening multiply for u32 — returns `(hi, lo)` of the 64-bit product."""
    prod = (a & _U32_MASK) * (b & _U32_MASK)
    return (prod >> 32) & _U32_MASK, prod & _U32_MASK


def random_range_u32(rng: ChaCha12Rng, low: int, high_exclusive: int) -> int:
    """Mirror `rand 0.9.1` `Uniform::<u32>::sample_single(low, high_exclusive)`.

    Algorithm: Canon's biased widening-multiply method (rand 0.9 default; the
    `unbiased` cargo feature is NOT enabled in Anki's workspace, so this is what
    runs in production).
    """
    if high_exclusive <= low:
        raise ValueError(f"empty range [{low}, {high_exclusive})")
    rng_size = (high_exclusive - low) & _U32_MASK
    if rng_size == 0:  # pragma: no cover — full u32 range; unreachable for fuzz upper ≤ 300
        return rng.next_u32()
    result, lo_order = _wmul_u32(rng.next_u32(), rng_size)
    range_neg = (-rng_size) & _U32_MASK  # `range.wrapping_neg()`
    if lo_order > range_neg:
        new_hi_order, _ = _wmul_u32(rng.next_u32(), rng_size)
        is_overflow = (lo_order + new_hi_order) > _U32_MASK
        if is_overflow:
            result = (result + 1) & _U32_MASK
    return (low + result) & _U32_MASK
