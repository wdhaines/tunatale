"""The narrator stresses the focus of an object-focus prompt (tunatale-ve4p.34).

Stage 6 (gi- / -on) is cued in English by which noun is meant: "HE ate fish"
against "he ate THE FISH". The owner asked for the focus to be heard as stress
(2026-10-15). A drill step names the stressed words as a span of its text, and
Azure is sent those words inside a prosody element of their own.

Measured 2026-10-15 on en-US-DavisMultilingualNeural, F0 of the stressed words
(Praat), plain -> stressed: "the fish" 111 -> 153 Hz with this prosody nested in
the line's own rate wrapper, "He" 190 -> 203 Hz. ``<emphasis>`` was tried and
raised nothing, so it is not used.

Two rules carried from the adapter: markup reaches the SSML only through an
argument, never through text (every piece is escaped), and the cache key grows
only when a span is present, so no clip already rendered is orphaned.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest
import respx

from app.audio.azure_tts import AzureTTSService
from app.audio.synth_plan import SynthRequest, plan_section
from app.generation.affix_drill import AffixDrill, DrillStep
from app.generation.section_builder import build_affix_drill_section
from app.models.lesson import Lesson, Phrase, Section, SectionType
from tests.test_azure_tts import SYNTH_URL, _svc
from tests.test_renderer_progress import FakeTTS, _renderer

_NARRATOR = "en-US-DavisMultilingualNeural"
_LINE = "He ate the fish."
_THE_FISH = (7, 15)
_STRESS = '<prosody pitch="+15%" volume="+30%" rate="-20%">'


def test_the_stressed_words_get_a_prosody_of_their_own():
    body = AzureTTSService._billable_body(_LINE, _NARRATOR, "+0%", speak_locale="en-US", stressed=_THE_FISH)
    assert body == (f'<lang xml:lang="en-US"><prosody rate="+0%">He ate {_STRESS}the fish</prosody>.</prosody></lang>')


def test_stress_at_the_start_of_a_line():
    body = AzureTTSService._billable_body("He ate fish.", _NARRATOR, "+0%", speak_locale="en-US", stressed=(0, 2))
    assert body == f'<lang xml:lang="en-US"><prosody rate="+0%">{_STRESS}He</prosody> ate fish.</prosody></lang>'


def test_the_stressed_words_are_escaped_like_the_rest():
    body = AzureTTSService._billable_body("Tom & Ann ate <it>.", _NARRATOR, "+0%", stressed=(0, 9))
    assert f"{_STRESS}Tom &amp; Ann</prosody> ate &lt;it&gt;." in body


@pytest.mark.parametrize("stressed", [None], ids=["none"])
def test_a_line_with_no_stress_is_byte_identical_to_before(stressed):
    assert (
        AzureTTSService._billable_body(_LINE, _NARRATOR, "+0%", speak_locale="en-US", stressed=stressed)
        == AzureTTSService._billable_body(_LINE, _NARRATOR, "+0%", speak_locale="en-US")
        == '<lang xml:lang="en-US"><prosody rate="+0%">He ate the fish.</prosody></lang>'
    )


def test_the_whole_request_carries_the_stress_inside_the_voice():
    ssml = AzureTTSService._build_ssml(_LINE, _NARRATOR, "+0%", speak_locale="en-US", stressed=_THE_FISH)
    assert f'<voice name="{_NARRATOR}"><lang xml:lang="en-US"><prosody rate="+0%">He ate {_STRESS}' in ssml


@pytest.mark.parametrize("span", [(0, 0), (5, 3), (-1, 2), (10, 40)])
def test_a_span_outside_the_text_is_an_error(span):
    with pytest.raises(ValueError, match="stress"):
        AzureTTSService._billable_body(_LINE, _NARRATOR, "+0%", stressed=span)


def test_stress_does_not_combine_with_a_reading_or_an_enunciation():
    """Only the narrator's English is stressed; a target-language line with IPA
    or word breaks never is, and silently dropping one of the two would hide it."""
    with pytest.raises(ValueError, match="stress"):
        AzureTTSService._billable_body(
            "hagen", "nb-NO-FinnNeural", "+0%", phonemes={"hagen": "ˈhɑːɡən"}, stressed=(0, 5)
        )
    with pytest.raises(ValueError, match="stress"):
        AzureTTSService._billable_body(
            "ha gen", "nb-NO-FinnNeural", "+0%", enunciation=(("ha",), ("gen",)), stressed=(0, 2)
        )


def test_stress_is_in_the_cache_key_only_when_present(tmp_path):
    svc = _svc(cache_dir=tmp_path)
    plain = svc._cache_path(_LINE, _NARRATOR, "+0%", None, "en-US")
    assert svc._cache_path(_LINE, _NARRATOR, "+0%", None, "en-US", None, None) == plain
    fish = svc._cache_path(_LINE, _NARRATOR, "+0%", None, "en-US", None, _THE_FISH)
    he = svc._cache_path(_LINE, _NARRATOR, "+0%", None, "en-US", None, (0, 2))
    assert len({plain, fish, he}) == 3


def test_the_cache_key_carries_the_markup_so_a_re_tune_re_renders(tmp_path):
    """The prosody numbers are tuned by ear; a key on the span alone would keep
    serving a clip stressed with the old numbers."""
    key = f"{_NARRATOR}|+0%|{_LINE}|lang:en-US|stress:7-15:{_STRESS}"
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    assert _svc(cache_dir=tmp_path)._cache_path(_LINE, _NARRATOR, "+0%", None, "en-US", None, _THE_FISH).name == (
        f"{digest}.mp3"
    )


class _DropsTheArticle:
    """A preprocessor that changes the text, as an Enunciated line's does."""

    def preprocess(self, text: str, section_type) -> str:
        return text.replace("the ", "")


def _planned(phrase: Phrase) -> list[SynthRequest]:
    section = Section(section_type=SectionType.AFFIX_DRILL, phrases=[phrase])
    return plan_section(
        section, "ceb", preprocessor=_DropsTheArticle(), planner=None, locale_for=lambda _: None, slow_word=None
    )


def test_the_span_is_moved_onto_the_text_the_voice_receives():
    phrase = Phrase(text="So the cat ate fish.", voice_id=_NARRATOR, language_code="en", stressed=(11, 14))
    [request] = _planned(phrase)
    assert request.text == "So cat ate fish."
    assert request.text[slice(*request.stressed)] == "ate"


def test_stressed_words_the_text_no_longer_has_are_an_error():
    """Refused, not guessed: the voice would stress whatever sat at those places."""
    phrase = Phrase(text=_LINE, voice_id=_NARRATOR, language_code="en", stressed=_THE_FISH)
    with pytest.raises(ValueError, match="not in the text sent to the voice"):
        _planned(phrase)


@respx.mock
async def test_the_stress_reaches_azure(tmp_path):
    route = respx.post(SYNTH_URL).mock(return_value=httpx.Response(200, content=b"ID3-audio"))
    await _svc().synthesize(_LINE, _NARRATOR, tmp_path / "o.mp3", speak_locale="en-US", stressed=_THE_FISH)
    assert f"He ate {_STRESS}the fish</prosody>." in route.calls[0].request.content.decode()


# ── from the drill to the request ────────────────────────────────────────────


def test_the_drill_section_carries_each_steps_stress():
    drill = AffixDrill(
        steps=[
            DrillStep("prompt", _LINE, "en", stressed=_THE_FISH),
            DrillStep("answer", "Gikaon niya ang isda.", "ceb"),
            DrillStep("model", "Listen and repeat.", "en"),
        ]
    )
    section = build_affix_drill_section(drill, narrator_voice=_NARRATOR, l2_voice="ceb-PH-CharonGemini")
    assert [p.stressed for p in section.phrases] == [None, _THE_FISH, None, None]  # the title first


def test_a_stored_lesson_keeps_its_stress_and_an_old_one_has_none():
    lesson = Lesson(
        title="Affix drill",
        language_code="ceb",
        sections=[
            Section(
                section_type=SectionType.AFFIX_DRILL,
                phrases=[Phrase(text=_LINE, voice_id=_NARRATOR, language_code="en", stressed=_THE_FISH)],
            )
        ],
    )
    stored = lesson.to_json()
    assert Lesson.from_json(stored).sections[0].phrases[0].stressed == _THE_FISH
    old = stored.replace('"stressed": [7, 15], ', "").replace(', "stressed": [7, 15]', "")
    assert "stressed" not in old
    assert Lesson.from_json(old).sections[0].phrases[0].stressed is None


def test_a_stressed_phrase_is_its_own_request():
    """The renderer voices one clip per key: the same words stressed and
    unstressed are two clips, or whichever came first would voice both."""
    assert SynthRequest(_LINE, _NARRATOR, "+0%", stressed=_THE_FISH).key != SynthRequest(_LINE, _NARRATOR, "+0%").key


class _StressRecordingTTS(FakeTTS):
    def __init__(self) -> None:
        super().__init__()
        self.stressed: dict[str, tuple[int, int] | None] = {}

    async def synthesize(self, text, voice_id, output_path, rate="+0%", stressed=None, **kwargs) -> None:
        self.stressed[text] = stressed
        await super().synthesize(text, voice_id, output_path, rate=rate, **kwargs)


async def test_the_renderer_hands_the_stress_to_the_voice(tmp_path):
    """Through the real renderer and plan. The span the voice receives must
    still cover the stressed words of the text it receives."""
    tts = _StressRecordingTTS()
    lesson = Lesson(
        title="Napredek",
        language_code="sl",
        sections=[
            Section(
                # A drill section, where stress lives. (A Key Phrases section
                # needs its key phrases grouped, which says nothing of stress.)
                section_type=SectionType.AFFIX_DRILL,
                phrases=[
                    Phrase(text=_LINE, voice_id=_NARRATOR, language_code="en", role="narrator", stressed=_THE_FISH),
                    Phrase(text="Dober dan.", voice_id="sl-SI-PetraNeural", language_code="sl"),
                ],
            )
        ],
    )
    await _renderer(tts).render(lesson, Path(tmp_path) / "out.wav", on_progress=None)
    [(text, span)] = [(t, s) for t, s in tts.stressed.items() if s is not None]
    assert text[slice(*span)] == "the fish"
    assert any(span is None for text, span in tts.stressed.items() if "Dober" in text)
