"""SRS state and review endpoints."""

from __future__ import annotations

import datetime
import json
import logging
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.models.srs_item import SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.feedback import ImplicitFeedbackAdapter
from app.srs.fsrs import Rating, schedule
from app.srs.lemmatizer import LowercaseLemmatizer
from app.srs.tokenizer import tokenize
from app.srs.transcript import extract_transcript

router = APIRouter(prefix="/api/srs", tags=["srs"])

_feedback_adapter = ImplicitFeedbackAdapter()
_lemmatizer = LowercaseLemmatizer()

_WORD_RATING_MAP: dict[str, Rating] = {
    "again": Rating.AGAIN,
    "hard": Rating.HARD,
    "good": Rating.GOOD,
    "easy": Rating.EASY,
}


def _item_to_dict(row_id: int, item: SRSItem, language_code: str) -> dict:
    """Serialize an SRSItem to a response dict for admin endpoints."""
    return {
        "id": row_id,
        "text": item.syntactic_unit.text,
        "translation": item.syntactic_unit.translation,
        "state": item.state.value,
        "due_date": item.due_date.isoformat(),
        "stability": item.stability,
        "difficulty": item.difficulty,
        "reps": item.reps,
        "lapses": item.lapses,
        "last_review": item.last_review.isoformat() if item.last_review else None,
        "language_code": language_code,
    }


class FeedbackRequest(BaseModel):
    collocation_text: str
    signal: str  # no_help | slowdown | translation_request | fast_forward


class ListenRequest(BaseModel):
    lesson_id: str
    word_ratings: dict[str, str] = {}  # lemma → "hard"|"easy"|"again"


@router.get("/due", status_code=200)
async def get_due_collocations(request: Request):
    db = request.app.state.srs_db
    today = datetime.date.today()
    items = db.get_due_collocations(today)
    return {"due": [{"text": i.syntactic_unit.text, "translation": i.syntactic_unit.translation} for i in items]}


@router.get("/new", status_code=200)
async def get_new_collocations(request: Request, limit: int = 10):
    db = request.app.state.srs_db
    items = db.get_new_collocations(limit=limit)
    return {"new": [{"text": i.syntactic_unit.text, "translation": i.syntactic_unit.translation} for i in items]}


@router.post("/feedback", status_code=200)
async def record_feedback(body: FeedbackRequest, request: Request):
    db = request.app.state.srs_db

    item = db.get_collocation(body.collocation_text)
    if item is None:
        return {"status": "not_found"}

    rating = _feedback_adapter.signal_to_rating(body.signal)
    updated = schedule(item, rating)
    db.update_collocation(updated)
    return {"status": "ok", "new_due_date": str(updated.due_date)}


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
            updated = schedule(item, rating)
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


# ── Admin endpoints ────────────────────────────────────────────────────────────


class CreateItemRequest(BaseModel):
    text: str
    language_code: str
    word_count: int
    translation: str = ""


class UpdateItemRequest(BaseModel):
    text: str
    translation: str


class BulkDeleteRequest(BaseModel):
    ids: list[int]


class SuspendRequest(BaseModel):
    suspended: bool


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
    unit = SyntacticUnit(
        text=body.text,
        translation=body.translation,
        word_count=body.word_count,
        difficulty=1,
        source="user",
        lemma=body.text.lower() if body.word_count == 1 else None,
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
    db.set_suspended(item_id, body.suspended)
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return _item_to_dict(row_id, item, lang)
