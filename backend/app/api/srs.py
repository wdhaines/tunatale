"""SRS state and review endpoints."""

from __future__ import annotations

import datetime
import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.llm.translate import translate_term
from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.feedback import rating_from_input
from app.srs.fsrs import Rating, schedule
from app.srs.lemmatizer import LowercaseLemmatizer
from app.srs.queue_stats import (
    count_anki_introduced_today,
    count_anki_review_remaining_today,
    resolve_bury_new,
    resolve_bury_review,
    resolve_daily_new_cap,
    resolve_fsrs_params,
    resolve_new_spread,
)
from app.srs.tokenizer import tokenize
from app.srs.transcript import extract_transcript

router = APIRouter(prefix="/api/srs", tags=["srs"])
_MEDIA_DIR = Path(__file__).parent.parent.parent / "media"

_lemmatizer = LowercaseLemmatizer()

_WORD_RATING_MAP: dict[str, Rating] = {
    "again": Rating.AGAIN,
    "hard": Rating.HARD,
    "good": Rating.GOOD,
    "easy": Rating.EASY,
}


def _direction_to_dict(ds: DirectionState) -> dict:
    result = {
        "state": ds.state.value,
        "due_date": ds.due_date.isoformat(),
        "stability": ds.stability,
        "difficulty": ds.difficulty,
        "reps": ds.reps,
        "lapses": ds.lapses,
        "last_review": ds.last_review.isoformat() if ds.last_review else None,
        "last_review_time_ms": ds.last_review_time_ms,
        "anki_card_id": ds.anki_card_id,
    }
    if ds.due_at is not None:
        result["due_at"] = ds.due_at.isoformat()
    if ds.left is not None:
        result["left"] = ds.left
    return result


def _item_to_dict(
    row_id: int,
    item: SRSItem,
    language_code: str,
    image_url: str | None = None,
    audio_url: str | None = None,
) -> dict:
    """Serialize an SRSItem to a response dict."""
    return {
        "id": row_id,
        "text": item.syntactic_unit.text,
        "translation": item.syntactic_unit.translation,
        "word_count": item.syntactic_unit.word_count,
        # Flat recognition shims (back-compat)
        "state": item.state.value,
        "due_date": item.due_date.isoformat(),
        "stability": item.stability,
        "difficulty": item.difficulty,
        "reps": item.reps,
        "lapses": item.lapses,
        "last_review": item.last_review.isoformat() if item.last_review else None,
        "language_code": language_code,
        "guid": item.guid,
        "anki_note_id": item.anki_note_id,
        "directions": {
            "recognition": _direction_to_dict(item.directions[Direction.RECOGNITION]),
            "production": _direction_to_dict(item.directions[Direction.PRODUCTION]),
        },
        "image_url": image_url,
        "audio_url": audio_url,
        "grammar": item.syntactic_unit.grammar,
        "note": item.syntactic_unit.note,
    }


class ListenRequest(BaseModel):
    lesson_id: str
    word_ratings: dict[str, str] = {}  # lemma → "hard"|"easy"|"again"


def _triples_to_dicts(db, triples: list[tuple[int, SRSItem, str]]) -> list[dict]:
    result = []
    seen_ids: set[int] = set()
    for row_id, item, lang in triples:
        if row_id in seen_ids:
            continue
        seen_ids.add(row_id)
        img = db.get_image_filename(row_id)
        image_url = f"/api/srs/media/{img}" if img else None
        aud = db.get_audio_filename(row_id)
        audio_url = f"/api/srs/media/{aud}" if aud else None
        result.append(_item_to_dict(row_id, item, lang, image_url, audio_url))
    return result


@router.get("/due", status_code=200)
async def get_due_collocations(request: Request, direction: str = "recognition"):
    db = request.app.state.srs_db
    today = datetime.date.today()
    if direction == "any":
        rec = db.get_due_items(today, Direction.RECOGNITION)
        prod = db.get_due_items(today, Direction.PRODUCTION)
        triples = rec + prod
    else:
        try:
            dir_enum = Direction(direction)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid direction: {direction!r}") from exc
        triples = db.get_due_items(today, dir_enum)
    return {"due": _triples_to_dicts(db, triples)}


@router.get("/new", status_code=200)
async def get_new_collocations(request: Request, limit: int = 10, direction: str = "recognition"):
    db = request.app.state.srs_db
    try:
        dir_enum = Direction(direction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid direction: {direction!r}") from exc
    triples = db.get_new_items(limit=limit, direction=dir_enum)
    return {"new": _triples_to_dicts(db, triples)}


class DrillRequest(BaseModel):
    rating: str | None = None
    signal: str | None = None
    time_ms: int = 0


@router.post("/items/{item_id}/direction/{direction}/feedback", status_code=200)
async def drill_feedback(item_id: int, direction: str, body: DrillRequest, request: Request):
    try:
        dir_enum = Direction(direction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid direction: {direction!r}") from exc

    try:
        rating = rating_from_input(rating=body.rating, signal=body.signal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db = request.app.state.srs_db
    result = db.get_collocation_by_id(item_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Item not found")
    _, item, _ = result

    fsrs_params, _ = resolve_fsrs_params(db)
    now = datetime.datetime.now(datetime.UTC)
    updated = schedule(item, rating, direction=dir_enum, params=fsrs_params, time_ms=body.time_ms, now=now)
    db.update_direction_by_id(item_id, dir_enum, updated.directions[dir_enum])

    new_dir = updated.directions[dir_enum]
    response = {
        "status": "ok",
        "direction": dir_enum.value,
        "new_due_date": new_dir.due_date.isoformat(),
        "new_state": new_dir.state.value,
    }
    if new_dir.left is not None:
        response["left"] = new_dir.left
    if new_dir.due_at is not None:
        response["due_at"] = new_dir.due_at.isoformat()
    return response


@router.get("/media/{filename}", status_code=200)
async def serve_media(filename: str, request: Request):
    media_dir = _MEDIA_DIR
    file_path = (media_dir / filename).resolve()
    if not str(file_path).startswith(str(media_dir.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(file_path)


@router.post("/listen", status_code=200)
async def mark_lesson_listened(body: ListenRequest, request: Request):
    store = request.app.state.content_store
    lesson = store.get_lesson(body.lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    db = request.app.state.srs_db

    # ── Word-level tracking from NATURAL_SPEED section ──────────────────
    from app.models.lesson import SectionType

    token_glosses: dict[str, str] = lesson.generation_metadata.get("token_glosses", {})

    natural_speed = next(
        (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
        None,
    )

    unique_lemmas: set[str] = set()
    if natural_speed is not None:
        for phrase in natural_speed.phrases:
            if phrase.language_code != lesson.language_code:
                continue
            for surface in tokenize(phrase.text):
                lemma = _lemmatizer.lemmatize(surface, lesson.language_code)
                unique_lemmas.add(lemma)

    for lemma in unique_lemmas:
        unit = SyntacticUnit(
            text=lemma,
            translation=token_glosses.get(lemma, ""),
            word_count=1,
            difficulty=1,
            source="llm",
            lemma=lemma,
        )
        db.add_collocation(unit, language_code=lesson.language_code)
        item = db.get_collocation_by_lemma(lemma)
        if item is not None:
            rating = _WORD_RATING_MAP.get(body.word_ratings.get(lemma, "good"), Rating.GOOD)
            now = datetime.datetime.now(datetime.UTC)
            updated = schedule(item, rating, params=resolve_fsrs_params(db)[0], now=now)
            db.update_collocation(updated)

    # ── Key phrase registration (preserves translations) ─────────────────
    for kp in lesson.key_phrases:
        unit = SyntacticUnit(
            text=kp.phrase,
            translation=kp.translation,
            word_count=min(8, max(1, len(kp.phrase.split()))),
            difficulty=1,
            source="llm",
        )
        db.add_collocation(unit, language_code=lesson.language_code)

    registered = len(unique_lemmas) + len(lesson.key_phrases)
    return {"status": "ok", "registered": registered}


@router.get("/lesson/{lesson_id}/transcript", status_code=200)
async def get_lesson_transcript(lesson_id: str, request: Request):
    store = request.app.state.content_store
    lesson = store.get_lesson(lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    db = request.app.state.srs_db
    transcript = extract_transcript(lesson, db, _lemmatizer)

    return {
        "lesson_id": lesson_id,
        "key_phrases": [{"phrase": kp.phrase, "translation": kp.translation} for kp in transcript.key_phrases],
        "dialogue_lines": [
            {
                "role": line.role,
                "words": [
                    {
                        "surface": w.surface,
                        "lemma": w.lemma,
                        "srs_state": w.srs_state,
                        "srs_item_id": w.srs_item_id,
                        "translation": w.translation,
                        "collocation_span_id": w.collocation_span_id,
                        "collocation_start": w.collocation_start,
                        "collocation_srs_state": w.collocation_srs_state,
                        "collocation_lemma": w.collocation_lemma,
                        "collocation_translation": w.collocation_translation,
                    }
                    for w in line.words
                ],
            }
            for line in transcript.dialogue_lines
        ],
    }


_TRANSLATE_BATCH_SIZE = 50
_TRANSLATE_SYSTEM = "You are a translation assistant. Return ONLY valid JSON, no other text."
_logger = logging.getLogger(__name__)


def _build_translate_prompt(words: list[str], language_name: str) -> str:
    word_list = "\n".join(f"- {w}" for w in words)
    return (
        f"Translate these {language_name} words/phrases to concise English.\n"
        f'Return a JSON object mapping each to its translation: {{"word": "translation", ...}}\n\n'
        f"Words:\n{word_list}"
    )


@router.post("/translate-missing", status_code=200)
async def translate_missing(request: Request):
    """Call the LLM to fill in translations for every card that has none."""
    db = request.app.state.srs_db
    llm = request.app.state.llm
    language = request.app.state.language

    untranslated = db.get_untranslated_collocations()
    if not untranslated:
        return {"translated": 0, "skipped": 0}

    translated = 0
    skipped = 0
    words = [text for text, _ in untranslated]

    for i in range(0, len(words), _TRANSLATE_BATCH_SIZE):
        batch = words[i : i + _TRANSLATE_BATCH_SIZE]
        try:
            prompt = _build_translate_prompt(batch, language.name)
            raw = await llm.complete(prompt, system_prompt=_TRANSLATE_SYSTEM, temperature=0.1, max_tokens=2048)
            raw = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip())
            raw = re.sub(r"\n?```\s*$", "", raw)
            glosses = json.loads(raw.strip())
        except Exception:
            _logger.warning("translate-missing: batch %d–%d failed", i, i + len(batch))
            skipped += len(batch)
            continue
        translated += db.backfill_translations(glosses)

    return {"translated": translated, "skipped": skipped}


@router.post("/backfill-translations", status_code=200)
async def backfill_translations(request: Request):
    """One-time repair: fill empty translations from all stored lesson glosses."""
    store = request.app.state.content_store
    db = request.app.state.srs_db
    glosses = store.get_all_token_glosses()
    updated = db.backfill_translations(glosses)
    return {"updated": updated, "glosses_found": len(glosses)}


@router.get("/stats", status_code=200)
async def get_stats(request: Request):
    db = request.app.state.srs_db
    today = datetime.date.today()
    return {"total": db.count_collocations(), "due_today": db.count_due_collocations(today)}


@router.get("/queue-stats", status_code=200)
async def get_queue_stats(request: Request):
    db = request.app.state.srs_db
    today = datetime.date.today()
    cap, source = resolve_daily_new_cap(db)
    _, fsrs_source = resolve_fsrs_params(db)
    # Source of truth for "new today" is Anki's revlog: count cards whose first
    # revlog entry is on or after today. TT's mirrored `reps` is unreliable
    # here because dual-grading bumps reps past 1 before we can detect.
    introduced_today = count_anki_introduced_today(today)
    remaining_quota = max(0, cap - introduced_today)
    # Prefer Anki's deck-overview review count (mirrors sibling burying +
    # RemainingLimits cap). Fall back to TT mirror when Anki is unavailable.
    anki_review = count_anki_review_remaining_today()
    review_count = anki_review if anki_review is not None else db.count_review_due(today)
    return {
        "new": min(remaining_quota, db.count_new_available()),
        "learning": db.count_learning(),
        "review": review_count,
        "daily_new_cap": cap,
        "cap_source": source,
        "fsrs_source": fsrs_source,
    }


# ── Admin endpoints ────────────────────────────────────────────────────────────


class CreateItemRequest(BaseModel):
    text: str
    language_code: str
    word_count: int
    translation: str = ""
    source_sentence: str = ""
    source_lesson_id: str | None = None
    source_line_index: int | None = None


class UpdateItemRequest(BaseModel):
    text: str
    translation: str


class BulkDeleteRequest(BaseModel):
    ids: list[int]


class SuspendRequest(BaseModel):
    suspended: bool
    direction: str | None = None


class SetStateRequest(BaseModel):
    state: str  # "new" | "learning" | "known" | "ignored"


_VALID_USER_STATES = {"new", "learning", "known", "ignored"}
_STATE_MAP = {
    "new": SRSState.NEW,
    "learning": SRSState.LEARNING,
    "known": SRSState.KNOWN,
    "ignored": SRSState.SUSPENDED,
}


@router.post("/items", status_code=201)
async def create_item(body: CreateItemRequest, request: Request):
    db = request.app.state.srs_db
    if body.word_count < 1:
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(status_code=422, detail="word_count must be >= 1")

    # LLM auto-translate if translation is empty
    translation = body.translation
    if translation == "":
        llm_client = getattr(request.app.state, "llm", None)
        if llm_client is not None:
            translation = await translate_term(llm_client, body.text, body.language_code)

    unit = SyntacticUnit(
        text=body.text,
        translation=translation,
        word_count=body.word_count,
        difficulty=1,
        source="user",
        lemma=body.text.lower() if body.word_count == 1 else None,
        source_sentence=body.source_sentence,
        source_lesson_id=body.source_lesson_id,
        source_line_index=body.source_line_index,
    )
    existing = db.get_collocation(body.text)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Item already exists: {body.text!r}")
    db.add_collocation(unit, language_code=body.language_code)
    rows, _ = db.list_collocations(search=body.text, limit=1)
    if not rows:
        raise HTTPException(status_code=500, detail="Failed to retrieve created item")
    row_id, item, lang = rows[0]
    return _item_to_dict(row_id, item, lang)


@router.get("/items", status_code=200)
async def list_items(
    request: Request,
    search: str | None = None,
    state: str | None = None,
    sort: str = "text",
    order: str = "asc",
    limit: int = 50,
    offset: int = 0,
):
    db = request.app.state.srs_db
    state_enum = SRSState(state) if state else None
    try:
        rows, total = db.list_collocations(
            limit=limit,
            offset=offset,
            search=search,
            state=state_enum,
            order_by=sort,
            order_dir=order,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": [_item_to_dict(rid, item, lang) for rid, item, lang in rows], "total": total}


@router.patch("/items/{item_id}", status_code=200)
async def patch_item(item_id: int, body: UpdateItemRequest, request: Request):
    db = request.app.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    try:
        db.update_collocation_fields(item_id, text=body.text, translation=body.translation)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return _item_to_dict(row_id, item, lang)


@router.delete("/items/{item_id}", status_code=200)
async def delete_item(item_id: int, request: Request):
    db = request.app.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete_collocation(item_id)
    return {"status": "deleted"}


@router.post("/items/bulk-delete", status_code=200)
async def bulk_delete_items(body: BulkDeleteRequest, request: Request):
    db = request.app.state.srs_db
    deleted = db.delete_collocations(body.ids)
    return {"deleted": deleted}


@router.post("/items/{item_id}/reset", status_code=200)
async def reset_item(item_id: int, request: Request):
    db = request.app.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    db.reset_collocation(item_id)
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return _item_to_dict(row_id, item, lang)


@router.post("/items/{item_id}/state", status_code=200)
async def set_item_state(item_id: int, body: SetStateRequest, request: Request):
    if body.state not in _VALID_USER_STATES:
        raise HTTPException(
            status_code=422, detail=f"Invalid state: {body.state!r}. Must be one of {sorted(_VALID_USER_STATES)}"
        )
    db = request.app.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    db.set_state_by_id(item_id, _STATE_MAP[body.state])
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return _item_to_dict(row_id, item, lang)


@router.post("/items/{item_id}/suspend", status_code=200)
async def suspend_item(item_id: int, body: SuspendRequest, request: Request):
    db = request.app.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    dir_enum: Direction | None = None
    if body.direction is not None:
        try:
            dir_enum = Direction(body.direction)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid direction: {body.direction!r}") from exc
    db.set_suspended(item_id, body.suspended, direction=dir_enum)
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return _item_to_dict(row_id, item, lang)


_FNV_OFFSET_BASIS_64 = 0xCBF29CE484222325
_FNV_PRIME_64 = 0x100000001B3


def _fnv1a_64_i64(*args: int) -> int:
    """Compute Anki's `fnvhash(args...)` over the i64 little-endian bytes.

    Mirrors rslib/src/storage/sqlite.rs:add_fnvhash_function — FNV-1a 64-bit
    hash, fed each i64 argument as 8 little-endian bytes via `write_i64`.
    Returned as a Python int in the signed-i64 range so direct comparison
    matches SQLite's `ORDER BY fnvhash(...)` ordering.
    """
    h = _FNV_OFFSET_BASIS_64
    for a in args:
        for byte in (a & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little"):
            h ^= byte
            h = (h * _FNV_PRIME_64) & 0xFFFFFFFFFFFFFFFF
    return h - (1 << 64) if h >= (1 << 63) else h


def _merge_by_retrievability_ascending(
    rec: list[tuple[int, SRSItem, str]],
    prod: list[tuple[int, SRSItem, str]],
    today: datetime.date,
) -> list[tuple[int, SRSItem, str, Direction]]:
    """Sort the combined due pool by retrievability ascending.

    Mirrors Anki's SortOrder::RetrievabilityAscending: every card with
    due_date <= today competes in one flat pool, ordered by R alone. An overdue
    but well-remembered card sits behind a today-due card the user is about to
    forget. Tie-break matches Anki exactly: `fnvhash(anki_card_id, anki_card_mod)`
    appended after the primary sort (rslib/src/storage/card/mod.rs:897). When
    either field is missing, fall back to anki_card_id then row_id so the order
    stays deterministic but no longer claims Anki parity.
    """
    from app.srs.fsrs import compute_retrievability

    combined: list[tuple[int, SRSItem, str, Direction]] = [
        (row_id, item, lang, Direction.RECOGNITION) for row_id, item, lang in rec
    ]
    combined.extend((row_id, item, lang, Direction.PRODUCTION) for row_id, item, lang in prod)

    def _key(t: tuple[int, SRSItem, str, Direction]) -> tuple:
        row_id, item, _, direction = t
        dstate = item.directions[direction]
        r = compute_retrievability(dstate, today)
        if dstate.anki_card_id is not None and dstate.anki_card_mod is not None:
            return (r, 0, _fnv1a_64_i64(dstate.anki_card_id, dstate.anki_card_mod), 0)
        # Fallback for rows that haven't been synced from Anki yet.
        return (r, 1, dstate.anki_card_id or 0, row_id)

    combined.sort(key=_key)
    return combined


def _merge_by_anki_due_then_id(
    rec: list[tuple[int, SRSItem, str]],
    prod: list[tuple[int, SRSItem, str]],
) -> list[tuple[int, SRSItem, str, Direction]]:
    """Combine and sort by (anki_due ASC NULLS LAST, anki_card_id ASC NULLS LAST, row_id)."""
    combined: list[tuple[int, SRSItem, str, Direction]] = []
    for row_id, item, lang in rec:
        combined.append((row_id, item, lang, Direction.RECOGNITION))
    for row_id, item, lang in prod:
        combined.append((row_id, item, lang, Direction.PRODUCTION))
    combined.sort(
        key=lambda t: (
            t[1].directions[t[3]].anki_due is None,
            t[1].directions[t[3]].anki_due or 0,
            t[1].directions[t[3]].anki_card_id is None,
            t[1].directions[t[3]].anki_card_id or 0,
            t[0],
        ),
    )
    return combined


def _spread_mix(
    reviews: list[tuple[int, SRSItem, str, Direction]],
    news: list[tuple[int, SRSItem, str, Direction]],
) -> list[tuple[int, SRSItem, str, Direction]]:
    """Interleave news into reviews matching Anki's Intersperser exactly.

    Port of rslib/src/scheduler/queue/builder/intersperser.rs. Uses the
    continuous ratio (one_len + 1) / (two_len + 1) so the first item comes from
    the longer iter when populations are imbalanced, and items are distributed
    evenly between the start and end. For 10 reviews + 2 news the first new
    appears at position 3, not position 5 like a floor-ratio approach.
    """
    if not news:
        return list(reviews)
    if not reviews:
        return list(news)
    one_len = len(reviews)
    two_len = len(news)
    ratio = (one_len + 1) / (two_len + 1)
    one_idx = 0
    two_idx = 0
    result: list[tuple[int, SRSItem, str, Direction]] = []
    while one_idx < one_len or two_idx < two_len:
        if one_idx < one_len and two_idx < two_len:
            relative_idx2 = (two_idx + 1) * ratio
            if relative_idx2 < (one_idx + 1):
                result.append(news[two_idx])
                two_idx += 1
            else:
                result.append(reviews[one_idx])
                one_idx += 1
        elif one_idx < one_len:
            result.append(reviews[one_idx])
            one_idx += 1
        else:
            result.append(news[two_idx])
            two_idx += 1
    return result


def _queue_item_to_dict(row_id: int, item: SRSItem, lang: str, direction: Direction, db) -> dict:
    img = db.get_image_filename(row_id)
    image_url = f"/api/srs/media/{img}" if img else None
    aud = db.get_audio_filename(row_id)
    audio_url = f"/api/srs/media/{aud}" if aud else None
    base = _item_to_dict(row_id, item, lang, image_url, audio_url)
    base["direction"] = direction.value
    # Override flat state with per-direction state
    base["state"] = item.directions[direction].state.value
    return base


@router.get("/review-queue", status_code=200)
async def get_review_queue(request: Request) -> dict:
    """Return the entire ordered review queue in one shot.

    Implements Anki's queue construction: combined new-card cap across directions,
    sibling burying, and newSpread ordering.
    """
    db = request.app.state.srs_db
    today = datetime.date.today()
    now = datetime.datetime.now(datetime.UTC)

    cap, _ = resolve_daily_new_cap(db)
    spread, _ = resolve_new_spread(db)
    bury_new, _ = resolve_bury_new(db)
    bury_review, _ = resolve_bury_review(db)

    buried = db.list_collocations_reviewed_today(today)

    # 1. Due review cards: recognition + production pooled and sorted by retrievability ASC
    due_rec = db.get_due_items(today, Direction.RECOGNITION)
    due_prod = db.get_due_items(today, Direction.PRODUCTION)
    due = _merge_by_retrievability_ascending(due_rec, due_prod, today)

    if bury_review:
        due = [t for t in due if t[0] not in buried]

    # 2. New cards: respect cap across BOTH directions, sibling-buried, ordered by anki_due then anki_card_id
    new_rec = db.get_new_items(direction=Direction.RECOGNITION, limit=cap)
    new_prod = db.get_new_items(direction=Direction.PRODUCTION, limit=cap)
    new_combined = _merge_by_anki_due_then_id(new_rec, new_prod)
    if bury_new:
        new_combined = [t for t in new_combined if t[0] not in buried]
    new_combined = new_combined[:cap]

    # 3. Extract learning-state cards (Anki queue=1 behavior: they go first).
    #    Also remove any new cards whose collocation has a learning direction.
    #    Filter by due_at: only include cards with due_at <= now (or None for legacy).
    # Anki parity for queue=1: pull learning cards via a dedicated query that
    # ignores due_date. The daily-bucket filter (`due_date <= today`) is correct
    # for REVIEW cards but excludes any LEARNING card whose 10-minute step
    # crossed UTC midnight (so due_date=tomorrow even though the user is still
    # on today). It also bypasses the bury_review filter — Anki's queue=1
    # dispatcher does not honour sibling-bury within the same day.
    learning_rec = db.get_learning_items(direction=Direction.RECOGNITION)
    learning_prod = db.get_learning_items(direction=Direction.PRODUCTION)
    learning_cards: list[tuple[int, SRSItem, str, Direction]] = [
        (row_id, item, lang, Direction.RECOGNITION) for row_id, item, lang in learning_rec
    ]
    learning_cards.extend((row_id, item, lang, Direction.PRODUCTION) for row_id, item, lang in learning_prod)

    # The non-learning pool is whatever the daily due-date query returned, minus
    # any rows whose state somehow resolved to LEARNING (defensive — get_due_items
    # already excludes 'buried'/'suspended').
    nonlearning_due = [t for t in due if t[1].directions[t[3]].state not in (SRSState.LEARNING, SRSState.RELEARNING)]

    learning_collocation_ids = {t[0] for t in learning_cards}
    nonlearning_new = [t for t in new_combined if t[0] not in learning_collocation_ids]

    # Anki-parity proactive sibling bury (rslib/.../queue/builder/gathering.rs).
    # As Anki gathers cards in priority order — intraday learning, then due
    # reviews (retrievability-asc), then new — it tracks the note id of every
    # card it adds. A later card whose note is already in the queue gets buried
    # if the relevant flag is on. We mirror it on collocation_id (TT's note
    # equivalent). Distinct from `buried = list_collocations_reviewed_today` and
    # `count_anki_review_remaining_today`'s SQL `COUNT(DISTINCT nid)` — those
    # answer different questions (past actions / count badge) at different layers.
    seen_collocation_ids: set[int] = set(learning_collocation_ids)

    def _bury_siblings_in_queue(
        cards: list[tuple[int, SRSItem, str, Direction]],
        bury_when_seen: bool,
    ) -> list[tuple[int, SRSItem, str, Direction]]:
        survivors: list[tuple[int, SRSItem, str, Direction]] = []
        for t in cards:
            if t[0] in seen_collocation_ids and bury_when_seen:
                continue
            seen_collocation_ids.add(t[0])
            survivors.append(t)
        return survivors

    nonlearning_due = _bury_siblings_in_queue(nonlearning_due, bury_review)
    nonlearning_new = _bury_siblings_in_queue(nonlearning_new, bury_new)

    # Sort learning cards by TT's `due_at` (authoritative after a fresh grade,
    # before sync has refreshed Anki's `anki_due`). Fall back to anki_due, then
    # stability, then anki_card_id, then row id for stable order.
    _SENTINEL_FUTURE = datetime.datetime.max.replace(tzinfo=datetime.UTC)
    learning_cards.sort(
        key=lambda t: (
            t[1].directions[t[3]].due_at is None,
            t[1].directions[t[3]].due_at or _SENTINEL_FUTURE,
            t[1].directions[t[3]].anki_due is None,
            t[1].directions[t[3]].anki_due or 0,
            t[1].directions[t[3]].stability,
            t[1].directions[t[3]].anki_card_id is None,
            t[1].directions[t[3]].anki_card_id or 0,
            t[0],
        ),
    )

    # Split learning into ready (past-due / null due_at) vs pending (future).
    # Anki parity: while a learning card's step timer is still ticking, the
    # dispatcher serves review cards instead of waking the user up early.
    ready_learning: list[tuple[int, SRSItem, str, Direction]] = []
    pending_learning: list[tuple[int, SRSItem, str, Direction]] = []
    for t in learning_cards:
        ds = t[1].directions[t[3]]
        if ds.due_at is None or ds.due_at <= now:
            ready_learning.append(t)
        else:
            pending_learning.append(t)

    # 4. Apply newSpread to nonlearning cards only
    if spread == 1:  # new_after_review
        ordered = nonlearning_due + nonlearning_new
    elif spread == 2:  # new_before_review
        ordered = nonlearning_new + nonlearning_due
    else:  # 0 = mix: interleave one new every N reviews
        ordered = _spread_mix(nonlearning_due, nonlearning_new)

    # 5. Ready learning first (Anki queue=1 priority), then reviews/new,
    #    then pending learning (cards waiting on their step timer).
    ordered = ready_learning + ordered + pending_learning

    return {"queue": [_queue_item_to_dict(*t, db) for t in ordered]}
