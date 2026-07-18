"""Curriculum generation and retrieval endpoints."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request

from app.api._serializers import serialize_lesson
from app.api.models import (
    GenerationModeRequest,
    ImportPlanRequest,
    PlanFeedbackRequest,
    PlanTurnRequest,
    StartPlanRequest,
)
from app.generation.planner import CurriculumPlanner, PlannerError, build_turn_prompt, parse_turn
from app.models.curriculum import Curriculum, CurriculumDay
from app.srs.planner_snapshot import build_learner_snapshot
from app.storage.plan_io import export_plan, get_planner_state, import_plan, mint_curriculum_id

router = APIRouter(prefix="/api/curriculum", tags=["curriculum"])


def _turn_inputs(curriculum_id: str, request: Request) -> tuple:
    """Assemble the common inputs for ``plan_turn`` and the prompt export endpoint.

    Returns ``(store, curriculum, planner, snapshot, language)``.

    Shared helper so the exported prompt can never drift from what the Groq
    path sends — both handlers call this and pass the result to
    ``build_turn_prompt``.
    """
    store = request.state.content_store
    curriculum = _get_curriculum_or_404(store, curriculum_id)
    planner: CurriculumPlanner = request.app.state.curriculum_planner
    snapshot = build_learner_snapshot(request.state.srs_db)
    language = request.state.language
    return store, curriculum, planner, snapshot, language


@router.post("/import", status_code=201)
async def import_curriculum_plan(body: ImportPlanRequest, request: Request):
    store = request.state.content_store
    try:
        cid, curriculum = import_plan(store, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    return {
        "id": cid,
        "topic": curriculum.topic,
        "language_code": curriculum.language_code,
        "days": len(curriculum.days),
    }


def _get_curriculum_or_404(store, curriculum_id: str) -> Curriculum:
    curriculum = store.get_curriculum(curriculum_id)
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")
    return curriculum


@router.post("/plan", status_code=201)
async def start_plan(body: StartPlanRequest, request: Request):
    """LLM-free: mint an id and save an empty curriculum with empty planner state."""
    store = request.state.content_store
    curriculum_id = mint_curriculum_id(body.topic)
    curriculum = Curriculum(
        id=curriculum_id,
        topic=body.topic,
        language_code=request.state.language_code,
        cefr_level=body.cefr_level,
        metadata={"planner": {"chat": [], "proposed": None, "feedback": []}},
    )
    store.save_curriculum(curriculum_id, curriculum)
    return {
        "id": curriculum_id,
        "topic": curriculum.topic,
        "language_code": curriculum.language_code,
        "cefr_level": curriculum.cefr_level,
        "days": 0,
    }


@router.post("/{curriculum_id}/plan/turn", status_code=200)
async def plan_turn(curriculum_id: str, body: PlanTurnRequest, request: Request):
    """One planner chat turn: snapshot → LLM / pasted response → append chat, set/replace proposed."""
    store, curriculum, planner, snapshot, language = _turn_inputs(curriculum_id, request)

    try:
        if body.pasted_response is not None:
            turn = parse_turn(
                body.pasted_response,
                curriculum=curriculum,
                batch_size=body.batch_size,
            )
        else:
            turn = await planner.turn(
                curriculum=curriculum,
                user_message=body.message,
                batch_size=body.batch_size,
                learner_snapshot=snapshot,
                language=language,
            )
    except PlannerError as e:
        # Nothing is persisted for a failed turn — the user retries.
        raise HTTPException(status_code=502, detail=str(e)) from e

    state = get_planner_state(curriculum)
    state["chat"].append({"role": "user", "content": body.message})
    state["chat"].append({"role": "planner", "content": turn.reply})
    if turn.proposed_days is not None:
        # A new proposing turn replaces any prior proposal (latest-wins);
        # a pure-chat/pure-prose turn leaves the existing proposal in place.
        state["proposed"] = {
            "start_day": turn.proposed_days[0].day,
            "days": [asdict(d) for d in turn.proposed_days],
        }
    curriculum.metadata["planner"] = state
    store.save_curriculum(curriculum_id, curriculum)
    return {"reply": turn.reply, "proposed": state["proposed"]}


@router.post("/{curriculum_id}/plan/turn/prompt", status_code=200)
async def plan_turn_prompt(curriculum_id: str, body: PlanTurnRequest, request: Request):
    """Export the exact prompts for a planner turn, without calling any LLM.

    The system and user prompts returned are byte-identical to what the
    Groq path would send for the same inputs.  Persists nothing.
    """
    _, curriculum, _planner, snapshot, language = _turn_inputs(curriculum_id, request)
    system_prompt, user_prompt = build_turn_prompt(
        curriculum=curriculum,
        user_message=body.message,
        batch_size=body.batch_size,
        learner_snapshot=snapshot,
        language=language,
    )
    return {"system_prompt": system_prompt, "user_prompt": user_prompt}


@router.post("/{curriculum_id}/plan/commit", status_code=200)
async def plan_commit(curriculum_id: str, request: Request):
    """Append the proposed batch to the committed days and clear the proposal."""
    store = request.state.content_store
    curriculum = _get_curriculum_or_404(store, curriculum_id)
    state = get_planner_state(curriculum)
    proposed = state.get("proposed")
    if not proposed:
        raise HTTPException(status_code=409, detail="No proposed batch to commit")

    # The proposal was numbered against the day list at turn time; if the
    # committed days changed since (e.g. a plan re-import), appending it would
    # collide with or gap the existing day numbers.
    expected_start = max((d.day for d in curriculum.days), default=0) + 1
    if proposed["days"][0]["day"] != expected_start:
        raise HTTPException(
            status_code=409,
            detail="Proposed batch is stale — the committed days changed since it was proposed; ask the planner to re-propose",
        )

    days = [CurriculumDay(**d) for d in proposed["days"]]
    curriculum.days.extend(days)
    first, last = days[0].day, days[-1].day
    label = f"day {first}" if first == last else f"days {first}-{last}"
    state["chat"].append({"role": "event", "content": f"Committed {label}."})
    state["proposed"] = None
    curriculum.metadata["planner"] = state
    store.save_curriculum(curriculum_id, curriculum)

    # Enqueue pipeline jobs for the newly committed days (gated on generation_mode)
    pipeline = getattr(request.app.state, "pipeline", None)
    if pipeline is not None and curriculum.metadata.get("generation_mode", "auto") != "manual":
        for day_entry in days:
            pipeline.enqueue(request.state.language_code, curriculum_id, day_entry.day, "generate")

    return {"id": curriculum_id, "days": len(curriculum.days)}


@router.post("/{curriculum_id}/plan/reset", status_code=200)
async def plan_reset(curriculum_id: str, request: Request):
    """Clear the planner chat and proposed batch (keeps feedback and committed days)."""
    store = request.state.content_store
    curriculum = _get_curriculum_or_404(store, curriculum_id)
    state = get_planner_state(curriculum)
    reply_count = sum(1 for m in state.get("chat", []) if m.get("role") == "planner")
    state["chat"] = []
    state["proposed"] = None
    curriculum.metadata["planner"] = state
    store.save_curriculum(curriculum_id, curriculum)
    return {"reply_count_cleared": reply_count}


@router.post("/{curriculum_id}/plan/feedback", status_code=200)
async def plan_feedback(curriculum_id: str, body: PlanFeedbackRequest, request: Request):
    """Record listening feedback for a committed day; it enters the next turn's prompt."""
    store = request.state.content_store
    curriculum = _get_curriculum_or_404(store, curriculum_id)
    if body.day not in {d.day for d in curriculum.days}:
        raise HTTPException(status_code=404, detail=f"Unknown day {body.day}")
    state = get_planner_state(curriculum)
    state["feedback"].append({"day": body.day, "note": body.note})
    curriculum.metadata["planner"] = state
    store.save_curriculum(curriculum_id, curriculum)
    return {"feedback": state["feedback"]}


@router.post("/{curriculum_id}/generation-mode", status_code=200)
async def set_generation_mode(curriculum_id: str, body: GenerationModeRequest, request: Request):
    """Set the generation mode for a curriculum: 'auto' (default, Groq pipeline) or 'manual' (copy/paste)."""
    store = request.state.content_store
    curriculum = _get_curriculum_or_404(store, curriculum_id)
    curriculum.metadata["generation_mode"] = body.mode
    store.save_curriculum(curriculum_id, curriculum)
    return {"mode": body.mode}


@router.get("", status_code=200)
async def list_curricula(request: Request):
    store = request.state.content_store
    return store.list_curricula()


@router.get("/{curriculum_id}", status_code=200)
async def get_curriculum(curriculum_id: str, request: Request):
    store = request.state.content_store
    curriculum = store.get_curriculum(curriculum_id)
    if curriculum is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")
    return {
        "id": curriculum_id,
        "topic": curriculum.topic,
        "language_code": curriculum.language_code,
        "cefr_level": curriculum.cefr_level,
        "days": sorted((asdict(d) for d in curriculum.days), key=lambda d: d["day"]),
        "proposed": get_planner_state(curriculum)["proposed"],
        "generation_mode": curriculum.metadata.get("generation_mode", "auto"),
    }


@router.get("/{curriculum_id}/progress")
async def get_curriculum_progress(curriculum_id: str, request: Request):
    store = request.state.content_store
    if store.get_curriculum(curriculum_id) is None:
        raise HTTPException(status_code=404, detail="Curriculum not found")
    return store.get_lesson_days(curriculum_id)


@router.get("/{curriculum_id}/source", status_code=200)
async def get_curriculum_source(curriculum_id: str, request: Request):
    store = request.state.content_store
    try:
        return export_plan(store, curriculum_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Curriculum not found") from None


@router.delete("/{curriculum_id}", status_code=200)
async def delete_curriculum(curriculum_id: str, request: Request):
    store = request.state.content_store
    if not store.delete_curriculum(curriculum_id):
        raise HTTPException(status_code=404, detail="Curriculum not found")
    return {"deleted": curriculum_id}


@router.get("/{curriculum_id}/days/{day}/lesson", status_code=200)
async def get_lesson_by_day(curriculum_id: str, day: int, request: Request):
    store = request.state.content_store
    result = store.get_latest_lesson_by_day(curriculum_id, day)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No lesson found for day {day}")
    lesson_id, lesson = result
    return serialize_lesson(lesson_id, lesson)
