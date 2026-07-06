"""Pipeline status and control endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.models import PipelineRegenerateRequest, PipelineRetryRequest

router = APIRouter(prefix="/api/curriculum", tags=["pipeline"])


def _pipeline(request: Request):
    pipeline = getattr(request.app.state, "pipeline", None)
    return pipeline


def _get_curriculum_or_404(store, curriculum_id: str) -> object:
    curriculum = store.get_curriculum(curriculum_id)
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")
    return curriculum


@router.get("/{curriculum_id}/pipeline", status_code=200)
async def pipeline_status(curriculum_id: str, request: Request):
    pipeline = _pipeline(request)
    if pipeline is None:
        return {"active": False, "days": []}

    language_code = request.state.language_code

    # 404 if curriculum doesn't exist
    store = request.state.content_store
    _get_curriculum_or_404(store, curriculum_id)

    pipeline.reconcile(language_code, curriculum_id)
    return pipeline.status_for(language_code, curriculum_id)


@router.post("/{curriculum_id}/pipeline/retry", status_code=200)
async def pipeline_retry(curriculum_id: str, body: PipelineRetryRequest, request: Request):
    pipeline = _pipeline(request)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="Pipeline not available")

    language_code = request.state.language_code
    try:
        status = pipeline.retry(language_code, curriculum_id, body.day)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Day {body.day} not found in curriculum") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"status": status}


@router.post("/{curriculum_id}/pipeline/regenerate", status_code=200)
async def pipeline_regenerate(curriculum_id: str, body: PipelineRegenerateRequest, request: Request):
    pipeline = _pipeline(request)
    if pipeline is None:
        raise HTTPException(status_code=404, detail="Pipeline not available")

    language_code = request.state.language_code
    try:
        status = pipeline.regenerate(language_code, curriculum_id, body.day, strategy=body.strategy)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Day {body.day} not found in curriculum") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"status": status}
