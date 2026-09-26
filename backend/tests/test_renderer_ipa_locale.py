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
from app.languages import get_ipa_for_drill_phrases, get_ipa_read_in_voice_locale, get_phoneme_planner
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from tests.test_renderer import _make_wav_bytes

_VOICE = "de-DE-FlorianMultilingualNeural"
_NARRATOR = "en-US-GuyNeural"
_CEB_VOICE = "ceb-PH-KoreGemini"


class _NoPre(TextPreprocessor):
    def preprocess(self, text, section_type):
        return text


def _lesson(code: str, phrases: list[Phrase], section_type: SectionType = SectionType.KEY_PHRASES) -> Lesson:
    return Lesson(
        title="Day 1",
        language_code=code,
        sections=[Section(section_type=section_type, phrases=phrases)],
        key_phrases=[KeyPhraseInfo(phrase="x", translation="y")],
    )


async def _render(tmp_path, lesson: Lesson, planner, locale: str, section_idx: int = 0) -> list[dict]:
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
    await renderer._render_section(
        lesson.sections[section_idx], tmp_path, section_idx, code, {}, __import__("asyncio").Lock()
    )
    return calls


def _tl(text: str, **kw) -> Phrase:
    return Phrase(text=text, voice_id=_VOICE, language_code="tl", **kw)


def _ceb(text: str, **kw) -> Phrase:
    return Phrase(text=text, voice_id=_CEB_VOICE, language_code="ceb", **kw)


async def test_a_cebuano_drill_fragment_reaches_the_voice_as_ipa(tmp_path):
    """The wiring the Cebuano plugin exists to switch on, end to end.

    Registering ``phoneme_planner_factory`` is all this needs: the renderer asks
    the language's planner about every chunk that carries provenance and hands
    the answer to the adapter as ``phonemes``. A planner that worked in unit
    tests and never arrived here would still render a Cebuano drill the way
    Gemini renders bare text — extra words, clipped endings — which is the whole
    reason the planner was written.

    The planner comes from the REGISTRY, not from a hand-built object, so this
    also pins that registering it was the only wiring step.
    """
    lesson = _lesson(
        "ceb",
        [
            _ceb("inyong"),
            _ceb("yong", source_word="inyong", syllable_span=(1, 2)),
            # Two multi-word phrases, one WITHOUT provenance and one with a
            # provenance the planner refuses. They are different cases now: a
            # bare drill step gets a per-word map, a chunk whose caption names
            # other letters than its span stays plain (below).
            _ceb("yong mao"),
            _ceb("yong mao", source_word="inyong", syllable_span=(1, 2)),
        ],
    )

    calls = await _render(tmp_path, lesson, get_phoneme_planner("ceb"), "ceb-PH")

    # Calls come back in phrase order, and the two "yong mao" phrases are
    # distinct memo keys, so the positions are what tells them apart. "mao"
    # reads "maʔo" because the splitter cuts the hiatus and the second piece
    # then opens on a glottal stop — the class's own rule, applied to the whole
    # word exactly as it is to each chunk of one.
    assert calls[0]["phonemes"] is None
    assert calls[1]["phonemes"] == {"yong": "ˈjoŋ"}
    assert calls[2]["phonemes"] == {"yong": "ˈjoŋ", "mao": "maʔo"}
    assert calls[3]["phonemes"] is None


_PHRASE = "ilubong ugma"
_PHRASE_IPAS = {"ilubong": "ʔiluboŋ", "ugma": "ʔuɡma"}


async def test_a_multi_word_drill_step_is_spoken_from_the_ipa_of_its_words(tmp_path):
    """The phrase path, and the three things that must stay off it.

    A two-word key phrase carries no provenance — provenance is what a
    BREAKDOWN chunk has — so before this it fell through to bare text, and the
    user's ear heard "ilubong ugma" as "ilubong uglak" (2026-09-25; plain Gemini
    got "ugma" wrong 2 of 3 blind, told the reading 2 of 2).

    The three controls are the whole reason the path is a flag and not a
    default. The same words inside a DIALOGUE line stay plain, because a
    sentence is not a drill step and phrase IPA on a full sentence is untested.
    The same drill step in another language stays plain, because that language's
    drill runs on Azure, where a per-word map would wrap every word in
    ``<phoneme>`` and change audio nobody has listened to.
    """
    [drill] = await _render(tmp_path, _lesson("ceb", [_ceb(_PHRASE)]), get_phoneme_planner("ceb"), "ceb-PH")
    assert drill["phonemes"] == _PHRASE_IPAS

    dialogue = _lesson("ceb", [_ceb("Ilubong ugma. Alas dyis sa buntag.")], section_type=SectionType.NATURAL_SPEED)
    [line] = await _render(tmp_path, dialogue, get_phoneme_planner("ceb"), "ceb-PH")
    assert line["phonemes"] is None

    [tl_drill] = await _render(tmp_path, _lesson("tl", [_tl(_PHRASE)]), get_phoneme_planner("tl"), "fil-PH")
    assert tl_drill["phonemes"] is None

    # The #209 path is untouched: a single-word chunk still gets ITS span's IPA.
    chunk = _lesson("ceb", [_ceb("yong", source_word="inyong", syllable_span=(1, 2))])
    [planned] = await _render(tmp_path, chunk, get_phoneme_planner("ceb"), "ceb-PH")
    assert planned["phonemes"] == {"yong": "ˈjoŋ"}


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


class _WholeWordPlanner(_Planner):
    """A planner that CAN read whole words, and declines one of them."""

    def __init__(self, unreadable: str):
        self._unreadable = unreadable

    def plan_word(self, word):
        return None if word == self._unreadable else "ˈʔa"


async def test_a_planner_that_cannot_read_a_whole_word_is_never_asked_to(tmp_path):
    """What the ``getattr`` on ``plan_word`` is for.

    ``plan_word`` is a method of the SPELLED planners, so a lexicon-backed one
    is the shape most likely to meet a drill phrase. Asking it anyway raises,
    and a raise here is a failed render of the whole lesson; skipping it leaves
    the plain text that language has always rendered.
    """
    [call] = await _render(tmp_path, _lesson("ceb", [_ceb(_PHRASE)]), _Planner(), "ceb-PH")

    assert call["phonemes"] is None


async def test_a_phrase_the_planner_cannot_read_all_of_stays_plain(tmp_path):
    """One unreadable word refuses the WHOLE map, rather than a gap in it.

    A gap is not a partial reading: the words that were read would then be
    aligned to positions the phrase never said, and a misaligned reading is
    worse than the plain render it replaced. (No Cebuano word actually refuses,
    which is exactly why this needs a stub — the guard has no real corpus
    behind it and would otherwise rot untested.)
    """
    [call] = await _render(tmp_path, _lesson("ceb", [_ceb(_PHRASE)]), _WholeWordPlanner("ugma"), "ceb-PH")

    assert call["phonemes"] is None


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


def test_only_cebuano_asks_for_phrase_ipa():
    # The sibling of the flag above, and the same shape of argument: this one
    # changes what a whole DRILL STEP is rendered from, and only the language
    # whose adapter can carry a per-word reading has asked for it. An unknown
    # code is False, not a crash — plain synthesis is every language's default.
    assert get_ipa_for_drill_phrases("ceb") is True
    assert get_ipa_for_drill_phrases("tl") is False
    assert get_ipa_for_drill_phrases("zz") is False
