"""Redo the word audio of cards that were given the wrong one (tunatale-r8hk).

Until 2026-10-06 the sync's media generator read the global default language,
so on a multi-language instance every card a sync minted was looked up in the
DEFAULT language's Forvo section and voiced by the default language's card
voice. A Cebuano deck seeded from a base list ended up with 12 Norwegian Forvo
recordings and 690 clips in a Norwegian voice. The code fix stops new damage;
nothing heals a card already minted, because a card's audio is fixed once stored.

This drops the audio of the cards named by the caller and gives back what is
free straight away — a Forvo recording, in the right language. It flags the
``audio`` field, so the next sync writes the new recording (or clears the wrong
one) into the Anki note through the ordinary push. TTS for whatever Forvo does
not have is ``prestage_card_audio``'s job: cards already met first, the rest as
they come up, so an unseen card costs no metered render.

Nothing here opens an Anki file, and no media file is deleted (the media dir is
shared by DBs this call cannot see — tunatale-ja9q).

A second run must be free (tunatale-2f5a). Nothing records that a card has been
redone except the name of its audio file: this repair and the audio pre-stage
are the only writers of ``<prefix>_<stem>_<sha8>.mp3``, so a card whose word
audio carries such a name of its own is marked ``repaired`` in the plan. The
script leaves those out unless told to force them; without that, running it
again dropped every clip the first run stored and rendered the same metered TTS
a second time.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from collections.abc import Collection
from typing import Any, NamedTuple

from .pipeline import MediaResult, fetch_card_media
from .vocab_media import safe_stem, store_tt_media

_WORD_AUDIO_KINDS = ("audio_forvo", "audio_tts")
_OUT_OF_ROTATION = {"new", "suspended", "known"}


class AudioRepair(NamedTuple):
    collocation_id: int
    text: str
    translation: str
    old_filename: str | None  # None: the card had no word audio to drop
    seen: bool  # a learner has met it, so it is first in line for a TTS render
    linked: bool  # has an Anki note, whose Audio field the next sync rewrites
    repaired: bool  # its word audio already has a content-hash name of its own: the repair or the pre-stage was here


class AudioRepairReport(NamedTuple):
    dropped: int = 0
    forvo: int = 0
    awaiting_tts_seen: int = 0
    awaiting_tts_unseen: int = 0


def _has_content_hash_name(filename: str | None, text: str, language_code: str) -> bool:
    """Whether *filename* is one the pre-stage or this repair wrote for this card.

    ``tts_<stem>_<sha8>.mp3`` for a render, ``<language>_<stem>_<sha8>.mp3`` for
    a Forvo recording, and nothing looser: a name that merely ends in eight hex
    letters (a legacy ``tts_kape_deadbeef.mp3``), or one from another language's
    Forvo section, is exactly the wrong audio this exists to redo.
    """
    if filename is None:
        return False
    return any(
        re.fullmatch(re.escape(safe_stem(text, prefix)) + r"_[0-9a-f]{8}\.mp3", filename)
        for prefix in ("tts", language_code)
    )


def plan_audio_repair(
    db: Any, *, sources: Collection[str], words: Collection[str] = (), language_code: str
) -> list[AudioRepair]:
    """Every vocab card whose ``source`` is in *sources* or whose text is in *words*.

    Named by the caller, not inferred: which cards carry the wrong audio is a
    fact about how they were minted, and nothing in a media row records it.
    Cards already redone are still listed, marked ``repaired``; leaving them
    out is the caller's decision.
    """
    rows, _ = db.list_collocations(limit=1_000_000)
    plan = []
    for coll_id, item, _guid in rows:
        unit = item.syntactic_unit
        if unit.card_type != "vocab" or not (unit.source in sources or unit.text in words):
            continue
        seen = any(d.state.value not in _OUT_OF_ROTATION for d in item.directions.values())
        old_filename = db.get_audio_filename(coll_id)
        repaired = _has_content_hash_name(old_filename, unit.text, language_code)
        plan.append(
            AudioRepair(
                coll_id,
                unit.text,
                unit.translation,
                old_filename,
                seen,
                item.anki_note_id is not None,
                repaired,
            )
        )
    return plan


async def apply_audio_repair(
    db: Any,
    plan: list[AudioRepair],
    language_code: str,
    *,
    fetch_fn: Any = None,
    delay: float = 0.0,
) -> AudioRepairReport:
    """Drop each planned card's word audio and store a Forvo recording if one exists.

    *delay* is seconds slept between Forvo requests — a sweep is hundreds of
    page loads from one address, against a site that blocks scrapers.
    """
    fetch = fetch_fn or fetch_card_media
    dropped = forvo = seen_left = unseen_left = 0
    for repair in plan:
        for kind in _WORD_AUDIO_KINDS:
            dropped += db.delete_all_media_for_kind(repair.collocation_id, kind) > 0
        # Flagged even when nothing replaces it yet: the note still plays the
        # wrong recording, and the push that clears it is the same push.
        db.add_dirty_field_by_id(repair.collocation_id, "audio")
        media = await fetch(
            repair.text,
            repair.translation,
            pixabay_key="",
            language_code=language_code,
            image_query="",
            audio="forvo",
        )
        if media is not None and media.audio_bytes is not None:
            digest = hashlib.sha256(media.audio_bytes).hexdigest()[:8]
            filename = f"{safe_stem(repair.text, language_code)}_{digest}.mp3"
            store_tt_media(db, repair.collocation_id, "audio_forvo", filename, media.audio_bytes)
            forvo += 1
        else:
            seen_left += repair.seen
            unseen_left += not repair.seen
        if delay:
            await asyncio.sleep(delay)
    return AudioRepairReport(dropped, forvo, seen_left, unseen_left)


class MemoFetch:
    """``fetch_card_media`` remembered per word, for a repair spanning several DBs.

    Two learners seeded from one list hold the same words. Without this the
    second DB repeats every Forvo request and — worse — renders every TTS clip
    again: a metered voice is not deterministic, so the second render would also
    be a second FILE under a second name.

    It also spares the Forvo request ``audio="full"`` would make for a word the
    sweep has already looked up.
    """

    def __init__(self, fetch_fn: Any = None) -> None:
        self._fetch = fetch_fn or fetch_card_media
        self._forvo: dict[str, MediaResult | None] = {}
        self._full: dict[str, MediaResult | None] = {}

    async def __call__(self, word: str, english: str, *, audio: str = "full", **kwargs: Any) -> MediaResult | None:
        if audio == "forvo":
            if word not in self._forvo:
                self._forvo[word] = await self._fetch(word, english, audio="forvo", **kwargs)
            return self._forvo[word]
        if word not in self._full:
            known = self._forvo.get(word)
            if known is not None and known.audio_bytes is not None:
                self._full[word] = known
            else:
                # Forvo already answered "nothing" for this word: go straight to TTS.
                looked_up = {"forvo_enabled": False} if word in self._forvo else {}
                self._full[word] = await self._fetch(word, english, audio=audio, **looked_up, **kwargs)
        return self._full[word]
