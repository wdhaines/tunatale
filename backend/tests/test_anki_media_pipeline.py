"""Tests for S3.8/S3.8+: media pipeline orchestration and image deduplication."""

from __future__ import annotations

from app.anki.media.pipeline import MediaResult, fetch_card_media

# ── Fakes ──────────────────────────────────────────────────────────────────────

_IMG_URL = "https://cdn.pixabay.com/photo/tree.jpg"


def _make_fakes(
    *,
    forvo_returns: bytes | None = None,
    tts_returns: bytes | None = None,
    pixabay_returns: tuple[bytes, str, str] | None = None,
    normalize_returns: bytes | None = None,
):
    """Return (forvo_fn, tts_fn, pixabay_fn, normalize_fn) configured fakes."""

    def fake_forvo(word, *, http_client=None):
        return forvo_returns

    async def fake_tts(text, *, voice=None):
        return tts_returns

    def fake_pixabay(english, *, api_key, http_client=None, used_urls=frozenset()):
        return pixabay_returns

    def fake_normalize(src_bytes, *, target_lufs=-23.0):
        return normalize_returns if normalize_returns is not None else src_bytes + b"_norm"

    return fake_forvo, fake_tts, fake_pixabay, fake_normalize


# ── TestFetchCardMedia ─────────────────────────────────────────────────────────


class TestFetchCardMedia:
    async def test_uses_forvo_when_available(self):
        forvo_fn, tts_fn, pix_fn, norm_fn = _make_fakes(forvo_returns=b"forvo_mp3")

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=pix_fn,
            _normalize_fn=norm_fn,
        )
        assert result.audio_source == "forvo"
        assert b"forvo_mp3" in result.audio_bytes

    async def test_falls_back_to_tts_when_forvo_returns_none(self):
        forvo_fn, tts_fn, pix_fn, norm_fn = _make_fakes(forvo_returns=None, tts_returns=b"tts_mp3")

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=pix_fn,
            _normalize_fn=norm_fn,
        )
        assert result.audio_source == "tts"
        assert b"tts_mp3" in result.audio_bytes

    async def test_audio_is_none_when_both_forvo_and_tts_fail(self):
        forvo_fn, tts_fn, pix_fn, norm_fn = _make_fakes(forvo_returns=None, tts_returns=None)

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=pix_fn,
            _normalize_fn=norm_fn,
        )
        assert result.audio_bytes is None
        assert result.audio_source is None

    async def test_normalizes_audio_by_default(self):
        norm_calls: list[bytes] = []

        def norm_fn(src, *, target_lufs=-23.0):
            norm_calls.append(src)
            return b"normalized"

        forvo_fn, tts_fn, pix_fn, _ = _make_fakes(forvo_returns=b"raw_audio")

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=pix_fn,
            _normalize_fn=norm_fn,
        )
        assert norm_calls == [b"raw_audio"]
        assert result.audio_bytes == b"normalized"

    async def test_skips_normalization_when_disabled(self):
        norm_calls: list[bytes] = []

        def norm_fn(src, *, target_lufs=-23.0):
            norm_calls.append(src)
            return b"normalized"

        forvo_fn, tts_fn, pix_fn, _ = _make_fakes(forvo_returns=b"raw_audio")

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            normalize=False,
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=pix_fn,
            _normalize_fn=norm_fn,
        )
        assert norm_calls == []
        assert result.audio_bytes == b"raw_audio"

    async def test_skips_normalization_when_no_audio(self):
        norm_calls: list[bytes] = []

        def norm_fn(src, *, target_lufs=-23.0):
            norm_calls.append(src)
            return b"normalized"

        forvo_fn, tts_fn, pix_fn, _ = _make_fakes(forvo_returns=None, tts_returns=None)

        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=pix_fn,
            _normalize_fn=norm_fn,
        )
        assert norm_calls == []

    async def test_includes_pixabay_image(self):
        forvo_fn, tts_fn, pix_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            pixabay_returns=(b"img_data", "jpg", _IMG_URL),
        )

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=pix_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_bytes == b"img_data"
        assert result.image_ext == "jpg"

    async def test_image_is_none_when_pixabay_returns_none(self):
        forvo_fn, tts_fn, pix_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            pixabay_returns=None,
        )

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=pix_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_bytes is None
        assert result.image_ext is None

    async def test_mediaresult_defaults_to_none(self):
        r = MediaResult()
        assert r.audio_bytes is None
        assert r.audio_source is None
        assert r.image_bytes is None
        assert r.image_ext is None
        assert r.image_url is None

    # ── Image deduplication ────────────────────────────────────────────────────

    async def test_image_url_stored_in_result(self):
        forvo_fn, tts_fn, pix_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            pixabay_returns=(b"img_data", "jpg", _IMG_URL),
        )
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=pix_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_url == _IMG_URL

    async def test_image_url_is_none_when_no_image(self):
        forvo_fn, tts_fn, pix_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            pixabay_returns=None,
        )
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=pix_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_url is None

    async def test_used_image_urls_receives_fetched_url(self):
        """Caller's used_image_urls set is mutated in place."""
        forvo_fn, tts_fn, pix_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            pixabay_returns=(b"img", "jpg", _IMG_URL),
        )
        used: set[str] = set()
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            used_image_urls=used,
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=pix_fn,
            _normalize_fn=norm_fn,
        )
        assert _IMG_URL in used

    async def test_used_image_urls_not_mutated_when_no_image(self):
        forvo_fn, tts_fn, pix_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            pixabay_returns=None,
        )
        used: set[str] = set()
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            used_image_urls=used,
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=pix_fn,
            _normalize_fn=norm_fn,
        )
        assert used == set()

    async def test_passes_used_urls_to_pixabay_fn(self):
        """The set is forwarded to the pixabay function as a frozenset."""
        received: list[frozenset] = []

        def tracking_pixabay(english, *, api_key, http_client=None, used_urls=frozenset()):
            received.append(used_urls)
            return None

        forvo_fn, tts_fn, _, norm_fn = _make_fakes(forvo_returns=b"audio")
        pre_existing = {"https://cdn.pixabay.com/old.jpg"}
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            used_image_urls=pre_existing,
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=tracking_pixabay,
            _normalize_fn=norm_fn,
        )
        assert received[0] == frozenset({"https://cdn.pixabay.com/old.jpg"})

    async def test_no_used_urls_when_not_passed(self):
        """When used_image_urls is None, pixabay receives an empty frozenset."""
        received: list[frozenset] = []

        def tracking_pixabay(english, *, api_key, http_client=None, used_urls=frozenset()):
            received.append(used_urls)
            return None

        forvo_fn, tts_fn, _, norm_fn = _make_fakes(forvo_returns=b"audio")
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _pixabay_fn=tracking_pixabay,
            _normalize_fn=norm_fn,
        )
        assert received[0] == frozenset()
