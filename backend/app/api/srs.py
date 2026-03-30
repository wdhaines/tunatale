"""SRS state and review endpoints."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.srs.feedback import ImplicitFeedbackAdapter
from app.srs.fsrs import schedule

router = APIRouter(prefix="/api/srs", tags=["srs"])

_feedback_adapter = ImplicitFeedbackAdapter()


class FeedbackRequest(BaseModel):
    collocation_text: str
    signal: str  # no_help | slowdown | translation_request | fast_forward


@router.get("/due", status_code=200)
async def get_due_collocations(request: Request):
    db = request.app.state.srs_db
    today = datetime.date.today()
    items = db.get_due_collocations(today)
    return {"due": [{"text": i.syntactic_unit.text, "translation": i.syntactic_unit.translation} for i in items]}


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


@router.get("/stats", status_code=200)
async def get_stats(request: Request):
    db = request.app.state.srs_db
    today = datetime.date.today()
    return {"total": db.count_collocations(), "due_today": db.count_due_collocations(today)}
