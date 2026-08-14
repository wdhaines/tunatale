"""Forvo pronunciation fetching, against the official API.

Network is intercepted with respx at the httpx transport layer, so nothing here
patches ``app.*``: the boundary being faked is a real socket, not one of our own
functions. That is why this file needs no ``mock_allowlist.txt`` entry.

The tests that matter most are the ones asserting *which* non-success outcome
comes back. The scraper this replaced funnelled every failure — markup change,
block, genuinely-no-recording — into a bare ``None``, so missing vocabulary
media was indistinguishable from a broken fetcher and nothing ever surfaced it.
Restoring that conflation would be the regression.
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from app.cards.media.forvo import ForvoOutcome, ForvoResult, fetch_forvo_pronunciation

API_HOST = "https://apifree.forvo.com"
MP3_URL = "https://audio00.forvo.com/audios/mp3/x.mp3"
MP3_BYTES = b"\xff\xfb\x90\x00fake_mp3"

KEY = "test-forvo-key"


def _meta(items: list[dict] | None = None, total: int | None = None) -> dict:
    items = [{"id": 1, "word": "voda", "pathmp3": MP3_URL}] if items is None else items
    return {"attributes": {"total": len(items) if total is None else total}, "items": items}


def _fetch(word: str = "voda", **kw) -> ForvoResult:
    kw.setdefault("language_code", "sl")
    kw.setdefault("api_key", KEY)
    return fetch_forvo_pronunciation(word, **kw)


# ── the happy path ────────────────────────────────────────────────────────────


@respx.mock
def test_returns_found_with_audio_bytes():
    respx.get(url__startswith=API_HOST).mock(return_value=httpx.Response(200, json=_meta()))
    respx.get(MP3_URL).mock(return_value=httpx.Response(200, content=MP3_BYTES))

    result = _fetch()

    assert result.outcome is ForvoOutcome.FOUND
    assert result.audio == MP3_BYTES
    assert result.is_failure is False


@respx.mock
def test_request_carries_key_language_word_and_ordering():
    """The API does the ranking, so we depend on exactly one response field."""
    route = respx.get(url__startswith=API_HOST).mock(return_value=httpx.Response(200, json=_meta(items=[])))

    _fetch("živ", language_code="sl")

    url = str(route.calls[0].request.url)
    assert f"/key/{KEY}/" in url
    assert "/format/json/" in url
    assert "/action/word-pronunciations/" in url
    assert "/language/sl/" in url
    # Non-ASCII must be percent-encoded into the path, not sent raw.
    assert "%C5%BEiv" in url
    # rate-desc + limit/1 means Forvo picks the best-rated recording server-side.
    assert "/order/rate-desc/" in url
    assert "/limit/1/" in url


@respx.mock
def test_language_travels_so_a_norwegian_card_cannot_get_slovene_audio():
    """Regression: the scraper picked the wrong language section for dual-language words."""
    route = respx.get(url__startswith=API_HOST).mock(return_value=httpx.Response(200, json=_meta(items=[])))

    _fetch("hotel", language_code="no")

    assert "/language/no/" in str(route.calls[0].request.url)


# ── the distinction the scraper could not make ────────────────────────────────


@respx.mock
def test_empty_items_is_no_pronunciation_not_a_failure():
    """Forvo answered; the word simply has no recording. That is not an error."""
    respx.get(url__startswith=API_HOST).mock(return_value=httpx.Response(200, json=_meta(items=[], total=0)))

    result = _fetch()

    assert result.outcome is ForvoOutcome.NO_PRONUNCIATION
    assert result.audio is None
    assert result.is_failure is False


@respx.mock
def test_http_error_is_request_failed_not_no_pronunciation():
    respx.get(url__startswith=API_HOST).mock(return_value=httpx.Response(500, text="boom"))

    result = _fetch()

    assert result.outcome is ForvoOutcome.REQUEST_FAILED
    assert result.is_failure is True
    assert "500" in result.detail


@respx.mock
def test_transport_error_is_request_failed():
    respx.get(url__startswith=API_HOST).mock(side_effect=httpx.ConnectError("no route"))

    result = _fetch()

    assert result.outcome is ForvoOutcome.REQUEST_FAILED
    assert result.is_failure is True


@respx.mock
def test_malformed_json_is_request_failed():
    respx.get(url__startswith=API_HOST).mock(return_value=httpx.Response(200, text="<html>not json</html>"))

    result = _fetch()

    assert result.outcome is ForvoOutcome.REQUEST_FAILED
    assert result.is_failure is True


@respx.mock
def test_item_without_pathmp3_is_request_failed_not_no_pronunciation():
    """A shape change must be loud. Silently reading it as 'no recording' is the old bug."""
    respx.get(url__startswith=API_HOST).mock(
        return_value=httpx.Response(200, json=_meta(items=[{"id": 1, "word": "voda"}]))
    )

    result = _fetch()

    assert result.outcome is ForvoOutcome.REQUEST_FAILED
    assert result.is_failure is True


@respx.mock
def test_audio_download_failure_is_its_own_outcome():
    """Metadata succeeded, the mp3 did not — a different problem from a failed lookup."""
    respx.get(url__startswith=API_HOST).mock(return_value=httpx.Response(200, json=_meta()))
    respx.get(MP3_URL).mock(return_value=httpx.Response(403))

    result = _fetch()

    assert result.outcome is ForvoOutcome.AUDIO_FETCH_FAILED
    assert result.audio is None
    assert result.is_failure is True


# ── the missing-key path ──────────────────────────────────────────────────────


@respx.mock
def test_missing_key_degrades_visibly_and_makes_no_request(caplog):
    route = respx.get(url__startswith=API_HOST).mock(return_value=httpx.Response(200, json=_meta()))

    with caplog.at_level(logging.WARNING):
        result = _fetch(api_key="")

    assert result.outcome is ForvoOutcome.NO_API_KEY
    assert result.is_failure is True
    assert not route.called, "must not call the API without a key"
    assert "FORVO_API_KEY" in caplog.text


def test_missing_key_reads_the_setting(monkeypatch):
    """api_key=None falls back to settings rather than assuming a key exists."""
    from app.config import settings

    monkeypatch.setattr(settings, "forvo_api_key", "")

    result = fetch_forvo_pronunciation("voda", language_code="sl")

    assert result.outcome is ForvoOutcome.NO_API_KEY


# ── the key must not leak ─────────────────────────────────────────────────────


@respx.mock
def test_api_key_never_appears_in_detail_or_logs(caplog):
    """The key travels in the URL path, so any naive error string would leak it."""
    respx.get(url__startswith=API_HOST).mock(return_value=httpx.Response(500, text="boom"))

    with caplog.at_level(logging.DEBUG):
        result = _fetch(api_key="super-secret-key")

    assert "super-secret-key" not in result.detail
    assert "super-secret-key" not in caplog.text


# ── defaults ──────────────────────────────────────────────────────────────────


@respx.mock
def test_language_falls_back_to_the_configured_target_language(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "target_language", "no")
    route = respx.get(url__startswith=API_HOST).mock(
        return_value=httpx.Response(200, json={"attributes": {"total": 0}, "items": []})
    )

    fetch_forvo_pronunciation("hei", api_key=KEY)

    assert "/language/no/" in str(route.calls[0].request.url)


@respx.mock
def test_caller_may_supply_its_own_client():
    """The pipeline passes a shared client; the fetcher must not close it."""
    respx.get(url__startswith=API_HOST).mock(return_value=httpx.Response(200, json=_meta()))
    respx.get(MP3_URL).mock(return_value=httpx.Response(200, content=MP3_BYTES))

    with httpx.Client() as client:
        result = _fetch(http_client=client)
        assert result.outcome is ForvoOutcome.FOUND
        # Still usable afterwards — proof we did not close someone else's client.
        assert not client.is_closed


@respx.mock
def test_owned_client_is_closed_when_none_supplied():
    respx.get(url__startswith=API_HOST).mock(return_value=httpx.Response(200, json=_meta()))
    respx.get(MP3_URL).mock(return_value=httpx.Response(200, content=MP3_BYTES))
    created: list[httpx.Client] = []

    def _tracking_client() -> httpx.Client:
        client = httpx.Client()
        created.append(client)
        return client

    monkeypatch_target = "app.cards.media.forvo._make_client"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(monkeypatch_target, _tracking_client)
        assert _fetch().outcome is ForvoOutcome.FOUND

    assert created and created[0].is_closed


# ── the redaction filter, directly ────────────────────────────────────────────


class TestRedactForvoKeyFilter:
    """Unit-level cover for the filter, including records httpx never emits.

    It is attached to a logger we do not own and sees EVERY httpx record in the
    process, so its behaviour on shapes Forvo did not produce is part of its
    contract — a filter that mangles an unrelated log line would be a worse bug
    than the leak it prevents.
    """

    def _record(self, msg, args=None):
        return logging.LogRecord("httpx", logging.INFO, __file__, 1, msg, args, None)

    def _filtered(self, record):
        from app.cards.media.forvo import _RedactForvoKeyFilter

        assert _RedactForvoKeyFilter().filter(record) is True
        return record

    def test_redacts_the_key_in_positional_args(self):
        record = self._record("HTTP Request: %s", ("https://apifree.forvo.com/key/hunter2/format/json/",))

        assert "hunter2" not in self._filtered(record).getMessage()
        assert "/key/***/" in record.getMessage()

    def test_leaves_unrelated_args_untouched(self):
        record = self._record("count %s", (7,))

        assert self._filtered(record).args == (7,)

    def test_leaves_dict_args_untouched(self):
        """%(name)s logging: coercing this to a tuple would break the record.

        LogRecord takes mapping args wrapped in a 1-tuple and unwraps them onto
        ``record.args`` itself, which is why this is constructed that way.
        """
        record = self._record("%(url)s", ({"url": "https://example.test/"},))
        assert record.args == {"url": "https://example.test/"}, "precondition: args unwrapped to a dict"

        assert self._filtered(record).args == {"url": "https://example.test/"}
        assert record.getMessage() == "https://example.test/"

    def test_redacts_a_key_baked_into_the_message(self):
        record = self._record("GET https://apifree.forvo.com/key/hunter2/format/json/")

        assert "hunter2" not in self._filtered(record).getMessage()

    def test_tolerates_a_non_string_message(self):
        record = self._record({"not": "a string"})

        assert self._filtered(record).msg == {"not": "a string"}
