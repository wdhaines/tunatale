"""English lines read by the speaker's own voice, and a new narrator (tunatale-ucpg).

Before this, every English line in a lesson was the narrator's: the translation
of a female-2 line was read by the same voice as the translation of a male-1
line and as the section titles. The user's calls, 2026-09-29:

* the narrator is ``en-US-DavisMultilingualNeural`` (Guy's intonation was
  rejected by ear) and it reads titles, scene labels and key-phrase glosses;
* the English of a DIALOGUE line is read by an English voice for that line's
  speaker — the speaker's own voice where it is Multilingual, a pitch-matched
  English voice where the Norwegian voice speaks only Norwegian;
* all four translated sections switch.

Three seams, each of which fails silently:

1. The builders — a translation phrase KEEPS ``role="narrator"``. That role is
   structural (``key_phrase_groups``, ``cues.py``, ``lesson_io``), and only the
   ``voice_id`` changes. Moving the role would break cue pairing without a
   single voice sounding wrong.
2. The gain — an English phrase's gain is looked up under its TEXT language,
   ``en``, and until this change the ``en`` table was empty. So the narrator's
   measured -0.9 dB (ca2006c) reached lesson titles and never one English line
   in a section: 0.0 for every phrase, while the table tests passed because they
   asked ``get_tts_voice_gain_db("no", ...)``, which the renderer never does.
   The seam test below goes through ``_assemble_section_audio``.
3. The ``<lang>`` wrapper — an Italian Multilingual voice reading English was
   sent no locale at all and left to guess. English phrases now declare en-US;
   for an en-US voice that emits no wrapper and leaves the cache key alone.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import pytest
import soundfile as sf

from app.audio.azure_tts import AzureTTSService
from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.preprocessing.base import TextPreprocessor
from app.audio.renderer import LessonRenderer
from app.generation.section_builder import (
    build_en_translated_section,
    build_slow_en_translated_section,
    build_slow_translated_section,
    build_translated_section,
    key_phrase_groups,
)
from app.languages import get_language, get_tts_voice_gain_db, known_language_codes
from app.models.language import NARRATOR_VOICE
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from tests.test_renderer import _make_wav_bytes

_DAVIS = "en-US-DavisMultilingualNeural"
_GIUSEPPE = "it-IT-GiuseppeMultilingualNeural"

_SCENES = [
    {
        "label": "Late Evening at the Police Station",
        "lines": [
            {"speaker": "female-2", "text": "Det er nesten elleve.", "translation": "It's almost eleven."},
            {
                "speaker": "male-1",
                "text": "Jeg må gå gjennom arkivet.",
                "translation": "I have to go through the archive.",
            },
            {"speaker": "male-3", "text": "Mappa ble flyttet.", "translation": "The file was moved."},
        ],
    }
]


def _no():
    return get_language("no")


# ── the cast ──────────────────────────────────────────────────────────────


def test_the_narrator_is_davis():
    assert NARRATOR_VOICE == _DAVIS


@pytest.mark.parametrize("code", ["sl", "no", "tl", "ceb"])
def test_every_plugin_narrates_with_davis(code):
    assert get_language(code).tts_voice_map["narrator"] == _DAVIS


def test_norwegian_english_cast_is_the_one_the_user_approved():
    """Literal table, confirmed by ear on 2026-09-29.

    The five Multilingual cast members read their own English. Pernille, Iselin
    and Finn speak only Norwegian, so each has a new English voice, chosen for
    pitch: Nancy sits in the gap between Shimmer and Emma, Amanda above Emma,
    Adam is the lowest English voice measured. The legacy two-role names follow
    female-1 / male-1, as they do in ``tts_voice_map``.
    """
    assert _no().tts_en_voice_map == {
        "female-1": "en-US-NancyMultilingualNeural",
        "female-2": "en-US-AmandaMultilingualNeural",
        "female-3": "en-US-EmmaMultilingualNeural",
        "female-4": "en-US-ShimmerTurboMultilingualNeural",
        "male-1": "en-US-AdamMultilingualNeural",
        "male-2": "en-US-DerekMultilingualNeural",
        "male-3": _GIUSEPPE,
        "male-4": "en-US-DustinMultilingualNeural",
        "female": "en-US-NancyMultilingualNeural",
        "male": "en-US-AdamMultilingualNeural",
        "narration": _DAVIS,  # narration is the narrator's (tunatale-fx5n, 2026-09-30)
    }


def test_norwegian_english_cast_covers_every_dialogue_role():
    """A role missing here falls back to the narrator, silently — so pin it."""
    roles = set(_no().tts_voice_map) - {"narrator"}
    assert set(_no().tts_en_voice_map) == roles


def test_multilingual_cast_members_keep_their_own_voice_in_english():
    no = _no()
    for role, voice in no.tts_voice_map.items():
        if role != "narrator" and "Multilingual" in voice:
            assert no.tts_en_voice_map[role] == voice, role


@pytest.mark.parametrize("code", sorted(known_language_codes()))
def test_no_english_voice_map_names_a_paid_hd_voice(code):
    """Same rule as ``test_no_voice_map_names_a_paid_hd_voice``, second map."""
    for role, voice in get_language(code).tts_en_voice_map.items():
        assert ":" not in voice and "DragonHD" not in voice, f"{code} {role}={voice}"


# ── seam 1: the builders ─────────────────────────────────────────────────

_BUILDERS = [
    build_translated_section,
    build_slow_translated_section,
    build_en_translated_section,
    build_slow_en_translated_section,
]


@pytest.mark.parametrize("builder", _BUILDERS)
def test_each_translation_is_read_by_its_speakers_english_voice(builder):
    no = _no()
    section = builder(_SCENES, no.tts_voice_map, _DAVIS, "no", en_voice_map=no.tts_en_voice_map)
    english = [p for p in section.phrases if p.language_code == "en"]
    by_text = {p.text: p.voice_id for p in english}
    assert by_text["It's almost eleven."] == "en-US-AmandaMultilingualNeural"
    assert by_text["I have to go through the archive."] == "en-US-AdamMultilingualNeural"
    assert by_text["The file was moved."] == _GIUSEPPE
    # The title and the scene label stay the narrator's.
    assert english[0].voice_id == _DAVIS
    assert by_text["Late Evening at the Police Station"] == _DAVIS


@pytest.mark.parametrize("builder", _BUILDERS)
def test_a_translation_keeps_the_narrator_role(builder):
    """The role is structure, not voice: every English phrase is still ``narrator``."""
    no = _no()
    section = builder(_SCENES, no.tts_voice_map, _DAVIS, "no", en_voice_map=no.tts_en_voice_map)
    assert {p.role for p in section.phrases if p.language_code == "en"} == {"narrator"}


@pytest.mark.parametrize("builder", _BUILDERS)
def test_without_an_english_map_the_narrator_reads_everything(builder):
    """sl / tl / ceb declare no English cast; their lessons are unchanged but for the narrator."""
    no = _no()
    section = builder(_SCENES, no.tts_voice_map, _DAVIS, "no")
    assert {p.voice_id for p in section.phrases if p.language_code == "en"} == {_DAVIS}


def test_a_speaker_missing_from_the_english_map_falls_back_to_the_narrator():
    no = _no()
    section = build_translated_section(_SCENES, no.tts_voice_map, _DAVIS, "no", en_voice_map={"female-2": "x"})
    by_text = {p.text: p.voice_id for p in section.phrases if p.language_code == "en"}
    assert by_text["It's almost eleven."] == "x"
    assert by_text["I have to go through the archive."] == _DAVIS


def test_key_phrase_grouping_is_unaffected_by_the_english_voice():
    """``key_phrase_groups`` keys on role; a new English voice must not regroup anything."""
    no = _no()
    with_cast = build_translated_section(_SCENES, no.tts_voice_map, _DAVIS, "no", en_voice_map=no.tts_en_voice_map)
    without = build_translated_section(_SCENES, no.tts_voice_map, _DAVIS, "no")
    assert key_phrase_groups(with_cast, "no") == key_phrase_groups(without, "no")


def test_a_story_built_for_norwegian_uses_the_plugin_english_cast():
    """The wiring: ``build_lesson_from_story`` hands the plugin map to all four builders."""
    from app.generation.story import build_lesson_from_story

    lesson = build_lesson_from_story({"title": "T", "key_phrases": [], "scenes": _SCENES}, _no())
    translated = {
        SectionType.TRANSLATED,
        SectionType.SLOW_TRANSLATED,
        SectionType.EN_TRANSLATED,
        SectionType.SLOW_EN_TRANSLATED,
    }
    for section in lesson.sections:
        if section.section_type not in translated:
            continue
        by_text = {p.text: p.voice_id for p in section.phrases if p.language_code == "en"}
        assert by_text["It's almost eleven."] == "en-US-AmandaMultilingualNeural", section.section_type
    assert lesson.narrator_voice == _DAVIS


# ── seam 2: the gain ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("voice_id", "expected"),
    [
        # Measured 2026-09-29 on 8 real English translation lines from a stored
        # Norwegian lesson, through synthesize() and the real tts cache, ffmpeg
        # ebur128 integrated LUFS per clip, gain = -20.0 - mean. That set read
        # 0.5 dB quieter than Guy's mean over 60 cached corpus lines (-19.48,
        # se 0.07), so every value is anchored by subtracting 0.5.
        (_DAVIS, 1.1),
        ("en-US-NancyMultilingualNeural", 2.1),
        ("en-US-AmandaMultilingualNeural", 1.5),
        ("en-US-AdamMultilingualNeural", -0.6),
        ("en-US-EmmaMultilingualNeural", -2.5),
        ("en-US-ShimmerTurboMultilingualNeural", -0.4),
        ("en-US-DerekMultilingualNeural", 0.9),
        (_GIUSEPPE, 0.9),
        ("en-US-DustinMultilingualNeural", 0.7),
        # Stored lessons still pin Guy until they are rebuilt; 0.0 for him would
        # un-normalise every one of them on their next render.
        ("en-US-GuyNeural", -0.5),
        # Cebuano male-1's English since 2026-10-01. Anchored to Adam's entry on
        # the 13 lines of 'An Evening Wake in Jimenez' both voices read for at
        # least 1.5 s: Adam -19.70 vs Lewis -21.81 LUFS, so -0.6 + 2.11.
        ("en-US-LewisMultilingualNeural", 1.5),
    ],
)
def test_english_gain_for_each_voice(voice_id, expected):
    assert get_tts_voice_gain_db("en", voice_id) == expected


def test_a_voice_has_a_different_gain_in_english_than_in_norwegian():
    """Why the English table exists: Giuseppe is 1.6 dB apart across the two languages."""
    assert get_tts_voice_gain_db("no", _GIUSEPPE) == -0.7
    assert get_tts_voice_gain_db("en", _GIUSEPPE) == 0.9


class _NoPre(TextPreprocessor):
    def preprocess(self, text, section_type):
        return text


def _renderer(tts) -> LessonRenderer:
    return LessonRenderer(tts=tts, preprocessors={"no": _NoPre()}, pause_calculator=NaturalPauseCalculator())


def _wav(path: Path, marker: float) -> Path:
    path.write_bytes(_make_wav_bytes(duration_ms=200, marker=marker))
    return path


def test_an_english_line_in_a_norwegian_lesson_gets_its_english_gain(tmp_path):
    """The seam the table tests missed: through the assembler, not the lookup.

    +1.1 dB is a factor of 1.1350 (10 ** (1.1 / 20), stated as a literal).
    Before this change the English phrase came out at exactly its input level.
    """
    section = Section(
        section_type=SectionType.TRANSLATED,
        phrases=[Phrase(text="It's almost eleven.", voice_id=_DAVIS, language_code="en", role="narrator")],
    )
    audio, _ = _renderer(AsyncMock())._assemble_section_audio(
        section, [_wav(tmp_path / "a.wav", 0.25)], NaturalPauseCalculator()
    )
    np.testing.assert_allclose(np.abs(audio.samples).max(), 0.25 * 1.1350, rtol=2e-3)


async def test_the_lesson_title_gets_the_narrators_english_gain(tmp_path):
    """The title is English text; it must use the ``en`` table, not the lesson's.

    Under the lesson's own table Davis has no entry, so keying the title on
    ``lesson.language_code`` would silently drop it to 0.0 dB.
    """

    async def fake_synthesize(
        text, voice_id, output_path, rate="+0%", phonemes=None, speak_locale=None, enunciation=None
    ):
        output_path.write_bytes(_make_wav_bytes(duration_ms=200, marker=0.5 if text == "Title" else 0.0))

    tts = AsyncMock()
    tts.synthesize = fake_synthesize
    lesson = Lesson(
        title="Title",
        language_code="no",
        narrator_voice=_DAVIS,
        sections=[
            Section(
                section_type=SectionType.TRANSLATED,
                phrases=[Phrase(text="x", voice_id=_DAVIS, language_code="en", role="narrator")],
            )
        ],
        key_phrases=[KeyPhraseInfo(phrase="x", translation="y")],
    )
    out = tmp_path / "out.wav"
    await _renderer(tts).render(lesson, out)
    samples, _ = sf.read(str(out), dtype="float32")
    np.testing.assert_allclose(np.abs(samples).max(), 0.5 * 1.1350, rtol=2e-3)


# ── seam 3: the <lang> wrapper ───────────────────────────────────────────


async def _locales(tmp_path, phrases: list[Phrase]) -> dict[str, str | None]:
    """Text -> the ``speak_locale`` it was synthesized with (calls run concurrently)."""
    seen: dict[str, str | None] = {}

    async def fake_synthesize(
        text, voice_id, output_path, rate="+0%", phonemes=None, speak_locale=None, enunciation=None
    ):
        seen[text] = speak_locale
        output_path.write_bytes(_make_wav_bytes())

    tts = AsyncMock()
    tts.synthesize = fake_synthesize
    renderer = LessonRenderer(
        tts=tts,
        preprocessors={"no": _NoPre()},
        pause_calculator=NaturalPauseCalculator(),
        tts_locales={"no": "nb-NO", "en": "en-US"},
    )
    section = Section(section_type=SectionType.TRANSLATED, phrases=phrases)
    await renderer._render_section(section, tmp_path, 0, "no", {}, asyncio.Lock())
    return seen


async def test_an_italian_voice_reading_english_is_told_it_is_english(tmp_path):
    seen = await _locales(
        tmp_path,
        [
            Phrase(text="Mappa ble flyttet.", voice_id=_GIUSEPPE, language_code="no", role="male-3"),
            Phrase(text="The file was moved.", voice_id=_GIUSEPPE, language_code="en", role="narrator"),
        ],
    )
    assert seen == {"Mappa ble flyttet.": "nb-NO", "The file was moved.": "en-US"}


def test_the_english_locale_leaves_a_native_en_us_voice_cache_key_alone():
    """A native voice cannot mis-detect, so its English clips stay cache hits."""
    az = AzureTTSService(cache_dir=Path("/nonexistent"), key="k", region="r")
    text = "It's almost eleven."
    assert az._cache_path(text, "en-US-GuyNeural", "+0%", None, "en-US") == az._cache_path(
        text, "en-US-GuyNeural", "+0%"
    )


def test_the_multilingual_narrator_is_told_its_english_is_english():
    """Davis is Multilingual: without <lang> he guesses the language per line.

    This used to pin the opposite (no wrapper, so no cache miss) for Davis. The
    stand-ins it covered mis-detected "Come in, come in." as Tagalog on 2026-09-30;
    see test_azure_tts.py::test_a_multilingual_voice_is_wrapped_even_in_its_own_locale.
    """
    ssml = AzureTTSService._build_ssml("It's almost eleven.", _DAVIS, "+0%", speak_locale="en-US")
    assert '<lang xml:lang="en-US">' in ssml


def test_the_renderer_factory_knows_the_english_locale():
    from app.audio.renderer import build_lesson_renderer
    from app.config import settings

    renderer = build_lesson_renderer(AsyncMock(), ["no"], settings)
    assert renderer._tts_locales["en"] == "en-US"


# ── every language (tunatale-ucpg, extended 2026-09-29) ──────────────────
#
# Cebuano: the user chose option 2 — pitch-matched Azure English stand-ins for
# all four Gemini voices (free allowance, deterministic, regression-testable),
# over the Gemini voices reading their own English. Tagalog and Slovene follow
# the Norwegian rule: a Multilingual cast member reads its own English; a
# native-only voice gets a stand-in. The stand-ins are one repertoire across
# languages (Emma, Nancy, Amanda, Adam, Dustin), each already gain-measured.
# Cebuano's male-1 left that repertoire for Lewis on 2026-10-01, by ear.

_EN_CASTS = {
    "ceb": {
        "female-1": "en-US-EmmaMultilingualNeural",  # Kore 209.4 Hz
        "female-2": "en-US-NancyMultilingualNeural",  # Despina 185.5
        # Not the pitch match (that was Adam, 105.9): the user's ear,
        # 2026-10-01, found Adam too close to the narrator on this lesson's
        # text. Lewis was their second pick from a nine-voice audition
        # (tunatale-fx5n); Brandon, the first, clips one-word lines.
        "male-1": "en-US-LewisMultilingualNeural",  # Charon 107.2
        "male-2": "en-US-DustinMultilingualNeural",  # Orus 139.2
        "female": "en-US-EmmaMultilingualNeural",
        "male": "en-US-LewisMultilingualNeural",
        "narration": _DAVIS,
    },
    "tl": {
        "female-1": "en-US-AmandaMultilingualNeural",  # Blessica, above Emma in tl too
        "female-2": "en-US-EmmaMultilingualNeural",
        "male-1": "en-US-AdamMultilingualNeural",  # Angelo
        "male-2": "en-US-SamuelMultilingualNeural",
        "female": "en-US-AmandaMultilingualNeural",
        "male": "en-US-AdamMultilingualNeural",
        "narration": _DAVIS,
    },
    "sl": {
        "female-1": "en-US-AmandaMultilingualNeural",  # Petra 185.7, above Emma's 166.9 in sl
        "female-2": "en-US-EmmaMultilingualNeural",
        "male-1": "en-US-AdamMultilingualNeural",  # Rok 91.0
        "male-2": "de-DE-FlorianMultilingualNeural",
        "female": "en-US-AmandaMultilingualNeural",
        "male": "en-US-AdamMultilingualNeural",
        "narration": _DAVIS,
    },
}


@pytest.mark.parametrize("code", sorted(_EN_CASTS))
def test_each_languages_english_cast_is_the_approved_one(code):
    assert get_language(code).tts_en_voice_map == _EN_CASTS[code]


@pytest.mark.parametrize("code", ["sl", "no", "tl", "ceb"])
def test_every_dialogue_role_has_an_english_voice(code):
    """A role missing from the English map falls back to the narrator silently.

    ``key-phrases`` (tl) is not a dialogue role: the key-phrase translation is
    always the narrator's, so it has no English voice to declare.
    """
    lang = get_language(code)
    roles = set(lang.tts_voice_map) - {"narrator", "key-phrases"}
    assert set(lang.tts_en_voice_map) == roles


@pytest.mark.parametrize("code", ["sl", "no", "tl", "ceb"])
def test_every_voice_that_reads_english_has_an_english_gain(code):
    lang = get_language(code)
    readers = {lang.tts_voice_map["narrator"], *lang.tts_en_voice_map.values()}
    assert not readers - set(get_language("en").tts_voice_gain_db)


@pytest.mark.parametrize("code", ["sl", "tl"])
def test_multilingual_cast_members_read_their_own_english(code):
    lang = get_language(code)
    for role, voice in lang.tts_voice_map.items():
        if role in lang.tts_en_voice_map and "Multilingual" in voice:
            assert lang.tts_en_voice_map[role] == voice, (code, role)


def test_no_gemini_voice_reads_english():
    """Option 2: Cebuano's English is Azure, so it bills against the free allowance."""
    assert not [v for v in get_language("ceb").tts_en_voice_map.values() if v.endswith("Gemini")]


@pytest.mark.parametrize(
    ("voice_id", "expected"),
    [
        # Same 8-line set and the same -0.5 anchor as the table above.
        # Raw means: Samuel -20.33, Florian -19.46 LUFS.
        ("en-US-SamuelMultilingualNeural", -0.2),
        ("de-DE-FlorianMultilingualNeural", -1.0),
    ],
)
def test_english_gain_for_the_tagalog_and_slovene_readers(voice_id, expected):
    assert get_tts_voice_gain_db("en", voice_id) == expected


def test_no_voice_in_the_cast_is_one_known_to_clip_one_word_lines():
    """Brandon cannot say a short one-word line (tunatale-fx5n, 2026-10-01).

    Measured through the product's own request: "Why?", "Yes.", "No.", "Okay.",
    "What?" and "Where?" each came back with 110-150 ms of sound, against
    190-470 ms for Lewis, Derek and Samuel, and it was the same in five request
    shapes (with and without <lang>, <prosody> and a <break>). The user heard it
    as "cut off". Azure is deterministic, so no re-render fixes it, and a lesson
    has no way to avoid a one-word line. He was picked by ear for Cebuano male-1
    and had to be dropped; this keeps him from being picked again.
    """
    for code in ("sl", "no", "tl", "ceb"):
        lang = get_language(code)
        voices = {*lang.tts_voice_map.values(), *lang.tts_en_voice_map.values()}
        assert "en-US-BrandonMultilingualNeural" not in voices, code
    assert "en-US-BrandonMultilingualNeural" not in get_language("en").tts_voice_gain_db
