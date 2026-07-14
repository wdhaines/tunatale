"""Tests for S3.8: Pixabay image fetcher with ranking."""

from __future__ import annotations

import math

import httpx
import pytest

from app.cards.media.pixabay import (
    QUERY_MAP,
    best_hit,
    build_query,
    download_hit,
    fetch_pixabay_image,
    score_hit,
    search_pixabay,
)

# ── build_query ────────────────────────────────────────────────────────────────


class TestBuildQuery:
    def test_uses_query_map_when_key_present(self):
        assert build_query("wing") == "bird wing"

    def test_strips_parens_when_no_map_entry(self):
        assert "(" not in build_query("castle (medieval)")

    def test_returns_plain_english_when_no_parens_and_not_in_map(self):
        assert build_query("castle") == "castle"

    def test_query_map_has_wing_entry(self):
        assert "wing" in QUERY_MAP

    def test_query_map_is_nonempty(self):
        assert len(QUERY_MAP) > 50


# ── score_hit ──────────────────────────────────────────────────────────────────


class TestScoreHit:
    def test_zero_likes_and_views_gives_zero_score(self):
        hit = {"likes": 0, "views": 0, "tags": "", "imageType": "photo"}
        score = score_hit(hit, frozenset())
        assert score == pytest.approx(0.0)

    def test_score_increases_with_likes(self):
        low = score_hit({"likes": 10, "views": 0, "tags": ""}, frozenset())
        high = score_hit({"likes": 1000, "views": 0, "tags": ""}, frozenset())
        assert high > low

    def test_score_increases_with_views(self):
        low = score_hit({"likes": 0, "views": 10, "tags": ""}, frozenset())
        high = score_hit({"likes": 0, "views": 10000, "tags": ""}, frozenset())
        assert high > low

    def test_tag_overlap_boosts_score(self):
        no_overlap = score_hit({"likes": 100, "views": 0, "tags": "dog, cat"}, frozenset({"bird"}))
        with_overlap = score_hit({"likes": 100, "views": 0, "tags": "dog, cat"}, frozenset({"dog"}))
        assert with_overlap > no_overlap

    def test_handles_missing_fields_gracefully(self):
        hit = {}
        score = score_hit(hit, frozenset())
        assert score == pytest.approx(0.0)

    def test_formula_relevance_dominates_with_squashed_engagement(self):
        # Relevance is weighted heavily (10 per overlapping tag); engagement is
        # squashed into [0, 1) so it can only ever break ties, never dominate.
        hit = {"likes": 99, "views": 999, "tags": "tree, forest, nature"}
        tokens = frozenset({"tree", "nature"})
        eng_raw = 0.5 * math.log(100) + 0.3 * math.log(1000)
        expected = 10.0 * 2 + eng_raw / (eng_raw + 1.0)
        assert score_hit(hit, tokens) == pytest.approx(expected)

    def test_one_tag_overlap_beats_unlimited_engagement(self):
        # A single on-topic tag must outrank any amount of likes/views.
        on_topic = score_hit({"likes": 0, "views": 0, "tags": "court"}, frozenset({"court"}))
        off_topic = score_hit({"likes": 10**9, "views": 10**9, "tags": "tennis"}, frozenset({"court"}))
        assert on_topic > off_topic

    def test_editors_choice_adds_bonus(self):
        plain = score_hit({"likes": 5, "views": 10, "tags": "tree"}, frozenset({"tree"}))
        chosen = score_hit({"likes": 5, "views": 10, "tags": "tree", "editors_choice": True}, frozenset({"tree"}))
        assert chosen > plain

    def test_engagement_is_bounded_below_one(self):
        # Even with astronomically high engagement and no overlap, the score
        # stays under 1.0 (the relevance floor for a single tag).
        score = score_hit({"likes": 10**12, "views": 10**12, "tags": "x"}, frozenset())
        assert score < 1.0


# ── best_hit ──────────────────────────────────────────────────────────────────


class TestBestHit:
    def test_returns_none_for_empty_list(self):
        assert best_hit([], "tree") is None

    def test_returns_highest_scoring_hit(self):
        low = {"likes": 1, "views": 0, "tags": "", "imageType": "photo"}
        high = {"likes": 10000, "views": 50000, "tags": "tree", "imageType": "photo"}
        result = best_hit([low, high], "tree")
        assert result is high

    def test_prefers_photos_over_illustrations(self):
        photo = {"likes": 1, "views": 0, "tags": "", "imageType": "photo", "type": "photo"}
        illus = {"likes": 9999, "views": 9999, "tags": "", "imageType": "illustration"}
        result = best_hit([illus, photo], "tree")
        assert result is photo

    def test_returns_best_among_photos_only(self):
        p1 = {"likes": 100, "views": 0, "tags": "", "imageType": "photo"}
        p2 = {"likes": 9000, "views": 0, "tags": "", "imageType": "photo"}
        result = best_hit([p1, p2], "tree")
        assert result is p2

    def test_falls_back_to_all_when_no_photos(self):
        v1 = {"likes": 100, "views": 0, "tags": "", "imageType": "vector"}
        v2 = {"likes": 9000, "views": 0, "tags": "", "imageType": "vector"}
        result = best_hit([v1, v2], "tree")
        assert result is v2

    def test_on_topic_low_engagement_beats_off_topic_viral(self):
        # The core fix: a relevant photo with almost no engagement must win over
        # a wildly popular but off-topic one. (Old engagement-weighted formula
        # picked the viral tennis shot for a "courtroom interior" query.)
        on_topic = {"likes": 1, "views": 0, "tags": "courtroom, justice", "imageType": "photo"}
        off_topic = {"likes": 99999, "views": 999999, "tags": "tennis, sport", "imageType": "photo"}
        result = best_hit([off_topic, on_topic], "courtroom interior")
        assert result is on_topic

    def test_editors_choice_breaks_relevance_tie(self):
        plain = {"likes": 5, "views": 10, "tags": "tree", "imageType": "photo"}
        chosen = {"likes": 5, "views": 10, "tags": "tree", "imageType": "photo", "editors_choice": True}
        result = best_hit([plain, chosen], "tree")
        assert result is chosen


# ── fetch_pixabay_image ───────────────────────────────────────────────────────


class _PixabayTransport(httpx.BaseTransport):
    def __init__(self, hits: list[dict], img_bytes: bytes = b"\xff\xd8fake_jpg") -> None:
        self._hits = hits
        self._img_bytes = img_bytes

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if "pixabay.com/api" in str(request.url):
            return httpx.Response(200, json={"hits": self._hits, "totalHits": len(self._hits)})
        # image download
        return httpx.Response(200, content=self._img_bytes)


class TestFetchPixabayImage:
    def _client(self, hits: list[dict], img_bytes: bytes = b"\xff\xd8fake") -> httpx.Client:
        return httpx.Client(transport=_PixabayTransport(hits, img_bytes))

    def test_returns_bytes_and_jpg_ext(self):
        hits = [
            {
                "likes": 10,
                "views": 100,
                "tags": "tree",
                "imageType": "photo",
                "webformatURL": "https://cdn.pixabay.com/photo/tree.jpg",
            }
        ]
        client = self._client(hits, b"fake_jpg_bytes")
        result = fetch_pixabay_image("tree", api_key="key123", http_client=client)
        assert result is not None
        data, ext, url = result
        assert data == b"fake_jpg_bytes"
        assert ext == "jpg"
        assert url == "https://cdn.pixabay.com/photo/tree.jpg"

    def test_returns_png_ext_for_png_url(self):
        hits = [
            {
                "likes": 10,
                "views": 100,
                "tags": "tree",
                "imageType": "photo",
                "webformatURL": "https://cdn.pixabay.com/photo/tree.png",
            }
        ]
        client = self._client(hits, b"fake_png")
        result = fetch_pixabay_image("tree", api_key="key123", http_client=client)
        assert result is not None
        _, ext, _ = result
        assert ext == "png"

    def test_jpeg_url_gets_jpg_ext_not_png(self):
        """Backlog #19: '.jpeg' doesn't contain 'jpg', so the old substring check
        mislabelled it png. jpg is Pixabay's dominant format — it is the default."""
        hits = [
            {
                "likes": 10,
                "views": 100,
                "tags": "tree",
                "imageType": "photo",
                "webformatURL": "https://cdn.pixabay.com/photo/tree.jpeg",
            }
        ]
        client = self._client(hits, b"fake")
        result = fetch_pixabay_image("tree", api_key="key123", http_client=client)
        assert result is not None
        _, ext, _ = result
        assert ext == "jpg"

    def test_png_url_with_query_string_gets_png_ext(self):
        hits = [
            {
                "likes": 10,
                "views": 100,
                "tags": "tree",
                "imageType": "photo",
                "webformatURL": "https://cdn.pixabay.com/photo/tree.png?w=300",
            }
        ]
        client = self._client(hits, b"fake")
        result = fetch_pixabay_image("tree", api_key="key123", http_client=client)
        assert result is not None
        _, ext, _ = result
        assert ext == "png"

    def test_returns_none_when_no_hits(self):
        client = self._client([])
        result = fetch_pixabay_image("tree", api_key="key123", http_client=client)
        assert result is None

    def test_returns_none_when_hit_has_no_url(self):
        hits = [{"likes": 10, "views": 0, "tags": "", "imageType": "photo", "webformatURL": ""}]
        client = self._client(hits)
        result = fetch_pixabay_image("tree", api_key="key123", http_client=client)
        assert result is None

    def test_returns_none_on_api_error(self):
        class ErrorTransport(httpx.BaseTransport):
            def handle_request(self, request):
                return httpx.Response(500)

        client = httpx.Client(transport=ErrorTransport())
        result = fetch_pixabay_image("tree", api_key="key123", http_client=client)
        assert result is None

    def test_creates_own_client_when_none_given(self, monkeypatch):
        calls: list[str] = []

        class FakeClient:
            def get(self, url, *, params=None, timeout=None):
                calls.append("get")
                raise httpx.ConnectError("no network in test")

            def close(self):
                calls.append("close")

        monkeypatch.setattr("app.cards.media.pixabay.httpx.Client", lambda: FakeClient())
        result = fetch_pixabay_image("tree", api_key="key")
        assert result is None
        assert "close" in calls

    def test_skips_hit_whose_url_is_in_used_urls(self):
        hits = [
            {
                "likes": 9999,
                "views": 9999,
                "tags": "tree",
                "imageType": "photo",
                "webformatURL": "https://cdn.pixabay.com/already_used.jpg",
            },
            {
                "likes": 1,
                "views": 0,
                "tags": "",
                "imageType": "photo",
                "webformatURL": "https://cdn.pixabay.com/fresh.jpg",
            },
        ]
        client = self._client(hits, b"fresh_image")
        result = fetch_pixabay_image(
            "tree",
            api_key="key123",
            http_client=client,
            used_urls=frozenset({"https://cdn.pixabay.com/already_used.jpg"}),
        )
        assert result is not None
        _, _, url = result
        assert url == "https://cdn.pixabay.com/fresh.jpg"

    def test_query_override_is_sent_verbatim(self):
        captured: dict[str, str] = {}

        class _CapturingTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if "pixabay.com/api" in str(request.url):
                    captured["q"] = request.url.params.get("q", "")
                    return httpx.Response(
                        200,
                        json={
                            "hits": [
                                {
                                    "likes": 1,
                                    "views": 1,
                                    "tags": "courtroom",
                                    "imageType": "photo",
                                    "webformatURL": "https://cdn.pixabay.com/c.jpg",
                                }
                            ]
                        },
                    )
                return httpx.Response(200, content=b"img")

        client = httpx.Client(transport=_CapturingTransport())
        # "court" maps to "courtroom interior" via QUERY_MAP, but an explicit
        # override must bypass build_query entirely.
        result = fetch_pixabay_image("court", api_key="k", http_client=client, query="empty jail cell")
        assert result is not None
        assert captured["q"] == "empty jail cell"

    def test_falsy_query_falls_back_to_build_query(self):
        captured: dict[str, str] = {}

        class _CapturingTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if "pixabay.com/api" in str(request.url):
                    captured["q"] = request.url.params.get("q", "")
                    return httpx.Response(200, json={"hits": []})
                return httpx.Response(200, content=b"img")

        client = httpx.Client(transport=_CapturingTransport())
        fetch_pixabay_image("wing", api_key="k", http_client=client, query=None)
        assert captured["q"] == "bird wing"  # QUERY_MAP entry, not raw "wing"

    def test_returns_none_when_all_hits_in_used_urls(self):
        hits = [
            {
                "likes": 10,
                "views": 0,
                "tags": "tree",
                "imageType": "photo",
                "webformatURL": "https://cdn.pixabay.com/used.jpg",
            }
        ]
        client = self._client(hits)
        result = fetch_pixabay_image(
            "tree",
            api_key="key123",
            http_client=client,
            used_urls=frozenset({"https://cdn.pixabay.com/used.jpg"}),
        )
        assert result is None


# ── search_pixabay ────────────────────────────────────────────────────────────


class _SearchTransport(httpx.BaseTransport):
    """Fake transport that returns a configurable status code or raises."""

    def __init__(
        self,
        *,
        status_code: int = 200,
        hits: list[dict] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._status_code = status_code
        self._hits = hits if hits is not None else []
        self._exc = exc

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self._exc is not None:
            raise self._exc
        if self._status_code == 429:
            raise httpx.HTTPStatusError(
                "429 Too Many Requests",
                request=request,
                response=httpx.Response(429),
            )
        if self._status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self._status_code}",
                request=request,
                response=httpx.Response(self._status_code),
            )
        return httpx.Response(200, json={"hits": self._hits, "totalHits": len(self._hits)})


class TestSearchPixabay:
    def test_429_returns_rate_limited(self):
        client = httpx.Client(transport=_SearchTransport(status_code=429))
        result = search_pixabay("tree", api_key="k", http_client=client)
        assert result.status == "rate_limited"
        assert result.hits == []

    def test_500_returns_api_error_and_logs(self, caplog):
        client = httpx.Client(transport=_SearchTransport(status_code=500))
        with caplog.at_level("WARNING"):
            result = search_pixabay("tree", api_key="k", http_client=client)
        assert result.status == "api_error"
        assert result.hits == []
        assert any("tree" in r.message for r in caplog.records)

    def test_network_exception_returns_api_error(self, caplog):
        client = httpx.Client(transport=_SearchTransport(exc=ConnectionError("no route")))
        with caplog.at_level("WARNING"):
            result = search_pixabay("tree", api_key="k", http_client=client)
        assert result.status == "api_error"
        assert any("tree" in r.message for r in caplog.records)

    def test_empty_hits_returns_no_results(self):
        client = httpx.Client(transport=_SearchTransport(hits=[]))
        result = search_pixabay("tree", api_key="k", http_client=client)
        assert result.status == "no_results"
        assert result.hits == []

    def test_happy_path_returns_ok_with_hits(self):
        hits = [{"tags": "tree", "webformatURL": "https://cdn.pixabay.com/t.jpg"}]
        client = httpx.Client(transport=_SearchTransport(hits=hits))
        result = search_pixabay("tree", api_key="k", http_client=client)
        assert result.status == "ok"
        assert len(result.hits) == 1
        assert result.hits[0]["tags"] == "tree"

    def test_programming_error_propagates(self):
        """A programming error (not HTTP/network) must raise, not be swallowed."""

        class _Bug(httpx.BaseTransport):
            def handle_request(self, request):
                raise TypeError("bug in test code")

        client = httpx.Client(transport=_Bug())
        with pytest.raises(TypeError, match="bug in test code"):
            search_pixabay("tree", api_key="k", http_client=client)

    def test_creates_own_client_when_none_given(self, monkeypatch):
        """When no http_client is passed, search_pixabay creates and closes its own."""
        calls: list[str] = []
        transport = _SearchTransport(hits=[])

        class FakeClient:
            def __init__(self):
                self._transport = transport

            def get(self, url, *, params=None, timeout=None):
                calls.append("get")
                return httpx.Response(200, json={"hits": [], "totalHits": 0}, request=httpx.Request("GET", url))

            def close(self):
                calls.append("close")

        monkeypatch.setattr("app.cards.media.pixabay.httpx.Client", lambda: FakeClient())
        result = search_pixabay("tree", api_key="k")
        assert result.status == "no_results"
        assert "close" in calls


# ── download_hit ──────────────────────────────────────────────────────────────


class TestDownloadHit:
    def test_returns_bytes_and_ext(self):
        class _ImgTransport(httpx.BaseTransport):
            def handle_request(self, request):
                return httpx.Response(200, content=b"\xff\xd8fake_jpg")

        client = httpx.Client(transport=_ImgTransport())
        hit = {"webformatURL": "https://cdn.pixabay.com/photo/tree.jpg"}
        result = download_hit(hit, http_client=client)
        assert result is not None
        data, ext, url = result
        assert data == b"\xff\xd8fake_jpg"
        assert ext == "jpg"
        assert url == "https://cdn.pixabay.com/photo/tree.jpg"

    def test_png_ext_for_png_url(self):
        class _ImgTransport(httpx.BaseTransport):
            def handle_request(self, request):
                return httpx.Response(200, content=b"fake_png")

        client = httpx.Client(transport=_ImgTransport())
        hit = {"webformatURL": "https://cdn.pixabay.com/photo/tree.png"}
        result = download_hit(hit, http_client=client)
        assert result is not None
        assert result[1] == "png"

    def test_network_failure_returns_none_and_logs(self, caplog):
        client = httpx.Client(transport=_SearchTransport(exc=ConnectionError("down")))
        hit = {"webformatURL": "https://cdn.pixabay.com/photo/tree.jpg"}
        with caplog.at_level("WARNING"):
            result = download_hit(hit, http_client=client)
        assert result is None
        assert any("download" in r.message.lower() or "tree" in r.message for r in caplog.records)

    def test_empty_url_returns_none(self):
        """A hit with no webformatURL should fail gracefully."""
        client = httpx.Client(transport=_SearchTransport())
        hit = {"webformatURL": ""}
        result = download_hit(hit, http_client=client)
        assert result is None

    def test_creates_own_client_when_none_given(self, monkeypatch):
        """When no http_client is passed, download_hit creates and closes its own."""
        calls: list[str] = []

        class FakeClient:
            def get(self, url, *, timeout=None):
                calls.append("get")
                return httpx.Response(200, content=b"img", request=httpx.Request("GET", url))

            def close(self):
                calls.append("close")

        monkeypatch.setattr("app.cards.media.pixabay.httpx.Client", lambda: FakeClient())
        hit = {"webformatURL": "https://cdn.pixabay.com/photo/tree.jpg"}
        result = download_hit(hit)
        assert result is not None
        assert "close" in calls


# ── wrapper still works ──────────────────────────────────────────────────────


class TestFetchPixabayImageWrapper:
    def test_wrapper_still_returns_tuple_on_happy_path(self):
        hits = [
            {
                "likes": 10,
                "views": 100,
                "tags": "tree",
                "imageType": "photo",
                "webformatURL": "https://cdn.pixabay.com/photo/tree.jpg",
            }
        ]
        client = httpx.Client(transport=_PixabayTransport(hits, b"wrapper_bytes"))
        result = fetch_pixabay_image("tree", api_key="key123", http_client=client)
        assert result is not None
        data, ext, url = result
        assert data == b"wrapper_bytes"
        assert ext == "jpg"
        assert url == "https://cdn.pixabay.com/photo/tree.jpg"
