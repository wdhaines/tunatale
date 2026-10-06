"""The renderer's hand-off of an Enunciated line to the TTS (tunatale-tyfk).

The Enunciated sections used to send `Dober ... dan` as text and hope. They now
send the natural line and say, separately, where its words are; each provider
pauses there in its own way. Decided by the user's ear on 2026-10-06, and the
end-to-end tests below finish at the REAL adapters, on the requests they heard:

* Azure (Tagalog, Norwegian): plain text, a ``<break>`` after every word and a
  short one inside a split word.
* Gemini (Cebuano): the natural line, with the pause asked for in the prompt
  beside the line's IPA.

The seam worth pinning is the one between "which words" (renderer, registry) and
"how to pause" (adapter). Each half was tested alone first and would pass alone
with the other broken — a renderer that never passed the cut, or passed it with
the old `` ... `` still in the text, sends a valid request that is simply the
wrong one.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from app.audio.azure_tts import AzureTTSService
from app.audio.gemini_tts import GeminiTTSService
from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.preprocessing.base import TextPreprocessor
from app.audio.renderer import LessonRenderer, build_lesson_renderer
from app.config import settings
from app.languages import get_phoneme_planner, get_slow_word, get_tts_locale
from app.models.lesson import Phrase, Section, SectionType
from tests.test_renderer import _make_wav_bytes

_AZURE_URL = "https://eastus.tts.speech.microsoft.com/cognitiveservices/v1"
_GEMINI_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
_NARRATOR = "en-US-DavisNeural"
_ANGELO = "fil-PH-AngeloNeural"
_ISELIN = "nb-NO-IselinNeural"
_ORUS = "ceb-PH-OrusGemini"

_SLOW = (SectionType.SLOW_SPEED, SectionType.SLOW_TRANSLATED, SectionType.SLOW_EN_TRANSLATED)


class _NoPre(TextPreprocessor):
    def preprocess(self, text, section_type):
        return text


def _line(text: str, voice: str, code: str) -> Phrase:
    return Phrase(text=text, voice_id=voice, language_code=code, role="male-1")


def _english(text: str) -> Phrase:
    return Phrase(text=text, voice_id=_NARRATOR, language_code="en", role="narrator")


def _renderer(tts, code: str, *, slow_word=None, planner=None) -> LessonRenderer:
    return LessonRenderer(
        tts=tts,
        preprocessors={code: _NoPre()},
        pause_calculator=NaturalPauseCalculator(),
        phoneme_planners={code: planner} if planner is not None else None,
        tts_locales={code: get_tts_locale(code), "en": "en-US"},
        slow_word_fns={code: slow_word} if slow_word is not None else None,
    )


async def _calls(tmp_path, code: str, sections: list[Section], **kw) -> list[dict]:
    """Render *sections* through a recording port, sharing one memo as a lesson does."""
    calls: list[dict] = []
    fake_audio = _make_wav_bytes()

    async def fake_synthesize(
        text, voice_id, output_path, rate="+0%", phonemes=None, speak_locale=None, enunciation=None
    ):
        calls.append(
            {"text": text, "voice": voice_id, "phonemes": phonemes, "locale": speak_locale, "enunciation": enunciation}
        )
        output_path.write_bytes(fake_audio)

    tts = AsyncMock()
    tts.synthesize = fake_synthesize
    renderer = _renderer(tts, code, **kw)
    memo: dict = {}
    lock = asyncio.Lock()
    for idx, section in enumerate(sections):
        await renderer._render_section(section, tmp_path, idx, code, memo, lock)
    return calls


# ---------------------------------------------------------------------------
# Which phrases are enunciated, and what the port is told.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("section_type", _SLOW)
async def test_a_target_language_line_in_an_enunciated_section_is_cut_into_words(tmp_path, section_type):
    section = Section(section_type, [_english("Enunciated"), _line("Hei, dette er Anders.", _ISELIN, "no")])

    calls = await _calls(tmp_path, "no", [section])

    assert calls[1]["text"] == "Hei, dette er Anders."
    assert calls[1]["enunciation"] == (("Hei,",), ("dette",), ("er",), ("Anders.",))
    assert calls[1]["phonemes"] is None
    # The English beside it is not being taught: it is read as it always was.
    assert calls[0] == {
        "text": "Enunciated",
        "voice": _NARRATOR,
        "phonemes": None,
        "locale": "en-US",
        "enunciation": None,
    }


@pytest.mark.parametrize(
    "section_type",
    [s for s in SectionType if s not in _SLOW],
)
async def test_no_other_section_is_enunciated(tmp_path, section_type):
    """Key phrases above all: nothing about them changes."""
    section = Section(section_type, [_line("Hei, dette er Anders.", _ISELIN, "no")])

    calls = await _calls(tmp_path, "no", [section])

    assert [c["enunciation"] for c in calls] == [None]


async def test_the_languages_own_cut_becomes_the_parts_of_a_word(tmp_path):
    section = Section(SectionType.SLOW_SPEED, [_line("Flyplassen er her.", _ISELIN, "no")])

    calls = await _calls(tmp_path, "no", [section], slow_word=get_slow_word("no"))

    assert calls[0]["enunciation"] == (("fly", "plassen"), ("er",), ("her.",))
    # The line itself is still the line as written.
    assert calls[0]["text"] == "Flyplassen er her."


async def test_a_one_word_line_is_the_natural_sections_clip(tmp_path):
    """Nothing to separate, so it is not a second request: the render-scoped
    memo serves the Enunciated section from the clip natural speed made."""
    natural = Section(
        SectionType.NATURAL_SPEED, [_line("Opo.", _ANGELO, "tl"), _line("Ako po si Mark.", _ANGELO, "tl")]
    )
    slow = Section(SectionType.SLOW_SPEED, [_line("Opo.", _ANGELO, "tl"), _line("Ako po si Mark.", _ANGELO, "tl")])

    calls = await _calls(tmp_path, "tl", [natural, slow])

    assert [(c["text"], c["enunciation"] is not None) for c in calls] == [
        ("Opo.", False),
        ("Ako po si Mark.", False),
        ("Ako po si Mark.", True),
    ]


async def test_the_same_line_in_two_enunciated_sections_is_one_request(tmp_path):
    """SLOW_TRANSLATED and SLOW_EN_TRANSLATED say the same lines; the cut is
    part of the memo key, so they still share."""
    sections = [Section(t, [_line("Ako po si Mark.", _ANGELO, "tl")]) for t in _SLOW]

    calls = await _calls(tmp_path, "tl", sections)

    assert len(calls) == 1


async def test_a_lesson_stored_in_the_old_notation_needs_only_a_re_render(tmp_path):
    """The stored text is a snapshot nobody rewrites. Its `` ... `` and its
    ``, `` are read as the cuts they were, and neither is sent as text."""
    stored = "Magandang ... gabi ... po. ... Nakikiramay ... po ... ako."
    section = Section(SectionType.SLOW_SPEED, [_line(stored, _ANGELO, "tl")])

    calls = await _calls(tmp_path, "tl", [section], slow_word=get_slow_word("tl"))

    assert calls[0]["text"] == "Magandang gabi po. Nakikiramay po ako."
    assert calls[0]["enunciation"] == (("Magandang",), ("gabi",), ("po.",), ("Nakiki", "ramay"), ("po",), ("ako.",))


async def test_tagalog_lines_carry_no_ipa_though_tagalog_has_a_planner(tmp_path):
    """Every Tagalog IPA route was heard and turned down for dialogue. The
    planner is there for the key-phrase drill and must not leak into a line."""
    section = Section(SectionType.SLOW_SPEED, [_line("Ako po si Mark.", _ANGELO, "tl")])

    calls = await _calls(tmp_path, "tl", [section], planner=get_phoneme_planner("tl"))

    assert calls[0]["phonemes"] is None
    # ...and so the line keeps its locale wrapper, which IPA would have dropped.
    assert calls[0]["locale"] == "fil-PH"


async def test_a_cebuano_line_carries_the_reading_of_every_word(tmp_path):
    section = Section(SectionType.SLOW_TRANSLATED, [_line("Liza! Maayong buntag. Kumusta ka?", _ORUS, "ceb")])

    calls = await _calls(tmp_path, "ceb", [section], planner=get_phoneme_planner("ceb"))

    assert calls[0]["text"] == "Liza! Maayong buntag. Kumusta ka?"
    assert calls[0]["enunciation"] == (("Liza!",), ("Maayong",), ("buntag.",), ("Kumusta",), ("ka?",))
    assert calls[0]["phonemes"] == {
        "liza": "lisa",
        "maayong": "maʔajoŋ",
        "buntag": "buntaɡ",
        "kumusta": "kumusta",
        "ka": "ka",
    }


async def test_a_cebuano_line_with_an_unreadable_word_is_still_enunciated(tmp_path):
    """No reading for one word means no reading for the line, not no pauses."""
    section = Section(SectionType.SLOW_SPEED, [_line("Naa koy 3 ka anak.", _ORUS, "ceb")])

    calls = await _calls(tmp_path, "ceb", [section], planner=get_phoneme_planner("ceb"))

    assert calls[0]["phonemes"] is None
    assert calls[0]["enunciation"] == (("Naa",), ("koy",), ("3",), ("ka",), ("anak.",))


async def test_a_natural_cebuano_line_stays_plain(tmp_path):
    """Dialogue at natural speed is unchanged: no reading, no instruction."""
    section = Section(SectionType.NATURAL_SPEED, [_line("Liza! Maayong buntag.", _ORUS, "ceb")])

    calls = await _calls(tmp_path, "ceb", [section], planner=get_phoneme_planner("ceb"))

    assert (calls[0]["phonemes"], calls[0]["enunciation"]) == (None, None)


@pytest.mark.parametrize("code", ["no", "tl", "ceb", "sl"])
def test_the_one_renderer_factory_wires_each_languages_cut(code):
    """A hand-built renderer drops whatever keyword it forgets, silently, and
    here that would be Norwegian compounds and long Tagalog words said whole."""
    renderer = build_lesson_renderer(MagicMock(), [code], settings)

    assert renderer._slow_word_fns == ({code: fn} if (fn := get_slow_word(code)) is not None else {})


# ---------------------------------------------------------------------------
# End to end: stored phrase -> registry -> renderer -> the REAL adapter -> wire.
# The expected requests are the ones the user listened to, verbatim.
# ---------------------------------------------------------------------------

_TL_HEARD = (
    '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="fil-PH">'
    '<voice name="fil-PH-AngeloNeural"><prosody rate="+0%">'
    'Magandang<break time="450ms"/> gabi<break time="450ms"/> po.<break time="450ms"/> '
    'Nakiki<break time="150ms"/>ramay<break time="450ms"/> po<break time="450ms"/> ako.'
    "</prosody></voice></speak>"
)
_NO_HEARD = (
    '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="nb-NO">'
    '<voice name="nb-NO-IselinNeural"><prosody rate="+0%">'
    'mappen<break time="450ms"/> ble<break time="450ms"/> jo<break time="450ms"/> '
    'over<break time="150ms"/>ført<break time="450ms"/> til<break time="450ms"/> en<break time="450ms"/> '
    'annen<break time="450ms"/> avdeling<break time="450ms"/> for<break time="450ms"/> '
    'mange<break time="450ms"/> år<break time="450ms"/> siden.'
    "</prosody></voice></speak>"
)
_CEB_HEARD = {
    "text": "Buntag sa Jimenez. Naglakaw si Paul.",
    "prompt": (
        "Say exactly this Cebuano phrase, once, with nothing before or after it. "
        "Say each word whole and at a normal speaking speed, then stop briefly before the next word. "
        "Pronounce it exactly as the IPA /buntaɡ sa himɛnɛs. naɡlakaw si pol./."
    ),
}


async def _render_real(tmp_path, tts, code: str, text: str, voice: str) -> None:
    renderer = build_lesson_renderer(tts, [code], settings)
    section = Section(SectionType.SLOW_SPEED, [_line(text, voice, code)])
    await renderer._render_section(section, tmp_path, 0, code, {}, asyncio.Lock())


def _azure() -> AzureTTSService:
    return AzureTTSService(key="test-key", region="eastus", min_delay=0, retry_base_delay=0)


@respx.mock
@pytest.mark.parametrize(
    "stored",
    ["Magandang gabi po. Nakikiramay po ako.", "Magandang ... gabi ... po. ... Nakikiramay ... po ... ako."],
    ids=["natural", "old-notation"],
)
async def test_a_tagalog_line_reaches_azure_as_the_request_the_user_chose(tmp_path, stored):
    route = respx.post(_AZURE_URL).mock(return_value=httpx.Response(200, content=_make_wav_bytes()))

    await _render_real(tmp_path, _azure(), "tl", stored, _ANGELO)

    assert [c.request.content.decode() for c in route.calls] == [_TL_HEARD]


@respx.mock
async def test_a_stored_norwegian_line_reaches_azure_as_the_request_the_user_chose(tmp_path):
    """The very line they heard, as its lesson stores it today."""
    stored = (
        "mappen ... ble ... jo ... over, ført ... til ... en ... annen ... avdeling ... for ... mange ... år ... siden."
    )
    route = respx.post(_AZURE_URL).mock(return_value=httpx.Response(200, content=_make_wav_bytes()))

    await _render_real(tmp_path, _azure(), "no", stored, _ISELIN)

    assert [c.request.content.decode() for c in route.calls] == [_NO_HEARD]


@respx.mock
@pytest.mark.parametrize(
    "stored",
    ["Buntag sa Jimenez. Naglakaw si Paul.", "Buntag ... sa ... Jimenez. ... Naglakaw ... si ... Paul."],
    ids=["natural", "old-notation"],
)
async def test_a_cebuano_line_reaches_gemini_as_the_request_the_user_chose(tmp_path, stored):
    """Names corrected, particles unstressed, the pause asked for: all three
    come from different modules and have to meet in this one string.

    The old notation matters most here. This provider SPEAKS its text, so a
    lesson stored as ``Buntag ... sa ...`` and re-rendered must not send that:
    the dots were the device the user heard and called inconsistent.
    """
    inputs: list[dict] = []

    async def _record(request):
        inputs.append(json.loads(request.content)["input"])
        return httpx.Response(200, json={"audioContent": __import__("base64").b64encode(_make_wav_bytes()).decode()})

    respx.post(_GEMINI_URL).mock(side_effect=_record)

    async def _token() -> str:
        return "test-access-token"

    gemini = GeminiTTSService(min_delay=0, token_provider=_token)

    await _render_real(tmp_path, gemini, "ceb", stored, _ORUS)

    assert inputs == [_CEB_HEARD]
