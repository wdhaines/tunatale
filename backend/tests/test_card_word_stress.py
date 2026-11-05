"""A card's word is told which syllable it stresses (tunatale-h0q3.39).

The owner's ear, blind, 2026-11-05: Gemini's card voice stressed 17 of 124
Cebuano cards on the wrong syllable, in both directions, and mispronounced 7
more. The IPA instruction cards were sent changed nothing audible (it scored
exactly like the plain word); an instruction naming the syllable in words
("Put the stress on the LAST syllable, "han": sim-ba-HAN.") was right 40 of 56
takes against the plain word's 27, and fixed 4 of the 7 mispronounced words.
It also said the word twice in 5 of its 56 takes, so its takes are measured:
every doubled take has a silence of at least 0.61 s inside it, and no single
saying of 148 has one over 0.28 s.

Network is respx at the httpx transport, as in ``test_gemini_tts.py``; the
pause measurement is injected like the adapter's other ffmpeg measurements.
"""

from __future__ import annotations

import base64
import json
import logging

import httpx
import pytest
import respx

from app.audio import gemini_tts
from app.audio.azure_tts import AzureTTSService
from app.audio.gemini_tts import GeminiTTSService
from app.audio.tts_router import RoutingTTSService
from app.cards.media.pipeline import fetch_card_media

GEMINI_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
AZURE_URL = "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1"
VOICE = "ceb-PH-KoreGemini"
MODEL = "gemini-2.5-flash-tts"


async def _token() -> str:
    return "test-access-token"


def _gemini(**kw) -> GeminiTTSService:
    kw.setdefault("min_delay", 0)
    kw.setdefault("token_provider", _token)
    kw.setdefault("speech_seconds", lambda audio: 0.0)
    kw.setdefault("longest_pause", lambda audio: 0.0)
    kw.setdefault("rewrite_gaps", lambda audio, words: audio)
    return GeminiTTSService(**kw)


def _audio(label: str) -> httpx.Response:
    return httpx.Response(200, json={"audioContent": base64.b64encode(label.encode()).decode()})


def _recording_route() -> list[dict]:
    bodies: list[dict] = []

    async def _record(request):
        bodies.append(json.loads(request.content))
        return _audio(f"take-{len(bodies)}")

    respx.post(GEMINI_URL).mock(side_effect=_record)
    return bodies


# ── the instruction ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("word", "spelling", "clause"),
    [
        ("simbahan", "sim-ba-HAN", 'Put the stress on the LAST syllable, "han": sim-ba-HAN.'),
        ("asawa", "a-SA-wa", 'Put the stress on the SECOND syllable, "sa": a-SA-wa.'),
        ("tambol", "TAM-bol", 'Put the stress on the FIRST syllable, "tam": TAM-bol.'),
        ("balikbalik", "ba-lik-BA-lik", 'Put the stress on the THIRD syllable, "ba": ba-lik-BA-lik.'),
    ],
)
@respx.mock
async def test_a_card_word_is_told_its_stressed_syllable_and_not_its_ipa(tmp_path, word, spelling, clause):
    """The measured wording, verbatim; the IPA is left out (it did nothing audible)."""
    bodies = _recording_route()

    await _gemini().synthesize(word, VOICE, tmp_path / "w.mp3", phonemes={word: "x.ˈy"}, stress_spelling=spelling)

    assert bodies == [
        {
            "input": {
                "text": word,
                "prompt": "Say only this one Cebuano word, exactly once, with nothing before or after it. " + clause,
            },
            "voice": {"languageCode": "ceb-PH", "name": "Kore", "model_name": MODEL},
            "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.0},
        }
    ]


@respx.mock
async def test_an_unread_ipa_is_not_announced_as_unsupported(tmp_path, caplog):
    """The reading is deliberately unused here, which is not the degradation the warning reports."""
    _recording_route()

    with caplog.at_level(logging.WARNING):
        await _gemini().synthesize(
            "simbahan", VOICE, tmp_path / "w.mp3", phonemes={"simbahan": "sim.ba.ˈhan"}, stress_spelling="sim-ba-HAN"
        )

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


@respx.mock
async def test_the_stress_spelling_is_its_own_cache_file(tmp_path):
    """Plain, IPA-prompted and stress-prompted renders of one word never serve each other."""
    bodies = _recording_route()
    svc = _gemini(cache_dir=tmp_path / "cache")
    reading = {"simbahan": "sim.ba.ˈhan"}

    for _ in range(2):
        await svc.synthesize("simbahan", VOICE, tmp_path / "plain.mp3")
        await svc.synthesize("simbahan", VOICE, tmp_path / "ipa.mp3", phonemes=reading)
        await svc.synthesize("simbahan", VOICE, tmp_path / "s1.mp3", phonemes=reading, stress_spelling="sim-ba-HAN")
        await svc.synthesize("simbahan", VOICE, tmp_path / "s2.mp3", phonemes=reading, stress_spelling="sim-BA-han")

    assert len(bodies) == 4
    assert len(list((tmp_path / "cache").iterdir())) == 4


def test_the_cache_key_carries_the_instruction_version(tmp_path):
    """A reworded instruction must not keep serving what the old words produced."""
    svc = _gemini(cache_dir=tmp_path)
    before = svc._cache_path("simbahan", VOICE, "+0%", stress="sim-ba-HAN")
    gemini_tts.STRESS_PROMPT_VERSION += 1
    try:
        after = svc._cache_path("simbahan", VOICE, "+0%", stress="sim-ba-HAN")
    finally:
        gemini_tts.STRESS_PROMPT_VERSION -= 1

    assert before != after


@pytest.mark.parametrize("kind", [{"line": True}, {"enunciation": (("simbahan",), ("ug",))}])
@respx.mock
async def test_a_line_is_never_given_the_word_instruction(tmp_path, kind):
    """The instruction says "this one word"; a line has its own instructions."""
    bodies = _recording_route()

    await _gemini().synthesize("simbahan ug", VOICE, tmp_path / "l.mp3", stress_spelling="sim-ba-HAN", **kind)

    assert "stress" not in bodies[0]["input"].get("prompt", "")


# ── the doubling guard ────────────────────────────────────────────────────────


@respx.mock
async def test_a_take_with_a_pause_inside_it_is_drawn_again(tmp_path):
    """A pause of 0.61 s or more inside one word is the word said twice."""
    bodies = _recording_route()
    pauses = {"take-1": 0.73, "take-2": 0.13}
    out = tmp_path / "w.mp3"

    await _gemini(longest_pause=lambda audio: pauses[audio.decode()]).synthesize(
        "bilar", VOICE, out, stress_spelling="bi-LAR"
    )

    assert len(bodies) == 2
    assert out.read_bytes() == b"take-2"


def test_the_limit_sits_between_the_measured_single_and_doubled_takes():
    """The table the limit was read off: no single saying over 0.28 s, no doubling under 0.61 s."""
    assert 0.28 < gemini_tts.WORD_PAUSE_LIMIT_SECONDS < 0.61


@respx.mock
async def test_when_every_take_pauses_the_shortest_pause_is_kept_and_logged(tmp_path, caplog):
    """A card must not be left without audio; the closest take is kept, loudly."""
    bodies = _recording_route()
    pauses = {"take-1": 0.9, "take-2": 0.62, "take-3": 1.1}
    out = tmp_path / "w.mp3"

    with caplog.at_level(logging.WARNING):
        await _gemini(longest_pause=lambda audio: pauses[audio.decode()]).synthesize(
            "Babay", VOICE, out, stress_spelling="BA-bay"
        )

    assert len(bodies) == gemini_tts.MAX_WORD_TAKES == 3
    assert out.read_bytes() == b"take-2"
    assert any("WORD_TAKE_GAVE_UP" in r.getMessage() for r in caplog.records)


@respx.mock
async def test_a_plain_word_is_not_measured(tmp_path):
    """Only the instruction was seen to double a word; a plain render keeps its one take."""
    bodies = _recording_route()

    def _never(audio):
        raise AssertionError("a plain word was measured")

    await _gemini(longest_pause=_never).synthesize("bilar", VOICE, tmp_path / "w.mp3")

    assert len(bodies) == 1


# ── the other adapter and the router ──────────────────────────────────────────


@respx.mock
async def test_azure_takes_the_stress_spelling_and_changes_nothing(tmp_path):
    route = respx.post(AZURE_URL).mock(return_value=httpx.Response(200, content=b"a"))
    svc = AzureTTSService(key="k", region="eastus", min_delay=0, retry_base_delay=0, cache_dir=tmp_path / "c")

    await svc.synthesize("hotell", "nb-NO-FinnNeural", tmp_path / "1.mp3")
    await svc.synthesize("hotell", "nb-NO-FinnNeural", tmp_path / "2.mp3", stress_spelling="ho-TELL")

    assert route.call_count == 1  # the second was the first's cache file


async def test_the_router_passes_the_stress_spelling_through(tmp_path):
    seen: list = []

    class _Adapter:
        async def synthesize(self, text, voice_id, output_path, **kw):
            seen.append(kw.get("stress_spelling"))
            output_path.write_bytes(b"x")

    router = RoutingTTSService(_Adapter(), _Adapter())
    await router.synthesize("simbahan", VOICE, tmp_path / "o.mp3", stress_spelling="sim-ba-HAN")

    assert seen == ["sim-ba-HAN"]


# ── the card pipeline builds it from the word's reading ───────────────────────


async def _spelling_sent(word: str, language_code: str) -> list:
    sent: list = []

    async def recording_tts(text, *, voice=None, phonemes=None, stress_spelling=None):
        sent.append(stress_spelling)
        return b"tts_mp3"

    await fetch_card_media(
        word,
        "gloss",
        pixabay_key="key",
        language_code=language_code,
        forvo_enabled=False,
        normalize=False,
        image_query="",
        _tts_fn=recording_tts,
    )
    return sent


@pytest.mark.parametrize(
    ("word", "spelling"),
    [
        ("simbahan", "sim-ba-HAN"),  # Wolff: simbahán
        ("asawa", "a-SA-wa"),
        ("lubong", "lu-BONG"),  # Wolff: lubóng
    ],
)
async def test_a_word_s_reading_becomes_its_stress_spelling(word, spelling):
    assert await _spelling_sent(word, "ceb") == [spelling]


@pytest.mark.parametrize(
    ("word", "language_code", "why"),
    [
        ("bus", "ceb", "one syllable has nothing to choose"),
        ("kape", "ceb", "the reading marks no stress (Wolff lists two)"),
        ("Maayong buntag", "ceb", "a phrase is not one word"),
        ("hotell", "no", "a language whose card voice is not told its reading"),
    ],
)
async def test_no_stress_spelling_without_one_known_stress_on_one_word(word, language_code, why):
    assert await _spelling_sent(word, language_code) == [None], why
