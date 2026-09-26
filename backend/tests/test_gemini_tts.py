"""GeminiTTSService — Google Cloud TTS behind the TTSService port.

Network is intercepted with respx at the httpx transport layer and credentials
are faked at google-auth's own boundary, so nothing here patches ``app.*``:
the boundaries being faked are a real socket and a real credential object, not
one of our own functions. That is why this file needs no
``mock_allowlist.txt`` entry — and no test here reads a real key file.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading

import google.auth
import google.auth.transport.requests
import google.oauth2.service_account
import httpx
import pytest
import respx

from app.audio import gemini_tts
from app.audio.gemini_tts import GeminiTTSService
from app.audio.ports import TTSExhausted

SYNTHESIS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

# A real greeting, a real Gemini voice id for it, and the base64 of a
# three-byte mp3 — small enough to read in an assertion, real enough that the
# response is a plausible provider answer.
TEXT = "Maayong buntag!"
VOICE = "ceb-PH-KoreGemini"
MODEL = "gemini-2.5-flash-tts"
AUDIO_B64 = base64.b64encode(b"ID3-audio").decode()
_OK = {"audioContent": AUDIO_B64}

# The request body the provider is measured to accept, verbatim. speakingRate
# is ALWAYS present, 1.0 included.
REQUEST_BODY = {
    "input": {"text": "Maayong buntag!"},
    "voice": {"languageCode": "ceb-PH", "name": "Kore", "model_name": "gemini-2.5-flash-tts"},
    "audioConfig": {"audioEncoding": "MP3", "speakingRate": 0.8},
}


def _ok() -> httpx.Response:
    return httpx.Response(200, json=_OK)


async def _fake_token() -> str:
    return "test-access-token"


def _svc(**kw):
    # Timing and credentials are injected, not patched: the retry ladder, the
    # inter-request pacing and the auth path are all real code paths here, they
    # just cost nothing. Patching asyncio.sleep or a credential builder would
    # have meant a mock_allowlist.txt entry for a tuning knob.
    kw.setdefault("min_delay", 0)
    kw.setdefault("token_provider", _fake_token)
    return GeminiTTSService(**kw)


def _recording_sleep() -> tuple[list[float], object]:
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    return sleeps, fake_sleep


class _FakeClock:
    """The injected ``now``/``sleep`` pair, as one object.

    ``sleep`` is the ONLY thing that advances time, so every timestamp a test
    records off it is exact and the test costs no wall-clock — the same
    strategy the pacing test below uses, wrapped so the retry ladder can read
    the same clock the transport does.

    The ``asyncio.sleep(0)`` comes BEFORE the advance, and that ordering is
    load-bearing. Two properties depend on it:

    - Without any yield, a task that never blocks runs its whole retry ladder
      without handing the loop to its siblings, so a cohort of callers never
      contends for the request lock and a cooldown test measures nothing.
    - The yield must come first. Advancing the clock and then suspending lets
      one caller's twenty-second wait elapse as a single instantaneous jump
      that carries the clock past the quota window before the waiter the lock
      just released has run a single step. That erases precisely the overlap
      the cooldown exists to fix. Suspending first models a sleep as what it
      is — a window in which OTHER callers make progress.
    """

    def __init__(self, start: float = 0.0):
        self.t = start
        self.slept: list[float] = []

    def now(self) -> float:
        return self.t

    async def sleep(self, delay: float) -> None:
        self.slept.append(delay)
        await asyncio.sleep(0)
        self.t += delay


def _retry_sleeps(sleeps: list[float], min_delay: float) -> list[float]:
    """The ladder's waits, with the pacing delays filtered out.

    Both go through the same injected sleep — that is the point of injecting
    it — so a test that wants to pin the ladder has to say which of the two it
    is looking at. Pacing has its own test below.
    """
    return [s for s in sleeps if s != min_delay]


def _warnings(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == logging.WARNING]


# ------------------------------------------------------------------
# Voice id and rate parsing
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "voice_id,language_code,name",
    [
        ("ceb-PH-KoreGemini", "ceb-PH", "Kore"),
        ("en-US-CharonGemini", "en-US", "Charon"),
    ],
)
@respx.mock
async def test_voice_id_splits_into_language_code_and_name(tmp_path, voice_id, language_code, name):
    """The id carries both the locale and the voice name, and both reach the wire."""
    route = respx.post(SYNTHESIS_URL).mock(return_value=_ok())

    await _svc().synthesize(TEXT, voice_id, tmp_path / "o.mp3")

    voice = json.loads(route.calls[0].request.content)["voice"]
    assert voice["languageCode"] == language_code
    assert voice["name"] == name


@pytest.mark.parametrize(
    "voice_id",
    ["ceb-PH-Gemini", "KoreGemini", "ceb-PH-KoreNeural"],
    ids=["empty-name", "no-locale", "not-a-gemini-id"],
)
@respx.mock
async def test_a_voice_id_that_is_not_a_gemini_id_raises_and_names_it(tmp_path, voice_id):
    """Anything the provider would not accept as a Gemini voice id is refused
    up front, quoting the id — a wrong id must never reach the wire."""
    route = respx.post(SYNTHESIS_URL).mock(return_value=_ok())

    with pytest.raises(ValueError, match=voice_id):
        await _svc().synthesize(TEXT, voice_id, tmp_path / "o.mp3")

    assert route.call_count == 0
    assert not (tmp_path / "o.mp3").exists()


@pytest.mark.parametrize(
    "rate,speaking_rate",
    [("+0%", 1.0), ("-20%", 0.8), ("-40%", 0.6), ("+10%", 1.1)],
)
@respx.mock
async def test_rate_strings_map_to_speaking_rate(tmp_path, rate, speaking_rate):
    """The renderer speaks in percent strings; the provider wants a multiplier.

    ``1 + N/100`` for both signs. speakingRate is sent even at 1.0, so the
    provider never has to guess the default.
    """
    route = respx.post(SYNTHESIS_URL).mock(return_value=_ok())

    await _svc().synthesize(TEXT, VOICE, tmp_path / "o.mp3", rate=rate)

    sent = json.loads(route.calls[0].request.content)["audioConfig"]
    assert sent == {"audioEncoding": "MP3", "speakingRate": speaking_rate}


@respx.mock
async def test_a_rate_that_is_not_a_percentage_raises(tmp_path):
    """A rate the renderer never emits is refused rather than guessed at."""
    route = respx.post(SYNTHESIS_URL).mock(return_value=_ok())

    with pytest.raises(ValueError, match="fast"):
        await _svc().synthesize(TEXT, VOICE, tmp_path / "o.mp3", rate="fast")

    assert route.call_count == 0


# ------------------------------------------------------------------
# The request body and the auth header
# ------------------------------------------------------------------


@respx.mock
async def test_the_request_body_is_exactly_the_measured_shape(tmp_path):
    """The whole body, asserted literally, on the shipped default model.

    Measured against the live endpoint 2026-09-25: model_name nested under
    `voice`, audioEncoding MP3, speakingRate honoured (0.75 -> +29% duration).
    """
    route = respx.post(SYNTHESIS_URL).mock(return_value=_ok())

    await _svc().synthesize(TEXT, VOICE, tmp_path / "o.mp3", rate="-20%")

    raw = route.calls[0].request.content.decode()
    assert json.loads(raw) == REQUEST_BODY
    # model_name is snake_case on the wire — the one thing a tidy-up refactor
    # would "fix" into camelCase, which the provider would silently ignore.
    # Asserted on the raw bytes because httpx serializes compactly (no space
    # after the colon); a parsed assertion would not see the key at all.
    assert '"model_name":"gemini-2.5-flash-tts"' in raw


@respx.mock
async def test_the_access_token_travels_as_a_bearer_token(tmp_path):
    """A Gemini voice needs an OAuth token minted from a service account. An
    API key is rejected outright (403 aiplatform.endpoints.predict)."""

    async def provider():
        return "ya29.fresh-token"

    route = respx.post(SYNTHESIS_URL).mock(return_value=_ok())

    await _svc(token_provider=provider).synthesize(TEXT, VOICE, tmp_path / "o.mp3")

    assert route.calls[0].request.headers["Authorization"] == "Bearer ya29.fresh-token"


@respx.mock
async def test_synthesize_writes_the_decoded_audio_bytes(tmp_path):
    """audioContent is base64; what lands on disk is the decoded mp3."""
    route = respx.post(SYNTHESIS_URL).mock(return_value=_ok())
    out = tmp_path / "nested" / "o.mp3"

    await _svc().synthesize(TEXT, VOICE, out)

    assert out.read_bytes() == b"ID3-audio"
    assert route.called


async def test_list_voices_is_empty():
    """Gemini voices are not enumerable per language and nothing asks for them."""
    assert await _svc().list_voices() == []


# ------------------------------------------------------------------
# Cache
# ------------------------------------------------------------------

# Oracles measured from the key string f"gemini|{model}|{voice_id}|{rate}|{text}"
# hashed and truncated to 16 hex chars. The `gemini|` prefix is what makes
# these entries disjoint from the other adapter's keys in the ONE shared cache
# dir: two key spaces, one directory, and these digests are what pin the
# prefix — change it and every row below breaks.
_ORACLE_DIGESTS = [
    (MODEL, "-20%", "e488d618284a366e.mp3"),
    ("gemini-2.5-pro-tts", "-20%", "f6afa55b0691dffd.mp3"),
    (MODEL, "+0%", "474fb452fc283966.mp3"),
]


@pytest.mark.parametrize("model,rate,digest", _ORACLE_DIGESTS)
def test_cache_digest_oracles(tmp_path, model, rate, digest):
    """The three measured (model, rate) pairs hash to these three files."""
    svc = _svc(cache_dir=tmp_path, model=model)

    assert svc._cache_path(TEXT, VOICE, rate).name == digest


def test_a_model_change_is_a_different_cache_file(tmp_path):
    """A model swap must never serve audio the old model produced.

    Rows 1 and 2 of the oracle table: the model is part of the key, so the
    two are separate entries rather than one silently overwriting the other.
    """
    flash = _svc(cache_dir=tmp_path, model=MODEL)._cache_path(TEXT, VOICE, "-20%")
    pro = _svc(cache_dir=tmp_path, model="gemini-2.5-pro-tts")._cache_path(TEXT, VOICE, "-20%")

    assert (flash.name, pro.name) == ("e488d618284a366e.mp3", "f6afa55b0691dffd.mp3")


@respx.mock
async def test_a_cache_hit_copies_the_file_and_makes_zero_requests(tmp_path):
    """The provider is nondeterministic per call, so the cache is load-bearing:
    a hit must make ZERO requests, not merely a cheaper one."""
    route = respx.post(SYNTHESIS_URL).mock(return_value=_ok())
    svc = _svc(cache_dir=tmp_path / "cache")

    await svc.synthesize(TEXT, VOICE, tmp_path / "one.mp3", rate="-20%")
    assert route.call_count == 1

    await svc.synthesize(TEXT, VOICE, tmp_path / "two.mp3", rate="-20%")

    assert route.call_count == 1, "a cache hit went to the network"
    assert (tmp_path / "two.mp3").read_bytes() == b"ID3-audio"


@respx.mock
async def test_a_miss_writes_both_the_output_and_the_cache_file(tmp_path):
    """The cache file and output_path are both written on a miss."""
    cache = tmp_path / "cache"
    out = tmp_path / "o.mp3"
    respx.post(SYNTHESIS_URL).mock(return_value=_ok())

    await _svc(cache_dir=cache).synthesize(TEXT, VOICE, out, rate="-20%")

    assert out.read_bytes() == b"ID3-audio"
    assert (cache / "e488d618284a366e.mp3").read_bytes() == b"ID3-audio"


@respx.mock
async def test_no_cache_dir_means_no_cache_and_still_renders(tmp_path):
    """cache_dir=None is "no cache", not "no rendering" — the sibling's
    contract, and the one the vocab-card media path relies on."""
    route = respx.post(SYNTHESIS_URL).mock(return_value=_ok())
    out = tmp_path / "o.mp3"

    await _svc().synthesize(TEXT, VOICE, out)
    await _svc().synthesize(TEXT, VOICE, out)

    assert route.call_count == 2
    assert out.read_bytes() == b"ID3-audio"


# ------------------------------------------------------------------
# phonemes / speak_locale — per-token IPA, and the one shape it fits
# ------------------------------------------------------------------


@respx.mock
async def test_phonemes_are_ignored_but_warned_about_once(tmp_path, caplog):
    """A MULTI-WORD utterance with IPA: degrade with a warning.

    Gemini takes no SSML, so per-token IPA cannot be expressed as markup, and an
    instruction only fits one fragment. Degrade with a warning, as the port
    requires of an adapter that cannot express what it was handed — and warn ONCE
    per adapter, because the renderer would otherwise emit a line per render.
    """
    respx.post(SYNTHESIS_URL).mock(return_value=_ok())
    svc = _svc()

    with caplog.at_level(logging.WARNING):
        await svc.synthesize(TEXT, VOICE, tmp_path / "1.mp3", phonemes={"buntag": "bun.tɑɡ"})
        await svc.synthesize(TEXT, VOICE, tmp_path / "2.mp3", phonemes={"buntag": "bun.tɑɡ"})

    assert len(_warnings(caplog)) == 1, f"expected one warning, got {[w.getMessage() for w in caplog.records]}"


@respx.mock
async def test_phonemes_change_neither_the_body_nor_the_cache_key(tmp_path):
    """A MULTI-WORD utterance: identical request body and cache key to phonemes=None.

    The cache key is the load-bearing half: a mapping must not orphan an
    already-rendered clip, or the whole corpus re-renders to say nothing new.
    """
    bodies: list[dict] = []

    async def _record(request):
        bodies.append(json.loads(request.content))
        return _ok()

    respx.post(SYNTHESIS_URL).mock(side_effect=_record)
    with_map = tmp_path / "c1"
    without = tmp_path / "c2"

    await _svc(cache_dir=with_map).synthesize(
        TEXT, VOICE, tmp_path / "1.mp3", rate="-20%", phonemes={"buntag": "bun.tɑɡ"}
    )
    await _svc(cache_dir=without).synthesize(TEXT, VOICE, tmp_path / "2.mp3", rate="-20%", phonemes=None)

    assert bodies[0] == bodies[1]
    assert "prompt" not in bodies[0]["input"]
    assert (with_map / "e488d618284a366e.mp3").exists()
    assert (without / "e488d618284a366e.mp3").exists()


# ------------------------------------------------------------------
# the one shape a Gemini instruction fits: ONE token, spoken from its IPA
# ------------------------------------------------------------------

# A whole word, and the IPA a Cebuano planner spells off it. The user picked
# this shape by ear (tunatale-u8nz): gemini-2.5-flash-tts renders a lone drill
# fragment badly as bare text — extra words, clipped endings, English readings —
# and a wrong-IPA control proved 2.5 follows the IPA when told to.
_ONE_TOKEN = "inyong"
_ONE_TOKEN_IPA = "ʔinjoŋ"
_ONE_TOKEN_PROMPT = (
    "Say only this one Cebuano syllable or word, exactly once, with nothing "
    "before or after it. Pronounce it exactly as the IPA /ʔinjoŋ/."
)


@respx.mock
async def test_a_single_token_is_spoken_from_its_ipa(tmp_path, caplog):
    """The IPA reaches the model as an instruction, in ``input.prompt``.

    Cloud TTS rejects SSML ``<phoneme>`` for Gemini voices, so the prompt is the
    ONLY channel; the locale is named in words because an adapter never sees the
    language name otherwise. And it must NOT also warn "the phoneme mapping is
    ignored" — that would be false, and would be logged on every fragment of
    every lesson.
    """
    bodies: list[dict] = []

    async def _record(request):
        bodies.append(json.loads(request.content))
        return _ok()

    respx.post(SYNTHESIS_URL).mock(side_effect=_record)

    with caplog.at_level(logging.WARNING):
        await _svc().synthesize(_ONE_TOKEN, VOICE, tmp_path / "1.mp3", phonemes={_ONE_TOKEN: _ONE_TOKEN_IPA})

    assert bodies == [
        {
            "input": {"text": _ONE_TOKEN, "prompt": _ONE_TOKEN_PROMPT},
            "voice": {"languageCode": "ceb-PH", "name": "Kore", "model_name": MODEL},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.0},
        }
    ]
    assert _warnings(caplog) == []


@respx.mock
async def test_a_prompted_fragment_gets_its_own_cache_file(tmp_path):
    """The prompt is part of the key; a plain render of the same text is not.

    Both halves matter. A key that ignored the IPA would serve a clip rendered
    from the bare text forever, and a key that ignored the WORDING would keep
    serving a clip an earlier wording produced — which is what PROMPT_VERSION is
    for. The two digests are pinned so neither half can move silently.
    """
    respx.post(SYNTHESIS_URL).mock(return_value=_ok())
    with_ipa = tmp_path / "ipa"
    plain = tmp_path / "plain"

    await _svc(cache_dir=with_ipa).synthesize(
        _ONE_TOKEN, VOICE, tmp_path / "1.mp3", phonemes={_ONE_TOKEN: _ONE_TOKEN_IPA}
    )
    await _svc(cache_dir=plain).synthesize(_ONE_TOKEN, VOICE, tmp_path / "2.mp3")

    assert (with_ipa / "0ea768a64fd8347a.mp3").exists()
    assert (plain / "280228822562fb94.mp3").exists()


@respx.mock
async def test_the_ipa_is_taken_from_the_mapping_not_from_looking_up_the_text(tmp_path):
    """A fragment keyed ``a`` in a chunk spelled ``-a``.

    The renderer's key is the bare word with sentence punctuation off, which
    is not always the text: a chunk whose caption carries a hyphen is keyed
    without one. Looking the entry up by text would find nothing, and the one
    case the prompt exists for would be the one case it skipped.
    """
    bodies: list[dict] = []

    async def _record(request):
        bodies.append(json.loads(request.content))
        return _ok()

    respx.post(SYNTHESIS_URL).mock(side_effect=_record)

    await _svc().synthesize("a", VOICE, tmp_path / "1.mp3", phonemes={"a": "ˈʔa"})

    assert bodies[0]["input"]["prompt"].endswith("the IPA /ˈʔa/.")


@respx.mock
async def test_a_single_token_is_measured_after_its_punctuation(tmp_path):
    """``inyong,`` is one token: the comma is on the phrase, not in the word."""
    bodies: list[dict] = []

    async def _record(request):
        bodies.append(json.loads(request.content))
        return _ok()

    respx.post(SYNTHESIS_URL).mock(side_effect=_record)

    await _svc().synthesize(f"{_ONE_TOKEN},", VOICE, tmp_path / "1.mp3", phonemes={_ONE_TOKEN: _ONE_TOKEN_IPA})

    assert bodies[0]["input"]["text"] == f"{_ONE_TOKEN},"
    assert bodies[0]["input"]["prompt"] == _ONE_TOKEN_PROMPT


@respx.mock
async def test_a_locale_no_language_registers_leaves_the_name_out(tmp_path):
    """No name, not a wrong one: the sentence still says what to do.

    ``xx-XX`` is a well-formed Gemini voice id that no plugin claims, and the
    prompt degrades to "this one syllable or word" rather than naming a language
    that is not being spoken.
    """
    bodies: list[dict] = []

    async def _record(request):
        bodies.append(json.loads(request.content))
        return _ok()

    respx.post(SYNTHESIS_URL).mock(side_effect=_record)

    await _svc().synthesize(_ONE_TOKEN, "xx-XX-TestGemini", tmp_path / "1.mp3", phonemes={_ONE_TOKEN: "ʔinjoŋ"})

    assert bodies[0]["input"]["prompt"] == (
        "Say only this one syllable or word, exactly once, with nothing "
        "before or after it. Pronounce it exactly as the IPA /ʔinjoŋ/."
    )


@respx.mock
async def test_two_phoneme_entries_degrade_to_the_warning(tmp_path, caplog):
    """Two entries means a phrase, and the instruction fits one fragment."""
    respx.post(SYNTHESIS_URL).mock(return_value=_ok())

    with caplog.at_level(logging.WARNING):
        await _svc().synthesize("a", VOICE, tmp_path / "1.mp3", phonemes={"a": "ˈʔa", "ko": "ko"})

    assert len(_warnings(caplog)) == 1


@respx.mock
async def test_a_single_token_with_two_phoneme_entries_warns_about_the_second(tmp_path, caplog):
    """The count is the discriminator, not the text: "a" has no whitespace."""
    respx.post(SYNTHESIS_URL).mock(return_value=_ok())
    bodies: list[dict] = []

    async def _record(request):
        bodies.append(json.loads(request.content))
        return _ok()

    respx.post(SYNTHESIS_URL).mock(side_effect=_record)

    with caplog.at_level(logging.WARNING):
        await _svc().synthesize(_ONE_TOKEN, VOICE, tmp_path / "1.mp3", phonemes={"in": "ʔin", "yong": "joŋ"})

    assert len(_warnings(caplog)) == 1
    assert "prompt" not in bodies[0]["input"]


# A MULTI-word drill step, and the phrase the user heard as "ilubong uglak".
# Plain Gemini got "ugma" wrong in 2 of 3 blind renders; told the reading, 2 of
# 2 right and not stiff (their A/B, 2026-09-25). A second instruction is needed
# because "say only this one syllable or word" is a lie about two words.
_PHRASE = "ilubong ugma"
_PHRASE_IPAS = {"ilubong": "ʔiluboŋ", "ugma": "ʔuɡma"}
_PHRASE_PROMPT = (
    "Say exactly this Cebuano phrase, once, with nothing before or after it. "
    "Pronounce it exactly as the IPA /ʔiluboŋ ʔuɡma/."
)


@respx.mock
async def test_a_phrase_is_spoken_from_the_ipa_of_its_words(tmp_path, caplog):
    """The mirror of the single-token path, and the row this adapter lacked.

    The words are joined into ONE reading, because the model is told one thing
    to say rather than one thing per word. And it must not warn: the mapping is
    honoured, and a warning here would be logged on every drill step of every
    Cebuano lesson.
    """
    bodies: list[dict] = []

    async def _record(request):
        bodies.append(json.loads(request.content))
        return _ok()

    respx.post(SYNTHESIS_URL).mock(side_effect=_record)

    with caplog.at_level(logging.WARNING):
        await _svc().synthesize(_PHRASE, VOICE, tmp_path / "1.mp3", phonemes=_PHRASE_IPAS)

    assert bodies[0]["input"]["prompt"] == _PHRASE_PROMPT
    assert _warnings(caplog) == []


@respx.mock
async def test_a_phrase_whose_map_does_not_cover_its_words_degrades_to_the_warning(tmp_path, caplog):
    """The counts disagree, so there is no reading to give.

    A half-filled map is not a phrase IPA with a missing word — it is a map
    whose alignment to the text is unknown, and speaking it would replace one
    word's reading with another's. A phrase REPEATING a word lands here for
    free: "ko ang ko" is three tokens and the map has collapsed to two.
    """
    respx.post(SYNTHESIS_URL).mock(return_value=_ok())
    bodies: list[dict] = []

    async def _record(request):
        bodies.append(json.loads(request.content))
        return _ok()

    respx.post(SYNTHESIS_URL).mock(side_effect=_record)

    with caplog.at_level(logging.WARNING):
        await _svc(cache_dir=tmp_path).synthesize(_PHRASE, VOICE, tmp_path / "1.mp3", phonemes={"ilubong": "ʔiluboŋ"})
        await _svc(cache_dir=tmp_path).synthesize(
            "ko ang ko", VOICE, tmp_path / "2.mp3", phonemes={"ko": "ko", "ang": "ˈʔaŋ"}
        )

    assert len(_warnings(caplog)) == 2
    assert "prompt" not in bodies[0]["input"]
    assert "prompt" not in bodies[1]["input"]
    # Today's key, unprompted: the fallback is exactly the render that was
    # heard and rejected, not a different one.
    assert (tmp_path / "7e03c60410f5ccb6.mp3").exists()


@respx.mock
async def test_a_prompted_phrase_gets_its_own_cache_file_and_never_the_plain_one(tmp_path):
    """The digests, pinned — and the plain one is the clip this replaces.

    feb482c2706b423c is the keyed render of "ilubong ugma"; 7e03c60410f5ccb6 is
    the bare-text render of the SAME words, which is the audio the user heard
    as "uglak". A phrase render served from it would be this change doing
    nothing at all, silently, for as long as the cache held.
    """
    respx.post(SYNTHESIS_URL).mock(return_value=_ok())
    with_ipa = tmp_path / "ipa"
    plain = tmp_path / "plain"

    await _svc(cache_dir=with_ipa).synthesize(_PHRASE, VOICE, tmp_path / "1.mp3", phonemes=_PHRASE_IPAS)
    await _svc(cache_dir=plain).synthesize(_PHRASE, VOICE, tmp_path / "2.mp3")

    assert (with_ipa / "feb482c2706b423c.mp3").exists()
    assert (plain / "7e03c60410f5ccb6.mp3").exists()


@respx.mock
async def test_speak_locale_is_ignored_silently(tmp_path, caplog):
    """The voice's own languageCode is explicit, so there is nothing to
    override and nothing to warn about."""
    bodies: list[dict] = []

    async def _record(request):
        bodies.append(json.loads(request.content))
        return _ok()

    respx.post(SYNTHESIS_URL).mock(side_effect=_record)

    with caplog.at_level(logging.WARNING):
        await _svc(cache_dir=tmp_path / "c1").synthesize(
            TEXT, VOICE, tmp_path / "1.mp3", rate="+0%", speak_locale="en-US"
        )
        await _svc(cache_dir=tmp_path / "c2").synthesize(TEXT, VOICE, tmp_path / "2.mp3", rate="+0%")

    assert bodies[0] == bodies[1]
    assert _warnings(caplog) == []
    # Same cache entry as a speak_locale=None render — row 3 of the oracle.
    assert (tmp_path / "c1" / "474fb452fc283966.mp3").exists()
    assert (tmp_path / "c2" / "474fb452fc283966.mp3").exists()


# ------------------------------------------------------------------
# Retries
# ------------------------------------------------------------------


def test_the_retry_ladder_is_six_attempts():
    """A throttling episode is exogenous and passes, so patience is the
    point; six rungs, the same sizing the other adapter uses."""
    assert gemini_tts.MAX_RETRIES == 6


@respx.mock
async def test_two_429s_wait_twenty_seconds_each_then_succeed(tmp_path):
    """429 waits a flat 20.0s: the quota is per-minute, so a doubling ladder
    would climb straight past the window it is waiting for.

    The wait is a COOLDOWN now, taken inside the request lock and shared with
    every queued caller, so what carries the twenty seconds is the gap between
    request STARTS rather than a sleep owned by the retry loop. On a fake clock
    the two waits are exact: 0.0, 20.0, 40.0.
    """
    clock = _FakeClock()
    starts: list[float] = []
    queued = [httpx.Response(429), httpx.Response(429), _ok()]

    def next_response(request):
        starts.append(clock.now())
        return queued.pop(0)

    route = respx.post(SYNTHESIS_URL).mock(side_effect=next_response)

    await _svc(min_delay=0.5, sleep=clock.sleep, now=clock.now).synthesize(TEXT, VOICE, tmp_path / "o.mp3")

    assert route.call_count == 3
    assert starts == [0.0, 20.0, 40.0], f"the two 20s cooldown waits did not arrive, starts were {starts}"
    # Pacing is still paid per attempt on top of the cooldown, exactly as the
    # sibling does: a throttled request that freed its slot instantly is what
    # turns a burst into a cascade.
    assert [s for s in clock.slept if s == 0.5] == [0.5, 0.5, 0.5]
    assert (tmp_path / "o.mp3").read_bytes() == b"ID3-audio"


@respx.mock
async def test_a_5xx_climbs_the_doubling_ladder_then_succeeds(tmp_path):
    """Server errors are transient; the ladder is 2**attempt seconds."""
    route = respx.post(SYNTHESIS_URL).mock(side_effect=[httpx.Response(500), httpx.Response(503), _ok()])
    sleeps, fake_sleep = _recording_sleep()

    await _svc(min_delay=0.5, sleep=fake_sleep).synthesize(TEXT, VOICE, tmp_path / "o.mp3")

    assert route.call_count == 3
    assert _retry_sleeps(sleeps, 0.5) == [1.0, 2.0], f"expected the doubling ladder, got {sleeps}"


@respx.mock
async def test_a_transport_error_is_retried(tmp_path):
    """A connection that never happened is transient too."""
    route = respx.post(SYNTHESIS_URL).mock(side_effect=[httpx.ConnectError("no route"), _ok()])
    sleeps, fake_sleep = _recording_sleep()

    await _svc(min_delay=0.5, sleep=fake_sleep).synthesize(TEXT, VOICE, tmp_path / "o.mp3")

    assert route.call_count == 2
    assert _retry_sleeps(sleeps, 0.5) == [1.0]
    assert (tmp_path / "o.mp3").read_bytes() == b"ID3-audio"


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 403, 404])
async def test_any_other_4xx_raises_immediately_with_the_body(tmp_path, status):
    """403 is a credentials or IAM problem; retrying it only spends the quota
    it is already short of. The body is the only place the reason is written
    down, so it has to be in the message."""
    route = respx.post(SYNTHESIS_URL).mock(
        return_value=httpx.Response(status, json={"error": {"message": "nope, not that"}})
    )
    sleeps, fake_sleep = _recording_sleep()

    with pytest.raises(RuntimeError) as exc:
        await _svc(min_delay=0.3, sleep=fake_sleep).synthesize(TEXT, VOICE, tmp_path / "o.mp3")

    assert str(status) in str(exc.value)
    assert "nope, not that" in str(exc.value)
    assert route.call_count == 1
    assert sleeps == [], f"a fatal status must record ZERO sleeps, got {sleeps}"


@respx.mock
async def test_exhaustion_raises_tts_exhausted_and_does_not_sleep_twice(tmp_path):
    """Six failures, five waits, and nothing after the last attempt.

    A trailing sleep is dead time on every terminal failure; on this ladder it
    is a 16-second rung nobody is waiting for.
    """
    route = respx.post(SYNTHESIS_URL).mock(return_value=httpx.Response(500))
    sleeps, fake_sleep = _recording_sleep()

    with pytest.raises(TTSExhausted):
        await _svc(min_delay=0.5, sleep=fake_sleep).synthesize(TEXT, VOICE, tmp_path / "o.mp3")

    assert route.call_count == 6
    assert _retry_sleeps(sleeps, 0.5) == [1.0, 2.0, 4.0, 8.0, 16.0], f"expected five rungs, got {sleeps}"
    assert not (tmp_path / "o.mp3").exists()


# ------------------------------------------------------------------
# Pacing
# ------------------------------------------------------------------


@respx.mock
async def test_consecutive_request_starts_are_at_least_min_delay_apart(tmp_path):
    """The per-minute quota is low — 429s arrive after ~30 fast requests — so
    the gap between request STARTS is the whole budget.

    On a fake clock: the injected sleep is the only thing that advances time,
    so the recorded starts are exact. Asserting the gap is exactly min_delay
    also pins that nothing double-pays it.
    """
    clock = [0.0]
    starts: list[float] = []

    async def _record(request):
        starts.append(clock[0])
        return _ok()

    respx.post(SYNTHESIS_URL).mock(side_effect=_record)

    async def fake_sleep(delay):
        clock[0] += delay

    svc = _svc(min_delay=0.75, sleep=fake_sleep)
    for i in range(4):
        await svc.synthesize(f"line {i}", VOICE, tmp_path / f"{i}.mp3")

    gaps = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
    assert len(starts) == 4, "no request reached the transport — the test proves nothing"
    assert gaps == [0.75, 0.75, 0.75], f"starts were {starts}, gaps {gaps}"


@respx.mock
async def test_one_429_pauses_the_whole_cohort_not_only_the_caller_that_hit_it(tmp_path):
    """A 429 is a fact about the per-MINUTE quota, not about one request, so
    the wait belongs to every queued caller.

    Sleeping the twenty seconds outside the request lock let each queued caller
    fire in turn and be refused: measured on the live instance, 33 of 136
    requests were 429s, all logged as "attempt 1". The cooldown is taken INSIDE
    the lock, so the second caller waits out the same window the first is
    already waiting for instead of spending it finding out.

    The side effect is a server whose quota is exhausted until clock time 20.0,
    so a caller that ignores the cooldown is COUNTED rather than merely failed.
    The assertion is that count, not the presence of a success: three callers
    that all eventually succeed is also what today's code does.
    """
    clock = _FakeClock()
    throttled: list[float] = []

    def quota_exhausted(request):
        if clock.now() < 20.0:
            throttled.append(clock.now())
            return httpx.Response(429)
        return _ok()

    route = respx.post(SYNTHESIS_URL).mock(side_effect=quota_exhausted)
    svc = _svc(min_delay=2.0, sleep=clock.sleep, now=clock.now)

    await asyncio.gather(*(svc.synthesize(f"line {i}", VOICE, tmp_path / f"{i}.mp3") for i in range(3)))

    assert len(throttled) == 1, f"the cohort spent {len(throttled)} requests being refused, at {throttled}"
    assert route.call_count == 4
    for i in range(3):
        assert (tmp_path / f"{i}.mp3").read_bytes() == b"ID3-audio", f"caller {i} did not get audio"


# ------------------------------------------------------------------
# Settings resolution
# ------------------------------------------------------------------


def test_the_model_comes_from_settings(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "gemini_tts_model", "gemini-2.5-pro-tts")

    assert GeminiTTSService()._model == "gemini-2.5-pro-tts"


def test_an_explicit_model_beats_the_setting(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "gemini_tts_model", "gemini-2.5-pro-tts")

    assert GeminiTTSService(model="gemini-2.5-flash-tts")._model == "gemini-2.5-flash-tts"


def test_the_min_delay_comes_from_settings(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "gemini_tts_min_delay", 3.5)

    assert GeminiTTSService()._min_delay == 3.5


def test_an_explicit_min_delay_beats_the_setting(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "gemini_tts_min_delay", 3.5)

    assert GeminiTTSService(min_delay=0.25)._min_delay == 0.25


def test_the_shipped_settings_defaults():
    """Two seconds between starts, against a quota that 429s after ~30 fast
    requests, and an empty credential path meaning "use google-auth's own
    default discovery" so the app boots without this set.

    Read off the FIELD, not the loaded instance: `settings` carries whatever
    this machine's .env says, so asserting the shipped default against it
    would be asserting the developer's .env.
    """
    from app.config import Settings

    defaults = {name: field.default for name, field in Settings.model_fields.items()}

    assert defaults["gemini_tts_model"] == "gemini-2.5-flash-tts"
    assert defaults["gemini_tts_min_delay"] == 2.0
    assert defaults["google_application_credentials"] == ""


# ------------------------------------------------------------------
# The default token provider
# ------------------------------------------------------------------------

_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class _FakeCreds:
    """A google-auth Credentials stand-in that records what it was asked to do.

    No key file is read and no token endpoint is contacted: the boundary being
    faked is google-auth's own object, not one of ours.
    """

    def __init__(self, token: str = "first-token", valid: bool = True):
        self.token = token
        self.valid = valid
        self.refresh_calls: list[object] = []
        self.refresh_threads: list[int] = []

    def refresh(self, request):
        self.refresh_calls.append(request)
        self.refresh_threads.append(threading.get_ident())
        self.valid = True
        self.token = f"refreshed-{len(self.refresh_calls)}"


def _clear_cache(monkeypatch):
    monkeypatch.setattr(gemini_tts, "_CREDENTIALS", None)


def _use_settings_path(monkeypatch, value: str):
    from app.config import settings

    monkeypatch.setattr(settings, "google_application_credentials", value)


def _boom(*args, **kwargs):
    raise AssertionError("a real credential lookup was attempted in a test")


async def test_the_token_provider_reads_a_service_account_file(monkeypatch):
    """A configured path wins over discovery.

    Pydantic loads .env into settings, NOT into os.environ, so google-auth's
    own GOOGLE_APPLICATION_CREDENTIALS lookup would never see the value — which
    is why the path is a setting and passed in explicitly.
    """
    _clear_cache(monkeypatch)
    _use_settings_path(monkeypatch, "/somewhere/sa.json")
    creds = _FakeCreds()
    seen = {}

    def fake_from_file(path, scopes=None):
        seen["path"] = path
        seen["scopes"] = scopes
        return creds

    monkeypatch.setattr(google.oauth2.service_account.Credentials, "from_service_account_file", fake_from_file)

    assert await gemini_tts.default_token_provider() == "first-token"
    assert seen["path"] == "/somewhere/sa.json"
    assert list(seen["scopes"]) == [_SCOPE]
    assert creds.refresh_calls == [], "a valid credential must not be refreshed"


async def test_an_empty_path_falls_back_to_default_discovery(monkeypatch):
    """The other way round: with no path configured, google-auth's own
    discovery (workload identity, metadata server, gcloud) is the source."""
    _clear_cache(monkeypatch)
    _use_settings_path(monkeypatch, "")
    creds = _FakeCreds()
    seen = {}

    def fake_default(scopes=None):
        seen["scopes"] = scopes
        return creds, "some-project"

    monkeypatch.setattr(google.auth, "default", fake_default)

    assert await gemini_tts.default_token_provider() == "first-token"
    assert list(seen["scopes"]) == [_SCOPE]
    assert creds.refresh_calls == []


async def test_an_invalid_credential_is_refreshed_off_the_event_loop(monkeypatch):
    """The refresh is blocking I/O, so it runs in a worker thread — on the
    loop it would stall every other render task for the length of an HTTP round
    trip. Asserted by thread identity, not by patching asyncio."""
    _clear_cache(monkeypatch)
    _use_settings_path(monkeypatch, "/somewhere/sa.json")
    creds = _FakeCreds(token="stale", valid=False)
    monkeypatch.setattr(
        google.oauth2.service_account.Credentials,
        "from_service_account_file",
        lambda path, scopes=None: creds,
    )

    token = await gemini_tts.default_token_provider()

    assert token == "refreshed-1"
    assert creds.refresh_threads and creds.refresh_threads[0] != threading.get_ident()
    assert isinstance(creds.refresh_calls[0], google.auth.transport.requests.Request)


async def test_the_credential_object_is_cached_across_calls(monkeypatch):
    """The cache holds the OBJECT, not the token: building credentials is the
    expensive part, and a call arriving after expiry must reuse the object and
    refresh it rather than build a second one."""
    _clear_cache(monkeypatch)
    _use_settings_path(monkeypatch, "/somewhere/sa.json")
    creds = _FakeCreds()
    builds = []

    def fake_from_file(path, scopes=None):
        builds.append(path)
        return creds

    monkeypatch.setattr(google.oauth2.service_account.Credentials, "from_service_account_file", fake_from_file)

    assert await gemini_tts.default_token_provider() == "first-token"
    assert await gemini_tts.default_token_provider() == "first-token"
    assert builds == ["/somewhere/sa.json"], "credentials were rebuilt on the second call"

    # Expire it, and the SAME object refreshes.
    creds.valid = False
    assert await gemini_tts.default_token_provider() == "refreshed-1"
    assert builds == ["/somewhere/sa.json"]


@respx.mock
async def test_an_unconfigured_adapter_uses_the_default_provider(tmp_path, monkeypatch):
    """No token_provider argument means the module-level default runs — here
    proven by the adapter rendering with nothing but google-auth faked."""
    _clear_cache(monkeypatch)
    _use_settings_path(monkeypatch, "")
    creds = _FakeCreds(token="from-discovery")
    monkeypatch.setattr(google.auth, "default", lambda scopes=None: (creds, "proj"))
    route = respx.post(SYNTHESIS_URL).mock(return_value=_ok())

    await GeminiTTSService(min_delay=0).synthesize(TEXT, VOICE, tmp_path / "o.mp3")

    assert route.calls[0].request.headers["Authorization"] == "Bearer from-discovery"
    assert (tmp_path / "o.mp3").read_bytes() == b"ID3-audio"


@respx.mock
async def test_a_cache_hit_costs_neither_a_request_nor_a_token(tmp_path, monkeypatch):
    """Both axes of "free", because both are load-bearing.

    The provider is nondeterministic per call and the quota is small, so a
    warm cache that still mints an access token would put a network round trip
    in front of a cache hit. The cache is primed with an injected provider,
    then read by an adapter that has only the default one.
    """
    _clear_cache(monkeypatch)
    _use_settings_path(monkeypatch, "")
    monkeypatch.setattr(google.oauth2.service_account.Credentials, "from_service_account_file", _boom)
    monkeypatch.setattr(google.auth, "default", _boom)
    cache = tmp_path / "cache"
    # One route, re-pointed between the two phases: `respx.post` for a URL
    # already on the router returns that SAME route rather than adding one, so
    # a second registration would silently re-mock the route under test.
    route = respx.post(SYNTHESIS_URL).mock(return_value=_ok())

    await _svc(cache_dir=cache).synthesize(TEXT, VOICE, tmp_path / "one.mp3", rate="-20%")
    assert route.call_count == 1

    route.mock(side_effect=AssertionError("a cache hit went to the wire"))
    await GeminiTTSService(min_delay=0, cache_dir=cache).synthesize(TEXT, VOICE, tmp_path / "two.mp3", rate="-20%")

    assert route.call_count == 1, "a cache hit went to the wire"
    assert (tmp_path / "two.mp3").read_bytes() == b"ID3-audio"


# ── Audit fixes (orchestrator, 2026-09-25) ───────────────────────────────────


async def test_concurrent_callers_never_overlap_a_request_with_anothers_pacing(tmp_path):
    """The renderer gathers sections concurrently, so pacing must hold ACROSS
    callers, not just between one caller's calls. Pacing that sleeps after each
    request but lets a second task start meanwhile turns a render into a burst
    against a per-minute quota. The sibling holds a semaphore across the request
    AND its pacing sleep; so must this one.

    The log records when each pacing sleep begins and ends, and the sleep
    yields to the loop. A request that starts between some pace-begin and its
    pace-end is the overlap. A plain "count the starts" log cannot see it: with
    a zero-cost sleep the starts interleave identically either way.
    """
    import asyncio

    log: list[str] = []

    async def pacing_sleep(delay):
        log.append("pace-begin")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        log.append("pace-end")

    async def yielding_token():
        await asyncio.sleep(0)
        return "t"

    def on_request(request):
        log.append("start")
        return _ok()

    svc = _svc(min_delay=2.0, sleep=pacing_sleep, token_provider=yielding_token)
    with respx.mock:
        respx.post(SYNTHESIS_URL).mock(side_effect=on_request)
        await asyncio.gather(*(svc.synthesize(f"line {i}", VOICE, tmp_path / f"{i}.mp3") for i in range(3)))

    assert log.count("start") == 3
    in_pacing = 0
    for event in log:
        if event == "pace-begin":
            in_pacing += 1
        elif event == "pace-end":
            in_pacing -= 1
        else:
            assert in_pacing == 0, f"a request started during another's pacing sleep: {log}"


@pytest.mark.parametrize("voice_id", ["ceb-PH-KoreGemini\n", "ceb-PH-KoreGemini\n\n"])
async def test_a_trailing_newline_is_not_a_voice_id(tmp_path, voice_id):
    """``$`` also matches before a final newline; the id must match exactly."""
    with pytest.raises(ValueError, match="not a Gemini voice id"):
        await _svc().synthesize(TEXT, voice_id, tmp_path / "out.mp3")


@pytest.mark.parametrize("rate", ["-100%", "-80%", "+301%", "-20%\n"])
async def test_a_rate_outside_what_the_provider_accepts_raises_locally(tmp_path, rate):
    """The provider accepts speakingRate 0.25 to 4.0. Outside that, a render
    would fail as an HTTP 400 mid-lesson; refusing it here names the rate."""
    with pytest.raises(ValueError, match="rate"):
        await _svc().synthesize(TEXT, VOICE, tmp_path / "out.mp3", rate=rate)


@pytest.mark.parametrize(("rate", "speaking_rate"), [("-75%", 0.25), ("+300%", 4.0), ("-70%", 0.3)])
async def test_the_rate_edges_are_accepted_and_carry_no_float_noise(tmp_path, rate, speaking_rate):
    """The inclusive bounds pass, and -70% is sent as 0.3, not
    0.30000000000000004: the body is what the provider sees."""
    with respx.mock:
        route = respx.post(SYNTHESIS_URL).mock(return_value=_ok())
        await _svc().synthesize(TEXT, VOICE, tmp_path / "out.mp3", rate=rate)
    sent = json.loads(route.calls.last.request.content)["audioConfig"]["speakingRate"]
    assert sent == speaking_rate
    assert repr(sent) == repr(speaking_rate)
