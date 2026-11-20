"""Minting a lesson's own card list into the deck (tunatale-h0q3.43).

The cards come from the lesson file (``app.srs.lesson_cards``). They mint when
the owner marks the lesson studied ("I listened": the owner's meaning is "I
went through it and did the lesson", 2026-11-06), all at once and NEW; the
deck's daily new-card limit paces them, and their Anki positions (the writer's
course region) keep them in the list's order ahead of the frequency deck. A
card list that arrives for a day already studied mints at import.

Rows go through ``add_collocation`` per the card-adding contract
(``.claude/rules/anki-sync.md``): NEW, no Anki ids, the next sync creates the
notes. Minting is idempotent: a card whose key is in the deck is not added
again, and neither is a word the deck already has (by lemma or by text),
whoever added it.

A lesson card's identity is its key, never its text (tunatale-adxy). A card the
lesson corrected is updated in place: its row's text, sentence and
``card_data`` follow the card, its guid stays. A sentence line is updated as
one unit, every row of it or none: the blanks the learner mined are re-anchored
in the corrected text (a correction before a blank moves its span, and so its
key). What the update cannot place holds the line, nothing written, with
``LESSON_LINE_UPDATE_HELD``; the owner settles it on the Cards page. Every
update logs ``LESSON_CARD_UPDATED`` with the old and new text, so a lesson that
was restructured rather than corrected shows as a run of unrelated pairs: keys
are positions, and no code can tell a reorder from a correction.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from pathlib import Path

from app.audio.sentence_card_audio import attach_drill_card_audio, attach_sentence_card_audio
from app.common.guid import compute_guid
from app.models.lesson import Lesson
from app.srs.drill_card import DrillCard, drill_card
from app.srs.lesson_cards import COURSE_SOURCE, LessonCard, lesson_cards
from app.srs.sentence_card import SentenceCard, is_sentence_key, sentence_card, sibling_group

_log = logging.getLogger(__name__)


def _in_deck(db, card: LessonCard, language_code: str) -> bool:
    unit = card.unit
    if is_sentence_key(unit.disambig_key):
        return bool(db.get_collocation_ids_by_disambig_key(unit.disambig_key))
    # Every other lesson card is a word (``lesson_cards`` builds sentences, drills and words only).
    return (
        db.get_collocation_id_by_guid(compute_guid(unit.text, language_code, unit.disambig_key)) is not None
        or db.get_collocation_by_lemma_with_id(unit.lemma) is not None
        or db.get_collocation(unit.text) is not None
    )


def mint_lesson_cards(
    db, language_code: str, cards: dict, drills: list[dict] | tuple = ()
) -> list[tuple[int, LessonCard]]:
    """Add the cards *cards* makes that the deck lacks, NEW and in list order; return ``(id, card)`` per row added.

    Cards already in the deck that the lesson has corrected are updated first
    (:func:`_update_lesson_rows`); a line it holds mints nothing new either.
    *drills* are the story's own, which a ``drill`` item names (tunatale-5i1p.4).
    """
    built = lesson_cards(cards, drills)
    held = _update_lesson_rows(db, built)
    minted: list[tuple[int, LessonCard]] = []
    for card in built:
        if sibling_group(card.unit) in held or _in_deck(db, card, language_code):
            continue
        db.add_collocation(card.unit, language_code=language_code)
        cid = db.get_collocation_id_by_guid(compute_guid(card.unit.text, language_code, card.unit.disambig_key))
        minted.append((cid, card))
    if minted:
        _log.info("LESSON_CARDS_MINTED set=%s count=%d", cards["set"], len(minted))
    return minted


def lesson_card_rows(db, cards: dict, drills: list[dict] | tuple = ()) -> list[tuple[int, LessonCard]]:
    """``(id, card)`` for every card of the list the deck holds under its key: the rows the lesson's takes go to.

    Not only the rows a mint added: a card minted before its lesson was
    rendered, or whose take an update dropped, gets its sound from the next
    attach (``attach_lesson_card_audio`` skips a kind a row already has).
    """
    rows: list[tuple[int, LessonCard]] = []
    for card in lesson_cards(cards, drills):
        key = card.unit.disambig_key
        ids = db.get_collocation_ids_by_disambig_key(key) if is_sentence_key(key) else []
        if len(ids) == 1:
            rows.append((ids[0], card))
    return rows


# --------------------------------------------------------------------------
# Updating the cards a lesson corrected (tunatale-adxy)
# --------------------------------------------------------------------------

_ANSWER_TAKE, _CUE_TAKE = "audio_tts_sentence", "audio_cue"


@dataclass(frozen=True)
class _Old:
    row_id: int
    item: object  # SRSItem
    card: SentenceCard

    @property
    def mined(self) -> bool:
        """A blank the learner added on the Lesson page: no card list names it."""
        return self.item.syntactic_unit.source != COURSE_SOURCE


def _hold(line: str, reason: str) -> None:
    _log.warning("LESSON_LINE_UPDATE_HELD line=%s reason=%s — nothing written for this line", line, reason)


def _update_lesson_rows(db, built: list[LessonCard]) -> set[str]:
    """Update every card of *built* the deck holds and the lesson changed; return the lines held."""
    held: set[str] = set()
    lines: dict[str, list[SentenceCard]] = {}
    for card in built:
        sentence = sentence_card(card.unit)
        if sentence is not None:
            lines.setdefault(sentence.line_key, []).append(sentence)
            continue
        drill = drill_card(card.unit)
        if drill is not None and not _update_drill(db, drill):
            held.add(drill.key)
    for line, new in lines.items():
        if not _update_line(db, line, new):
            held.add(line)
    return held


def _update_drill(db, new: DrillCard) -> bool:
    """Update the one row under *new*'s key if the lesson changed the drill; False when it is held.

    A drill whose clip texts changed loses those takes, and a drill that lost
    steps loses theirs; the next attach fills what the lesson has. A linked
    drill note is not flagged: the rotation rewrites a note whose fields differ
    from what its step calls for.
    """
    ids = db.get_collocation_ids_by_disambig_key(new.key)
    if new.step is not None and new.step == len(new.steps) - 1:
        _log_orphans(db, new)
    if not ids:
        return True
    if len(ids) > 1:
        _hold(new.key, f"{len(ids)} rows under one drill key {ids}")
        return False
    _, item, _ = db.get_collocation_by_id(ids[0])
    old = drill_card(item.syntactic_unit)
    unit = replace(new.unit(), source=item.syntactic_unit.source)
    if old is None:
        _hold(new.key, "the row's card_data is not a drill card")
        return False
    if item.syntactic_unit.card_data == unit.card_data:
        return True
    kept = dict(new.media_kinds())
    dropped = {kind for kind, text in old.media_kinds() if kept.get(kind) != text}
    db.rewrite_lesson_rows([(ids[0], unit, False)], {ids[0]: dropped} if dropped else {})
    _log.info("LESSON_CARD_UPDATED key=%s old=%r new=%r", new.key, _drill_summary(old), _drill_summary(new))
    return True


def _drill_summary(card: DrillCard) -> str:
    return " / ".join([card.model, *(f"{s.cue} -> {s.answer}" for s in card.steps)]).strip(" /")


def _log_orphans(db, new: DrillCard) -> None:
    """A question-and-answer drill that lost steps from its end leaves their rows: logged, left untouched."""
    k = len(new.steps) + 1
    base = new.key.rsplit(":", 1)[0]
    while ids := db.get_collocation_ids_by_disambig_key(f"{base}:{k}"):
        _log.warning("LESSON_CARD_ORPHANED key=%s:%d rows=%s — the lesson no longer has this step", base, k, ids)
        k += 1


def _update_line(db, line: str, new: list[SentenceCard]) -> bool:
    """Update the rows of *line* to the cards *new* from the lesson, all of them or none; False when it is held."""
    rows = db.list_sibling_rows(line)
    in_line = {row_id for _, _, row_id in rows}
    for card in new:
        if any(i not in in_line for i in db.get_collocation_ids_by_disambig_key(card.key)):
            _hold(line, f"{card.key} is a legacy row (no sibling group)")
            return False
    if not rows:
        return True
    old = [_Old(row_id, item, card) for _, item, row_id in rows if (card := sentence_card(item.syntactic_unit))]
    if len(old) < len(rows):
        _hold(line, "a row of the line is not a sentence card")
        return False
    if len({o.item.anki_note_id is None for o in old}) > 1:
        _hold(line, "the line is partly linked to its note; sync first")
        return False
    pairs = _match(old, new)
    if pairs is None:
        _hold(line, "a card of the line has no place in the corrected text")
        return False
    changed = [
        (o, c, unit)
        for o, c in pairs
        if (unit := c.unit()).card_data != o.item.syntactic_unit.card_data or c.key != o.card.key
    ]
    if not changed:
        return True
    was, now = old[0].card, new[0]
    dropped = set()
    if was.spoken != now.spoken:
        dropped.add(_ANSWER_TAKE)
    if (was.cue.spoken if was.cue else None) != (now.cue.spoken if now.cue else None):
        dropped.add(_CUE_TAKE)
    linked = old[0].item.anki_note_id is not None
    db.rewrite_lesson_rows(
        [(o.row_id, unit, linked) for o, _, unit in changed],
        {o.row_id: dropped for o in old} if dropped else {},
    )
    for o, c, _ in changed:
        _log.info(
            "LESSON_CARD_UPDATED key=%s old=%r new=%r",
            c.key,
            f"{o.card.key} {o.card.cloze()} | {o.card.english}",
            f"{c.cloze()} | {c.english}",
        )
    return True


def _match(old: list[_Old], new: list[SentenceCard]) -> list[tuple[_Old, SentenceCard]] | None:
    """Pair each row of a line with its card in the corrected list (design A1); None when one is left over.

    1. the whole-line card to the whole-line row; a blank by its exact key;
    2. a blank by its blanked form at the same occurrence in the corrected text
       (the only rule for a mined blank, which keeps its own gloss);
    3. exactly one list blank left on each side: the corrected word was the blank.
    A card of the list with no row is minted after the update, as any new card.
    """
    base = replace(new[0], blank=None, blank_gloss="")
    pairs: list[tuple[_Old, SentenceCard]] = []
    left_old, left_new = list(old), list(new)

    def pair(o: _Old, c: SentenceCard) -> None:
        pairs.append((o, c))
        left_old.remove(o)
        if c in left_new:
            left_new.remove(c)

    for o in list(left_old):
        if o.card.blank is None:
            c = next((c for c in left_new if c.blank is None), None)
        else:
            c = next((c for c in left_new if c.key == o.card.key), None)
        if c is not None:
            pair(o, c)
    for o in list(left_old):
        span = _relocate(o.card, base.text) if o.card.blank is not None else None
        if span is None:
            continue
        if o.mined:
            pair(o, base.with_blank(span, o.card.blank_gloss))
            continue
        c = next((c for c in left_new if c.blank == span), None)
        if c is not None:
            pair(o, c)
    list_old = [o for o in left_old if not o.mined and o.card.blank is not None]
    list_new = [c for c in left_new if c.blank is not None]
    if len(left_old) == len(list_old) == len(list_new) == 1:
        pair(list_old[0], list_new[0])
    return None if left_old else pairs


def _relocate(card: SentenceCard, text: str) -> tuple[int, int] | None:
    """Where *card*'s blank sits in *text*: the same form, bounded as it was, at the same occurrence."""
    s, e = card.blank
    old = card.text
    left = r"(?<=\w)" if s > 0 and re.match(r"\w", old[s - 1]) else r"(?<!\w)"
    right = r"(?=\w)" if e < len(old) and re.match(r"\w", old[e]) else r"(?!\w)"
    pattern = re.compile(left + re.escape(old[s:e]) + right, re.IGNORECASE)
    starts = [m.start() for m in pattern.finditer(old)]
    if s not in starts:  # an earlier overlapping match hid the blank's own one ('xabababa', (3, 6)): hold the line
        return None
    occurrence = starts.index(s)
    found = list(pattern.finditer(text))
    return found[occurrence].span() if occurrence < len(found) else None


def attach_lesson_card_audio(
    db,
    minted: list[tuple[int, LessonCard]],
    lesson: Lesson,
    voice_map: dict[str, str],
    *,
    cache_dir: Path,
    media_dir: Path,
) -> None:
    """Give each lesson sentence card the lesson's own takes of its line and its cue, and each
    drill card the takes of every clip its steps play. *minted* is every row to
    voice (:func:`lesson_card_rows`); a kind a row already has is left as it is.

    The blanks the learner mined on a line are in no card list: each gets a copy
    of its line's takes, as ``sentence_mining.add_blank`` gives one at the tap.
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
        _copy_takes_to_mined(db, cid, sentence.line_key)


def _copy_takes_to_mined(db, source: int, line: str) -> None:
    """Give each mined blank of *line* the takes *source* has and it lacks, flagged ``audio`` for its note."""
    for _, item, row_id in db.list_sibling_rows(line):
        if item.syntactic_unit.source == COURSE_SOURCE:
            continue
        added = False
        for kind in (_ANSWER_TAKE, _CUE_TAKE):
            if db.has_media_row(source, kind) and not db.has_media_row(row_id, kind):
                db.copy_media_row(source, row_id, kind)
                added = True
        if added:
            db.add_dirty_field_by_id(row_id, "audio")


def day_was_studied(db, store, curriculum_id: str, day: int) -> bool:
    """Whether any lesson of *curriculum_id*'s *day* has a listen: an import's lesson is a new id."""
    for listened in db.get_listened_lessons():
        row = store.get_lesson_row(listened["lesson_id"])
        if row is not None and row["curriculum_id"] == curriculum_id and row["day"] == day:
            return True
    return False
