"""The affix drill as lesson audio: the section, its pauses and its cues (tunatale-ve4p.4).

``app.generation.affix_drill`` writes the script; this is what carries it into
a lesson. Three things are decided here and nowhere else:

* **The section.** Each step becomes a phrase whose ``role`` says what it is
  (the narrator's English, a prompt, a modelled form, an answer).
* **The pause after a prompt.** The learner has to think of the form and then
  say it, so the gap is thinking time plus the length of the ANSWER, which is
  the next clip. Every other pause in a lesson is decided by the clip it
  follows; this one looks ahead.
* **The cues.** An English line and the form that follows it are one item, so
  the player's sentence step and its repeat latch move by prompt-and-answer.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import numpy as np
import pytest
import soundfile as sf

from app.audio.cues import CueTiming, build_cue_manifest
from app.audio.pause_calculator import NaturalPauseCalculator
from app.audio.preprocessing.base import TextPreprocessor
from app.audio.renderer import LessonRenderer
from app.generation.affix_drill import AffixDrill, DrillStep, build_affix_drill
from app.generation.section_builder import SECTION_TITLES, build_affix_drill_section
from app.languages import get_a1_morphology
from app.models.lesson import Lesson, Phrase, Section, SectionType
from tests.test_renderer import _make_wav_bytes

_NARRATOR = "en-US-DavisMultilingualNeural"
_KORE = "ceb-PH-KoreGemini"
_MO_MI = next(p for p in get_a1_morphology("ceb").patterns if p.key == "mo-mi")


def _section(drill: AffixDrill) -> Section:
    return build_affix_drill_section(drill, narrator_voice=_NARRATOR, l2_voice=_KORE)


def _drill() -> AffixDrill:
    return AffixDrill(
        steps=[
            DrillStep("model", "walk", "en"),
            DrillStep("model", "lakaw", "ceb"),
            DrillStep("prompt", "Say: walked.", "en"),
            DrillStep("answer", "milakaw", "ceb"),
        ]
    )


# --- the section -----------------------------------------------------------


def test_the_section_opens_with_its_spoken_title_then_one_phrase_per_step():
    section = _section(_drill())

    assert section.section_type is SectionType.AFFIX_DRILL
    assert [(p.text, p.voice_id, p.language_code, p.role) for p in section.phrases] == [
        (SECTION_TITLES[SectionType.AFFIX_DRILL], _NARRATOR, "en", "narrator"),
        ("walk", _NARRATOR, "en", "narrator"),
        ("lakaw", _KORE, "ceb", "model"),
        ("Say: walked.", _NARRATOR, "en", "prompt"),
        ("milakaw", _KORE, "ceb", "answer"),
    ]


def test_a_built_up_piece_keeps_where_it_came_from():
    """A piece of a word is voiced from the word's reading, the way a Key Phrases
    piece is (synth_plan plans a chunk from its source word and span), so the
    section carries both through."""
    drill = AffixDrill(steps=[DrillStep("model", "nom", "ceb", "moinom", (2, 3)), DrillStep("model", "moinom", "ceb")])

    piece, whole = _section(drill).phrases[1:]
    assert (piece.source_word, piece.syllable_span, piece.role) == ("moinom", (2, 3), "model")
    assert (whole.source_word, whole.syllable_span) == (None, None)


def test_a_real_drill_keeps_every_step_in_order():
    """Built from the real script, so the builder is not just fitted to a stub."""
    drill = build_affix_drill("ceb", _MO_MI, roots=["inom", "lakaw"])
    section = _section(drill)

    assert [p.text for p in section.phrases[1:]] == [step.text for step in drill.steps]
    assert [p.role for p in section.phrases].count("prompt") == 4
    assert [p.role for p in section.phrases].count("answer") == 4


def test_a_drill_section_survives_the_stored_form():
    lesson = Lesson(title="Mo and mi", language_code="ceb", sections=[_section(_drill())])

    restored = Lesson.from_json(lesson.to_json())

    assert restored.sections[0].section_type is SectionType.AFFIX_DRILL
    assert restored.sections[0].phrases == lesson.sections[0].phrases


# --- the pauses ------------------------------------------------------------


@pytest.fixture
def calc() -> NaturalPauseCalculator:
    return NaturalPauseCalculator()


def _pause(calc: NaturalPauseCalculator, duration_s: float, language_code: str, **kwargs) -> int:
    return calc.get_phrase_pause(
        audio_duration_s=duration_s,
        word_count=1,
        section_type=SectionType.AFFIX_DRILL,
        language_code=language_code,
        **kwargs,
    )


def test_a_prompt_waits_thinking_time_plus_the_length_of_its_answer(calc):
    """2 s to find the form, then as long as the form takes to say."""
    assert _pause(calc, 1.2, "en", role="prompt", next_duration_s=0.8) == 2800
    assert _pause(calc, 1.2, "en", role="prompt", next_duration_s=2.5) == 4500


def test_the_prompts_own_length_does_not_move_its_pause(calc):
    assert _pause(calc, 0.4, "en", role="prompt", next_duration_s=0.8) == _pause(
        calc, 3.0, "en", role="prompt", next_duration_s=0.8
    )


def test_a_prompt_with_nothing_after_it_still_leaves_thinking_time(calc):
    assert _pause(calc, 1.2, "en", role="prompt") == 2000


def test_a_form_is_followed_by_room_to_say_it_back(calc):
    """Its own length, as in Key Phrases, but never under a second."""
    assert _pause(calc, 2.5, "ceb", role="answer") == 2500
    assert _pause(calc, 0.6, "ceb", role="answer") == 1000
    assert _pause(calc, 0.6, "ceb", role="model") == 1000


def test_the_narrators_other_english_keeps_the_base_pause(calc):
    assert _pause(calc, 1.2, "en", role="narrator") == 500


def test_a_prompt_role_means_nothing_outside_the_drill(calc):
    """The role is the drill's; another section's line is paced as before."""
    pause = calc.get_phrase_pause(
        audio_duration_s=1.2,
        word_count=1,
        section_type=SectionType.NATURAL_SPEED,
        language_code="en",
        role="prompt",
        next_duration_s=0.8,
    )
    assert pause == 500


# --- through the assembler -------------------------------------------------


class _NoPre(TextPreprocessor):
    def preprocess(self, text, section_type):
        return text


def _wav(path: Path, duration_ms: int) -> Path:
    path.write_bytes(_make_wav_bytes(duration_ms=duration_ms))
    return path


def _gaps(section: Section, files: list[Path], pace_files: list[Path] | None = None) -> list[int]:
    """The silence after each phrase, in ms, read off the assembled cues."""
    renderer = LessonRenderer(
        tts=AsyncMock(), preprocessors={"ceb": _NoPre()}, pause_calculator=NaturalPauseCalculator()
    )
    audio, cues = renderer._assemble_section_audio(section, files, NaturalPauseCalculator(), pace_files)
    ends = [*(start for _, start, _ in cues[1:]), len(audio.samples)]
    return [round((nxt - end) / audio.rate * 1000) for (_, _, end), nxt in zip(cues, ends, strict=True)]


def test_the_assembled_gap_after_a_prompt_is_sized_by_the_answer_clip(tmp_path):
    """The seam: the calculator can only look ahead if the assembler tells it what is there."""
    section = _section(_drill())
    durations = [300, 400, 600, 900, 800]  # title, "walk", "lakaw", the prompt, "milakaw"
    files = [_wav(tmp_path / f"{i}.wav", ms) for i, ms in enumerate(durations)]

    assert _gaps(section, files) == [
        500,  # the title
        500,  # "walk"
        1000,  # "lakaw": 600 ms said, a second to say it back
        2800,  # the prompt: 2000 + the 800 ms answer, NOT its own 900
        1000,  # "milakaw"
    ]


def test_a_prompt_that_ends_the_section_does_not_read_past_it(tmp_path):
    section = Section(
        section_type=SectionType.AFFIX_DRILL,
        phrases=[Phrase(text="Say: walked.", voice_id=_NARRATOR, language_code="en", role="prompt")],
    )

    assert _gaps(section, [_wav(tmp_path / "0.wav", 900)]) == [2000]


def test_the_answers_length_is_taken_from_its_pace_file(tmp_path):
    """Pacing follows the pace file wherever the two differ, here as for every other pause."""
    section = Section(
        section_type=SectionType.AFFIX_DRILL,
        phrases=[
            Phrase(text="Say: walked.", voice_id=_NARRATOR, language_code="en", role="prompt"),
            Phrase(text="milakaw", voice_id=_KORE, language_code="ceb", role="answer"),
        ],
    )
    prompt = _wav(tmp_path / "p.wav", 900)
    played, paced = _wav(tmp_path / "a.wav", 500), _wav(tmp_path / "a-pace.wav", 1500)

    assert _gaps(section, [prompt, played], [prompt, paced]) == [3500, 1500]


# --- the vendor's own silence ----------------------------------------------
#
# Measured on the first render (2026-10-07): Azure returns a short English clip
# padded to exactly 1.872 s whatever it says ("walk" is 226 ms of voice in
# 1872 ms of file), and a Gemini word arrives with about 300 ms of silence in
# front of it. Paced from the file, the drill had over a second of dead air
# after every one-word English line, on top of the pause that was meant.


def _padded(path: Path, lead_ms: int, voiced_ms: int, tail_ms: int, rate: int = 24000) -> Path:
    def frames(ms: int) -> int:
        return ms * rate // 1000

    samples = np.concatenate(
        [np.zeros(frames(lead_ms)), np.full(frames(voiced_ms), 0.5), np.zeros(frames(tail_ms))]
    ).astype("float32")
    sf.write(str(path), samples.reshape(-1, 1), rate, format="WAV", subtype="PCM_16")
    return path


def _lengths_and_gaps(section: Section, files: list[Path]) -> tuple[list[int], list[int]]:
    renderer = LessonRenderer(
        tts=AsyncMock(), preprocessors={"ceb": _NoPre()}, pause_calculator=NaturalPauseCalculator()
    )
    audio, cues = renderer._assemble_section_audio(section, files, NaturalPauseCalculator())
    ends = [*(start for _, start, _ in cues[1:]), len(audio.samples)]
    lengths = [round((end - start) / audio.rate * 1000) for _, start, end in cues]
    gaps = [round((nxt - end) / audio.rate * 1000) for (_, _, end), nxt in zip(cues, ends, strict=True)]
    return lengths, gaps


def test_a_drill_clip_is_cut_to_what_is_said_and_paced_from_that(tmp_path):
    """20 ms is kept in front of the voice and 100 ms after it; the rest is the vendor's."""
    section = Section(
        section_type=SectionType.AFFIX_DRILL,
        phrases=[
            Phrase(text="walk", voice_id=_NARRATOR, language_code="en", role="narrator"),
            Phrase(text="Say: walked.", voice_id=_NARRATOR, language_code="en", role="prompt"),
            Phrase(text="milakaw", voice_id=_KORE, language_code="ceb", role="answer"),
        ],
    )
    files = [
        _padded(tmp_path / "walk.wav", 150, 230, 1490),  # Azure's 1872 ms file
        _padded(tmp_path / "prompt.wav", 150, 640, 1080),
        _padded(tmp_path / "answer.wav", 330, 1200, 150),
    ]

    lengths, gaps = _lengths_and_gaps(section, files)

    assert lengths == [350, 760, 1320]
    assert gaps == [
        500,  # not 500 + 1390 of padding
        3320,  # 2000 + the answer as SAID (1320), not as filed (1680)
        1320,
    ]


def test_only_the_drill_is_cut(tmp_path):
    """Every other section's rhythm was settled by ear on the files as they are."""
    section = Section(
        section_type=SectionType.NATURAL_SPEED,
        phrases=[Phrase(text="walk", voice_id=_NARRATOR, language_code="en", role="narrator")],
    )

    lengths, gaps = _lengths_and_gaps(section, [_padded(tmp_path / "walk.wav", 150, 230, 1490)])

    assert (lengths, gaps) == ([1870], [500])


def test_voice_close_to_the_edge_of_its_file_loses_nothing(tmp_path):
    section = Section(
        section_type=SectionType.AFFIX_DRILL,
        phrases=[Phrase(text="milakaw", voice_id=_KORE, language_code="ceb", role="answer")],
    )

    lengths, _ = _lengths_and_gaps(section, [_padded(tmp_path / "a.wav", 10, 800, 40)])

    assert lengths == [850]


def test_a_clip_shorter_than_one_measuring_window_is_left_alone(tmp_path):
    section = Section(
        section_type=SectionType.AFFIX_DRILL,
        phrases=[Phrase(text="a", voice_id=_KORE, language_code="ceb", role="answer")],
    )
    path = tmp_path / "tiny.wav"
    sf.write(str(path), np.full((100, 1), 0.5, dtype="float32"), 24000, format="WAV", subtype="PCM_16")

    audio, cues = LessonRenderer(
        tts=AsyncMock(), preprocessors={"ceb": _NoPre()}, pause_calculator=NaturalPauseCalculator()
    )._assemble_section_audio(section, [path], NaturalPauseCalculator())

    assert cues == [(0, 0, 100)]


# --- the cues --------------------------------------------------------------


def _cue_refs(section: Section) -> list[dict | None]:
    lesson = Lesson(title="Mo and mi", language_code="ceb", sections=[section])
    timing = [
        CueTiming(section_index=0, phrase_index=i, start_frame=i * 1000, end_frame=i * 1000 + 500)
        for i in range(len(section.phrases))
    ]
    return [cue.ref for cue in build_cue_manifest(lesson, timing, rate=1000)]


def test_an_english_line_and_the_form_after_it_are_one_item():
    assert _cue_refs(_section(_drill())) == [
        {"kind": "narration"},  # the section title
        {"kind": "drill", "target_index": 0},  # "walk"
        {"kind": "drill", "target_index": 0},  # "lakaw"
        {"kind": "drill", "target_index": 1},  # the prompt
        {"kind": "drill", "target_index": 1},  # its answer
    ]


def test_drill_cues_never_point_at_a_dialogue_line():
    """A ``line`` ref is an index into the transcript's dialogue, which a drill is not in."""
    drill = build_affix_drill("ceb", _MO_MI, roots=["inom", "lakaw"], lines=[("Milakaw si Paul.", "Paul walked.")])
    refs = _cue_refs(_section(drill))

    assert {ref["kind"] for ref in refs} == {"narration", "drill"}
    items = [ref["target_index"] for ref in refs if ref["kind"] == "drill"]
    assert items == sorted(items)
    assert all(items.count(n) >= 2 for n in set(items))


def test_a_built_up_form_is_one_item_with_the_english_before_it():
    """The player's sentence step and repeat latch move by item, so a build-up
    split into one item per piece would repeat 'nom' alone. Key Phrases keeps
    a phrase and its pieces together; so does the drill (tunatale-ve4p.16)."""
    drill = AffixDrill(
        steps=[
            DrillStep("model", "will drink", "en"),
            DrillStep("model", "moinom", "ceb"),
            DrillStep("model", "nom", "ceb", "moinom", (2, 3)),
            DrillStep("model", "moinom", "ceb"),
            DrillStep("prompt", "Say: will drink.", "en"),
            DrillStep("answer", "moinom", "ceb"),
        ]
    )

    assert _cue_refs(_section(drill)) == [
        {"kind": "narration"},
        {"kind": "drill", "target_index": 0},
        {"kind": "drill", "target_index": 0},
        {"kind": "drill", "target_index": 0},
        {"kind": "drill", "target_index": 0},
        {"kind": "drill", "target_index": 1},
        {"kind": "drill", "target_index": 1},
    ]


def test_english_with_no_form_after_it_is_narration():
    section = Section(
        section_type=SectionType.AFFIX_DRILL,
        phrases=[
            Phrase(text="Affix Drill", voice_id=_NARRATOR, language_code="en", role="narrator"),
            Phrase(text="Now you.", voice_id=_NARRATOR, language_code="en", role="narrator"),
            Phrase(text="Say: walked.", voice_id=_NARRATOR, language_code="en", role="prompt"),
            Phrase(text="milakaw", voice_id=_KORE, language_code="ceb", role="answer"),
            Phrase(text="Say: drank.", voice_id=_NARRATOR, language_code="en", role="prompt"),
        ],
    )

    assert _cue_refs(section) == [
        {"kind": "narration"},
        {"kind": "narration"},
        {"kind": "drill", "target_index": 0},
        {"kind": "drill", "target_index": 0},
        {"kind": "narration"},
    ]
