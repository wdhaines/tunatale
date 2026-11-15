"""Tests for app.auth.passwords — argon2 hashing, verification, and rehash detection."""

from __future__ import annotations

import pytest
from argon2 import PasswordHasher, extract_parameters

from app.auth.passwords import MINIMAL_PARAMETERS, hash_password, needs_rehash, verify_password
from app.config import settings


@pytest.fixture
def default_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production's argon2 cost, over the suite-wide ``minimal`` pin in conftest."""
    monkeypatch.setattr(settings, "password_hash_cost", "default")


class TestHashAndVerify:
    def test_round_trip(self) -> None:
        hashed = hash_password("s3cret")
        assert verify_password(hashed, "s3cret") is True

    def test_wrong_password(self) -> None:
        hashed = hash_password("s3cret")
        assert verify_password(hashed, "other") is False

    def test_different_hashes_per_call(self) -> None:
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2
        assert verify_password(h1, "same") is True
        assert verify_password(h2, "same") is True

    def test_verify_empty_string(self) -> None:
        assert verify_password("", "anything") is False

    def test_verify_not_a_hash(self) -> None:
        assert verify_password("not-a-hash", "anything") is False

    def test_verify_corrupt_stored_hash(self) -> None:
        """A garbled stored hash returns False rather than raising into a login.

        Built by clobbering a live hash's last digest character rather than
        pasting a PHC literal. Two reasons, in order: a hardcoded
        ``$argon2id$…`` constant is what secret scanners flag — GitGuardian's
        "Generic Password" detector tripped on the previous form of this line,
        a false positive with nothing to revoke but a real cost in noise — and
        corrupting a real hash models the failure that actually happens (a DB
        value truncated or garbled in storage) instead of a synthetic string.

        The flip is deterministic, not random: picking a replacement that
        differs from the original character means this can never accidentally
        reconstruct a verifying hash.
        """
        real = hash_password("real")
        corrupt = real[:-1] + ("A" if real[-1] != "A" else "B")
        assert corrupt != real
        assert verify_password(corrupt, "real") is False

    def test_verify_empty_password(self) -> None:
        hashed = hash_password("real")
        assert verify_password(hashed, "") is False


class TestNeedsRehash:
    def test_fresh_hash_needs_no_rehash(self) -> None:
        hashed = hash_password("check")
        assert needs_rehash(hashed) is False

    def test_weak_hash_needs_rehash(self, default_cost: None) -> None:
        weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
        legacy = weak.hash("check")
        assert needs_rehash(legacy) is True


class TestCostProfile:
    """``settings.password_hash_cost``: production hashes at argon2-cffi's defaults;
    the test suite pins ``minimal`` (conftest) because the throttling and session
    tests assert route behaviour, not hash strength, and paid ~0.13 s CPU per login.
    """

    def test_the_suite_runs_at_minimal_cost(self) -> None:
        """The conftest pin itself: without it every login in the suite costs real argon2."""
        assert settings.password_hash_cost == "minimal"

    def test_default_cost_hashes_with_the_library_defaults(self, default_cost: None) -> None:
        params = extract_parameters(hash_password("pw"))
        defaults = PasswordHasher()
        assert (params.time_cost, params.memory_cost, params.parallelism) == (
            defaults.time_cost,
            defaults.memory_cost,
            defaults.parallelism,
        )

    def test_minimal_cost_hashes_with_the_minimal_parameters(self) -> None:
        params = extract_parameters(hash_password("pw"))
        assert (params.time_cost, params.memory_cost, params.parallelism) == MINIMAL_PARAMETERS

    def test_a_minimal_hash_is_rehashed_under_the_default_cost(self, default_cost: None) -> None:
        """A minimal-cost hash that ever reached a default-cost process upgrades on the next login."""
        cheap = PasswordHasher(*MINIMAL_PARAMETERS).hash("pw")
        assert needs_rehash(cheap) is True
        assert verify_password(cheap, "pw") is True

    def test_the_profile_is_read_per_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cheap = hash_password("pw")
        monkeypatch.setattr(settings, "password_hash_cost", "default")
        assert extract_parameters(hash_password("pw")).memory_cost == PasswordHasher().memory_cost
        assert needs_rehash(cheap) is True
