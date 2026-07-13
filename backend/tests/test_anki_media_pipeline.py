"""Tests for the media pipeline orchestration, image deduplication, retry, and chooser wiring."""

from __future__ import annotations

from app.anki.media.pipeline import MediaResult, fetch_card_media
from app.anki.media.pixabay import PixabaySearch

# ── Fakes ──────────────────────────────────────────────────────────────────────

_IMG_URL = "https://cdn.pixabay.com/photo/tree.jpg"
_IMG_URL_2 = "https://cdn.pixabay.com/photo/dog.jpg"

_HIT_1 = {"webformatURL": _IMG_URL, "tags": "tree, forest", "imageWidth": 800, "imageHeight": 600, "likes": 42}
_HIT_2 = {"webformatURL": _IMG_URL_2, "tags": "dog, pet", "imageWidth": 1024, "imageHeight": 768, "likes": 100}


def _make_fakes(
    *,
    forvo_returns: bytes | None = None,
    tts_returns: bytes | None = None,
    search_returns: PixabaySearch | None = None,
    download_returns: tuple[bytes, str, str] | None = None,
    normalize_returns: bytes | None = None,
):
    """Return (forvo_fn, tts_fn, search_fn, download_fn, normalize_fn) configured fakes."""

    def fake_forvo(word, *, language_code="sl", http_client=None):
        return forvo_returns

    async def fake_tts(text, *, voice=None):
        return tts_returns

    def fake_search(query, *, api_key, http_client=None, per_page=50):
        return search_returns or PixabaySearch(hits=[], status="no_results")

    def fake_download(hit, *, http_client=None):
        return download_returns

    def fake_normalize(src_bytes, *, target_lufs=-23.0):
        return normalize_returns if normalize_returns is not None else src_bytes + b"_norm"

    return fake_forvo, fake_tts, fake_search, fake_download, fake_normalize


# ── TestMediaResult ────────────────────────────────────────────────────────────


class TestMediaResult:
    async def test_defaults_to_none(self):
        r = MediaResult()
        assert r.audio_bytes is None
        assert r.audio_source is None
        assert r.image_bytes is None
        assert r.image_ext is None
        assert r.image_url is None
        assert r.image_status is None
        assert r.image_query_used is None
        assert r.image_chooser is None


# ── TestLanguageThreading ──────────────────────────────────────────────────────


class TestLanguageThreading:
    """Backlog #28: language_code selects the Forvo section + TTS voice."""

    async def test_resolves_tts_voice_from_language_code(self):
        captured_voice: list[str | None] = []

        def fake_forvo(word, *, language_code="sl", http_client=None):
            return None  # force TTS fallback

        async def recording_tts(text, *, voice=None):
            captured_voice.append(voice)
            return b"tts_mp3"

        await fetch_card_media(
            "hotell",
            "hotel",
            pixabay_key="key",
            language_code="no",
            _forvo_fn=fake_forvo,
            _tts_fn=recording_tts,
            _search_fn=lambda q, **k: PixabaySearch(hits=[], status="no_results"),
            _download_fn=lambda h, **k: None,
        )
        assert captured_voice == ["nb-NO-PernilleNeural"]

    async def test_threads_language_code_to_forvo(self):
        captured_lang: list[str] = []

        def recording_forvo(word, *, language_code="sl", http_client=None):
            captured_lang.append(language_code)
            return b"forvo_mp3"

        async def fake_tts(text, *, voice=None):
            return None

        await fetch_card_media(
            "hotell",
            "hotel",
            pixabay_key="key",
            language_code="no",
            _forvo_fn=recording_forvo,
            _tts_fn=fake_tts,
            _search_fn=lambda q, **k: PixabaySearch(hits=[], status="no_results"),
            _download_fn=lambda h, **k: None,
            _normalize_fn=lambda b, **k: b,
        )
        assert captured_lang == ["no"]

    async def test_explicit_tts_voice_overrides_language_default(self):
        captured_voice: list[str | None] = []

        async def recording_tts(text, *, voice=None):
            captured_voice.append(voice)
            return b"tts_mp3"

        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            language_code="no",
            tts_voice="custom-voice",
            _forvo_fn=lambda *a, **k: None,
            _tts_fn=recording_tts,
            _search_fn=lambda q, **k: PixabaySearch(hits=[], status="no_results"),
            _download_fn=lambda h, **k: None,
        )
        assert captured_voice == ["custom-voice"]


# ── TestFetchCardMedia ─────────────────────────────────────────────────────────


class TestFetchCardMedia:
    async def test_uses_forvo_when_available(self):
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(forvo_returns=b"forvo_mp3")

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.audio_source == "forvo"
        assert b"forvo_mp3" in result.audio_bytes

    async def test_falls_back_to_tts_when_forvo_returns_none(self):
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(forvo_returns=None, tts_returns=b"tts_mp3")

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.audio_source == "tts"
        assert b"tts_mp3" in result.audio_bytes

    async def test_audio_is_none_when_both_forvo_and_tts_fail(self):
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(forvo_returns=None, tts_returns=None)

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.audio_bytes is None
        assert result.audio_source is None

    async def test_normalizes_audio_by_default(self):
        norm_calls: list[bytes] = []

        def norm_fn(src, *, target_lufs=-23.0):
            norm_calls.append(src)
            return b"normalized"

        forvo_fn, tts_fn, search_fn, dl_fn, _ = _make_fakes(forvo_returns=b"raw_audio")

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert norm_calls == [b"raw_audio"]
        assert result.audio_bytes == b"normalized"

    async def test_skips_normalization_when_disabled(self):
        norm_calls: list[bytes] = []

        def norm_fn(src, *, target_lufs=-23.0):
            norm_calls.append(src)
            return b"normalized"

        forvo_fn, tts_fn, search_fn, dl_fn, _ = _make_fakes(forvo_returns=b"raw_audio")

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            normalize=False,
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert norm_calls == []
        assert result.audio_bytes == b"raw_audio"

    async def test_skips_normalization_when_no_audio(self):
        norm_calls: list[bytes] = []

        def norm_fn(src, *, target_lufs=-23.0):
            norm_calls.append(src)
            return b"normalized"

        forvo_fn, tts_fn, search_fn, dl_fn, _ = _make_fakes(forvo_returns=None, tts_returns=None)

        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert norm_calls == []

    async def test_includes_pixabay_image(self):
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[_HIT_1], status="ok"),
            download_returns=(b"img_data", "jpg", _IMG_URL),
        )

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_bytes == b"img_data"
        assert result.image_ext == "jpg"

    async def test_image_is_none_when_search_returns_no_results(self):
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[], status="no_results"),
        )

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_bytes is None
        assert result.image_ext is None

    # ── Image deduplication ────────────────────────────────────────────────────

    async def test_image_url_stored_in_result(self):
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[_HIT_1], status="ok"),
            download_returns=(b"img_data", "jpg", _IMG_URL),
        )
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_url == _IMG_URL

    async def test_image_url_is_none_when_no_image(self):
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[], status="no_results"),
        )
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_url is None

    async def test_used_image_urls_receives_fetched_url(self):
        """Caller's used_image_urls set is mutated in place."""
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[_HIT_1], status="ok"),
            download_returns=(b"img", "jpg", _IMG_URL),
        )
        used: set[str] = set()
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            used_image_urls=used,
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert _IMG_URL in used

    async def test_used_image_urls_not_mutated_when_no_image(self):
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[], status="no_results"),
        )
        used: set[str] = set()
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            used_image_urls=used,
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert used == set()

    async def test_used_image_urls_filtered_before_search(self):
        """Hits whose webformatURL is in used_image_urls are excluded."""
        captured_queries: list[str] = []

        def tracking_search(query, *, api_key, http_client=None, per_page=50):
            captured_queries.append(query)
            return PixabaySearch(hits=[_HIT_1], status="ok")

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(forvo_returns=b"audio")
        used: set[str] = {_IMG_URL}  # already used
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            used_image_urls=used,
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=tracking_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_bytes is None  # hit was filtered out
        assert captured_queries

    async def test_passes_used_urls_to_search_fn(self):
        received: list[str] = []

        def tracking_search(query, *, api_key, http_client=None, per_page=50):
            received.append(query)
            return PixabaySearch(hits=[], status="no_results")

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(forvo_returns=b"audio")
        pre_existing = {"https://cdn.pixabay.com/old.jpg"}
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            used_image_urls=pre_existing,
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=tracking_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert received  # search was called

    async def test_no_used_urls_when_not_passed(self):
        """When used_image_urls is None, filtering uses an empty set."""
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(forvo_returns=b"audio")
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        # No crash

    # ── LLM image-query override ───────────────────────────────────────────────

    async def test_image_query_override_forwarded_to_search(self):
        received: list[str | None] = []

        def tracking_search(query, *, api_key, http_client=None, per_page=50):
            received.append(query)
            # Return a hit whose tags overlap with the query so no retry fires
            return PixabaySearch(
                hits=[
                    {"webformatURL": _IMG_URL, "tags": "jail, cell", "imageWidth": 800, "imageHeight": 600, "likes": 42}
                ],
                status="ok",
            )

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            download_returns=(b"img", "jpg", _IMG_URL),
        )
        await fetch_card_media(
            "sodišče",
            "court",
            pixabay_key="key",
            image_query="empty jail cell",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=tracking_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert received == ["empty jail cell"]

    async def test_empty_image_query_skips_search_entirely(self):
        called: list[str] = []

        def tracking_search(query, *, api_key, http_client=None, per_page=50):
            called.append(query)
            return PixabaySearch(hits=[_HIT_1], status="ok")

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(forvo_returns=b"audio")
        result = await fetch_card_media(
            "zato",
            "therefore",
            pixabay_key="key",
            image_query="",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=tracking_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert called == []
        assert result.image_bytes is None
        assert result.image_url is None

    async def test_none_image_query_uses_legacy_path(self):
        received: list[str | None] = []

        def tracking_search(query, *, api_key, http_client=None, per_page=50):
            received.append(query)
            return PixabaySearch(hits=[], status="no_results")

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(forvo_returns=b"audio")
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=tracking_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert received[0] is not None  # build_query produced something

    # ── Status fields ──────────────────────────────────────────────────────────

    async def test_status_skipped_on_empty_image_query(self):
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(forvo_returns=b"audio")
        result = await fetch_card_media(
            "zato",
            "therefore",
            pixabay_key="key",
            image_query="",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_status == "skipped"

    async def test_status_ok_on_successful_image(self):
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[_HIT_1], status="ok"),
            download_returns=(b"img", "jpg", _IMG_URL),
        )
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_status == "ok"
        assert result.image_bytes == b"img"

    async def test_status_rate_limited_propagates(self):
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[], status="rate_limited"),
        )
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_status == "rate_limited"

    async def test_status_api_error_propagates(self):
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[], status="api_error"),
        )
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_status == "api_error"

    async def test_download_failure_sets_api_error(self):
        forvo_fn, tts_fn, search_fn, _, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[_HIT_1], status="ok"),
        )

        def failing_download(hit, *, http_client=None):
            return None

        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=failing_download,
            _normalize_fn=norm_fn,
        )
        assert result.image_status == "api_error"
        assert result.image_bytes is None

    async def test_image_query_used_recorded(self):
        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[_HIT_1], status="ok"),
            download_returns=(b"img", "jpg", _IMG_URL),
        )
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            image_query="forest trees",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_query_used == "forest trees"

    # ── Retry logic ────────────────────────────────────────────────────────────

    async def test_retry_fires_on_no_results(self):
        """Search returns no_results → retry with build_query/english fallback."""
        call_count = 0

        def count_search(query, *, api_key, http_client=None, per_page=50):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return PixabaySearch(hits=[], status="no_results")
            return PixabaySearch(hits=[_HIT_1], status="ok")

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            download_returns=(b"img", "jpg", _IMG_URL),
        )
        # Use explicit image_query that differs from build_query(english) so retry fires
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            image_query="fresh water stream",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=count_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert call_count == 2
        assert result.image_status == "ok"
        assert result.image_bytes == b"img"

    async def test_retry_fires_on_zero_overlap_ok(self):
        """Search ok but no overlap with primary query → retry."""
        call_count = 0

        def count_search(query, *, api_key, http_client=None, per_page=50):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # ok but no overlap with "clear water" query tags
                return PixabaySearch(
                    hits=[
                        {
                            "webformatURL": _IMG_URL,
                            "tags": "sunset, beach",
                            "imageWidth": 800,
                            "imageHeight": 600,
                            "likes": 10,
                        }
                    ],
                    status="ok",
                )
            return PixabaySearch(hits=[_HIT_1], status="ok")

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            download_returns=(b"img", "jpg", _IMG_URL),
        )
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            image_query="clear water",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=count_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert call_count == 2
        assert result.image_status == "ok"

    async def test_retry_fires_on_ok_all_duplicates(self):
        """Status ok but all hits already used → retry fires (available is empty)."""
        call_count = 0

        def count_search(query, *, api_key, http_client=None, per_page=50):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # ok but the only hit is already on the card
                return PixabaySearch(
                    hits=[
                        {
                            "webformatURL": "http://img/used.jpg",
                            "tags": "clear, water",
                            "imageWidth": 800,
                            "imageHeight": 600,
                            "likes": 10,
                        }
                    ],
                    status="ok",
                )
            return PixabaySearch(hits=[_HIT_1], status="ok")

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            download_returns=(b"img", "jpg", _IMG_URL),
        )
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            image_query="clear water",
            used_image_urls={"http://img/used.jpg"},
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=count_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert call_count == 2, "retry should fire when all hits are duplicates"
        assert result.image_status == "ok"

    async def test_no_retry_when_retry_query_equals_primary(self):
        """If build_query(english) == image_query, no retry even on no_results."""
        call_count = 0

        def count_search(query, *, api_key, http_client=None, per_page=50):
            nonlocal call_count
            call_count += 1
            return PixabaySearch(hits=[], status="no_results")

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(forvo_returns=b"audio")
        # image_query="water" which IS build_query("water"), so retry_query == primary
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            image_query="water",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=count_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert call_count == 1  # no retry

    async def test_never_two_retries(self):
        """At most one retry even if the retry also returns no_results."""
        call_count = 0

        def count_search(query, *, api_key, http_client=None, per_page=50):
            nonlocal call_count
            call_count += 1
            return PixabaySearch(hits=[], status="no_results")

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(forvo_returns=b"audio")
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            image_query="fresh water stream",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=count_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert call_count == 2  # primary + 1 retry max

    async def test_no_retry_on_rate_limited(self):
        call_count = 0

        def count_search(query, *, api_key, http_client=None, per_page=50):
            nonlocal call_count
            call_count += 1
            return PixabaySearch(hits=[], status="rate_limited")

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(forvo_returns=b"audio")
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=count_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert call_count == 1

    async def test_no_retry_on_api_error(self):
        call_count = 0

        def count_search(query, *, api_key, http_client=None, per_page=50):
            nonlocal call_count
            call_count += 1
            return PixabaySearch(hits=[], status="api_error")

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(forvo_returns=b"audio")
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=count_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert call_count == 1

    async def test_retry_error_keeps_retry_status(self):
        """A retry that itself errors keeps the retry's status."""
        call_count = 0

        def count_search(query, *, api_key, http_client=None, per_page=50):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return PixabaySearch(hits=[], status="no_results")
            return PixabaySearch(hits=[], status="rate_limited")

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(forvo_returns=b"audio")
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            image_query="fresh water stream",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=count_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_status == "rate_limited"

    async def test_retry_uses_two_word_fallback(self):
        """When build_query(english) == primary, retry uses first 2 words of stripped gloss."""
        call_count = 0
        captured_queries: list[str] = []

        def count_search(query, *, api_key, http_client=None, per_page=50):
            nonlocal call_count
            call_count += 1
            captured_queries.append(query)
            return PixabaySearch(hits=[], status="no_results")

        forvo_fn, tts_fn, _, dl_fn, norm_fn = _make_fakes(forvo_returns=b"audio")
        # english has 3+ words; no image_query so primary = build_query(english) = english
        # build_query("water everywhere always") == "water everywhere always" (not in QUERY_MAP)
        # retry: first 2 words of stripped = "water everywhere"
        await fetch_card_media(
            "voda",
            "water everywhere always",
            pixabay_key="key",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=count_search,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        assert call_count == 2
        assert captured_queries[0] == "water everywhere always"
        assert captured_queries[1] == "water everywhere"

    # ── LLM chooser wiring ─────────────────────────────────────────────────────

    async def test_llm_choice_wins_over_tag_overlap(self):
        async def choosing_fn(word, english, query, hits, *, llm):
            return hits[0]  # always pick first

        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[_HIT_1], status="ok"),
            download_returns=(b"img", "jpg", _IMG_URL),
        )
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            llm="fake_llm",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _choose_fn=choosing_fn,
            _normalize_fn=norm_fn,
        )
        assert result.image_chooser == "llm"

    async def test_choose_fn_none_falls_back_to_tag_overlap(self):
        async def noop_choose(word, english, query, hits, *, llm):
            return None

        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[_HIT_1], status="ok"),
            download_returns=(b"img", "jpg", _IMG_URL),
        )
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            llm="fake_llm",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _choose_fn=noop_choose,
            _normalize_fn=norm_fn,
        )
        assert result.image_chooser == "tag_overlap"

    async def test_llm_none_never_calls_choose_fn(self):
        choose_called = False

        async def spy_choose(word, english, query, hits, *, llm):
            nonlocal choose_called
            choose_called = True
            return None

        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[_HIT_1], status="ok"),
            download_returns=(b"img", "jpg", _IMG_URL),
        )
        result = await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            llm=None,
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _choose_fn=spy_choose,
            _normalize_fn=norm_fn,
        )
        assert not choose_called
        assert result.image_chooser == "tag_overlap"

    async def test_chooser_empty_available_skips_choose(self):
        choose_called = False

        async def spy_choose(word, english, query, hits, *, llm):
            nonlocal choose_called
            choose_called = True
            return None

        forvo_fn, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes(
            forvo_returns=b"audio",
            search_returns=PixabaySearch(hits=[], status="no_results"),
        )
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            llm="fake_llm",
            _forvo_fn=forvo_fn,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _choose_fn=spy_choose,
            _normalize_fn=norm_fn,
        )
        assert not choose_called

    # ── Event loop liveness ─────────────────────────────────────────────────────

    async def test_blocking_fetchers_do_not_block_event_loop(self):
        """Forvo/search/download/normalize are synchronous — offloaded to worker threads."""
        import asyncio
        import time

        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        def slow_forvo(word, *, language_code="sl", http_client=None):
            time.sleep(0.2)
            return b"audio"

        _, tts_fn, search_fn, dl_fn, norm_fn = _make_fakes()
        task = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        await fetch_card_media(
            "voda",
            "water",
            pixabay_key="key",
            _forvo_fn=slow_forvo,
            _tts_fn=tts_fn,
            _search_fn=search_fn,
            _download_fn=dl_fn,
            _normalize_fn=norm_fn,
        )
        ticks_during = ticks
        task.cancel()
        assert ticks_during >= 3, f"event loop was blocked during the fetch (ticks={ticks_during})"
