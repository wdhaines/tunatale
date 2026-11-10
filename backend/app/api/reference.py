"""Reference tab endpoints: the per-language reference pages (the verb chart)."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.api.models import ReferencePagesResponse, VerbChartResponse
from app.languages import get_verb_chart_path

router = APIRouter()


@cache
def _load(path: Path) -> VerbChartResponse:
    """Parse the chart file at *path* once per process (it is a static data file)."""
    return VerbChartResponse.model_validate_json(path.read_text(encoding="utf-8"))


@router.get("/api/reference", response_model=ReferencePagesResponse)
async def list_reference_pages(request: Request):
    """The Reference tab's page list for the active language.

    An empty list, not a 404, when the language registers no chart: the
    frontend hides the tab on it. The one page today is the verb chart.
    """
    code = request.state.language_code
    path = get_verb_chart_path(code)
    if path is None:
        return {"pages": []}
    return {"pages": [{"id": "verb-chart", "title": _load(path).title}]}


@router.get("/api/reference/verb-chart", response_model=VerbChartResponse)
async def get_verb_chart(request: Request):
    """The active language's verb chart, served whole from its data file.

    The response model mirrors the file key for key, and ``extra="forbid"``
    refuses a file carrying a key the model lacks, so the frontend draws from
    precisely what the plugin committed.
    """
    code = request.state.language_code
    path = get_verb_chart_path(code)
    if path is None:
        raise HTTPException(status_code=404, detail=f"No verb chart for language '{code}'")
    return _load(path)
