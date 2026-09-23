"""The renderer's hand-off of planned IPA to the TTS (tunatale-w4m7.16).

Two seams, each of which failed SILENTLY before it was pinned:

* The mapping key. The adapter looks up bare word tokens, so a chunk stored as
  ``"bing?"`` keyed ``{"bing?": ipa}`` matched nothing and was spoken as text.
  Norwegian never met this because its chunks carry no punctuation; the generic
  breakdown keeps it (``li|bing?``).
* The ``<lang>`` wrapper. Azure's fil-PH front end ignores ``<phoneme>``:
  "salamat" came back byte-identical under its own IPA, ``[sɐˈlaː.mɐt̪̚]`` and
  the IPA of "kumusta", and a Multilingual voice wrapped in fil-PH ignored it
  too; unwrapped, the same voice said "kumusta". A language whose locale
  ignores IPA declares it, and its IPA-bearing utterances go out unwrapped.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.preprocessing.base import TextPreprocessor
from app.audio.renderer import LessonRenderer
from app.languages import get_ipa_read_in_voice_locale, get_phoneme_planner
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from tests.test_renderer import _make_wav_bytes

_VOICE = "de-DE-FlorianMultilingualNeural"
_NARRATOR = "en-US-GuyNeural"


class _NoPre(TextPreprocessor):
    def preprocess(self, text, section_type):
        return text


def _lesson(code: str, phrases: list[Phrase]) -> Lesson:
    return Lesson(
        title="Day 1",
        language_code=code,
        sections=[Section(section_type=SectionType.KEY_PHRASES, phrases=phrases)],
        key_phrases=[KeyPhraseInfo(phrase="x", translation="y")],
    )


async def _render(tmp_path, lesson: Lesson, planner, locale: str) -> list[dict]:
    calls: list[dict] = []
    fake_audio = _make_wav_bytes()

    async def fake_synthesize(text, voice_id, output_path, rate="+0%", phonemes=None, speak_locale=None):
        calls.append({"text": text, "phonemes": phonemes, "speak_locale": speak_locale})
        output_path.write_bytes(fake_audio)

    tts = AsyncMock()
    tts.synthesize = fake_synthesize
    code = lesson.language_code
    renderer = LessonRenderer(
        tts=tts,
        preprocessors={code: _NoPre()},
        pause_calculator=NaturalPauseCalculator(),
        phoneme_planners={code: planner},
        tts_locales={code: locale},
    )
    await renderer._render_section(lesson.sections[0], tmp_path, 0, code, {}, __import__("asyncio").Lock())
    return calls


def _tl(text: str, **kw) -> Phrase:
    return Phrase(text=text, voice_id=_VOICE, language_code="tl", **kw)


async def test_a_punctuated_chunk_keys_its_ipa_by_the_bare_word(tmp_path):
    lesson = _lesson("tl", [_tl("bing?", source_word="libing?", syllable_span=(1, 2))])

    [call] = await _render(tmp_path, lesson, get_phoneme_planner("tl"), "fil-PH")

    assert call["phonemes"] == {"bing": "ˈbɪŋ"}


async def test_an_ipa_bearing_tagalog_chunk_is_spoken_without_the_locale_wrapper(tmp_path):
    lesson = _lesson(
        "tl",
        [
            _tl("ng", source_word="ng", syllable_span=(0, 1)),  # IPA: nɐŋ
            _tl("ng abuloy"),  # a multi-word partial: plain text
        ],
    )

    calls = await _render(tmp_path, lesson, get_phoneme_planner("tl"), "fil-PH")
    by_text = {c["text"]: c for c in calls}

    assert by_text["ng"]["phonemes"] == {"ng": "nɐŋ"}
    assert by_text["ng"]["speak_locale"] is None
    # Plain Tagalog text still needs the wrapper: without it a Multilingual
    # voice guesses the language per utterance.
    assert by_text["ng abuloy"]["phonemes"] is None
    assert by_text["ng abuloy"]["speak_locale"] == "fil-PH"


async def test_a_chunk_the_planner_declines_keeps_the_wrapper(tmp_path):
    # An unlisted whole word gets no IPA, so it is plain Tagalog text.
    lesson = _lesson("tl", [_tl("nakikiramay", source_word="nakikiramay", syllable_span=(0, 5))])

    [call] = await _render(tmp_path, lesson, get_phoneme_planner("tl"), "fil-PH")

    assert call["phonemes"] is None
    assert call["speak_locale"] == "fil-PH"


class _Planner:
    def plan_chunk(self, source_word, span, upos=None, chunk_text=None):
        return "ɡən"


async def test_a_language_whose_locale_honours_ipa_keeps_the_wrapper(tmp_path):
    # Norwegian IPA is read by the nb-NO front end, which honours it: the flag
    # is Tagalog's, and nothing about Norwegian renders changes.
    phrase = Phrase(text="gen", voice_id=_VOICE, language_code="no", source_word="hagen", syllable_span=(1, 2))

    [call] = await _render(tmp_path, _lesson("no", [phrase]), _Planner(), "nb-NO")

    assert call["phonemes"] == {"gen": "ɡən"}
    assert call["speak_locale"] == "nb-NO"


def test_only_tagalog_declares_its_locale_ignores_ipa():
    assert get_ipa_read_in_voice_locale("tl") is True
    assert get_ipa_read_in_voice_locale("no") is False
    assert get_ipa_read_in_voice_locale("zz") is False
