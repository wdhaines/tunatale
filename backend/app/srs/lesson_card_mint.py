"""Minting a lesson's own card list into the deck (tunatale-h0q3.43).

The cards come from the lesson file (``app.srs.lesson_cards``). They mint when
the owner marks the lesson studied ("I listened": the owner's meaning is "I
went through it and did the lesson", 2026-11-06), all at once and NEW; the
deck's daily new-card limit paces them, and their Anki positions (the writer's
course region) keep them in the list's order ahead of the frequency deck. A
card list that arrives for a day already studied mints at import.

Rows go through ``add_collocation`` per the card-adding contract
(``.claude/rules/anki-sync.md``): NEW, no Anki ids, the next sync creates the
notes. Minting is idempotent: a card whose key is in the deck is skipped, and so
is a word the deck already has (by lemma or by text), whoever added it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.audio.sentence_card_audio import attach_drill_card_audio, attach_sentence_card_audio
from app.common.guid import compute_guid
from app.models.lesson import Lesson
from app.srs.drill_card import drill_card
from app.srs.lesson_cards import LessonCard, lesson_cards
from app.srs.sentence_card import sentence_card

_log = logging.getLogger(__name__)


def _in_deck(db, card: LessonCard, language_code: str) -> bool:
    unit = card.unit
    if db.get_collocation_id_by_guid(compute_guid(unit.text, language_code, unit.disambig_key)) is not None:
        return True
    if unit.card_type == "vocab":
        return db.get_collocation_by_lemma_with_id(unit.lemma) is not None or db.get_collocation(unit.text) is not None
    return False


def mint_lesson_cards(
    db, language_code: str, cards: dict, drills: list[dict] | tuple = ()
) -> list[tuple[int, LessonCard]]:
    """Add the cards *cards* makes that the deck lacks, NEW and in list order; return ``(id, card)`` per row added.

    *drills* are the story's own, which a ``drill`` item names (tunatale-5i1p.4).
    """
    minted: list[tuple[int, LessonCard]] = []
    for card in lesson_cards(cards, drills):
        if _in_deck(db, card, language_code):
            continue
        db.add_collocation(card.unit, language_code=language_code)
        cid = db.get_collocation_id_by_guid(compute_guid(card.unit.text, language_code, card.unit.disambig_key))
        minted.append((cid, card))
    if minted:
        _log.info("LESSON_CARDS_MINTED set=%s count=%d", cards["set"], len(minted))
    return minted


def attach_lesson_card_audio(
    db,
    minted: list[tuple[int, LessonCard]],
    lesson: Lesson,
    voice_map: dict[str, str],
    *,
    cache_dir: Path,
    media_dir: Path,
) -> None:
    """Give each minted sentence card the lesson's own takes of its line and its cue, and each
    drill card the takes of every clip its steps play.

    A word's audio is not here: it is an ordinary vocab card, voiced by the
    listen's media step like any other.
    """
    for cid, card in minted:
        drill = drill_card(card.unit)
        if drill is not None:
            attach_drill_card_audio(
                db, cid, drill, lesson, cache_dir=cache_dir, media_dir=media_dir, answer_voice=voice_map.get(card.role)
            )
            continue
        sentence = sentence_card(card.unit)
        if sentence is None:
            continue
        attach_sentence_card_audio(
            db,
            cid,
            sentence,
            lesson,
            cache_dir=cache_dir,
            media_dir=media_dir,
            answer_voice=voice_map.get(card.role),
            cue_voice=voice_map.get(card.cue_role) if card.cue_role else None,
        )


def day_was_studied(db, store, curriculum_id: str, day: int) -> bool:
    """Whether any lesson of *curriculum_id*'s *day* has a listen: an import's lesson is a new id."""
    for listened in db.get_listened_lessons():
        row = store.get_lesson_row(listened["lesson_id"])
        if row is not None and row["curriculum_id"] == curriculum_id and row["day"] == day:
            return True
    return False
