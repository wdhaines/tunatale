"""LLM rate-limit visibility endpoints.

GET /api/llm/rate-limit — the Groq quota state captured passively from the most
recent call's response headers, plus TT's own 24h token tally (Groq's daily
token cap has no header). Times are relative (age_s, *_reset_in_s, retry_in_s),
computed server-side so the frontend can count down without clock-skew issues.

POST /api/llm/rate-limit/probe — fire a 1-token Groq request purely to refresh
the headers (manual button or one-shot frontend auto-probe on first page open
per session; still never polled — a poll would burn the daily request quota
the endpoint exists to protect).
"""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

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
    return {
        "provider": "groq",
        "model": getattr(client, "groq_model", None),
        "llm_mode": settings.llm_mode,
        "snapshot": out_snapshot,
        "last_429": out_429,
        "tokens_used_24h": ledger.tokens_used_last_24h() if ledger is not None else None,
        "tokens_per_day_limit": settings.groq_tokens_per_day_limit,
    }


@router.get("/rate-limit")
async def rate_limit_status(request: Request) -> dict:
    return _status_payload(_unwrap(request))


@router.get("/activity", status_code=200)
async def llm_activity(request: Request, since: int = 0) -> dict:
    log = getattr(request.app.state, "activity_log", None)
    if log is None:
        return {"latest": 0, "events": []}
    events, latest = log.events_since(since)
    return {"latest": latest, "events": events}


@router.post("/rate-limit/probe")
async def rate_limit_probe(request: Request) -> dict:
    client = _unwrap(request)
    if not getattr(client, "groq_api_key", None):
        raise HTTPException(status_code=503, detail="No GROQ_API_KEY configured")
    await client.probe_rate_limits()
    return _status_payload(client)
