"""LanGeek's picture dictionary as a card-image source (tunatale-xsbh).

The fixtures are two real responses (2026-10-27): ``happiness`` (one illustrated
sense) and ``band`` (a PREFIX search: ten entries, the exact one with three
illustrated senses of eighteen). Every request goes through httpx.MockTransport.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.cards.media.langeek import (
    LangeekSense,
    _Throttle,
    base_forms,
    build_sense_choice_prompt,
    choose_langeek_sense,
    download_langeek,
    langeek_term,
    search_langeek,
)
from app.config import settings
from app.llm.call_sites import CallSite

FIXTURES = Path(__file__).parent / "fixtures" / "langeek"
API = "https://api.example/word/"


def _fixture(name: str) -> list:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class _NoWait(_Throttle):
    def __init__(self) -> None:
        super().__init__(1.0, lambda: 0.0, lambda _s: None)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _search(term: str, body=None, *, status: int = 200, raw: bytes | None = None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if raw is not None:
            return httpx.Response(status, content=raw)
        return httpx.Response(status, json=body)

    result = search_langeek(term, http_client=_client(handler), api_url=API, throttle=_NoWait())
    return result, seen


# ── The lookup word ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("gloss", "term"),
    [
        ("to want; to mean", "want"),
        ("mother", "mother"),
        ("be there (near you); there is", "be there"),
        ("(near you)", None),
        ("Happiness", "happiness"),
        ("over  there,  yonder", "over there"),
        ("", None),
        # live misses, 2026-10-27: LanGeek's entries carry no punctuation
        ("Bye!", "bye"),
        ("where?", "where"),
        ("don't!", "don't"),
        ("?!", None),
    ],
)
def test_the_lookup_word_is_the_glosss_first_alternative(gloss, term):
    assert langeek_term(gloss) == term


# ── Search ──────────────────────────────────────────────────────────────────


def test_one_illustrated_sense_and_the_request_it_sends():
    result, seen = _search("happiness", _fixture("happiness"))

    assert result.status == "ok"
    assert [(s.sense_id, s.pos, s.definition) for s in result.senses] == [
        (86262, "noun", "the feeling of being happy and well")
    ]
    assert result.senses[0].photo_url == "https://cdn.langeek.co/photo/51927/original/?type=jpeg"
    assert seen[0].url.params["term"] == "happiness"
    assert seen[0].url.params["filter"] == ",inCategory,photo"
    assert str(seen[0].url).startswith(API)


def test_a_prefix_search_yields_only_the_exact_entrys_illustrated_senses():
    result, _ = _search("band", _fixture("band"))

    assert [s.sense_id for s in result.senses] == [14799, 14807, 14808]  # never bandage's or bandana's


def test_the_exact_entry_is_matched_without_regard_to_case():
    result, _ = _search("Happiness", _fixture("happiness"))
    assert [s.sense_id for s in result.senses] == [86262]


@pytest.mark.parametrize(
    "body",
    [
        [],
        [{"entry": "bandage", "translations": {"noun": [{"id": 1, "translation": "x", "wordPhoto": {"photo": "u"}}]}}],
        [{"entry": "band", "translations": {"noun": [{"id": 2, "translation": "x", "wordPhoto": None}]}}],
        [{"entry": "band", "translations": None}],
    ],
    ids=["empty", "prefix-only", "no-photo", "no-senses"],
)
def test_nothing_illustrated_for_the_exact_word_is_no_results(body):
    result, _ = _search("band", body)
    assert (result.status, result.senses) == ("no_results", [])


@pytest.mark.parametrize(
    ("status", "raw", "body"),
    [(500, None, []), (200, b"<html>not json", None), (200, None, {"not": "a list"})],
    ids=["http-500", "bad-json", "wrong-shape"],
)
def test_a_failed_search_is_an_api_error(status, raw, body, caplog):
    result, _ = _search("band", body, status=status, raw=raw)
    assert (result.status, result.senses) == ("api_error", [])
    assert "LanGeek search 'band'" in caplog.text


def test_a_timeout_is_an_api_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    result = search_langeek("band", http_client=_client(handler), api_url=API, throttle=_NoWait())
    assert result.status == "api_error"


def test_an_empty_endpoint_setting_turns_the_source_off_without_a_request():
    # The test suite's own pin (conftest): no card-creating test can reach LanGeek.
    assert settings.langeek_api_url == ""
    calls = []
    result = search_langeek("band", http_client=_client(lambda r: calls.append(r)), throttle=_NoWait())
    assert (result.status, calls) == ("disabled", [])


def test_the_default_client_is_opened_and_closed_per_call(monkeypatch):
    # No client passed: the module makes its own, and must close it.
    closed = []
    real_client = httpx.Client

    class Recording(real_client):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])), **kwargs)

        def close(self):
            closed.append(True)
            super().close()

    monkeypatch.setattr(httpx, "Client", Recording)
    assert search_langeek("band", api_url=API, throttle=_NoWait()).status == "no_results"
    assert download_langeek(LangeekSense(1, "noun", "d", "https://cdn/x?type=jpeg")) == (
        b"[]",
        "jpg",
        "https://cdn/x?type=jpeg",
    )
    assert closed == [True, True]


# ── Throttle ────────────────────────────────────────────────────────────────


def test_the_throttle_spaces_calls_one_second_apart():
    clock = [100.0]
    slept: list[float] = []
    throttle = _Throttle(1.0, lambda: clock[0], slept.append)

    throttle.wait()  # first call: nothing to wait for
    clock[0] = 100.25
    throttle.wait()  # 0.25 s later: waits out the remaining 0.75 s
    clock[0] = 102.0
    throttle.wait()  # well past the interval: no wait

    assert slept == [pytest.approx(0.75)]


# ── Download ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "ctype", "ext"),
    [
        ("https://cdn.langeek.co/photo/1/original/?type=jpeg", "image/jpeg", "jpg"),
        ("https://cdn.langeek.co/photo/1/original/?type=png", "", "png"),
        ("https://cdn.langeek.co/photo/1/original/", "image/png", "png"),
    ],
)
def test_download_returns_bytes_extension_and_url(url, ctype, ext):
    client = _client(lambda r: httpx.Response(200, content=b"IMG", headers={"content-type": ctype}))
    assert download_langeek(LangeekSense(1, "noun", "d", url), http_client=client) == (b"IMG", ext, url)


@pytest.mark.parametrize("response", [httpx.Response(404, content=b"no"), httpx.Response(200, content=b"")])
def test_a_failed_download_is_none(response, caplog):
    sense = LangeekSense(1, "noun", "d", "https://cdn/x?type=jpeg")
    assert download_langeek(sense, http_client=_client(lambda r: response)) is None
    assert "LanGeek download" in caplog.text


def test_a_network_error_on_download_is_none():
    def handler(request):
        raise httpx.ConnectError("down", request=request)

    assert download_langeek(LangeekSense(1, "noun", "d", "https://cdn/x"), http_client=_client(handler)) is None


# ── Choosing the sense ──────────────────────────────────────────────────────

BAND = [
    LangeekSense(14799, "noun", "a narrow strip of material", "u1"),
    LangeekSense(14807, "noun", "a group of people", "u2"),
    LangeekSense(14808, "noun", "a group of musicians", "u3"),
]


class _LLM:
    def __init__(self, reply: str | Exception) -> None:
        self.reply, self.calls = reply, []

    async def complete(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


async def test_one_sense_is_taken_without_asking():
    llm = _LLM("3")
    assert await choose_langeek_sense("banda", "band", BAND[:1], llm=llm) == BAND[0]
    assert llm.calls == []


async def test_the_model_picks_among_several():
    llm = _LLM("3")
    assert (
        await choose_langeek_sense("banda", "band (music)", BAND, llm=llm, source_sentence="Nindot ang banda.")
        == BAND[2]
    )
    prompt, kwargs = llm.calls[0]
    assert kwargs["call_site"] == CallSite.MEDIA_SENSE
    assert kwargs["temperature"] == 0.0
    assert prompt == build_sense_choice_prompt("banda", "band (music)", BAND, "Nindot ang banda.")


def test_the_prompt_numbers_each_definition_and_carries_the_sentence():
    prompt = build_sense_choice_prompt("banda", "band", BAND, "Nindot ang banda.")
    assert prompt.splitlines() == [
        "Word: banda",
        "Meaning: band",
        "Sentence: Nindot ang banda.",
        "1. (noun) a narrow strip of material",
        "2. (noun) a group of people",
        "3. (noun) a group of musicians",
    ]
    assert "Sentence:" not in build_sense_choice_prompt("banda", "band", BAND)


@pytest.mark.parametrize("reply", ["0", "9", "none of them", "", RuntimeError("groq down")])
async def test_no_usable_answer_is_no_sense(reply):
    assert await choose_langeek_sense("banda", "band", BAND, llm=_LLM(reply)) is None


async def test_several_senses_and_no_model_is_no_sense():
    # A homograph's wrong illustration is never guessed at: Pixabay gets the word instead.
    assert await choose_langeek_sense("banda", "band", BAND, llm=None) is None


async def test_no_senses_is_no_sense():
    assert await choose_langeek_sense("banda", "band", [], llm=_LLM("1")) is None


# ── The word's base form, when the gloss is inflected ───────────────────────
# Live misses, 2026-10-27: hibalo 'knew', sapatos 'shoes', pabuto 'fireworks',
# apektado 'affected' found no entry, though LanGeek illustrates know, shoe.


@pytest.mark.parametrize(
    ("term", "forms"),
    [
        ("knew", ["know"]),
        ("children", ["child"]),
        ("shoes", ["shoe"]),
        ("fireworks", ["firework"]),
        ("cities", ["city"]),
        ("boxes", ["box", "boxe"]),
        ("affected", ["affect", "affecte"]),
        ("liked", ["lik", "like"]),
        ("stopped", ["stopp", "stoppe", "stop"]),
        ("cooking", ["cook", "cooke"]),
        ("glass", []),  # -ss is no plural
        ("take long", []),  # a phrase: no guess at which word inflects
        ("is", []),  # too short to strip
    ],
)
def test_base_forms_are_the_irregular_form_then_the_stripped_endings(term, forms):
    assert base_forms(term) == forms


def _by_term(bodies: dict[str, list]):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params["term"])
        return httpx.Response(200, json=bodies.get(request.url.params["term"], []))

    return handler, seen


def _entry(word: str, sense_id: int) -> list:
    return [
        {"entry": word, "translations": {"verb": [{"id": sense_id, "translation": "d", "wordPhoto": {"photo": "u"}}]}}
    ]


def test_an_inflected_word_is_looked_up_by_its_base_form():
    handler, seen = _by_term({"know": _entry("know", 7)})
    result = search_langeek("knew", http_client=_client(handler), api_url=API, throttle=_NoWait())
    assert (result.status, [s.sense_id for s in result.senses], seen) == ("ok", [7], ["knew", "know"])


def test_the_base_forms_stop_at_the_first_entry():
    handler, seen = _by_term({"affect": _entry("affect", 3)})
    search_langeek("affected", http_client=_client(handler), api_url=API, throttle=_NoWait())
    assert seen == ["affected", "affect"]


def test_an_exact_entry_needs_no_second_request():
    handler, seen = _by_term({"shoes": _entry("shoes", 1), "shoe": _entry("shoe", 2)})
    result = search_langeek("shoes", http_client=_client(handler), api_url=API, throttle=_NoWait())
    assert ([s.sense_id for s in result.senses], seen) == ([1], ["shoes"])


def test_no_base_form_with_an_entry_is_still_no_results():
    handler, seen = _by_term({})
    result = search_langeek("stopped", http_client=_client(handler), api_url=API, throttle=_NoWait())
    assert (result.status, seen) == ("no_results", ["stopped", "stopp", "stoppe", "stop"])


def test_a_failed_search_tries_no_base_form():
    seen = []

    def handler(request):
        seen.append(request.url.params["term"])
        return httpx.Response(500)

    result = search_langeek("knew", http_client=_client(handler), api_url=API, throttle=_NoWait())
    assert (result.status, seen) == ("api_error", ["knew"])
