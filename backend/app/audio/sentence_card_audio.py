"""A sentence card's audio is the lesson's own takes (tunatale-h0q3.42).

The answer plays as the lesson said it, and so does the cue on the front: the
two clips the lesson's Natural Speed section already rendered, found in the TTS
cache by :func:`app.audio.render_cost.natural_take` and copied into TT's media
dir as the card's ``audio_tts_sentence`` and ``audio_cue`` rows. The sync builds
the note's ``[sound:]`` tags from those rows and copies the files to Anki.

A drill card (tunatale-5i1p.4) gets one row per clip any of its steps plays
(``DrillCard.media_kinds``: ``drill_model``, ``drill_cue_<k>``,
``drill_answer_<k>``), each the lesson's take from its Drill section.

Nothing here synthesizes. A take the cache does not hold (a lesson not rendered
yet, or rendered on another machine) is logged and left out; attaching again
after a render fills it in, and the ``audio`` flag carries it to a minted note.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from app.audio.render_cost import natural_take, section_take
from app.models.lesson import Lesson, SectionType
from app.srs.drill_card import DrillCard
from app.srs.sentence_card import SentenceCard

_log = logging.getLogger(__name__)

_ANSWER, _CUE = "audio_tts_sentence", "audio_cue"


def attach_sentence_card_audio(
    db,
    collocation_id: int,
    card: SentenceCard,
    lesson: Lesson,
    *,
    cache_dir: Path,
    media_dir: Path,
    answer_voice: str | None = None,
    cue_voice: str | None = None,
) -> tuple[str | None, str | None]:
    """Attach *card*'s answer and cue takes from *lesson*; return their media filenames (or None).

    Idempotent: a kind already attached is left as it is. When a new row lands,
    the row is flagged ``audio`` so a note already minted gets its sound.
    """
    added = False
    wanted = [(_ANSWER, card.spoken, answer_voice)]
    if card.cue is not None:
        wanted.append((_CUE, card.cue.spoken, cue_voice))
    for kind, spoken, voice in wanted:
        if db.has_media_row(collocation_id, kind):
            continue
        take = natural_take(lesson, spoken, cache_dir=cache_dir, voice_id=voice)
        if take is None:
            _log.warning(
                "SENTENCE_CARD_NO_TAKE key=%s kind=%s text=%r — the lesson's take is not in the TTS cache",
                card.key,
                kind,
                spoken,
            )
            continue
        _add_take(db, collocation_id, kind, take, media_dir)
        added = True
    if added:
        db.add_dirty_field_by_id(collocation_id, "audio")
    return db.get_media_filename(collocation_id, _ANSWER), db.get_media_filename(collocation_id, _CUE)


def attach_drill_card_audio(
    db,
    collocation_id: int,
    card: DrillCard,
    lesson: Lesson,
    *,
    cache_dir: Path,
    media_dir: Path,
    answer_voice: str | None = None,
) -> None:
    """Attach every clip *card*'s steps play, from *lesson*'s Drill section.

    Answers are looked up in *answer_voice* (the card's role); a cue or the
    model in whichever voice the drill says it. Idempotent, and flags the row
    ``audio`` when a row lands, as for a sentence card.
    """
    added = False
    answers = {kind for kind, _ in card.media_kinds() if kind.startswith("drill_answer_")}
    for kind, text in card.media_kinds():
        if db.has_media_row(collocation_id, kind):
            continue
        voice = answer_voice if kind in answers else None
        take = section_take(lesson, text, SectionType.AFFIX_DRILL, cache_dir=cache_dir, voice_id=voice)
        if take is None:
            _log.warning(
                "DRILL_CARD_NO_TAKE key=%s kind=%s text=%r — the lesson's take is not in the TTS cache",
                card.key,
                kind,
                text,
            )
            continue
        _add_take(db, collocation_id, kind, take, media_dir)
        added = True
    if added:
        db.add_dirty_field_by_id(collocation_id, "audio")


def _add_take(db, collocation_id: int, kind: str, take: Path, media_dir: Path) -> None:
    name = f"take_{take.name}"
    dest = media_dir / name
    if not dest.exists():
        shutil.copyfile(take, dest)
    data = dest.read_bytes()
    db.add_media(
        collocation_id=collocation_id,
        kind=kind,
        filename=name,
        path=f"media/{name}",
        anki_filename=name,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )
