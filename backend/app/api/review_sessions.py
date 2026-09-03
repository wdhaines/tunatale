"""Review sessions — content that belongs to no curriculum.

bd tunatale-9p9d. A review session has no theme, no position in a sequence, and
its content is drawn from the WHOLE language deck rather than one plan, so it
gets its own surface rather than borrowing a curriculum day it does not deserve.

⚠️ THE ROUTER PREFIX IS PART OF THE DECISION. These routes deliberately do not
live under ``/api/story``, which is the curriculum-shaped surface: creating a
session there would have been the same placement error the epic exists to
correct — the one that put a "Review story" button beside Regenerate — expressed
in a URL instead of a button.

What this module does NOT contain is as deliberate: there is no reconciliation,
no status-by-day, and no retry-by-day. ``LessonPipeline`` is keyed
(language_code, curriculum_id, day) throughout and its ``reconcile`` /
``status_for`` are literally "walk this curriculum's days in order" — machinery a
session has nothing to walk. Rendering instead calls ``render_lesson_audio``
directly, which already takes no curriculum and no day (measured on
tunatale-uv55), so the audio pipeline is reused verbatim rather than widened.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.api._serializers import serialize_lesson

# Reached across modules rather than duplicated. These are the shared post-generation
# steps — UPOS tagging BEFORE the write, gloss pre-warm after, speaker warnings
# mirrored to the log — and a second copy here would drift from the lesson path
# exactly where the two must agree. No import cycle: generation.py knows nothing
# about review sessions.
from app.api.generation import (
    _injected_lemmatizer,
    _logged_speaker_warnings,
    _prewarm_lesson,
    annotate_chunk_upos_for_lesson,
)
from app.api.models import (
    CreateReviewSessionRequest,
    CreateReviewSessionResponse,
    ListReviewSessionsResponse,
    RenderAudioResponse,
    ReviewSessionResponse,
)
from app.audio.render_service import render_lesson_audio
from app.generation.ids import mint_id
from app.generation.story import NoReviewVocabularyError, StoryGenerationError
from app.llm.client import LLMError, LLMQuotaExceededError

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/review-sessions", tags=["review-sessions"])

# Strong refs to fire-and-forget pre-warm tasks: the event loop only keeps a weak
# reference, so an un-anchored task can be garbage-collected mid-flight.
_background_tasks: set[asyncio.Task] = set()

_FALLBACK_CEFR_LEVEL = "A2"

# The refusal the learner reads when nothing is due. Lifted verbatim from the
# lesson-page handler deleted in e17a6ba (recorded on bd tunatale-q2np) rather
# than rewritten: "no vocabulary is due" is the part that tells them this is a
# normal Tuesday and not a broken button.
_NOTHING_DUE = "Nothing to review right now — no vocabulary is due in this language today"


def _latest_cefr_level(store) -> str:
    """The level a new review session should be pitched at.

    A session belongs to no curriculum, so it has no level of its own. Taking it
    from the learner's most recent plan in this language is the answer the user
    chose over a per-language setting and over a picker on the button, with the
    downside stated: a session inherits a level from a plan it has nothing to do
    with.

    ⚠️ THE FALLBACK IS NOT AN EDGE CASE. A learner with no curricula at all must
    still be able to make a review session — that independence is the whole point
    of the surface — so "no plans yet" resolves to the same default
    ``GenerateStoryRequest`` has always used, not to an error.

    ``list_curricula`` is newest-first, and the store is already scoped to the
    request's language.
    """
    rows = store.list_curricula()
    if not rows:
        return _FALLBACK_CEFR_LEVEL
    return store.get_curriculum(rows[0]["id"]).cefr_level


async def _generate_and_store(request: Request, *, session_id: str | None, session_date: str | None) -> dict:
    """Generate one session and write it, minting id and date only when asked.

    Shared by create and regenerate, which differ in EXACTLY two things: whether
    the id and date are minted or supplied, and the status code. Everything else
    — the four-way error mapping, the UPOS pass before the write, the gloss
    pre-warm after it — has to be identical, and a second copy would drift
    precisely where the two must agree.

    ⚠️ The generation happens BEFORE anything is written, and that ordering is
    load-bearing for regenerate: a refusal (nothing due, a 429, an upstream
    failure) must leave the dialogue the user already had intact rather than
    replacing it with nothing.
    """
    store = request.state.content_store
    language = request.state.language
    generator = request.app.state.story_generator
    srs_db = getattr(request.state, "srs_db", None)

    try:
        lesson = await generator.generate_review_session(language, _latest_cefr_level(store), srs_db=srs_db)
    except NoReviewVocabularyError as e:
        # 409, and with the learner's wording rather than the exception's. This is
        # the ONLY refusal that gets reworded, so the neighbouring 502 still reads
        # as an upstream failure.
        raise HTTPException(status_code=409, detail=_NOTHING_DUE) from e
    except StoryGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except LLMQuotaExceededError as e:
        # 429, not 502: nothing upstream failed. TT declined to call because the
        # day budget is exhausted, and a retry cannot succeed against that.
        raise HTTPException(status_code=429, detail=str(e)) from e
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    # No `if srs_db is not None` guard, and that is not an oversight: reaching this
    # line proves it is not None. A session with no SRS database selects no words,
    # and no words raises NoReviewVocabularyError above, before any LLM call.
    # Guarding here would add a branch nothing can execute.
    await annotate_chunk_upos_for_lesson(lesson, srs_db, **_injected_lemmatizer(request))

    session_id = session_id or mint_id(lesson.title)
    metadata = lesson.generation_metadata
    # Read the clock ONCE. Called twice, this would store one date and report
    # another across a midnight boundary — rare, silent, and unreproducible.
    session_date = session_date or date.today().isoformat()
    store.save_review_session(
        session_id,
        request.state.language_code,
        session_date,
        lesson,
        review_requested=metadata.get("review_requested"),
        review_used=metadata.get("review_used"),
    )

    task = asyncio.create_task(_prewarm_lesson(lesson, srs_db))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return {
        "id": session_id,
        "session_date": session_date,
        "title": lesson.title,
        "review_requested": metadata.get("review_requested", []),
        "review_used": metadata.get("review_used", []),
        "warnings": _logged_speaker_warnings(metadata.get("story"), language),
    }


@router.post("", status_code=201, response_model=CreateReviewSessionResponse)
async def create_review_session(body: CreateReviewSessionRequest, request: Request):
    """Generate one review session: no curriculum, no day, no theme.

    Takes no identifiers at all — see ``CreateReviewSessionRequest`` for why that
    is enforced rather than merely documented.
    """
    return await _generate_and_store(request, session_id=None, session_date=None)


@router.post("/{session_id}/regenerate", status_code=200, response_model=CreateReviewSessionResponse)
async def regenerate_review_session(session_id: str, request: Request):
    """Rewrite one session's dialogue without it becoming a different session.

    The lesson page has had "Regenerate Day N" since long before sessions
    existed. A session had no equivalent, so a dialogue the user wanted improved
    could only be replaced by generating a WHOLE NEW one — new id, new URL, new
    row in the dated list, different words. That is a different act, and the
    absence of this route was reported as "the lesson tools are missing".

    ⚠️ Not routed through ``LessonPipeline`` the way the lesson page's Regenerate
    is. That pipeline is keyed (language_code, curriculum_id, day) throughout, so
    a session has nothing to key on — the same reason rendering calls
    ``render_lesson_audio`` directly. The cost, stated rather than hidden: no
    429 wait-and-retry and no sticky-failed + Retry, so a quota refusal surfaces
    to the user as a 429 they must act on themselves.

    ⚠️ The word selection is NOT pinned to the previous run. A review session's
    whole claim is that it is about what has decayed NOW, and
    ``build_review_session_prompts`` re-selects at call time; pinning the old
    list would make a regenerated session assert a staleness it no longer
    measures. The coverage pair is rewritten to match, for the same reason.
    """
    store = request.state.content_store
    row = store.get_review_session_row(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Review session not found")

    result = await _generate_and_store(request, session_id=session_id, session_date=row["session_date"])

    # AFTER the write, and only on success. Audio rows key on the session id, so
    # renders of the dialogue we just replaced would otherwise still be served
    # for the new one — the player would read a script no longer on screen.
    # Unlink as well as delete the rows, missing_ok for the same reason the
    # day-delete path gives: a file already gone is the outcome we want.
    for file_path in store.delete_review_session_audio(session_id):
        Path(file_path).unlink(missing_ok=True)

    return result


@router.get("", status_code=200, response_model=ListReviewSessionsResponse)
async def list_review_sessions(request: Request):
    """Every review session in this language, newest first.

    Dated, not numbered. The store returns the coverage pair without touching a
    lesson body, so this stays one query however long the list grows.
    """
    return {"sessions": request.state.content_store.list_review_sessions(request.state.language_code)}


@router.get(
    "/{session_id}",
    status_code=200,
    response_model=ReviewSessionResponse,
    # ⚠️ REQUIRED, not decoration. LessonResponse declares `day: int | None = None`,
    # so without this a session read reports "day": null — a session asserting a
    # position in a sequence it has none of. serialize_lesson leaves the key out
    # entirely for a session; exclude_unset is what keeps it out of the payload.
    response_model_exclude_unset=True,
)
async def get_review_session(session_id: str, request: Request):
    """One session's body, shaped like a lesson minus the field it has no right to.

    ``serialize_lesson`` omits ``day`` when it is not passed, so the reading
    frontend reuses its lesson renderer unchanged and a session still cannot
    claim a position in a sequence it is not part of.
    """
    store = request.state.content_store
    row = store.get_review_session_row(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Review session not found")
    lesson = store.get_review_session(session_id)
    return serialize_lesson(session_id, lesson) | {"session_date": row["session_date"]}


@router.post(
    "/{session_id}/render",
    status_code=202,
    response_model=RenderAudioResponse,
    # cues[].ref omits target_index on narration cues — a plain response_model
    # would re-add "target_index": null to every narration ref.
    response_model_exclude_unset=True,
)
async def render_review_session(session_id: str, request: Request):
    """Render a session's audio through the ordinary audio pipeline.

    ``render_lesson_audio`` takes no curriculum and no day and keys on the id
    alone, so this reuses it verbatim; ``audio_files`` rows land under the
    session id and ``GET /api/audio/lesson/{id}`` then serves them with no
    change at all.
    """
    store = request.state.content_store
    lesson = store.get_review_session(session_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Review session not found")

    try:
        return await render_lesson_audio(
            store=store,
            renderer=request.app.state.renderer,
            audio_dir=request.app.state.audio_dir,
            lesson_id=session_id,
            lesson=lesson,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
