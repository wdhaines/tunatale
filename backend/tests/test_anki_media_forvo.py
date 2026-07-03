"""Tests for S3.8: Forvo audio fetcher."""

from __future__ import annotations

import base64

import httpx

from app.anki.media.forvo import _extract_mp3_url, fetch_forvo_audio


def _make_forvo_html(b64_path: str, *, use_single_quotes: bool = False) -> str:
    quote = "'" if use_single_quotes else '"'
    return f"""
<html>
<div id={quote}language-container-sl{quote}>
  <article>
    <span onclick="Play(1,'{b64_path}',null,false,null,null,null)">Play</span>
  </article>
</div>
</html>
"""


def _b64(path: str) -> str:
    return base64.b64encode(path.encode()).decode()


# ── _extract_mp3_url ───────────────────────────────────────────────────────────


class TestExtractMp3Url:
    def test_returns_url_with_double_quote_container(self):
        b64 = _b64("audios/mp3/abc123.mp3")
        html = _make_forvo_html(b64, use_single_quotes=False)
        url = _extract_mp3_url(html, "test")
        assert url == "https://audio00.forvo.com/mp3/audios/mp3/abc123.mp3"

    def test_returns_url_with_single_quote_container(self):
        b64 = _b64("audios/mp3/xyz.mp3")
        html = _make_forvo_html(b64, use_single_quotes=True)
        url = _extract_mp3_url(html, "test")
        assert url == "https://audio00.forvo.com/mp3/audios/mp3/xyz.mp3"

    def test_returns_none_when_no_slovenian_section(self):
        html = "<html><div>no slovenian here</div></html>"
        assert _extract_mp3_url(html, "test") is None

    def test_returns_none_when_no_article_in_chunk(self):
        html = '<div id="language-container-sl"><span>no article tag</span></div>'
        assert _extract_mp3_url(html, "test") is None

    def test_returns_none_when_no_play_call(self):
        html = '<div id="language-container-sl"><article><p>no play call</p></article></div>'
        assert _extract_mp3_url(html, "test") is None

    def test_returns_none_when_base64_decodes_to_invalid_utf8(self):
        # b"\xff\xfe" encodes to "//4=" in base64 (valid base64 chars, invalid UTF-8)
        invalid_utf8_b64 = "//4="
        html = f'<div id="language-container-sl"><article><span onclick="Play(1,\'{invalid_utf8_b64}\')"></span></article></div>'
        assert _extract_mp3_url(html, "test") is None

    def test_make_client_returns_httpx_client(self):
        from app.anki.media.forvo import _make_client

        client = _make_client()
        assert isinstance(client, httpx.Client)
        client.close()

    def test_matches_requested_language_container(self):
        # Backlog #28: a Norwegian card must scrape the "no" section, not "sl".
        b64 = _b64("audios/mp3/norsk.mp3")
        html = f'<div id="language-container-no"><article><span onclick="Play(1,\'{b64}\')"></span></article></div>'
        assert (
            _extract_mp3_url(html, "hotell", language_code="no") == "https://audio00.forvo.com/mp3/audios/mp3/norsk.mp3"
        )

    def test_returns_none_when_only_other_language_container_present(self):
        # A "no"-only page must NOT hand back a URL when Slovene is requested —
        # this is the bug where a dual-language word ("hotel") got the wrong voice.
        b64 = _b64("audios/mp3/norsk.mp3")
        html = f'<div id="language-container-no"><article><span onclick="Play(1,\'{b64}\')"></span></article></div>'
        assert _extract_mp3_url(html, "hotell", language_code="sl") is None


# ── fetch_forvo_audio ──────────────────────────────────────────────────────────


class _SequenceTransport(httpx.BaseTransport):
    """Returns a fixed sequence of responses."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._responses.pop(0)


class TestFetchForvoAudio:
    def _client(self, responses: list[httpx.Response]) -> httpx.Client:
        return httpx.Client(transport=_SequenceTransport(responses))

    def test_returns_mp3_bytes_on_success(self):
        b64 = _b64("audios/mp3/test.mp3")
        html_body = _make_forvo_html(b64)
        mp3_bytes = b"\xff\xfb\x90\x00fake_mp3"
        client = self._client(
            [
                httpx.Response(200, text=html_body),
                httpx.Response(200, content=mp3_bytes),
            ]
        )
        result = fetch_forvo_audio("voda", http_client=client)
        assert result == mp3_bytes

    def test_returns_none_when_no_slovenian_section(self):
        html_body = "<html><p>no slovenian</p></html>"
        client = self._client([httpx.Response(200, text=html_body)])
        result = fetch_forvo_audio("voda", http_client=client)
        assert result is None

    def test_returns_none_on_http_error(self):
        client = self._client([httpx.Response(404)])
        result = fetch_forvo_audio("voda", http_client=client)
        assert result is None

    def test_returns_none_on_mp3_download_error(self):
        b64 = _b64("audios/mp3/test.mp3")
        html_body = _make_forvo_html(b64)
        client = self._client(
            [
                httpx.Response(200, text=html_body),
                httpx.Response(403),
            ]
        )
        result = fetch_forvo_audio("voda", http_client=client)
        assert result is None

    def test_url_encodes_non_ascii_word(self):
        """Words like 'živ' must be URL-encoded in the Forvo path."""
        recorded_urls: list[str] = []

        class RecordingTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                recorded_urls.append(str(request.url))
                return httpx.Response(200, text="<html>no slovenian</html>")

        client = httpx.Client(transport=RecordingTransport())
        fetch_forvo_audio("živ", http_client=client)
        assert "%C5%BEiv" in recorded_urls[0] or "živ" in recorded_urls[0]  # either encoded or httpx encodes

    def test_creates_own_client_when_none_given(self, monkeypatch):
        """fetch_forvo_audio creates and closes its own client when http_client=None."""
        calls: list[str] = []

        class FakeClient:
            def get(self, url, *, timeout):
                calls.append(url)
                raise RuntimeError("network disabled in test")

            def close(self):
                calls.append("close")

        monkeypatch.setattr("app.anki.media.forvo._make_client", lambda: FakeClient())
        result = fetch_forvo_audio("voda")
        assert result is None
        assert "close" in calls
