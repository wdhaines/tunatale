"""Photographs shared by two or more cards: re-picture all but the most common word (tunatale-jlwy).

A production front shows the picture and asks for the word, so two words on one
photograph is a prompt with more than one right answer (see
``db_media.py::image_digest_owner``). The write paths stopped making new ones in
PR 289; this repairs the rows that were already there.

The user's rule (2026-10-07, confirmed that evening): "just re-picture the less
common word". In each group the word the language's frequency source ranks
highest keeps its photo, and every other card is re-fetched with the shared
picture barred. Frequency is ``app.languages.zipf_for`` — NOT
``collocations.corpus_frequency``, which is 0 on every row of every live deck.
A tie (both words missing from the table, or equal) keeps the OLDEST card's
photo, the description's original default, and the plan says it was a tie.

The re-fetch is the image pre-stage's own: the sync's media generator, asked
for a picture alone, and ``vocab_media.image_no_other_card_holds`` to refuse a
picture another card holds, bar its URL and ask once more. A card is changed
only when a new photo is in hand. One that cannot get one keeps the shared
photo and the result says why, so no card that had a picture is left without
one.

Like ``picture_redraw``, this rewrites the Image field of whatever note a row
points at, through the dirty flag and the ordinary push: no Anki file is opened.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

from app.cards.media import vocab_media
from app.cards.media.query_llm import IMAGE_QUERY_MODEL_VERSION
from app.languages import zipf_for


class SharedCard(NamedTuple):
    collocation_id: int
    text: str
    gloss: str
    filename: str
    zipf: float
    linked: bool  # has an Anki note, so the push must carry a new picture
    query_cached: bool  # its image query is cached, so a re-fetch costs no LLM call
    source_sentence: str = ""
    grammar: str = ""


class SharedGroup(NamedTuple):
    sha256: str
    keeper: SharedCard
    others: tuple[SharedCard, ...]
    tied: bool  # the keeper won on age, not frequency


class Repicture(NamedTuple):
    collocation_id: int
    text: str
    old_filename: str
    new_filename: str | None  # None: the card kept its old photo
    reason: str | None  # why it kept it; None when re-pictured


def plan_shared_photos(db: Any, language_code: str, *, zipf: Callable[[str], float] | None = None) -> list[SharedGroup]:
    """Every photograph two or more cards in *db* share, with the card that keeps it.

    *zipf* ranks the words; by default the language's own frequency source, and
    with none at all every group is a tie.
    """
    rank = zipf or zipf_for(language_code) or (lambda _word: 0.0)
    plan = []
    for sha, ids in db.list_shared_photo_groups():
        cards = []
        for coll_id in ids:
            _, item, _guid = db.get_collocation_by_id(coll_id)
            unit = item.syntactic_unit
            cards.append(
                SharedCard(
                    coll_id,
                    unit.text,
                    unit.translation,
                    db.get_image_filename(coll_id),
                    rank(unit.text.casefold()),
                    item.anki_note_id is not None,
                    db.get_image_query(unit.text, unit.translation, IMAGE_QUERY_MODEL_VERSION) is not None,
                    unit.source_sentence or "",
                    unit.grammar or "",
                )
            )
        # Highest zipf first; ids ascend within a zipf, so a tie keeps the oldest.
        ordered = sorted(cards, key=lambda c: -c.zipf)
        keeper = ordered[0]
        others = tuple(sorted(ordered[1:], key=lambda c: c.collocation_id))
        plan.append(SharedGroup(sha, keeper, others, ordered[1].zipf == keeper.zipf))
    return plan


async def apply_repictures(
    db: Any, plan: list[SharedGroup], media_fn: Callable[..., Awaitable[Any]]
) -> list[Repicture]:
    """Re-fetch a picture for every card the plan does not keep; store it only when it is new.

    *media_fn* is the sync's media generator (``api/anki.py::_build_media_fn``).
    One ``used_image_urls`` set spans the run, as in the pre-stage, and every
    store is visible to the next card's digest check, so two cards in one run
    cannot land on one new photo.
    """
    used_image_urls: set[str] = set()
    results = []
    for group in plan:
        for card in group.others:

            async def fetch(card: SharedCard = card) -> Any:
                return await media_fn(
                    card.text,
                    card.gloss,
                    used_image_urls=used_image_urls,
                    source_sentence=card.source_sentence,
                    grammar=card.grammar,
                    audio="none",
                )

            results.append(await _repicture(db, card, fetch, used_image_urls))
    return results


async def _repicture(
    db: Any, card: SharedCard, fetch: Callable[[], Awaitable[Any]], used_image_urls: set[str]
) -> Repicture:
    def kept(reason: str) -> Repicture:
        return Repicture(card.collocation_id, card.text, card.filename, None, reason)

    try:
        media = await fetch()
    except Exception as exc:  # noqa: BLE001 — one card's failure must not end the run
        return kept(f"fetch failed: {exc}")
    if media is None or media.image_bytes is None:
        return kept("no picture found")
    accepted, owner = await vocab_media.image_no_other_card_holds(
        db, card.collocation_id, media, used_image_urls=used_image_urls, refetch=fetch
    )
    if accepted is None:
        return kept(f"still a duplicate of card {owner}")
    digest = hashlib.sha256(accepted.image_bytes).hexdigest()
    filename = f"{vocab_media.safe_stem(card.gloss, 'img')}_{digest[:8]}.{accepted.image_ext or 'jpg'}"
    vocab_media._drop_image_rows(db, card.collocation_id, "image")
    vocab_media.store_tt_media(db, card.collocation_id, "image", filename, accepted.image_bytes)
    if card.linked:
        db.add_dirty_field_by_id(card.collocation_id, "image")
    return Repicture(card.collocation_id, card.text, card.filename, filename, None)
