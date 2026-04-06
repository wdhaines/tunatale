"""SRS state and review endpoints."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

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
            translation="",
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
                "words": [{"surface": w.surface, "lemma": w.lemma, "srs_state": w.srs_state} for w in line.words],
            }
            for line in transcript.dialogue_lines
        ],
    }


@router.get("/stats", status_code=200)
async def get_stats(request: Request):
    db = request.app.state.srs_db
    today = datetime.date.today()
    return {"total": db.count_collocations(), "due_today": db.count_due_collocations(today)}
