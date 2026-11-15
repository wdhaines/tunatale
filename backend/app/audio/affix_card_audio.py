"""An affix drill card's speech, synthesized once at mint (tunatale-5i1p.6).

A lesson's drill card plays its lesson's takes (``sentence_card_audio``); an
affix drill card's clips are in no lesson, so they are made here, once,
best-effort. The card is built as the drill section the audio drill itself
uses (``build_affix_drill_section``: the key-phrases voice, per-word readings
via ``plan_section``), sent through the renderer's own planned requests into
the TTS cache, then attached by the same section-aware lookup a lesson drill
card uses (``render_cost.section_take``). One address space: the clip the
estimator prices is the clip that is attached.

Never ``cloze_tts.synthesize_cloze_audios``: it sends the plain text, and read
plain the Cebuano voice says "ugna" for "ugma" (tunatale-u8nz.26).

A failure is logged (``DRILL_CARD_SYNTH_FAILED``) and the card keeps no sound;
running this again fills it in. Every clip is synthesized before any is
attached, so a half-voiced card never reaches a note.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from app.audio.render_cost import section_take
from app.audio.sentence_card_audio import add_take
from app.audio.synth_plan import plan_section
from app.generation.affix_drill import AffixDrill
from app.generation.affix_drill import DrillStep as SpokenStep
from app.generation.section_builder import build_affix_drill_section
from app.languages import get_language, get_phoneme_planner, get_preprocessor, get_slow_word, get_tts_locale
from app.models.lesson import Lesson, Section, SectionType
from app.srs.drill_card import DrillCard

_log = logging.getLogger(__name__)


def affix_drill_card_section(card: DrillCard, language_code: str) -> Section:
    """*card*'s clips as an AFFIX_DRILL section, one phrase per clip in ``media_kinds`` order.

    The model and each cue are said as ``model`` steps, each answer as an
    ``answer`` step, all in the key-phrases voice. The narrator's title the
    section builder opens on is dropped: the card never plays it.
    """
    answers = {kind for kind, _ in card.media_kinds() if kind.startswith("drill_answer_")}
    drill = AffixDrill(
        steps=[
            SpokenStep("answer" if kind in answers else "model", text, language_code)
            for kind, text in card.media_kinds()
        ]
    )
    voices = get_language(language_code).tts_voice_map
    section = build_affix_drill_section(
        drill, narrator_voice=voices["narrator"], l2_voice=voices.get("key-phrases") or voices["female-1"]
    )
    section.phrases = [phrase for phrase in section.phrases if phrase.language_code == language_code]
    return section


async def synthesize_drill_card_audio(
    db, collocation_id: int, card: DrillCard, language_code: str, *, tts, cache_dir: Path, media_dir: Path
) -> None:
    """Synthesize every clip *card* plays into *cache_dir*, then attach each as its media row."""
    section = affix_drill_card_section(card, language_code)
    lesson = Lesson(title=card.key, language_code=language_code, sections=[section], narrator_voice=None)
    wanted = [(kind, text) for kind, text in card.media_kinds() if not db.has_media_row(collocation_id, kind)]
    if not wanted:
        return
    try:
        if tts is None:
            raise RuntimeError("no TTS service")
        requests = plan_section(
            section,
            language_code,
            preprocessor=get_preprocessor(language_code),
            planner=get_phoneme_planner(language_code),
            locale_for=get_tts_locale,
            slow_word=get_slow_word(language_code),
        )
        with tempfile.TemporaryDirectory() as tmp:
            for i, request in enumerate(requests):
                await tts.synthesize(
                    request.text,
                    request.voice_id,
                    Path(tmp) / f"{i}.mp3",
                    rate=request.rate,
                    phonemes=request.phonemes,
                    speak_locale=request.speak_locale,
                    enunciation=request.enunciation,
                    line=request.line,
                    stressed=request.stressed,
                )
    except Exception as exc:
        _log.warning("DRILL_CARD_SYNTH_FAILED key=%s error=%r — the card is left without sound", card.key, exc)
        return
    added = False
    for kind, text in wanted:
        take = section_take(lesson, text, SectionType.AFFIX_DRILL, cache_dir=cache_dir)
        if take is None:
            _log.warning("DRILL_CARD_NO_TAKE key=%s kind=%s text=%r — not in the TTS cache", card.key, kind, text)
            continue
        add_take(db, collocation_id, kind, take, media_dir)
        added = True
    if added:
        db.add_dirty_field_by_id(collocation_id, "audio")
