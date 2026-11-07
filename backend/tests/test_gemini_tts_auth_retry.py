"""A 401 mid-render gets one fresh token and one more try (tunatale-c0da).

Measured 2026-11-06 on the live laptop instance: dozens of Gemini calls 200,
then one ``401 Unauthorized``, and the whole lesson render ended with no audio.
A re-render minutes later succeeded with no 401: the token had gone stale under
a render, the key was fine. ``default_token_provider`` refreshes only when
google-auth itself says the credential is invalid, so nothing refreshed it.

The rule: on a 401, force a refresh (``token_refresher``) and send the request
once more. A second 401 for the same request is a real auth failure (a revoked
key) and fails loudly, exactly as any other 4xx does. Every other 4xx still
fails at once and refreshes nothing.

Faked at the same boundaries as test_gemini_tts.py: respx at the httpx
transport, and google-auth's own credential object.
"""

from __future__ import annotations

import threading

import google.auth.transport.requests
import google.oauth2.service_account
import httpx
import pytest
import respx

from app.audio import gemini_tts
from tests.test_gemini_tts import (
    SYNTHESIS_URL,
    TEXT,
    VOICE,
    _clear_cache,
    _FakeCreds,
    _ok,
    _recording_sleep,
    _retry_sleeps,
    _svc,
    _use_settings_path,
)


def _recording_refresher():
    calls: list[int] = []

    async def refresh() -> None:
        calls.append(1)

    return calls, refresh


def _unauthorized() -> httpx.Response:
    return httpx.Response(401, json={"error": {"message": "Request had invalid authentication credentials."}})


@respx.mock
async def test_a_401_refreshes_the_token_and_the_retry_succeeds(tmp_path):
    route = respx.post(SYNTHESIS_URL).mock(side_effect=[_unauthorized(), _ok()])
    refreshes, refresh = _recording_refresher()
    sleeps, fake_sleep = _recording_sleep()

    await _svc(min_delay=0.3, sleep=fake_sleep, token_refresher=refresh).synthesize(TEXT, VOICE, tmp_path / "o.mp3")

    assert (tmp_path / "o.mp3").read_bytes() == b"ID3-audio"
    assert route.call_count == 2
    assert refreshes == [1]
    assert _retry_sleeps(sleeps, 0.3) == [], "a fresh token needs no backoff"


@respx.mock
async def test_the_retry_sends_the_new_token(tmp_path):
    tokens = iter(["stale-token", "fresh-token"])

    async def provider() -> str:
        return next(tokens)

    route = respx.post(SYNTHESIS_URL).mock(side_effect=[_unauthorized(), _ok()])
    _refreshes, refresh = _recording_refresher()

    await _svc(token_provider=provider, token_refresher=refresh).synthesize(TEXT, VOICE, tmp_path / "o.mp3")

    sent = [call.request.headers["Authorization"] for call in route.calls]
    assert sent == ["Bearer stale-token", "Bearer fresh-token"]


@respx.mock
async def test_a_second_401_for_the_same_request_fails_loudly(tmp_path):
    """A revoked key must stay a hard error: one refresh, then the 4xx rule."""
    route = respx.post(SYNTHESIS_URL).mock(side_effect=[_unauthorized(), _unauthorized(), _ok()])
    refreshes, refresh = _recording_refresher()

    with pytest.raises(RuntimeError) as exc:
        await _svc(token_refresher=refresh).synthesize(TEXT, VOICE, tmp_path / "o.mp3")

    assert "401" in str(exc.value)
    assert "invalid authentication credentials" in str(exc.value)
    assert route.call_count == 2
    assert refreshes == [1]
    assert not (tmp_path / "o.mp3").exists()


@respx.mock
async def test_each_request_gets_its_own_refresh(tmp_path):
    """A long render can outlive more than one token: a later 401 is refreshed too."""
    respx.post(SYNTHESIS_URL).mock(side_effect=[_unauthorized(), _ok(), _unauthorized(), _ok()])
    refreshes, refresh = _recording_refresher()
    svc = _svc(token_refresher=refresh)

    await svc.synthesize(TEXT, VOICE, tmp_path / "a.mp3")
    await svc.synthesize("Maayong gabii!", VOICE, tmp_path / "b.mp3")

    assert refreshes == [1, 1]
    assert (tmp_path / "b.mp3").read_bytes() == b"ID3-audio"


@respx.mock
@pytest.mark.parametrize("status", [400, 403, 404])
async def test_no_other_4xx_refreshes_anything(tmp_path, status):
    route = respx.post(SYNTHESIS_URL).mock(return_value=httpx.Response(status, json={"error": {"message": "no"}}))
    refreshes, refresh = _recording_refresher()

    with pytest.raises(RuntimeError):
        await _svc(token_refresher=refresh).synthesize(TEXT, VOICE, tmp_path / "o.mp3")

    assert route.call_count == 1
    assert refreshes == []


# ------------------------------------------------------------------
# The default refresher
# ------------------------------------------------------------------


async def test_the_default_refresher_refreshes_even_a_credential_google_auth_calls_valid(monkeypatch):
    """That is the whole bug: the provider said 401 while google-auth still said valid."""
    _clear_cache(monkeypatch)
    _use_settings_path(monkeypatch, "/somewhere/sa.json")
    creds = _FakeCreds(token="first-token", valid=True)
    monkeypatch.setattr(
        google.oauth2.service_account.Credentials,
        "from_service_account_file",
        lambda path, scopes=None: creds,
    )
    assert await gemini_tts.default_token_provider() == "first-token"

    await gemini_tts.refresh_default_token()

    assert len(creds.refresh_calls) == 1
    assert isinstance(creds.refresh_calls[0], google.auth.transport.requests.Request)
    assert creds.refresh_threads[0] != threading.get_ident(), "the refresh is blocking I/O: off the event loop"
    assert await gemini_tts.default_token_provider() == "refreshed-1"


async def test_the_default_refresher_builds_the_credential_when_none_is_cached(monkeypatch):
    _clear_cache(monkeypatch)
    _use_settings_path(monkeypatch, "/somewhere/sa.json")
    creds = _FakeCreds(valid=True)
    builds = []

    def fake_from_file(path, scopes=None):
        builds.append(path)
        return creds

    monkeypatch.setattr(google.oauth2.service_account.Credentials, "from_service_account_file", fake_from_file)

    await gemini_tts.refresh_default_token()

    assert builds == ["/somewhere/sa.json"]
    assert len(creds.refresh_calls) == 1


def test_the_service_uses_the_default_refresher_unless_given_one():
    svc = gemini_tts.GeminiTTSService(min_delay=0, model="m")
    assert svc._token_refresher is gemini_tts.refresh_default_token
