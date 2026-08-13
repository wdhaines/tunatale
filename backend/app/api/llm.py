"""LLM rate-limit visibility endpoints.

GET /api/llm/rate-limit — the Groq quota state captured passively from the most
recent call's response headers, plus TT's own day-budget ledger (tokens AND
requests/day; the daily ceilings have no header, so the ledger is the only
thing that knows them). Times are relative (age_s, *_reset_in_s, retry_in_s),
computed server-side so the frontend can count down without clock-skew issues.

POST /api/llm/rate-limit/probe — fire a 1-token Groq request purely to refresh
the headers (manual button or one-shot frontend auto-probe on first page open
per session; still never polled — a poll would burn the daily request quota
the endpoint exists to protect).
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

from app.api.models import LlmActivityResponse, LlmHealthResponse, RateLimitStatusResponse
from app.config import settings

router = APIRouter(prefix="/api/llm", tags=["llm"])


def _unwrap(request: Request):
    """The live Groq state lives on the real client, under any cassette wrapper."""
    llm = getattr(request.app.state, "llm", None)
    return getattr(llm, "_real_client", None) or llm


def _relative(anchor: float, offset_s: float | None, now: float) -> float | None:
    if offset_s is None:
        return None
    return max(0.0, round(anchor + offset_s - now, 1))


def _status_payload(client) -> dict:
    now = time.time()

    snapshot = getattr(client, "last_rate_limits", None)
    out_snapshot = None
    if snapshot:
        out_snapshot = {
            "age_s": round(now - snapshot["captured_at"], 1),
            "requests_limit": snapshot["requests_limit"],
            "requests_remaining": snapshot["requests_remaining"],
            "requests_reset_in_s": _relative(snapshot["captured_at"], snapshot["requests_reset_s"], now),
            "tokens_limit": snapshot["tokens_limit"],
            "tokens_remaining": snapshot["tokens_remaining"],
            "tokens_reset_in_s": _relative(snapshot["captured_at"], snapshot["tokens_reset_s"], now),
        }

    last_429 = getattr(client, "last_429", None)
    out_429 = None
    if last_429:
        out_429 = {
            "ago_s": round(now - last_429["at"], 1),
            "retry_in_s": _relative(last_429["at"], last_429["retry_after_s"], now),
        }

    ledger = getattr(client, "usage_ledger", None)
    if ledger is not None:
        budget = ledger.budget(
            tokens_limit=settings.groq_tokens_per_day_limit,
            requests_limit=settings.groq_requests_per_day_limit,
        )
        tokens_used_day = budget.tokens_used
        tokens_day_reset_in_s = budget.tokens_reset_in_s
        requests_used_day = budget.requests_used
        requests_day_reset_in_s = budget.requests_reset_in_s
    else:
        tokens_used_day = None
        tokens_day_reset_in_s = None
        requests_used_day = None
        requests_day_reset_in_s = None
    return {
        "provider": "groq",
        "model": getattr(client, "groq_model", None),
        "llm_mode": settings.llm_mode,
        "snapshot": out_snapshot,
        "last_429": out_429,
        "tokens_used_day": tokens_used_day,
        "tokens_per_day_limit": settings.groq_tokens_per_day_limit,
        "tokens_day_reset_in_s": tokens_day_reset_in_s,
        "requests_used_day": requests_used_day,
        "requests_per_day_limit": settings.groq_requests_per_day_limit,
        "requests_day_reset_in_s": requests_day_reset_in_s,
    }


@router.get("/health", response_model=LlmHealthResponse)
async def llm_health(request: Request) -> dict:
    client = _unwrap(request)
    if settings.llm_mode == "mock":
        return {
            "healthy": True,
            "consecutive_failures": 0,
            "last_error": None,
            "fallback_allowed": settings.llm_allow_fallback,
            "llm_mode": settings.llm_mode,
        }
    now = time.time()
    last_error = getattr(client, "last_primary_error", None)
    out_error = None
    if last_error is not None:
        out_error = {
            "status": last_error["status"],
            "message": last_error["message"],
            "ago_s": round(now - last_error["at"], 1),
        }
    consecutive = getattr(client, "consecutive_primary_failures", 0)
    return {
        "healthy": consecutive < 2,
        "consecutive_failures": consecutive,
        "last_error": out_error,
        "fallback_allowed": settings.llm_allow_fallback,
        "llm_mode": settings.llm_mode,
    }


@router.get("/rate-limit", response_model=RateLimitStatusResponse)
async def rate_limit_status(request: Request) -> dict:
    return _status_payload(_unwrap(request))


@router.get("/activity", status_code=200, response_model=LlmActivityResponse)
async def llm_activity(request: Request, since: int = 0) -> dict:
    log = getattr(request.app.state, "activity_log", None)
    if log is None:
        return {"latest": 0, "events": []}
    events, latest = log.events_since(since)
    return {"latest": latest, "events": events}


@router.post("/rate-limit/probe", response_model=RateLimitStatusResponse)
async def rate_limit_probe(request: Request) -> dict:
    client = _unwrap(request)
    if not getattr(client, "groq_api_key", None):
        raise HTTPException(status_code=503, detail="No GROQ_API_KEY configured")
    await client.probe_rate_limits()
    return _status_payload(client)
