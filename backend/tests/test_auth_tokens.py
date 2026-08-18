"""Tests for app.auth.tokens — session-token minting and SHA-256 digest."""

from __future__ import annotations

from app.auth.tokens import hash_token, mint_token


class TestMintToken:
    def test_two_calls_differ(self) -> None:
        t1 = mint_token()
        t2 = mint_token()
        assert t1 != t2

    def test_url_safe(self) -> None:
        import string

        token = mint_token()
        allowed = set(string.ascii_letters + string.digits + "-_.~")
        assert all(c in allowed for c in token)

    def test_min_length(self) -> None:
        token = mint_token()
        assert len(token) >= 43


class TestHashToken:
    def test_deterministic(self) -> None:
        assert hash_token("abc") == hash_token("abc")

    def test_returns_64_hex_chars(self) -> None:
        h = hash_token("test")
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_tokens_hash_differently(self) -> None:
        assert hash_token("a") != hash_token("b")
