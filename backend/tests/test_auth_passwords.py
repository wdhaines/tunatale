"""Tests for app.auth.passwords — argon2 hashing, verification, and rehash detection."""

from __future__ import annotations

from argon2 import PasswordHasher

from app.auth.passwords import hash_password, needs_rehash, verify_password


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

    def test_verify_malformed_argon2(self) -> None:
        assert verify_password("$argon2id$v=19$m=1,t=1,p=1$AAAA$AAAA", "anything") is False

    def test_verify_empty_password(self) -> None:
        hashed = hash_password("real")
        assert verify_password(hashed, "") is False


class TestNeedsRehash:
    def test_fresh_hash_needs_no_rehash(self) -> None:
        hashed = hash_password("check")
        assert needs_rehash(hashed) is False

    def test_weak_hash_needs_rehash(self) -> None:
        weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
        legacy = weak.hash("check")
        assert needs_rehash(legacy) is True
