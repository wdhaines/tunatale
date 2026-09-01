"""Durable browser-side logging — the frontend's equivalent of sync.log.

WHY THIS EXISTS. Two backend counters were fixed today (PRODUCTION_MINT and
PRESTAGE_IMAGES) whose only fault was reaching a logger nobody reads: uvicorn
runs at ``--log-level warning`` under start-dev.sh and is redirected nowhere, so
a signal present only in that channel does not exist. The browser has the same
defect and no fix at all — a device console dies with the tab, and the machine
that could read it is not the machine running the app.

That made a real bug undiagnosable: a gloss that will not reveal on Android
Brave reproduces on no emulator, because Playwright's ``tap()`` dispatches at a
geometric centre with no fuzzy tap-targeting. Measurement from the real device
is the only evidence, and until now there was nowhere to put it.

⚠️ This is a WRITE endpoint fed by an untrusted browser. Every limit below is
load-bearing, not defensive decoration:
  * OFF by default (``client_log_enabled``), so it is opt-in per debugging
    session and never an ambient write channel;
  * newlines stripped, so one submitted line is exactly one log line and a page
    cannot forge entries that appear to come from elsewhere;
  * per-line and per-batch caps, so a loop cannot fill the disk.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.api.models import ClientLogRequest, ClientLogResponse
from app.config import settings

router = APIRouter(tags=["client-log"])

#: Caps. The pre-stage runs after every sync and this can be driven by a loop in
#: a page, so both bounds are on the endpoint rather than trusted to the client.
MAX_LINES = 50
MAX_LINE_CHARS = 300


def _sanitise(line: str) -> str:
    """One submitted line becomes exactly one log line.

    Collapsing whitespace is what closes the injection: a client string
    containing ``\\n2026-01-01T00:00:00 FORGED …`` would otherwise write a
    second entry indistinguishable from a genuine one, timestamp and all. The
    text is kept — as content, never as its own record.
    """
    return " ".join(line.split())[:MAX_LINE_CHARS]


@router.post("/api/client-log", status_code=200, response_model=ClientLogResponse)
async def client_log(body: ClientLogRequest) -> dict:
    """Append browser-supplied lines to ``settings.client_log``.

    404 rather than 403 when disabled: a debug channel that is switched off
    should not advertise that it exists.
    """
    if not settings.client_log_enabled:
        raise HTTPException(status_code=404, detail="Not Found")

    lines = [_sanitise(line) for line in body.lines[:MAX_LINES]]
    if not lines:
        return {"accepted": 0}

    path = settings.client_log
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8") as fh:
        for line in lines:
            fh.write(f"{ts} CLIENT {line}\n")
    return {"accepted": len(lines)}
