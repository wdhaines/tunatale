"""Tests for S3.8: Forvo audio fetcher."""

from __future__ import annotations

import base64

import httpx

from app.cards.media.forvo import ForvoOutcome, _extract_mp3_url, fetch_forvo_pronunciation


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
        url = _extract_mp3_url(html)
        assert url == "https://audio00.forvo.com/mp3/audios/mp3/abc123.mp3"

    def test_returns_url_with_single_quote_container(self):
        b64 = _b64("audios/mp3/xyz.mp3")
        html = _make_forvo_html(b64, use_single_quotes=True)
        url = _extract_mp3_url(html)
        assert url == "https://audio00.forvo.com/mp3/audios/mp3/xyz.mp3"

    def test_returns_none_when_no_slovenian_section(self):
        html = "<html><div>no slovenian here</div></html>"
        assert _extract_mp3_url(html) is None

    def test_returns_none_when_no_article_in_chunk(self):
        html = '<div id="language-container-sl"><span>no article tag</span></div>'
        assert _extract_mp3_url(html) is None

    def test_returns_none_when_no_play_call(self):
        html = '<div id="language-container-sl"><article><p>no play call</p></article></div>'
        assert _extract_mp3_url(html) is None

    def test_returns_none_when_base64_decodes_to_invalid_utf8(self):
        # b"\xff\xfe" encodes to "//4=" in base64 (valid base64 chars, invalid UTF-8)
        invalid_utf8_b64 = "//4="
        html = f'<div id="language-container-sl"><article><span onclick="Play(1,\'{invalid_utf8_b64}\')"></span></article></div>'
        assert _extract_mp3_url(html) is None

    def test_make_client_returns_httpx_client(self):
        from app.cards.media.forvo import _make_client

        client = _make_client()
        assert isinstance(client, httpx.Client)
        client.close()

    def test_matches_requested_language_container(self):
        # Backlog #28: a Norwegian card must scrape the "no" section, not "sl".
        b64 = _b64("audios/mp3/norsk.mp3")
        html = f'<div id="language-container-no"><article><span onclick="Play(1,\'{b64}\')"></span></article></div>'
        assert _extract_mp3_url(html, language_code="no") == "https://audio00.forvo.com/mp3/audios/mp3/norsk.mp3"

    def test_returns_none_when_only_other_language_container_present(self):
        # A "no"-only page must NOT hand back a URL when Slovene is requested —
        # this is the bug where a dual-language word ("hotel") got the wrong voice.
        b64 = _b64("audios/mp3/norsk.mp3")
        html = f'<div id="language-container-no"><article><span onclick="Play(1,\'{b64}\')"></span></article></div>'
        assert _extract_mp3_url(html, language_code="sl") is None


# ── fetch_forvo_pronunciation ──────────────────────────────────────────────────

# The scraper used to answer every one of these cases with a bare ``None``, so a
# Cloudflare block, a Forvo markup change and "nobody recorded this word" were
# indistinguishable at the call site — which is how Forvo could stop working
# without anything surfacing it. Each test below pins one outcome to one cause.

_CLOUDFLARE_BODY = (
    '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>'
    '</head><body><div class="cf-browser-verification"></div></body></html>'
)


class _SequenceTransport(httpx.BaseTransport):
    """Returns a fixed sequence of responses."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._responses.pop(0)


class TestFetchForvoPronunciation:
    def _client(self, responses: list[httpx.Response]) -> httpx.Client:
        return httpx.Client(transport=_SequenceTransport(responses))

    def test_found_returns_audio_and_found_outcome(self):
        b64 = _b64("audios/mp3/test.mp3")
        mp3_bytes = b"\xff\xfb\x90\x00fake_mp3"
        client = self._client(
            [
                httpx.Response(200, text=_make_forvo_html(b64)),
                httpx.Response(200, content=mp3_bytes),
            ]
        )
        result = fetch_forvo_pronunciation("voda", language_code="sl", http_client=client)
        assert result.outcome is ForvoOutcome.FOUND
        assert result.audio == mp3_bytes
        assert result.is_failure is False

    def test_no_container_for_our_language_is_no_pronunciation_not_failure(self):
        """Other languages have a section, ours doesn't — Forvo answered, the answer is 'none'."""
        html_body = '<html><div id="language-container-no"><article>x</article></div></html>'
        client = self._client([httpx.Response(200, text=html_body)])
        result = fetch_forvo_pronunciation("voda", language_code="sl", http_client=client)
        assert result.outcome is ForvoOutcome.NO_PRONUNCIATION
        assert result.is_failure is False
        assert result.audio is None

    def test_container_without_play_call_is_no_pronunciation(self):
        html_body = '<html><div id="language-container-sl"><article><p>no play</p></article></div></html>'
        client = self._client([httpx.Response(200, text=html_body)])
        result = fetch_forvo_pronunciation("voda", language_code="sl", http_client=client)
        assert result.outcome is ForvoOutcome.NO_PRONUNCIATION
        assert result.is_failure is False

    def test_no_language_containers_at_all_is_markup_changed(self):
        """A 200 with no language-container anywhere means the scraper can no longer parse Forvo.

        Distinct from NO_PRONUNCIATION on purpose: 'nobody recorded this word' is
        normal and must stay quiet, while 'we can no longer read the page' is a
        standing defect that needs to be loud.
        """
        client = self._client([httpx.Response(200, text="<html><body>totally new markup</body></html>")])
        result = fetch_forvo_pronunciation("voda", language_code="sl", http_client=client)
        assert result.outcome is ForvoOutcome.MARKUP_CHANGED
        assert result.is_failure is True

    def test_cloudflare_challenge_is_blocked(self):
        """The 2026-08-15 signature: HTTP 403 plus a 'Just a moment...' interstitial."""
        client = self._client([httpx.Response(403, text=_CLOUDFLARE_BODY)])
        result = fetch_forvo_pronunciation("voda", language_code="sl", http_client=client)
        assert result.outcome is ForvoOutcome.BLOCKED
        assert result.is_failure is True
        assert "403" in result.detail

    def test_plain_http_error_is_request_failed_not_blocked(self):
        """A 404 is not a block — conflating them would make the block signal useless."""
        client = self._client([httpx.Response(404)])
        result = fetch_forvo_pronunciation("voda", language_code="sl", http_client=client)
        assert result.outcome is ForvoOutcome.REQUEST_FAILED
        assert result.is_failure is True

    def test_403_without_challenge_body_is_request_failed(self):
        """Only the challenge body earns BLOCKED; a bare 403 could be anything."""
        client = self._client([httpx.Response(403, text="<html>go away</html>")])
        result = fetch_forvo_pronunciation("voda", language_code="sl", http_client=client)
        assert result.outcome is ForvoOutcome.REQUEST_FAILED

    def test_mp3_download_failure_is_its_own_outcome(self):
        """Metadata parsed fine, the audio host failed — a different thing to chase."""
        b64 = _b64("audios/mp3/test.mp3")
        client = self._client(
            [
                httpx.Response(200, text=_make_forvo_html(b64)),
                httpx.Response(500),
            ]
        )
        result = fetch_forvo_pronunciation("voda", language_code="sl", http_client=client)
        assert result.outcome is ForvoOutcome.AUDIO_FETCH_FAILED
        assert result.is_failure is True

    def test_transport_error_is_request_failed_and_never_raises(self):
        class ExplodingTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                raise httpx.ConnectError("no route to host")

        client = httpx.Client(transport=ExplodingTransport())
        result = fetch_forvo_pronunciation("voda", language_code="sl", http_client=client)
        assert result.outcome is ForvoOutcome.REQUEST_FAILED
        assert "ConnectError" in result.detail

    def test_url_encodes_non_ascii_word(self):
        """Words like 'živ' must be URL-encoded in the Forvo path."""
        recorded_urls: list[str] = []

        class RecordingTransport(httpx.BaseTransport):
            def handle_request(self, request: httpx.Request) -> httpx.Response:
                recorded_urls.append(str(request.url))
                return httpx.Response(200, text="<html>no slovenian</html>")

        client = httpx.Client(transport=RecordingTransport())
        fetch_forvo_pronunciation("živ", language_code="sl", http_client=client)
        assert "%C5%BEiv" in recorded_urls[0] or "živ" in recorded_urls[0]

    def test_creates_own_client_when_none_given(self, monkeypatch):
        """Creates and closes its own client when http_client=None."""
        calls: list[str] = []

        class FakeClient:
            def get(self, url, *, timeout):
                calls.append(url)
                raise RuntimeError("network disabled in test")

            def close(self):
                calls.append("close")

        monkeypatch.setattr("app.cards.media.forvo._make_client", lambda: FakeClient())
        result = fetch_forvo_pronunciation("voda", language_code="sl")
        assert result.outcome is ForvoOutcome.REQUEST_FAILED
        assert "close" in calls

    def test_defaults_language_to_configured_target(self, monkeypatch):
        monkeypatch.setattr("app.config.settings.target_language", "sl")
        b64 = _b64("audios/mp3/test.mp3")
        client = self._client(
            [
                httpx.Response(200, text=_make_forvo_html(b64)),
                httpx.Response(200, content=b"mp3"),
            ]
        )
        assert fetch_forvo_pronunciation("voda", http_client=client).outcome is ForvoOutcome.FOUND

    def test_logs_a_warning_naming_the_cause_on_failure(self, caplog):
        """The whole point: a failure must leave a readable trace, not vanish."""
        client = self._client([httpx.Response(403, text=_CLOUDFLARE_BODY)])
        with caplog.at_level("WARNING"):
            fetch_forvo_pronunciation("voda", language_code="sl", http_client=client)
        assert "voda" in caplog.text
        assert "blocked" in caplog.text.lower()

    def test_does_not_warn_when_nobody_recorded_the_word(self, caplog):
        """NO_PRONUNCIATION is a normal answer. Warning here would drown the real signal."""
        html_body = '<html><div id="language-container-no"><article>x</article></div></html>'
        client = self._client([httpx.Response(200, text=html_body)])
        with caplog.at_level("WARNING"):
            fetch_forvo_pronunciation("voda", language_code="sl", http_client=client)
        assert caplog.text == ""
