"""SRS state and review endpoints."""

from __future__ import annotations

import datetime
import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import Literal, NamedTuple

import anyio
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from app.api.models import (
    BackfillTranslationsResponse,
    BulkDeleteRequest,
    BulkDeleteResponse,
    CommitPendingResponse,
    CreateBaseCardRequest,
    CreateCardResponse,
    CreateItemRequest,
    DrillFeedbackResponse,
    DrillRequest,
    DueCollocationsResponse,
    IgnoreLemmaRequest,
    ImportListensRequest,
    ImportListensResponse,
    InflectionClozeRequest,
    LessonReviewQueueResponse,
    LessonTranscriptResponse,
    ListenPreviewResponse,
    ListenRequest,
    ListenResponse,
    ListensResponse,
    ListItemsResponse,
    MarkLessonReviewedResponse,
    NewCollocationsResponse,
    QueueStatsResponse,
    ReviewQueueResponse,
    SetStateRequest,
    SrsItemResponse,
    SrsStatsResponse,
    StatusResponse,
    SuspendRequest,
    TranslateMissingResponse,
    TranslateRequest,
    TranslateResponse,
    UndoGradeResponse,
    UntrackItemResponse,
    UpdateItemRequest,
)
from app.audio.cloze_tts import synthesize_cloze_audios
from app.common.guid import compute_guid
from app.config import settings
from app.languages import (
    format_vocab_headword,
    get_gender_article,
    get_lemma_plausible,
    get_tts_voice,
    get_wordfreq_lang,
    known_language_codes,
)
from app.llm.translate import generate_word_gloss, translate_term
from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_day_bounds_utc_dt, anki_today, due_at_rollover_utc
from app.srs.feedback import rating_from_input
from app.srs.fsrs import Rating, build_revlog_row, schedule
from app.srs.function_words import (
    format_morphology_hint,
    is_clozes_only_verb,
    is_function_word_for,
    make_cloze_text,
    make_morphology_cloze_text,
    normalize_sentence_key,
)
from app.srs.gloss_definiteness import align_gloss_definiteness
from app.srs.gloss_verb_form import align_gloss_verb_form
from app.srs.grade_undo import UndoNotAvailable, record_grade_snapshot, undo_last_grade
from app.srs.lemmatizer import analyze_sentence_cached, get_lemmatizer, lemmatize_surfaces_in_context, model_version_for
from app.srs.mastery import is_due_beyond_horizon, is_well_known
from app.srs.multiword import is_trapped_occurrence
from app.srs.queue_engine import _compute_live_main as _compute_live_main
from app.srs.queue_engine import _fnv1a_64_i64 as _fnv1a_64_i64
from app.srs.queue_engine import _merge_by_retrievability_ascending as _merge_by_retrievability_ascending
from app.srs.queue_engine import _merge_directions as _merge_directions
from app.srs.queue_engine import _spread_mix as _spread_mix
from app.srs.queue_engine import assemble_review_queue as assemble_review_queue
from app.srs.queue_engine import build_and_freeze_main_queue as build_and_freeze_main_queue
from app.srs.queue_stats import (
    advance_learning_cutoff,
    build_live_load_balancer,
    effective_review_budget,
    resolve_bury_new,
    resolve_col_crt,
    resolve_daily_new_cap,
    resolve_daily_review_cap,
    resolve_fsrs_params,
    resolve_learning_steps,
    resolve_new_cards_ignore_review_limit,
    resolve_relearning_steps,
)
from app.srs.tokenizer import tokenize
from app.srs.transcript import _build_variant_index, extract_transcript

_logger = logging.getLogger(__name__)


def _balancer_add(balancer: object | None, *, card_id: int | None, note_id: int | None, interval: int) -> None:
    """Feed a just-graded card back into the live load-balancer histogram (Layer 55).

    Mirrors Anki's per-answer ``load_balancer.add_card`` so later grades in the
    same request see this one. No-op when the balancer is absent (LB off / pre-sync).
    """
    if balancer is not None:
        balancer.add_card(card_id or 0, note_id or 0, interval)


router = APIRouter(prefix="/api/srs", tags=["srs"])
# Resolved from the setting, not from __file__, so the serve side cannot drift
# from the import side (media/importer.py) — see Settings.media_dir. Read at
# import, matching the ~/.tunatale expanduser pattern: env is set before launch.
_MEDIA_DIR = settings.media_dir

# The lemmatizer is resolved per-request from the content's language_code, never a
# process-wide singleton — multi-language mode (settings.database_urls) serves both
# languages from one process, so a frozen import-time lemmatizer would analyze e.g.
# Norwegian transcripts with the Slovene model. See get_lemmatizer(language_code).

# Rating-word → Rating. /listen no longer consults this (it stores the raw word on
# the pending row); it is the string→Rating seam for the pending-grade APPLY path.
# Currently unreferenced — a module-level dict is invisible to both ruff and the
# coverage gate, so this note is the only thing marking it as deliberate.
_WORD_RATING_MAP: dict[str, Rating] = {
    "again": Rating.AGAIN,
    "hard": Rating.HARD,
    "good": Rating.GOOD,
    "easy": Rating.EASY,
}


def _direction_to_dict(ds: DirectionState) -> dict:
    result = {
        "state": ds.state.value,
        "due_at": ds.due_at.isoformat(),
        "stability": ds.stability,
        "difficulty": ds.difficulty,
        "reps": ds.reps,
        "lapses": ds.lapses,
        "last_review": ds.last_review.isoformat() if ds.last_review else None,
        "last_review_time_ms": ds.last_review_time_ms,
        "anki_card_id": ds.anki_card_id,
    }
    if ds.left is not None:
        result["left"] = ds.left
    return result


def _item_to_dict(
    row_id: int,
    item: SRSItem,
    language_code: str,
    image_url: str | None = None,
    audio_url: str | None = None,
    ambiguous_surfaces: set[str] | None = None,
) -> dict:
    """Serialize an SRSItem to a response dict.

    Single-template Anki notes (e.g., Basic phonics) have no production
    direction after migration v15→v16 — emit `null` rather than fabricating
    one. Flat back-compat fields read from recognition for vocab cards and
    from production for cloze cards (which have no recognition direction).
    """
    rec = item.directions.get(Direction.RECOGNITION)
    prod = item.directions.get(Direction.PRODUCTION)
    flat_src = prod if item.syntactic_unit.card_type == "cloze" else rec
    flat: dict[str, object] = {
        "state": flat_src.state.value if flat_src else SRSState.NEW.value,
        "due_at": flat_src.due_at.isoformat() if flat_src else None,
        "stability": flat_src.stability if flat_src else 1.0,
        "difficulty": flat_src.difficulty if flat_src else 5.0,
        "reps": flat_src.reps if flat_src else 0,
        "lapses": flat_src.lapses if flat_src else 0,
        "last_review": flat_src.last_review.isoformat() if flat_src and flat_src.last_review else None,
    }
    return {
        "id": row_id,
        "text": item.syntactic_unit.text,
        "translation": item.syntactic_unit.translation,
        "word_count": item.syntactic_unit.word_count,
        **flat,
        "language_code": language_code,
        "guid": item.guid,
        "anki_note_id": item.anki_note_id,
        "directions": {
            "recognition": _direction_to_dict(rec) if rec else None,
            "production": _direction_to_dict(prod) if prod else None,
        },
        "card_type": item.syntactic_unit.card_type,
        "source_sentence": item.syntactic_unit.source_sentence,
        "source_sentence_translation": item.syntactic_unit.source_sentence_translation,
        "image_url": image_url,
        "audio_url": audio_url,
        "grammar": item.syntactic_unit.grammar,
        "note": item.syntactic_unit.note,
        # Gender article (en/ei/et) — display-time prefix on the headword.
        "article": item.syntactic_unit.article,
        # Rich back-of-card fields (IPA, inflections, dictionary entry…), each
        # tagged with where it renders: summary (always visible), details
        # (collapsed disclosure), or deep (its own nested disclosure). Empty list
        # for cards without any.
        "extras": [{"label": e.label, "html": e.html, "tier": e.tier} for e in item.syntactic_unit.extras],
        # Part of speech, shown ONLY when the surface is ambiguous across POS
        # (e.g. "fange" noun vs verb). Empty otherwise, so unambiguous cards
        # stay uncluttered. ``ambiguous_surfaces`` is None on endpoints that
        # don't compute it (single-item views) → no POS shown there.
        "pos": (
            item.syntactic_unit.disambig_key
            if ambiguous_surfaces is not None and item.syntactic_unit.text.casefold() in ambiguous_surfaces
            else ""
        ),
    }


def _triples_to_dicts(db, triples: list[tuple[int, SRSItem, str]]) -> list[dict]:
    result = []
    seen_ids: set[int] = set()
    for row_id, item, lang in triples:
        if row_id in seen_ids:
            continue
        seen_ids.add(row_id)
        img = db.get_image_filename(row_id)
        image_url = f"/api/srs/media/{img}" if img else None
        aud = db.get_audio_filename(row_id)
        audio_url = f"/api/srs/media/{aud}" if aud else None
        result.append(_item_to_dict(row_id, item, lang, image_url, audio_url))
    return result


async def _generate_add_time_media(
    db,
    llm,
    coll_id: int,
    unit: SyntacticUnit,
    *,
    language_code: str,
    used_image_urls: set[str] | None = None,
    media_word: str | None = None,
) -> None:
    """Fetch image + word audio for a freshly-created vocab card, inline.

    So a card the user creates in TunaTale is complete in /review immediately —
    not blank until its first sync (the nasvidenje gap). Cloze cards are skipped
    (they get sentence audio via ``synthesize_cloze_audios``), and the underlying
    ``generate_vocab_media`` no-ops when no Pixabay key is configured. Best-effort:
    never raises, so a media hiccup can't fail card creation. ``sync_create_new``
    reuses whatever this stores rather than re-fetching.

    ``media_word`` decouples "what to fetch media for" from "what the card
    displays": a Norwegian verb card fronts as ``"å lyve"`` but Forvo/Pixabay are
    indexed by the bare lemma, so callers pass ``media_word=lemma``. ``None``
    falls back to ``unit.text`` (today's behavior for every pre-existing caller).
    """
    if unit.card_type == "cloze":
        return
    from app.cards.media.vocab_media import generate_vocab_media
    from app.config import settings

    word = media_word if media_word is not None else unit.text
    await generate_vocab_media(
        db,
        coll_id,
        word,
        unit.translation,
        llm=llm,
        pixabay_key=settings.pixabay_api_key,
        language_code=language_code,
        source_sentence=unit.source_sentence or "",
        grammar=unit.grammar or "",
        used_image_urls=used_image_urls,
    )


@router.get("/due", status_code=200, response_model=DueCollocationsResponse, response_model_exclude_unset=True)
async def get_due_collocations(request: Request, direction: str = "recognition"):
    db = request.state.srs_db
    today = anki_today()
    if direction == "any":
        rec = db.get_due_items(today, Direction.RECOGNITION)
        prod = db.get_due_items(today, Direction.PRODUCTION)
        triples = rec + prod
    else:
        try:
            dir_enum = Direction(direction)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid direction: {direction!r}") from exc
        triples = db.get_due_items(today, dir_enum)
    return {"due": _triples_to_dicts(db, triples)}


@router.get("/new", status_code=200, response_model=NewCollocationsResponse, response_model_exclude_unset=True)
async def get_new_collocations(request: Request, limit: int = 10, direction: str = "recognition"):
    db = request.state.srs_db
    try:
        dir_enum = Direction(direction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid direction: {direction!r}") from exc
    triples = db.get_new_items(limit=limit, direction=dir_enum)
    return {"new": _triples_to_dicts(db, triples)}


@router.post(
    "/items/{item_id}/direction/{direction}/feedback",
    status_code=200,
    response_model=DrillFeedbackResponse,
    # `left` is appended only when the new direction has one; a plain
    # response_model would re-add "left": null to the omitting branch.
    response_model_exclude_unset=True,
)
async def drill_feedback(item_id: int, direction: str, body: DrillRequest, request: Request):
    try:
        dir_enum = Direction(direction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid direction: {direction!r}") from exc

    try:
        rating = rating_from_input(rating=body.rating, signal=body.signal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db = request.state.srs_db
    result = db.get_collocation_by_id(item_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Item not found")
    _, item, _ = result

    fsrs_params, _ = resolve_fsrs_params(db)
    learn_steps, _ = resolve_learning_steps(db)
    relearn_steps, _ = resolve_relearning_steps(db)
    col_crt = resolve_col_crt(db)
    now = datetime.datetime.now(datetime.UTC)
    balancer = build_live_load_balancer(db, now=now, col_crt=col_crt)
    prev_dir = item.directions[dir_enum]
    updated = schedule(
        item,
        rating,
        direction=dir_enum,
        params=fsrs_params,
        time_ms=body.time_ms,
        now=now,
        col_crt=col_crt,
        load_balancer=balancer,
        learn_steps=learn_steps,
        relearn_steps=relearn_steps,
    )
    db.update_direction_by_id(item_id, dir_enum, updated.directions[dir_enum])
    # Lesson "Check your work" re-grade of a card the listen already reviewed
    # today: keep the FSRS state change (an Again is a real same-day lapse) but
    # don't re-charge the daily review budget — the card was counted once already.
    # Guarded by has_counting_review_today so a genuine first review still counts.
    budget_neutral = body.lesson_review and db.has_counting_review_today(item_id, dir_enum, anki_today())
    # Releasing a staged listen grade: the provisional row was never a real grade,
    # so this IS the card's first grade — nothing to overwrite. Anki's review-ahead
    # kind is decided from the card's dueness NOW, not from the grade_class stored
    # at stage time: a sync or a day rollover in between can have moved due_at, and
    # a stale "due" would log a not-due grade as an ordinary review.
    releasing = db.get_pending_grade(item_id, dir_enum.value) is not None
    review_kind = _release_review_kind(prev_dir) if releasing else None
    row = build_revlog_row(
        item_id,
        dir_enum,
        prev_dir,
        updated.directions[dir_enum],
        rating,
        body.time_ms,
        now=now,
        col_crt=col_crt,
        review_kind=review_kind,
        budget_neutral=budget_neutral,
    )
    db.append_revlog(row)
    # Unconditional, not just on the lesson_review path: a real grade must never
    # leave a pending row behind, or the card stays hidden from the main queue
    # (count_review_due_collocations / _compute_live_main) indefinitely.
    db.clear_pending_grade(item_id, dir_enum.value)
    # Single-level undo: snapshot the verbatim pre-grade state so the popover's
    # "Got it ✓" can cycle back via "Undo ↩" (see app.srs.grade_undo).
    record_grade_snapshot(db, item_id=item_id, direction=dir_enum, prior=prev_dir, revlog_id=row.id)
    _balancer_add(balancer, card_id=prev_dir.anki_card_id, note_id=item.anki_note_id, interval=row.interval)
    # Anki parity: advance the learning cutoff at grade time. The next /review-queue
    # call uses this snapshot (not live `now`) to decide which queue=1 cards are
    # ready, so a learning card whose timer expired between this grade and the
    # previous one becomes eligible — but a card that ticks past-due *after* this
    # grade stays pending until the next grade.
    advance_learning_cutoff(db, now)

    new_dir = updated.directions[dir_enum]
    response = {
        "status": "ok",
        "direction": dir_enum.value,
        "new_due_at": new_dir.due_at.isoformat(),
        "new_state": new_dir.state.value,
    }
    if new_dir.left is not None:
        response["left"] = new_dir.left
    return response


@router.post("/items/{item_id}/direction/{direction}/undo", status_code=200, response_model=UndoGradeResponse)
async def undo_grade(item_id: int, direction: str, request: Request):
    """Undo the most recent TT-native grade on (item, direction).

    409 when the grade was superseded by a newer one, already synced to Anki
    (dirty_fsrs cleared), or there is nothing to undo.
    """
    try:
        dir_enum = Direction(direction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid direction: {direction!r}") from exc

    db = request.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")

    try:
        restored = undo_last_grade(db, item_id=item_id, direction=dir_enum)
    except UndoNotAvailable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "status": "ok",
        "direction": dir_enum.value,
        "restored_state": restored.state.value,
        "restored_due_at": restored.due_at.isoformat(),
    }


@router.get(
    "/media/{filename}",
    status_code=200,
    response_class=FileResponse,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def serve_media(filename: str):
    media_dir = _MEDIA_DIR
    file_path = (media_dir / filename).resolve()
    # is_relative_to, not str.startswith — a prefix check passes for sibling
    # directories whose name extends the media dir's ("media" vs "media-evil").
    if not file_path.is_relative_to(media_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(file_path)


def _headword_upos(
    lemma: str,
    first_surface: str,
    surfaces: set[str],
    surface_upos: dict[str, str],
) -> str | None:
    """The headword's UPOS from the surface→UPOS map, or None.

    The map is keyed by surface, so the lemma itself may be absent (classla
    ``sem`` → ``biti``); walk lemma then surfaces in gloss-lookup priority order
    so the upos and the gloss agree on which form they describe.
    """
    for key in (lemma.casefold(), first_surface.casefold(), *(s.casefold() for s in sorted(surfaces))):
        upos = surface_upos.get(key)
        if upos:
            return upos
    return None


def _lookup_verb_base_gloss(
    verb_base_glosses: dict[str, str] | None,
    lemma: str,
    first_surface: str,
    surfaces: set[str],
) -> str | None:
    """Look up a VERB's base-form gloss in the lesson's verb-only map.

    Mirrors ``_resolve_gloss_translation``'s key order against ``token_glosses``
    (lemma first, then the first surface, then any other surface) because the
    map is keyed with the same surface-key + lemma-fallback shape. ``None`` —
    no map at all, or no entry for this verb — means "fall back to the
    aligner", never a partial pass-through.
    """
    if not verb_base_glosses:
        return None
    for key in (lemma, first_surface.lower(), *(s.lower() for s in sorted(surfaces))):
        base = verb_base_glosses.get(key)
        if base:
            return base
    return None


def _resolve_gloss_translation(
    lemma: str,
    token_glosses: dict[str, str],
    surfaces: set[str],
    first_surface: str,
    *,
    language_code: str,
    warn_on_missing: bool = True,
    surface_upos: dict[str, str] | None = None,
    verb_base_glosses: dict[str, str] | None = None,
) -> str:
    """Resolve a token's English translation from the lesson's gloss map.

    The card is keyed by the runtime lemmatizer's lemma, but the LLM may have
    glossed the token under a different key: an inflected surface (Stanza
    lemmatizes ``snøen`` → ``snø`` while the gloss is keyed ``snøen``) or the
    dictionary infinitive (``gå``) while the card is the surface ``går``. Try the
    lemma first, then the surface as it first appeared, then any other surface.
    Returns ``""`` when no gloss covers the token — and logs a warning so the
    silent-empty-translation class (the ``går`` bug: lemma and surface are both
    ``går``, but the LLM only glossed ``gå`` + the multiword ``i går``) is
    visible instead of shipping a blank card.

    The UPOS is derived here too (from *surface_upos*), never at a call site:
    the create path and the listen preview must resolve the SAME gloss for the
    SAME headword, and a VERB headword gets its gloss reduced to the bare
    dictionary form (``lyver`` → "is lying" but ``lyve`` → "lie") so the card's
    back cannot contradict its infinitive front. ``surface_upos=None`` (no
    analyzer) skips the verb branch entirely.

    *verb_base_glosses* is the generation-time map of VERB base forms (the
    lesson's ``verb_base_glosses`` metadata; empty/None for lessons generated
    before it existed). For a VERB headword it is preferred verbatim over the
    aligner — an LLM-authored base form has no wordfreq ceiling — and the
    aligner is the fallback when the map has no entry, so old lessons are
    repaired rather than passed through.

    ``warn_on_missing=False`` for read-only callers. The listen preview resolves
    the same glosses to display them, but it creates nothing and re-runs every
    time the modal opens — warning there would repeat indefinitely and claim a
    card was created when none was.
    """
    for key in [lemma, first_surface.lower(), *sorted(s.lower() for s in surfaces)]:
        gloss = token_glosses.get(key)
        if gloss:
            # The LLM glosses a bare noun as if it were definite ("the murderer"
            # for `morder`), so the card contradicted its own front. Drops the
            # article only when the headword cannot carry one; a no-op for
            # languages with no definiteness checker registered.
            gloss = align_gloss_definiteness(lemma, gloss, language_code)
            if surface_upos is not None and _headword_upos(lemma, first_surface, surfaces, surface_upos) == "VERB":
                base = _lookup_verb_base_gloss(verb_base_glosses, lemma, first_surface, surfaces)
                gloss = base if base is not None else align_gloss_verb_form(gloss)
            return gloss
    if not warn_on_missing:
        return ""
    _logger.warning(
        "No gloss for lemma %r (surfaces=%s) in %s lesson; card created with empty translation",
        lemma,
        sorted(surfaces),
        language_code,
    )
    return ""


# Definition lives in app.srs.mastery so the transcript (which renders a
# past-the-horizon word as "known") and the listen preview (which stops asking
# about it) cannot drift apart. Re-exported under the old private name because
# the listen call sites and their tests reference it.
_is_due_beyond_horizon = is_due_beyond_horizon


def _listen_day_window() -> tuple[datetime.datetime, datetime.datetime, str]:
    """The (today_start, today_end, end_of_day_utc) triple ``_listen_grade_class`` needs.

    Anki-day rollover, NOT local midnight (a card graded in [midnight, 4 AM) is
    still "today" for Anki until rollover). ``end_of_day_utc`` is a naive
    local-date cutoff string, correct ONLY because REVIEW-state ``due_at`` is
    date-encoded at 04:00 UTC (``rollover.py::due_at_rollover_utc``) — the
    lexicographic compare is a due-DATE <= today check in disguise, and it must
    stay identical to ``count_review_due_collocations``'.

    Factored out because both callers of ``_listen_grade_class`` need it: /listen
    when it stages, and ``drill_feedback`` when it re-classifies at release time.
    Two hand-rolled copies of this window is how the two sides drift apart.
    """
    today = anki_today()
    today_start, today_end = anki_day_bounds_utc_dt(today)
    return today_start, today_end, datetime.datetime.combine(today, datetime.time.max).isoformat()


def _listen_grade_class(
    rec: DirectionState | None,
    today_start: datetime.datetime,
    today_end: datetime.datetime,
    *,
    end_of_day_utc: str,
) -> str | None:
    """Classify a recognition direction for /listen grading.

    Returns one of ``"new"``, ``"learning"``, ``"due"``, ``"ahead"``, or
    ``None`` (not eligible).  A NEW-state direction classifies as ``"new"``:
    the card exists but has never been introduced, and hearing the word is
    allowed to introduce it (subject to the shared introduction budget — see
    ``_allocate_intro_pool``).  ``last_review`` gates ONLY the once-per-day window
    (already-graded-today returns ``None`` for both due and ahead); a missing
    or legacy non-datetime ``last_review`` means "not graded today" and falls
    through to the dueness check.  The REVIEW-state dueness boundary matches
    ``count_review_due_collocations`` (``due_at <= end_of_day_utc``; a NULL
    ``due_at`` is not counted as due there, so it classifies as ``"ahead"``).
    """
    if rec is None:
        return None
    if rec.state == SRSState.NEW:
        return "new"
    if rec.state in (SRSState.LEARNING, SRSState.RELEARNING):
        return "learning"
    if rec.state != SRSState.REVIEW:
        return None
    lr = rec.last_review
    if isinstance(lr, datetime.datetime) and today_start <= lr.astimezone(datetime.UTC) < today_end:
        return None
    if rec.due_at is not None:
        due_str = rec.due_at.isoformat() if isinstance(rec.due_at, datetime.datetime) else str(rec.due_at)
        if due_str <= end_of_day_utc:
            return "due"
    return "ahead"


def _listen_deferred_reason(
    rec: DirectionState,
    grade_cls: str,
    today: datetime.date,
    horizon: int,
) -> Literal["known", "learning"] | None:
    """Why a listen defers this row instead of staging it by default.

    Returns ``"known"``, ``"learning"``, or ``None``. A deferred row is still
    fully visible in the preview — it is rated ``skip`` by default and staged
    only when the client sends an explicit rating for it. That is the opt-in
    the well-known population has always used; ``"learning"`` joins it under
    the 2026-08-04 decision, because the learning step exists to test recall at
    a specific interval and a listen is not that test (``hage`` was introduced
    at 10:09, due at 10:20, and rated "good" by a listen in between).

    ONE function, called by BOTH the preview (which stamps the reason) and both
    commit loops (which skip on it). The preview and the commit disagreeing
    about which rows a listen acts on is the ``6a5c718`` bug class; sharing the
    predicate makes them agree by construction instead of by review. The
    learning arm needs no horizon or due-date clause — state alone decides.
    """
    if grade_cls == "learning":
        return "learning"
    if grade_cls == "ahead" and is_well_known(rec, today, horizon):
        return "known"
    return None


def _release_review_kind(prev_dir: DirectionState) -> int | None:
    """Anki's review-ahead kind (3) for a card being released from the pending bucket.

    Decided from the card's dueness NOW, never from the ``grade_class`` stored on
    the pending row: a sync or a day rollover between staging and release can have
    moved ``due_at``, and a stale "due" would log a not-due grade as an ordinary
    review. The stored class is a record of stage time, not an input here.
    """
    start, end, end_of_day_utc = _listen_day_window()
    return 3 if _listen_grade_class(prev_dir, start, end, end_of_day_utc=end_of_day_utc) == "ahead" else None


def _created_in_window(
    created_at: str | None,
    today_start: datetime.datetime,
    today_end: datetime.datetime,
) -> bool:
    """Whether a collocation's ``created_at`` falls inside today's Anki day.

    Deliberately takes the window from ``_listen_day_window`` rather than
    consulting ``datetime.date.today()``: the Anki day rolls over at 4 AM local,
    so a card created at 01:00 belongs to *yesterday*'s allowance
    (``scripts/check_date_today.py`` enforces this repo-wide).

    ``collocations.created_at`` is SQLite ``datetime('now')`` format — UTC,
    space-separated, no timezone suffix — which ``fromisoformat`` accepts; a
    legacy or malformed value simply reads as "not today" rather than 500ing
    the preview.
    """
    if not created_at:
        return False
    try:
        stamp = datetime.datetime.fromisoformat(created_at)
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.UTC)
    return today_start <= stamp.astimezone(datetime.UTC) < today_end


def _allocate_intro_pool[T](
    new_state_rows: list[tuple[T, bool, str, bool]],
    creation_lemmas: list[str],
    budget: int,
    *,
    zipf: Callable[[str], float] | None = None,
    occurrences: Mapping[str, int],
) -> tuple[list[T], list[T], list[str], list[str]]:
    """Allocate ONE introduction budget across NEW-state rows AND creations.

    Returns ``(live_new, tail_new, ranked_creates, live_creates)``.
    ``new_state_rows`` is ``(payload, created_today, text, is_key_phrase)`` in
    lesson order; ``creation_lemmas`` is the untracked lemmas.

    Releasing a staged grade on a NEW-state card *introduces* it, so
    introductions and creations draw on one budget — otherwise a lesson with 40
    NEW-state words would introduce 40 cards in a single listen.

    **The two kinds compete on corpus frequency in a single ranked pool** (F-2,
    user decision 2026-08-04). The predecessor ``_allocate_new_state_budget``
    allocated NEW-state rows *first* and returned the remainder for creation,
    which with >= cap NEW-state rows is always 0 — so a creation candidate could
    never outrank a NEW-state row however common it was, and the frequency
    ranking shipped in ``9e42b83`` was dead code against real lesson data. That
    also means the old docstring's rule, "cards already in the deck get finished
    before more are added", is **deliberately abandoned**: frequency alone
    decides. The two kinds are not equal in cost (a create writes a collocation
    row and fetches media; a NEW-state row only stamps ``introduced_at``) and
    the decision is that this does not matter — do not reintroduce a weighting.

    Two rules survive the merge, both load-bearing:

    * A card **created today is free**. It already holds a slot via
      ``count_new_created_today``, which is subtracted when the budget is
      computed; charging it again would double-count the same card. Free rows
      never enter the pool and are live regardless of frequency.
    * **Key phrases are never frequency-ranked.** ``_rank_listen_candidates``
      has always held them ahead of the lemmas in lesson order — they are the
      lesson's pedagogical core. A multi-word phrase is OOV in wordfreq (zipf
      0.0), so ranking it in the pool would sink every key phrase below every
      word and they would stop being introduced at all. Charged key phrases
      lead the charged order instead; only NEW-state *words* join the pool.

    Ranking goes through ``_rank_listen_candidates`` rather than a second sort
    here, so the pool and the creation list cannot drift apart. Callers must
    pass the SAME ``zipf`` object to both call sites (resolve once per request
    via ``_zipf_for``) or preview and commit diverge — the 6a5c718 bug class.
    """
    free = [row for row in new_state_rows if row[1]]
    charged_kps = [row for row in new_state_rows if not row[1] and row[3]]
    charged_words = [row for row in new_state_rows if not row[1] and not row[3]]

    # One pool. A lemma cannot be both untracked (a create) and carded-and-NEW,
    # so text -> entry is a bijection and the rank order maps back cleanly.
    pooled: dict[str, tuple[bool, object]] = {row[2]: (True, row[0]) for row in charged_words}
    pooled.update({lemma: (False, lemma) for lemma in creation_lemmas})
    ranked_pool = _rank_listen_candidates([], list(pooled), occurrences, zipf=zipf)

    charged: list[tuple[bool, object]] = [(True, row[0]) for row in charged_kps]
    charged += [pooled[text] for _kind, text in ranked_pool]

    live_charged, tail_charged = charged[:budget], charged[budget:]
    live_new = [row[0] for row in free] + [value for is_new, value in live_charged if is_new]
    tail_new = [value for is_new, value in tail_charged if is_new]
    ranked_creates = [text for _kind, text in ranked_pool if not pooled[text][0]]
    live_creates = [value for is_new, value in live_charged if not is_new]
    return live_new, tail_new, ranked_creates, live_creates


def _bump_grade_clock(last_ms: int) -> tuple[datetime.datetime, int]:
    """Monotonic per-grade timestamp for a request that applies many grades.

    ``tt_revlog.id`` is a millisecond-epoch primary key and ``append_revlog``
    is INSERT OR IGNORE (sync-replay idempotency), so two grades landing in
    the same millisecond would silently drop the second row — losing FSRS
    replay history and under-counting ``count_reviews_completed_today``.
    Anki never faces this (one answer per user interaction).

    /listen was the original caller; under the pending-bucket model it stages
    rather than grades, so this has NO app-side caller right now — only its own
    unit test, which is why the coverage gate can't flag it. It is retained for
    the bulk pending-apply path ("Sync it"), which reinstates exactly the
    many-grades-in-one-request shape it was written for.
    """
    now = datetime.datetime.now(datetime.UTC)
    ms = int(now.timestamp() * 1000)
    if ms <= last_ms:
        ms = last_ms + 1
        now = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.UTC)
    return now, ms


def _rank_listen_candidates(
    key_phrases, lemmas, occurrences, *, zipf: Callable[[str], float] | None = None
) -> list[tuple[str, object]]:
    """Rank untracked creation candidates for a staged listen (plan D2).

    Key phrases first, in lesson order — they're the lesson's pedagogical
    core, never reordered by frequency. Then lemmas: when ``zipf`` is provided,
    by corpus frequency (wordfreq zipf) descending — the most useful word is
    created first, and OOV lemmas (zipf 0.0, typically proper nouns) sink to
    the end, so names stop becoming a lesson's first cards. Equal frequency
    breaks on in-lesson occurrence count descending, then first-appearance
    order (the stable sort keeps ``lemmas``' order). With ``zipf=None`` the
    sort is EXACTLY today's behavior — in-lesson occurrence count descending,
    stable ties — the fallback for a language with no ``wordfreq_lang``.
    Returns ``("kp", key_phrase)`` / ``("lemma", lemma)`` tuples.
    """
    if zipf is None:
        ranked_lemmas = sorted(lemmas, key=lambda lem: -occurrences.get(lem, 0))
    else:
        ranked_lemmas = sorted(lemmas, key=lambda lem: (-zipf(lem), -occurrences.get(lem, 0)))
    return [("kp", kp) for kp in key_phrases] + [("lemma", lem) for lem in ranked_lemmas]


def _zipf_for(language_code: str) -> Callable[[str], float] | None:
    """Return a lemma → wordfreq zipf-frequency callable for *language_code*.

    ``None`` when the language has no ``wordfreq_lang`` — callers fall back to
    occurrence-count ranking. ``import wordfreq`` lives inside this function
    (no module-level side effects per repo convention), so a language that
    never ranks pays no import cost.
    """
    wordfreq_lang = get_wordfreq_lang(language_code)
    if wordfreq_lang is None:
        return None
    import wordfreq

    return lambda lem: wordfreq.zipf_frequency(lem, wordfreq_lang)


class _LessonWords(NamedTuple):
    """Lemma-level analysis of a lesson's NATURAL_SPEED section.

    Shared by /listen and /lesson/{id}/review-queue so their word-set
    derivations cannot drift (plan Step 4). All maps are keyed by lemma;
    insertion order of ``first_sentence`` is first-appearance order and
    doubles as the ordered set of unique lemmas.
    """

    occurrences: Counter[str]
    first_sentence: dict[str, str]
    surfaces: dict[str, set[str]]
    first_surface: dict[str, str]
    surface_upos: dict[str, str]


def _analyze_lesson_words(lesson, db) -> _LessonWords:
    """Run the lemmatizer over the lesson's L2 NATURAL_SPEED phrases.

    Blocking (classla under the hood) — callers offload via
    ``anyio.to_thread.run_sync``. Cheap after the first listen thanks to
    ``db_lemma_cache``. ``surface_upos`` maps casefolded surface → UPOS for
    POS-first function-word detection: empty under LowercaseLemmatizer (the
    curated include-list is then the only signal, legacy behavior); classla
    supplies AUX/ADP/PRON/… and catches the whole biti paradigm (ste/smo/so)
    without enumerating surfaces.
    """
    from app.models.lesson import SectionType

    words = _LessonWords(Counter(), {}, {}, {}, {})
    natural_speed = next(
        (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
        None,
    )
    if natural_speed is None:
        return words
    lemmatizer = get_lemmatizer(lesson.language_code)
    model_version = model_version_for(lemmatizer)
    for phrase in natural_speed.phrases:
        if phrase.language_code != lesson.language_code:
            continue
        surfaces = tokenize(phrase.text)
        phrase_lemmas = lemmatize_surfaces_in_context(
            surfaces, phrase.text, lemmatizer, lesson.language_code, db, model_version
        )
        for ta in analyze_sentence_cached(db, lemmatizer, phrase.text, lesson.language_code, model_version):
            words.surface_upos.setdefault(ta.surface.casefold(), ta.upos)
        previous_surface = ""
        for surface, lemma in zip(surfaces, phrase_lemmas, strict=True):
            # "i går" is *yesterday*; the lemmatizer reads the second token as a
            # standalone NOUN and TT would card it as the verb `gå`. Suppress this
            # occurrence only — the word is still cardable where it stands alone.
            if is_trapped_occurrence(previous_surface, surface, lesson.language_code):
                previous_surface = surface
                continue
            previous_surface = surface
            words.occurrences[lemma] += 1
            if lemma not in words.first_sentence:
                words.first_sentence[lemma] = phrase.text
                words.first_surface[lemma] = surface
            words.surfaces.setdefault(lemma, set()).add(surface)
    return words


def _resolve_card_for_lemma(
    db,
    lemma: str,
    surfaces: set[str],
    variant_index: dict[str, tuple[int, SRSItem]] | None = None,
):
    """Resolve the tracked card for a lemma, or None if untracked.

    Falls back to surface-keyed rows (e.g. greeting "dobrodošli", whose
    dictionary lemma "dobrodošel" has no card) so /listen grades the surface
    card instead of spawning a duplicate — and the lesson review queue
    resolves the same card /listen would.

    When *variant_index* is provided (built once per request by
    ``_build_variant_index``), also resolves comma-variant card fronts
    (Norwegian ``mot, imot``) whose surface matches a variant spelling.
    """
    res = db.get_collocation_by_lemma_with_id(lemma)
    if res is None:
        for s in surfaces:
            if s.lower() != lemma:
                res = db.get_collocation_by_lemma_with_id(s.lower())
                if res is not None:
                    break
    if res is None and variant_index:
        # Index keys are casefolded (_build_variant_index) — match that, or a
        # capitalized lemma from the Norwegian lemmatizer silently misses.
        res = variant_index.get(lemma.casefold())
    return res


@router.post("/listen", status_code=200, response_model=ListenResponse)
async def mark_lesson_listened(body: ListenRequest, request: Request):
    store = request.state.content_store
    lesson = store.get_lesson(body.lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    db = request.state.srs_db
    llm = getattr(request.app.state, "llm", None)
    # One shared set across this request so two new words don't pick the same image.
    used_image_urls: set[str] = set()

    # ── Word-level tracking from NATURAL_SPEED section ──────────────────
    from app.models.lesson import extract_sentence_translations_from_translated

    token_glosses: dict[str, str] = lesson.generation_metadata.get("token_glosses", {})
    sentence_translations: dict[str, str] = lesson.generation_metadata.get("sentence_translations", {})
    # Backfill path: pre-Layer-N lessons have no `sentence_translations` in
    # metadata. Recover from the TRANSLATED section so old lessons can still
    # populate cloze cards' Back Extra. First-occurrence wins on the merge.
    derived_st = extract_sentence_translations_from_translated(lesson)
    for k, v in derived_st.items():
        sentence_translations.setdefault(k, v)
    # VERB base-form glosses (Part A): lessons generated before it existed have
    # no key — treat as an empty map, so _resolve_gloss_translation falls back
    # to the aligner for them. Never a crash.
    verb_base_glosses: dict[str, str] = lesson.generation_metadata.get("verb_base_glosses", {})

    # Lemma analysis shared verbatim with /lesson/{id}/review-queue — the
    # blocking (classla) pass runs on a worker thread so the event loop
    # doesn't stall; the await means no concurrent access to the maps.
    words = await anyio.to_thread.run_sync(_analyze_lesson_words, lesson, db)
    lemma_occurrences = words.occurrences
    lemma_to_sentence = words.first_sentence
    lemma_to_surfaces = words.surfaces
    lemma_to_first_surface = words.first_surface
    surface_to_upos = words.surface_upos
    # Corpus-frequency ranker for creation candidates — None when the language
    # has no wordfreq code, which falls back to in-lesson occurrence ranking.
    # Resolved once per request; the preview passes the SAME callable so the
    # two orderings cannot drift (the 6a5c718 bug class).
    zipf = _zipf_for(lesson.language_code)

    # Card-less ignore list: lemmas the user explicitly opted out of.
    # Fetched once per request; both-sides casefolded so a capitalized stored
    # entry (ex. "Hansen") still matches the lowercase lemma the lemmatizer emits.
    ignored = {lem.lower() for lem in db.get_ignored_lemmas(lesson.language_code)}

    # Today window (Anki-day rollover, not local midnight) — see _listen_day_window.
    today = anki_today()
    today_start, today_end, end_of_day_utc = _listen_day_window()

    # This listen IS the lesson's current assessment, not an addition to the
    # last one. Reset the bucket first, then stage fresh below: without this
    # "skip" (a bare `continue`) left the previous listen's autograde queued,
    # so re-listening and skipping everything still offered the old rows in
    # "Check your work". Lesson-scoped — another lesson's rows are not this
    # listen's to discard.
    db.clear_pending_grades_for_lesson(body.lesson_id)

    created_count = 0
    staged_count = 0

    # ── Confirmed grades: applied here, not staged ──────────────────────
    # A grade the user picked in the preview is a review they performed, so
    # making them answer it again in "Check your work" asks the same question
    # twice. Only the auto-rated remainder goes to the pending bucket.
    confirmed_words = set(body.confirmed_words)
    confirmed_kps = set(body.confirmed_kps)
    # Opt-ins past the daily new-card cap — one deliberate, per-row action per
    # over-budget row (Anki's own "Increase today's new card limit"). Kept
    # separate from the ratings maps: presence in `word_ratings` is overloaded
    # (absent = default "good" = create), so it cannot carry the opt-in signal.
    over_cap_words = set(body.over_cap_words)
    over_cap_kps = set(body.over_cap_kps)
    # Built on first use: most listens confirm nothing, and the load-balancer
    # histogram is not free. The shared balancer + monotonic grade clock across
    # the batch mirror commit-pending exactly.
    grade_ctx: dict = {
        "applied": 0,
        "now": None,
        "last_ms": 0,
        "params": None,
        "learn_steps": None,
        "relearn_steps": None,
        "col_crt": None,
        "balancer": None,
    }

    def _apply_confirmed(collocation_id: int, rating_str: str) -> None:
        if grade_ctx["params"] is None:
            grade_ctx["params"], _ = resolve_fsrs_params(db)
            grade_ctx["learn_steps"], _ = resolve_learning_steps(db)
            grade_ctx["relearn_steps"], _ = resolve_relearning_steps(db)
            grade_ctx["col_crt"] = resolve_col_crt(db)
            grade_ctx["balancer"] = build_live_load_balancer(
                db, now=datetime.datetime.now(datetime.UTC), col_crt=grade_ctx["col_crt"]
            )
        now, last_ms = _apply_grade_now(
            db,
            collocation_id,
            Direction.RECOGNITION,
            rating_str,
            fsrs_params=grade_ctx["params"],
            col_crt=grade_ctx["col_crt"],
            balancer=grade_ctx["balancer"],
            last_grade_ms=grade_ctx["last_ms"],
            learn_steps=grade_ctx["learn_steps"],
            relearn_steps=grade_ctx["relearn_steps"],
        )
        grade_ctx["now"] = now
        grade_ctx["last_ms"] = last_ms
        grade_ctx["applied"] += 1
        # The card has just been graded for real, so any pending row for it is
        # stale — releasing it later would grade the same assessment a SECOND
        # time (another schedule() off the advanced state, another revlog row).
        # Not lesson-scoped: a row another lesson staged is equally stale now.
        # Mirrors drill_feedback's release, which has always cleared here.
        db.clear_pending_grade(collocation_id, Direction.RECOGNITION.value)

    # ── Per-listen creation budget (plan D1) ────────────────────────────
    # One listen queues at most one Anki-day's worth of new cards, net of
    # today's introductions and of still-NEW cards created earlier today
    # (a same-day re-listen therefore creates ~0 more; refills next Anki
    # day). Deliberately does NOT subtract the whole-deck NEW backlog —
    # the queue engine's new-quota remains the real flow limiter.
    new_cap, _ = resolve_daily_new_cap(db)
    intro_budget = max(0, new_cap - db.count_new_introduced_today(today) - db.count_new_created_today(today))
    # ((collocation_id, rating, is_confirmed, opted_past_cap), created_today)
    # for every NEW-state candidate, in the same candidate order the preview
    # uses. The over-cap flag is resolved AT APPEND TIME — the payload carries
    # no text, and each loop already knows whether it is looking at a word or a
    # key phrase, so matching there makes it structurally impossible to honour a
    # key phrase named in `over_cap_words`.
    new_state_pending: list[tuple[tuple[int, str, bool, bool], bool, str, bool]] = []
    lemma_candidates: list[str] = []
    # Lemmas the user rated "skip" this listen. They stay in `lemma_candidates`
    # (so they hold their rank position) but the staged-creation loop below
    # consumes their slot instead of creating a card — a skip is "not today",
    # not the ignore list, so the lemma is still offered on the next listen.
    skipped_lemmas: set[str] = set()

    # Build variant index once for this listen request — mirrors the transcript's
    # _build_variant_index usage so /listen resolves the same cards the transcript
    # shows as tracked.
    variant_index = _build_variant_index(db, lesson.language_code)

    for lemma in lemma_to_sentence:
        # Cloze cards are always on, for every language (no feature flag, no
        # language gate — see ~/.claude/plans/word-learning-state-machine.md
        # Phase 1). Whether a cloze is actually created is capability-driven:
        # `is_func` is only true where a function-word config exists for the
        # language, so non-Slovene content words still fall through to vocab.
        # POS-first: each surface carries its UPOS (when an analyzer is present).
        is_func = is_function_word_for(
            lemma, lemma_to_surfaces.get(lemma, set()), lesson.language_code, surface_to_upos
        )

        res = _resolve_card_for_lemma(db, lemma, lemma_to_surfaces.get(lemma, set()), variant_index)
        existing_id, existing = res if res is not None else (None, None)

        if existing is None:
            # ── Untracked → staged-creation candidate (created below, budget permitting) ──
            # Clozes-only verbs (e.g. biti) get no base card — only per-form
            # conjugation clozes created by click. Skip entirely.
            if is_func and is_clozes_only_verb(lemma, lesson.language_code):
                continue
            # Explicitly ignored lemmas: card-less ignore list (see
            # db.get_ignored_lemmas). Casefolded to match the lemmatizer output.
            if lemma.lower() in ignored:
                continue
            # "skip" on an untracked lemma consumes its rank slot rather than
            # freeing it for the next-ranked lemma to be promoted into.
            if body.word_ratings.get(lemma, "good") == "skip":
                skipped_lemmas.add(lemma)
            lemma_candidates.append(lemma)
        else:
            # ── Existing row — skip cloze, grade recognition for eligible vocab ──
            if existing.syntactic_unit.card_type == "cloze":
                # Backfill empty sentence_translation on existing cloze rows so
                # the user's pre-existing cards can still surface the English
                # sentence in Anki / TT review. Mark dirty so sync_push picks it
                # up and rewrites Back Extra.
                if not existing.syntactic_unit.source_sentence_translation:
                    # Translations are keyed by the raw sentence; the stored
                    # source_sentence may now be pre-clozed (Phase 2b), so use the raw
                    # sentence from this lesson (lemma is always present in the loop).
                    sent = lemma_to_sentence.get(lemma, "")
                    new_st = sentence_translations.get(sent, "")
                    if new_st:
                        db.set_sentence_translation_dirty(existing.guid, new_st)
                # Try to generate missing audio for existing cloze rows.
                # Use the raw sentence (lemma_to_sentence) — the stored
                # source_sentence contains {{c1::…}} markup under Phase-2b.
                sent = lemma_to_sentence.get(lemma, "")
                if sent and not db.get_sentence_audio_filename(existing_id):
                    try:
                        await synthesize_cloze_audios(
                            db,
                            existing_id,
                            sent,
                            lemma_to_first_surface.get(lemma, lemma),
                            voice=get_tts_voice(lesson.language_code),
                        )
                    except Exception:
                        _logger.warning("Failed to synthesize cloze audio for %r", lemma)
                continue

            rec = existing.directions.get(Direction.RECOGNITION)
            if rec is None:
                continue

            grade_cls = _listen_grade_class(rec, today_start, today_end, end_of_day_utc=end_of_day_utc)
            if grade_cls is None:
                continue
            # Deferred opt-in: a known or learning row is never staged silently.
            # Membership in word_ratings is the opt-in — an explicit "skip" is
            # still a skip, handled by the shared rating check below.
            if (
                _listen_deferred_reason(rec, grade_cls, today, settings.listen_due_horizon_days) is not None
                and lemma not in body.word_ratings
            ):
                continue
            rating_str = body.word_ratings.get(lemma, "good")
            listen_coll_id = db.get_collocation_id_by_guid(existing.guid)
            assert listen_coll_id is not None
            if grade_cls == "new":
                # Deferred to the shared introduction budget below. Allocation
                # is rating-independent — the preview has no ratings to consult,
                # so consulting them here would let the two sides disagree about
                # which rows are live (the 6a5c718 preview↔commit bug class).
                # The skip filter is applied when the allocated rows are staged.
                new_state_pending.append(
                    (
                        (listen_coll_id, rating_str, lemma in confirmed_words, lemma in over_cap_words),
                        _created_in_window(db.get_created_at_by_guid(existing.guid), today_start, today_end),
                        lemma,
                        False,
                    )
                )
                continue
            if rating_str == "skip":
                continue
            if lemma in confirmed_words:
                _apply_confirmed(listen_coll_id, rating_str)
            else:
                db.stage_pending_grade(
                    body.lesson_id,
                    listen_coll_id,
                    Direction.RECOGNITION.value,
                    rating_str,
                    grade_cls,
                )
                staged_count += 1

    # ── Key phrase staging (existing cards only; creation deferred) ─────
    for kp in lesson.key_phrases:
        if kp.phrase.lower() in ignored:
            continue
        existing = db.get_collocation(kp.phrase)
        if existing is None:
            continue
        if existing.syntactic_unit.card_type == "cloze":
            continue
        rec = existing.directions.get(Direction.RECOGNITION)
        if rec is None:
            continue
        grade_cls = _listen_grade_class(rec, today_start, today_end, end_of_day_utc=end_of_day_utc)
        if grade_cls is None:
            continue
        # Deferred opt-in — the SAME predicate as the word loop above. This
        # loop having its own hand-rolled copy is what 9af858e recorded going
        # wrong; sharing the function is what stops it recurring.
        if (
            _listen_deferred_reason(rec, grade_cls, today, settings.listen_due_horizon_days) is not None
            and kp.phrase not in body.kp_ratings
        ):
            continue
        rating_str = body.kp_ratings.get(kp.phrase, "good")
        kp_coll_id = db.get_collocation_id_by_guid(existing.guid)
        assert kp_coll_id is not None
        if grade_cls == "new":
            # Same shared budget as NEW-state words — mirrors the preview.
            new_state_pending.append(
                (
                    (kp_coll_id, rating_str, kp.phrase in confirmed_kps, kp.phrase in over_cap_kps),
                    _created_in_window(db.get_created_at_by_guid(existing.guid), today_start, today_end),
                    kp.phrase,
                    True,
                )
            )
            continue
        if rating_str == "skip":
            continue
        if kp.phrase in confirmed_kps:
            _apply_confirmed(kp_coll_id, rating_str)
        else:
            db.stage_pending_grade(
                body.lesson_id,
                kp_coll_id,
                Direction.RECOGNITION.value,
                rating_str,
                grade_cls,
            )
            staged_count += 1

    # ── Staged creation over ranked candidates, truncated to budget (D2/D3) ──
    # No persisted cursor: each listen recomputes lesson-word-set minus tracked
    # cards and takes the top of the ranking; cards created by this listen are
    # "existing" for the next one.
    # ── One introduction budget, one ranking pool ─────────────────────────
    # NEW-state rows and creation candidates compete on corpus frequency
    # together (F-2). Mirrors get_listen_preview exactly: same call, same
    # arguments, the same `zipf` object resolved once per request.
    live_new, tail_new, ranked, live_creates = _allocate_intro_pool(
        new_state_pending, lemma_candidates, intro_budget, zipf=zipf, occurrences=lemma_occurrences
    )
    # Opting in a tail NEW-state row stages it exactly as a live one is — the
    # two lists differ only in that the allocation cut `tail_new` at the shared
    # introduction budget. The live-create set is deliberately NOT re-reduced
    # for opt-ins: the allocation already priced these rows, and shrinking it
    # here would let the opt-in slide the live/tail divider (promote-on-uncheck,
    # rejected 2026-07-31).
    opted_in_tail = [row for row in tail_new if row[3]]
    for _coll_id, _rating, _confirmed, _over_cap in live_new + opted_in_tail:
        if _rating == "skip":
            continue
        if _confirmed:
            _apply_confirmed(_coll_id, _rating)
        else:
            db.stage_pending_grade(body.lesson_id, _coll_id, Direction.RECOGNITION.value, _rating, "new")
            staged_count += 1

    live_create_set = set(live_creates)
    # Resolved once per request: the lemma-plausibility predicate (or None —
    # "cannot tell", keep the lemma as-is).
    lemma_plausible = get_lemma_plausible(lesson.language_code)
    for cand in ranked:
        # Over-budget rows are skipped (a gated `continue`, not a `break`) so
        # an opted-in tail lemma ranked BELOW the first over-budget row is still
        # reached. A name is honoured only here, while iterating real ranked
        # candidates — an unknown or in-budget lemma matches nothing and
        # creates nothing, structurally (no standalone validation pass).
        if cand not in live_create_set and cand not in over_cap_words:
            continue
        if cand in skipped_lemmas:
            continue  # slot consumed, nothing created — no promotion
        lemma = cand
        is_func = is_function_word_for(
            lemma, lemma_to_surfaces.get(lemma, set()), lesson.language_code, surface_to_upos
        )
        upos_for_lemma = None
        if not is_func:
            upos_for_lemma = next(
                (
                    surface_to_upos.get(s.casefold())
                    for s in lemma_to_surfaces.get(lemma, set())
                    if s.casefold() in surface_to_upos
                ),
                None,
            )
        sent = lemma_to_sentence.get(lemma, "")
        # Cloze rows blank the surface as it appeared, not the dictionary lemma:
        # the lemmatizer may map an inflected surface to a different lemma (classla
        # "sem" → "biti") that isn't in the sentence. Store the cloze pre-built;
        # sync's idempotent make_cloze_text passes it through. (Phase 2b.)
        stored_sentence = make_cloze_text(lemma_to_first_surface.get(lemma, lemma), sent) if is_func else sent
        # Stanza can return a truncated fragment as the lemma (`trøtt` → `trø`).
        # When the fragment fails the language's plausibility check, key the card
        # on the surface as it appeared instead — the headword stays a real word,
        # and the surface key means the next listen resolves the same card rather
        # than spawning a duplicate (a `trø` lookup falls back to the `trøtt` row).
        first_surface = lemma_to_first_surface.get(lemma, lemma)
        card_lemma = lemma
        if not is_func and lemma_plausible is not None and not lemma_plausible(first_surface, lemma):
            card_lemma = first_surface.casefold()
        unit = SyntacticUnit(
            text=format_vocab_headword(card_lemma, upos_for_lemma, lesson.language_code) if not is_func else lemma,
            translation=_resolve_gloss_translation(
                lemma,
                token_glosses,
                lemma_to_surfaces.get(lemma, set()),
                first_surface,
                language_code=lesson.language_code,
                surface_upos=surface_to_upos,
                verb_base_glosses=verb_base_glosses,
            ),
            word_count=1,
            difficulty=1,
            source="llm",
            lemma=card_lemma,
            card_type="cloze" if is_func else "vocab",
            source_sentence=stored_sentence,
            source_sentence_translation=sentence_translations.get(sent, ""),
        )
        db.add_collocation(unit, language_code=lesson.language_code)
        if is_func:
            coll = db.get_collocation_by_lemma_with_id(card_lemma)
            new_id, _ = coll
            try:
                await synthesize_cloze_audios(
                    db,
                    new_id,
                    sent,
                    lemma_to_first_surface.get(lemma, lemma),
                    voice=get_tts_voice(lesson.language_code),
                )
            except Exception:
                _logger.warning("Failed to synthesize cloze audio for %r", lemma)
        else:
            new_id, _ = db.get_collocation_by_lemma_with_id(card_lemma)
            await _generate_add_time_media(
                db,
                llm,
                new_id,
                unit,
                language_code=lesson.language_code,
                used_image_urls=used_image_urls,
                media_word=card_lemma,
            )
        created_count += 1
    remaining_candidates = len(ranked) - created_count

    # Server-side listened state (TT-only, never syncs): one row per listen.
    db.record_listen(body.lesson_id)

    # Anki parity: grading advances the learning cutoff. A batch of confirmed
    # grades is still one grade event, so advance once at the end.
    if grade_ctx["applied"]:
        advance_learning_cutoff(db, grade_ctx["now"])

    return {
        "status": "ok",
        "staged": staged_count,
        "applied": grade_ctx["applied"],
        "created": created_count,
        "remaining_candidates": remaining_candidates,
        "listen_count": db.count_listens(body.lesson_id),
    }


@router.get("/listens", status_code=200, response_model=ListensResponse)
async def get_listens(request: Request):
    db = request.state.srs_db
    return {"lessons": db.get_listened_lessons()}


@router.post("/listens/import", status_code=200, response_model=ImportListensResponse)
async def import_listens(body: ImportListensRequest, request: Request):
    store = request.state.content_store
    db = request.state.srs_db
    seen: set[str] = set()
    imported: list[str] = []
    already_present: list[str] = []
    unknown: list[str] = []
    for lesson_id in body.lesson_ids:
        if lesson_id in seen:
            continue
        seen.add(lesson_id)
        if store.get_lesson(lesson_id) is None:
            unknown.append(lesson_id)
        elif db.has_listen(lesson_id):
            already_present.append(lesson_id)
        else:
            db.record_listen(lesson_id, source="import")
            imported.append(lesson_id)
    return {"imported": imported, "already_present": already_present, "unknown": unknown}


def _has_unreviewed_listen(latest_listen: str | None, latest_review: str | None) -> bool:
    """True when there is a listen strictly newer than the last completed review.

    Gates the lesson page's "Check your work" link to one-shot-per-listen.
    ISO-8601 UTC timestamps compare correctly as strings.
    """
    return latest_listen is not None and (latest_review is None or latest_listen > latest_review)


@router.get(
    "/lesson/{lesson_id}/review-queue",
    status_code=200,
    response_model=LessonReviewQueueResponse,
    # The nested DirectionStateResponse omits `left` when None
    # (srs.py::_direction_to_dict) — a plain response_model would put
    # "left": null back into every NEW/REVIEW direction.
    response_model_exclude_unset=True,
)
async def get_lesson_review_queue(lesson_id: str, request: Request, response: Response) -> dict:
    """Lesson-scoped "Check your work" queue: exactly the listen's autograded cards.

    Items share ``_queue_item_to_dict``'s shape with /review-queue; grading a
    served item goes through the normal per-item feedback endpoint (an Again
    on an auto-Good'ed card is an ordinary same-day lapse). Strictly
    read-only w.r.t. parity state: no learning-cutoff advance, no
    session_main_queue write, no unbury sweep, no queue-engine involvement —
    the frozen main-queue order must survive this endpoint unchanged (pinned
    by the parity-guard test).

    Inclusion is now exactly this lesson's ``pending_listen_grades`` rows, so
    the served queue and what "Sync it" (``commit-pending``) would release are
    the same set by construction — they are read from the same query. Scoping
    narrowed here 2026-07-27; the endpoint used to be a lesson-scoped *study*
    queue (D6 buckets: learning, tracked NEW in D2 rank order, REVIEW
    touched-today or due). After the confirmed/staged split that produced two
    visible wrongs: cards the user had just confirmed in the preview came back
    (applied immediately, so no pending row, but re-admitted by "touched
    today" — the double-question the split existed to remove), and due *cloze*
    cards appeared that a listen can never autograde, since staging is
    RECOGNITION-only and cloze is production-only. Everything dropped stays
    reachable from the main queue: with no pending row, the Layer 81 exclusion
    does not hold it back.

    Consequences worth knowing: a NEW card CAN appear here (since 2026-08
    ``_listen_grade_class`` returns ``"new"`` for a NEW-state direction, so a
    listen stages carded-but-never-introduced words), but only up to the shared
    introduction budget — releasing such a row *introduces* the card, spending
    Anki's daily new-card allowance, so ``_allocate_intro_pool`` caps how
    many a single listen can arm. A cloze still can never appear: staging is
    RECOGNITION-only and cloze is production-only. Release by any path —
    per-card grade, ``commit-pending``, or an Anki-side grade arriving via
    ``sync_pull`` — clears the row, which is what drops the card from this
    queue; no separate "graded since the arming listen" filter is needed.
    """
    response.headers["Cache-Control"] = "no-store"
    store = request.state.content_store
    lesson = store.get_lesson(lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    db = request.state.srs_db

    # Learning/relearning first, then by dueness — the surviving half of the old
    # bucket order (the NEW bucket is unreachable now, see the docstring).
    learning: list[tuple[datetime.datetime, int, SRSItem, Direction, str]] = []
    review: list[tuple[datetime.datetime, int, SRSItem, Direction, str]] = []
    for pending in db.get_pending_grades(lesson_id):
        rid = pending["collocation_id"]
        got = db.get_collocation_by_id(rid)
        if got is None:
            # Orphaned row (card deleted after staging). commit-pending clears
            # these as it goes; the queue just skips them rather than 500ing.
            continue
        _, item, _ = got
        direction = Direction(pending["direction"])
        ds = item.directions.get(direction)
        if ds is None:
            # Single-template row (no recognition direction after v15→v16).
            # Nothing to serve — skip rather than 500 the whole page.
            continue
        bucket = learning if ds.state in (SRSState.LEARNING, SRSState.RELEARNING) else review
        bucket.append((ds.due_at, rid, item, direction, pending["rating"]))

    learning.sort(key=lambda t: (t[0], t[1]))
    review.sort(key=lambda t: (t[0], t[1]))

    ambiguous = db.get_ambiguous_surfaces(lesson.language_code)
    has_unreviewed_listen = _has_unreviewed_listen(db.latest_listen_at(lesson_id), db.latest_review_at(lesson_id))
    queue = []
    for _, rid, item, d, rating in learning + review:
        entry = _queue_item_to_dict(rid, item, lesson.language_code, d, db, ambiguous)
        # Provisional rating per card, so the UI can pre-fill what the listen staged.
        entry["pending_rating"] = rating
        queue.append(entry)
    return {"queue": queue, "has_unreviewed_listen": has_unreviewed_listen}


@router.post("/lesson/{lesson_id}/reviewed", status_code=200, response_model=MarkLessonReviewedResponse)
async def mark_lesson_reviewed(lesson_id: str, request: Request) -> dict:
    """Record completion of a lesson-scoped 'Check your work' review.

    Gates the lesson page's "Check your work" link to one-shot-per-listen:
    a completed review clears the link until the next listen re-arms it
    (has_unreviewed_listen on the review-queue response). TT-only; touches
    no parity/FSRS state.
    """
    store = request.state.content_store
    if store.get_lesson(lesson_id) is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    db = request.state.srs_db
    db.record_review(lesson_id)
    return {"ok": True}


def _apply_grade_now(
    db,
    collocation_id: int,
    dir_enum: Direction,
    rating_str: str,
    *,
    fsrs_params,
    col_crt,
    balancer,
    last_grade_ms: int,
    learn_steps,
    relearn_steps,
) -> tuple[datetime.datetime, int]:
    """Apply one grade for real: ``schedule`` → revlog → ``dirty_fsrs``.

    The single place a listen-originated grade is actually applied, shared by
    the pending-release paths (``commit-pending``, per-card review) and by the
    listen's own confirmed grades. Sharing it is the point: a grade the user
    picked in the preview must land byte-identically to the same grade released
    later, or the two routes drift (the b0a4b8a inline-a-phase-subset class).

    Returns ``(now, last_grade_ms)`` so a batch can keep the monotonic grade
    clock: ``tt_revlog.id`` is a millisecond PK and ``append_revlog`` is INSERT
    OR IGNORE, so two grades landing in the same millisecond silently drop one.
    """
    _, item, _ = db.get_collocation_by_id(collocation_id)
    prev_dir = item.directions[dir_enum]
    rating = _WORD_RATING_MAP[rating_str]
    now, last_grade_ms = _bump_grade_clock(last_grade_ms)
    updated = schedule(
        item,
        rating,
        direction=dir_enum,
        params=fsrs_params,
        now=now,
        col_crt=col_crt,
        load_balancer=balancer,
        learn_steps=learn_steps,
        relearn_steps=relearn_steps,
    )
    db.update_direction_by_id(collocation_id, dir_enum, updated.directions[dir_enum])
    row = build_revlog_row(
        collocation_id,
        dir_enum,
        prev_dir,
        updated.directions[dir_enum],
        rating,
        0,
        now=now,
        col_crt=col_crt,
        review_kind=_release_review_kind(prev_dir),
    )
    db.append_revlog(row)
    _balancer_add(balancer, card_id=prev_dir.anki_card_id, note_id=item.anki_note_id, interval=row.interval)
    return now, last_grade_ms


@router.post("/lesson/{lesson_id}/commit-pending", status_code=200)
async def commit_pending_grades(lesson_id: str, request: Request) -> CommitPendingResponse:
    """Bulk "Sync it": release every staged grade for a lesson without reviewing it.

    Applies each pending row at its provisional rating through the SAME path a
    per-card release takes (``schedule`` → revlog → ``dirty_fsrs``), then clears
    it. Afterwards these are ordinary dirty grades and the next normal "Sync to
    AnkiWeb" pushes them — this endpoint deliberately does NOT sync by itself.

    One shared balancer and a monotonic grade clock across the batch, exactly as
    the pre-staging /listen grade loop had: ``tt_revlog.id`` is a millisecond PK
    and ``append_revlog`` is INSERT OR IGNORE, so two grades in the same
    millisecond would silently drop one.
    """
    store = request.state.content_store
    if store.get_lesson(lesson_id) is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    db = request.state.srs_db

    fsrs_params, _ = resolve_fsrs_params(db)
    learn_steps, _ = resolve_learning_steps(db)
    relearn_steps, _ = resolve_relearning_steps(db)
    col_crt = resolve_col_crt(db)
    now = datetime.datetime.now(datetime.UTC)
    balancer = build_live_load_balancer(db, now=now, col_crt=col_crt)
    last_grade_ms = 0
    applied = 0

    for pending in db.get_pending_grades(lesson_id):
        collocation_id = pending["collocation_id"]
        dir_enum = Direction(pending["direction"])
        if db.get_collocation_by_id(collocation_id) is None:
            # Orphaned row (the card was deleted after staging). Nothing to
            # apply, but the row must still go or it lingers forever.
            db.clear_pending_grade(collocation_id, dir_enum.value)
            continue
        now, last_grade_ms = _apply_grade_now(
            db,
            collocation_id,
            dir_enum,
            pending["rating"],
            fsrs_params=fsrs_params,
            col_crt=col_crt,
            balancer=balancer,
            last_grade_ms=last_grade_ms,
            learn_steps=learn_steps,
            relearn_steps=relearn_steps,
        )
        db.clear_pending_grade(collocation_id, dir_enum.value)
        applied += 1

    # Anki parity: grading advances the learning cutoff. A batch is still a grade
    # event, so advance once at the end rather than per card.
    if applied:
        advance_learning_cutoff(db, now)
    return {"status": "ok", "applied": applied}


def _tracked_sort_key(c: dict) -> tuple[int, str, float]:
    """Within-group order for the listen preview's tracked rows (F-4).

    Group rank stays primary. The secondary key differs by group because the
    two groups carry different information in ``due_at``:

    * **learning / relearning** (rank 0) — ``due_at`` at FULL precision, the
      ripening order. These cards come up in minutes and carry real sub-day
      times, so truncating them to a day would order ripening cards by
      something else entirely. Their mastery is uninformative anyway
      (``component_mastery`` pins every in-steps component at a fixed floor).
    * **due / ahead** (ranks 1 and 2) — the due DAY, then mastery ascending
      (least-known first), which is what the docstring always promised and what
      the red→green colour ramp is showing. Day-truncation costs nothing here
      because a REVIEW ``due_at`` is date-encoded at 04:00 UTC already
      (``rollover.py::due_at_rollover_utc``) — which is precisely why the old
      ``(rank, due_at)`` key left all same-day cards EXACTLY tied and fell back
      to lesson-appearance order, i.e. to no order at all.

    NEW-state rows (rank -1) carry ``due_at: None`` and ``progress: None``, so
    they all tie and the stable sort preserves the introduction pool's
    frequency ranking (F-2). Do not give them a tie-break — the pool order is
    the contract ``mark_lesson_listened`` mirrors.
    """
    rank = c["_group_rank"]
    due = c["due_at"] or "￿"
    if rank <= 0:
        return (rank, due, 0.0)
    progress = c["progress"]
    return (rank, due[:10], progress if progress is not None else 0.0)


@router.get("/lesson/{lesson_id}/listen-preview", status_code=200)
async def get_listen_preview(lesson_id: str, request: Request) -> ListenPreviewResponse:
    """Read-only classification of what a listen would stage for a lesson.

    The ``create`` rows are every untracked lemma, flagged with ``will_create``
    against the same per-listen introduction budget ``mark_lesson_listened``
    uses (``resolve_daily_new_cap`` minus today's introductions and still-NEW
    same-day creations): rows within budget are True (live), the over-budget
    tail is False. Without the flag the preview and the commit disagree — a
    same-day re-listen has ~0 budget left and would create nothing even though
    the preview showed every untracked lemma as checked.

    Creations do **not** get their own budget. ``_allocate_intro_pool`` ranks
    them in ONE pool with the NEW-state rows, by corpus frequency across both
    kinds (F-2), so a common untracked lemma can outrank a rarer card that is
    already in the deck. Cards created earlier today are free and always live;
    NEW-state key phrases lead the charged order and are never
    frequency-ranked (a phrase is OOV, so ranking it would sink every key
    phrase below every word).

    Tracked word/kp candidates follow the creations, grouped (new → learning →
    due → ahead) and then ordered by ``_tracked_sort_key``: ripening time for
    the learning group, due DAY then mastery ascending (least-known first) for
    the two review groups. Strictly read-only — no pending
    writes, no card creation, no side effects beyond the
    ``_analyze_lesson_words`` lemma-cache warm-up. The response informs the
    frontend preview modal without committing anything.

    Array order (frontend contract): create rows come first, in rank order,
    live rows (``will_create`` True) before tail rows (``will_create`` False);
    then the tracked rows sorted as today. Do not reorder or interleave. Live
    creates stay a prefix of the create list even though the pool interleaves
    the two kinds, because the live cut is a prefix of the ranked pool and the
    creates keep their relative order inside it. The preview and commit agree
    because both make the SAME ``_allocate_intro_pool`` call with the same
    ``zipf`` callable (resolved once per request via ``_zipf_for``), so the
    first N live create rows are exactly what ``mark_lesson_listened`` will
    create. Un-checking one does NOT promote the next-ranked tail row: a skip
    consumes its slot server-side (``1535071``), which is why ``will_create``
    is a static flag on the response rather than something the frontend
    re-derives from the current ratings.
    """
    store = request.state.content_store
    lesson = store.get_lesson(lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    db = request.state.srs_db

    words = await anyio.to_thread.run_sync(_analyze_lesson_words, lesson, db)

    # Corpus-frequency ranker for creation candidates — mirrors
    # mark_lesson_listened: the SAME callable must stamp `will_create` here and
    # drive creation there, or preview↔commit diverge (the 6a5c718 bug class).
    zipf = _zipf_for(lesson.language_code)

    # Card-less ignore list — mirrored from mark_lesson_listened.
    ignored = {lem.lower() for lem in db.get_ignored_lemmas(lesson.language_code)}

    today = anki_today()
    today_start, today_end, end_of_day_utc = _listen_day_window()

    variant_index = _build_variant_index(db, lesson.language_code)

    from app.srs.mastery import compute_mastery_progress

    # NEW-state rows are introductions, so they sort with the creations that
    # share their budget — ahead of "learning" (0). -1 is the create rank.
    _GROUP_RANK = {"new": -1, "learning": 0, "due": 1, "ahead": 2}
    horizon = settings.listen_due_horizon_days

    candidates: list[dict] = []
    lemma_candidates: list[str] = []
    # (row, created_today, ranking text, is_key_phrase) for every NEW-state
    # candidate, in candidate order. Held back from `candidates` until the
    # shared introduction budget is allocated below, which is what stamps their
    # `will_create`.
    new_state_rows: list[tuple[dict, bool, str, bool]] = []

    # ── Word-level candidates (tracked + untracked) ─────────────────────
    for lemma in words.first_sentence:
        is_func = is_function_word_for(
            lemma, words.surfaces.get(lemma, set()), lesson.language_code, words.surface_upos
        )
        if is_func and is_clozes_only_verb(lemma, lesson.language_code):
            continue

        res = _resolve_card_for_lemma(db, lemma, words.surfaces.get(lemma, set()), variant_index)
        if res is None:
            # Untracked → ranked/budget-truncated below, mirroring
            # mark_lesson_listened exactly.
            # The card-less ignore list suppresses CREATION only, so the check
            # belongs inside this branch — mark_lesson_listened applies it in
            # the same place. Hoisting it above _resolve_card_for_lemma hides a
            # carded ignored lemma from the preview while the commit still
            # stages it: preview↔commit divergence, the 6a5c718 bug class.
            if lemma.lower() in ignored:
                continue
            lemma_candidates.append(lemma)
        else:
            existing_id, existing = res
            if existing.syntactic_unit.card_type == "cloze":
                continue
            rec = existing.directions.get(Direction.RECOGNITION)
            if rec is None:
                continue
            grade_cls = _listen_grade_class(rec, today_start, today_end, end_of_day_utc=end_of_day_utc)
            if grade_cls is None:
                continue
            progress = compute_mastery_progress(existing.directions.values())
            due_at_str = (
                (rec.due_at.isoformat() if isinstance(rec.due_at, datetime.datetime) else str(rec.due_at))
                if rec.due_at is not None
                else None
            )
            # Why (if at all) a listen defers this row — the SAME predicate
            # mark_lesson_listened skips on, so preview and commit cannot
            # disagree about it. `well_known` is derived, never computed twice.
            deferred = _listen_deferred_reason(rec, grade_cls, today, horizon)
            row = {
                "kind": "word",
                "text": lemma,
                "item_id": existing_id,
                "grade_class": grade_cls,
                "rating": "good",
                "translation": existing.syntactic_unit.translation or "",
                # A NEW-state card has no schedule yet: `progress: None` renders
                # it in the unknown colour like a create row, rather than a
                # misleading red-for-0%.
                "progress": None if grade_cls == "new" else progress,
                "deferred_reason": deferred,
                "well_known": deferred == "known",
                "due_at": None if grade_cls == "new" else due_at_str,
                "_group_rank": _GROUP_RANK.get(grade_cls, 3),
            }
            if grade_cls == "new":
                new_state_rows.append(
                    (
                        row,
                        _created_in_window(db.get_created_at_by_guid(existing.guid), today_start, today_end),
                        lemma,
                        False,
                    )
                )
            else:
                candidates.append(row)

    # ── Key phrase candidates (tracked only; creation deferred) ──────────
    for kp in lesson.key_phrases:
        if kp.phrase.lower() in ignored:
            continue
        item = db.get_collocation(kp.phrase)
        if item is None:
            continue
        if item.syntactic_unit.card_type == "cloze":
            continue
        rec = item.directions.get(Direction.RECOGNITION)
        if rec is None:
            continue
        grade_cls = _listen_grade_class(rec, today_start, today_end, end_of_day_utc=end_of_day_utc)
        if grade_cls is None:
            continue
        kp_id = db.get_collocation_id_by_guid(item.guid)
        progress = compute_mastery_progress(item.directions.values())
        due_at_str = (
            (rec.due_at.isoformat() if isinstance(rec.due_at, datetime.datetime) else str(rec.due_at))
            if rec.due_at is not None
            else None
        )
        deferred = _listen_deferred_reason(rec, grade_cls, today, horizon)
        kp_row = {
            "kind": "kp",
            "text": kp.phrase,
            "item_id": kp_id,
            "grade_class": grade_cls,
            "rating": "good",
            "translation": item.syntactic_unit.translation or kp.translation or "",
            "progress": None if grade_cls == "new" else progress,
            "deferred_reason": deferred,
            "well_known": deferred == "known",
            "due_at": None if grade_cls == "new" else due_at_str,
            "_group_rank": _GROUP_RANK.get(grade_cls, 3),
        }
        # A NEW-state key phrase is an introduction too — it draws on the same
        # budget as NEW-state words and creations. Leaving it out would let a
        # lesson introduce key phrases without limit, and (worse) would stage
        # in the commit a row the preview never budgeted.
        if grade_cls == "new":
            new_state_rows.append(
                (
                    kp_row,
                    _created_in_window(db.get_created_at_by_guid(item.guid), today_start, today_end),
                    kp.phrase,
                    True,
                )
            )
        else:
            candidates.append(kp_row)

    # ── Ranked, budget-truncated creation candidates (mirrors
    # mark_lesson_listened's staged-creation loop exactly, D2/D3) ──────────
    new_cap, _ = resolve_daily_new_cap(db)
    intro_budget = max(0, new_cap - db.count_new_introduced_today(today) - db.count_new_created_today(today))
    # ONE budget AND one ranking pool: NEW-state rows and creation candidates
    # compete on corpus frequency together (F-2). Same call, same arguments as
    # mark_lesson_listened — that is what keeps preview and commit in step.
    live_new, tail_new, ranked, live_creates = _allocate_intro_pool(
        new_state_rows, lemma_candidates, intro_budget, zipf=zipf, occurrences=words.occurrences
    )
    for _row in live_new:
        _row["will_create"] = True
    for _row in tail_new:
        # Past the budget → the existing collapsed "N more — next listen" tail,
        # the same mechanism over-budget creations already use.
        _row["will_create"] = False
    candidates.extend(live_new)
    candidates.extend(tail_new)
    live_create_set = set(live_creates)
    # A create row has no card yet, so there is no stored translation to read —
    # the gloss comes from the lesson's own map, resolved through the SAME
    # helper mark_lesson_listened uses when it creates the card. Reusing it (as
    # opposed to a second lookup here) is what makes the previewed gloss and the
    # stored gloss identical by construction rather than by coincidence.
    token_glosses: dict[str, str] = (lesson.generation_metadata or {}).get("token_glosses", {})
    verb_base_glosses: dict[str, str] = (lesson.generation_metadata or {}).get("verb_base_glosses", {})
    creates = [
        {
            "kind": "create",
            "text": lemma,
            "item_id": None,
            "grade_class": "create",
            "rating": "good",
            "translation": _resolve_gloss_translation(
                lemma,
                token_glosses,
                words.surfaces.get(lemma, set()),
                words.first_surface.get(lemma, lemma),
                language_code=lesson.language_code,
                warn_on_missing=False,
                surface_upos=words.surface_upos,
                verb_base_glosses=verb_base_glosses,
            ),
            "progress": None,
            # A create is the most-wanted row in the list; it is never deferred.
            "deferred_reason": None,
            "well_known": False,
            "will_create": lemma in live_create_set,
            "due_at": None,
            "_group_rank": -1,
        }
        for lemma in ranked
    ]

    # ── Ordering: creations first, then tracked by group, then per
    # _tracked_sort_key within the group. Stable sort. ─────────────────────
    tracked = candidates
    tracked.sort(key=_tracked_sort_key)
    # Strip internal sort key before returning
    for c in creates + tracked:
        c.pop("_group_rank", None)

    return {"candidates": creates + tracked}


@router.get("/lesson/{lesson_id}/transcript", status_code=200, response_model=LessonTranscriptResponse)
async def get_lesson_transcript(lesson_id: str, request: Request):
    store = request.state.content_store
    lesson = store.get_lesson(lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    db = request.state.srs_db
    # Anki-day rollover, not local midnight — feeds extract_transcript's is_due
    # bolding (verdict item 5): a card due "tomorrow" by real calendar date but
    # not yet due by Anki's still-active prior day must not bold early.
    today = anki_today()
    # extract_transcript runs the (classla) lemmatizer synchronously and can take
    # seconds — especially right after restart before the warm-up finishes. Offload it
    # to a worker thread so it doesn't block the event loop and stall every other
    # in-flight request (the lesson page fires several API calls at once).
    lemmatizer = get_lemmatizer(lesson.language_code)
    transcript = await anyio.to_thread.run_sync(extract_transcript, lesson, db, lemmatizer, today)

    return {
        "lesson_id": lesson_id,
        "key_phrases": [{"phrase": kp.phrase, "translation": kp.translation} for kp in transcript.key_phrases],
        "dialogue_lines": [
            {
                "role": line.role,
                "sentence": line.sentence,
                "words": [
                    {
                        "surface": w.surface,
                        "prefix_punct": w.prefix_punct,
                        "suffix_punct": w.suffix_punct,
                        "lemma": w.lemma,
                        "srs_state": w.srs_state,
                        "srs_item_id": w.srs_item_id,
                        "translation": w.translation,
                        "collocation_span_id": w.collocation_span_id,
                        "collocation_start": w.collocation_start,
                        "collocation_srs_state": w.collocation_srs_state,
                        "collocation_lemma": w.collocation_lemma,
                        "collocation_translation": w.collocation_translation,
                        "collocation_progress": w.collocation_progress,
                        "card_type": w.card_type,
                        "active_state": w.active_state,
                        "active_direction": w.active_direction,
                        "is_due": w.is_due,
                        "progress": w.progress,
                        "inflectable": w.inflectable,
                        "inflection_feature": w.inflection_feature,
                        "known_marked": w.known_marked,
                        "recognition_reviewable": w.recognition_reviewable,
                        "recognition_state": w.recognition_state,
                        "recognition_is_due": w.recognition_is_due,
                        "well_known": w.well_known,
                    }
                    for w in line.words
                ],
            }
            for line in transcript.dialogue_lines
        ],
    }


_TRANSLATE_BATCH_SIZE = 50
_TRANSLATE_SYSTEM = "You are a translation assistant. Return ONLY valid JSON, no other text."


def _build_translate_prompt(words: list[str], language_name: str) -> str:
    word_list = "\n".join(f"- {w}" for w in words)
    return (
        f"Translate these {language_name} words/phrases to concise English.\n"
        f'Return a JSON object mapping each to its translation: {{"word": "translation", ...}}\n\n'
        f"Words:\n{word_list}"
    )


_VALID_LANGUAGE_CODES = known_language_codes()


@router.post("/translate", status_code=200, response_model=TranslateResponse)
async def translate(body: TranslateRequest, request: Request):
    if not body.text.strip():
        raise HTTPException(status_code=422, detail="text must not be empty")
    if body.language_code not in _VALID_LANGUAGE_CODES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid language_code: {body.language_code!r}. Must be one of {sorted(_VALID_LANGUAGE_CODES)}",
        )
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        raise HTTPException(status_code=503, detail="LLM not configured")
    translation = await translate_term(llm, body.text, body.language_code)
    return {"translation": translation}


@router.post("/translate-missing", status_code=200, response_model=TranslateMissingResponse)
async def translate_missing(request: Request):
    """Call the LLM to fill in translations for every card that has none."""
    db = request.state.srs_db
    llm = request.app.state.llm
    language = request.state.language

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


@router.post("/backfill-translations", status_code=200, response_model=BackfillTranslationsResponse)
async def backfill_translations(request: Request):
    """One-time repair: fill empty translations from all stored lesson glosses."""
    store = request.state.content_store
    db = request.state.srs_db
    glosses = store.get_all_token_glosses()
    updated = db.backfill_translations(glosses)
    return {"updated": updated, "glosses_found": len(glosses)}


@router.get("/stats", status_code=200, response_model=SrsStatsResponse)
async def get_stats(request: Request):
    db = request.state.srs_db
    today = anki_today()
    return {"total": db.count_collocations(), "due_today": db.count_due_collocations(today)}


@router.get("/queue-stats", status_code=200, response_model=QueueStatsResponse)
async def get_queue_stats(request: Request, response: Response):
    # Live state; never cache. Without this, a normal browser refresh can be
    # served from heuristic disk cache and the badges go stale.
    response.headers["Cache-Control"] = "no-store"
    db = request.state.srs_db
    today = anki_today()
    db.unbury_if_needed(today)
    new_cap, new_cap_source = resolve_daily_new_cap(db)
    _, fsrs_source = resolve_fsrs_params(db)
    # "Introduced today" is reconstructed from TT state (`prior_state='new'` +
    # `last_review` today): captures TT-side grades immediately and synced Anki
    # grades after the next sync. No live `collection.anki2` read on the
    # request path — sync is the cross-app alignment moment.
    introduced_today = db.count_new_introduced_today(today)
    remaining_quota = max(0, new_cap - introduced_today)
    # Badge tracks TT's view directly so every TT grade visibly decrements
    # the count (the graded card's due_date moves into the future and drops
    # out of `count_review_due_collocations`). Cross-app catch-up happens at
    # sync time: sync_pull updates TT's due_dates from Anki, so after sync
    # the count reflects Anki's grades too. Tab-visibility refetch (added in
    # the same layer) keeps the badge fresh between syncs as TT state mutates.
    review_due_raw = db.count_review_due_collocations(today)
    review_cap, review_cap_source = resolve_daily_review_cap(db)
    reviews_today = db.count_reviews_completed_today(today)
    # Anki's "New cards ignore review limit" deck option (default OFF, synced from
    # `newCardsIgnoreReviewLimit` into the cache — brief #4a). When OFF, today's
    # new-card intros ALSO charge the review-per-day limit AND the review budget
    # caps the new count; when ON, both couplings are lifted.
    ignore_review_limit = resolve_new_cards_ignore_review_limit(db)
    # Anki charges today's new-card introductions against the review-per-day
    # limit too (Layer 76 — rslib/decks/limits.rs:104-108), unless the flag is ON.
    # Interday learning cards due today charge it as well (Layer 79 —
    # gathering.rs gathers queue=3 under LimitKind::Review), flag or no flag.
    review_budget = effective_review_budget(
        review_cap,
        reviews_today,
        introduced_today,
        interday_learning_due=db.count_interday_learning_due(today),
        new_cards_ignore_review_limit=ignore_review_limit,
    )
    review_remaining = min(review_due_raw, review_budget)
    # New-sibling bury (Anki's bury_new): a new card whose sibling is gathered
    # into today's queue (review-due-today / learning / graded-today) is buried
    # out of the new pool. `_compute_live_main` already applies this to the
    # served queue; the bury-aware count keeps the badge consistent with it.
    # Falls back to the raw count when bury_new is off (no regression).
    bury_new, _ = resolve_bury_new(db)
    new_available = db.count_new_available_collocations(today) if bury_new else db.count_new_available()
    new_badge = min(remaining_quota, new_available)
    # When "New cards ignore review limit" is OFF, the review limit also caps new
    # cards: when the day's review budget is consumed by due reviews, Anki shows 0
    # new even with new/day > 0 (e.g. review cap 50 + 194 due → 0 new). The served
    # queue applies the same cap (`_compute_live_main`, Layer 77). `review_budget`
    # already nets out reviews AND new intros done today (Layer 76), matching Anki's
    # `new = min(new_limit, review_limit - review_count)` after both are charged.
    # Brief #4a: skip this cap entirely when the flag is ON (new ignores the limit).
    if not ignore_review_limit:
        new_badge = min(new_badge, max(0, review_budget - review_remaining))
    return {
        "new": new_badge,
        "learning": db.count_learning(),
        "review": review_remaining,
        "daily_new_cap": new_cap,
        "cap_source": new_cap_source,
        "daily_review_cap": review_cap,
        "review_cap_source": review_cap_source,
        "fsrs_source": fsrs_source,
    }


# ── Admin endpoints ────────────────────────────────────────────────────────────


_VALID_USER_STATES = {"new", "learning", "review", "known", "ignored"}
_STATE_MAP = {
    "new": SRSState.NEW,
    "learning": SRSState.LEARNING,
    # `set_state_by_id` only changes the state label, preserving stability /
    # difficulty / due_at / reps — so cycling a card back to `review` restores
    # its original FSRS schedule rather than fabricating one.
    "review": SRSState.REVIEW,
    "known": SRSState.KNOWN,
    "ignored": SRSState.SUSPENDED,
}


@router.post("/items", status_code=201, response_model=SrsItemResponse, response_model_exclude_unset=True)
async def create_item(body: CreateItemRequest, request: Request):
    db = request.state.srs_db
    if body.word_count < 1:
        from fastapi import HTTPException as _HTTPException

        raise _HTTPException(status_code=422, detail="word_count must be >= 1")

    # LLM auto-translate if translation is empty
    translation = body.translation
    if translation == "":
        llm_client = getattr(request.app.state, "llm", None)
        if llm_client is not None:
            translation = await translate_term(llm_client, body.text, body.language_code)

    unit = SyntacticUnit(
        text=body.text,
        translation=translation,
        word_count=body.word_count,
        difficulty=1,
        source="user",
        lemma=body.text.lower() if body.word_count == 1 else None,
        source_sentence=body.source_sentence,
        source_lesson_id=body.source_lesson_id,
        source_line_index=body.source_line_index,
    )
    existing = db.get_collocation(body.text)
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"Item already exists: {body.text!r}")
    db.add_collocation(unit, language_code=body.language_code)
    # Exact guid lookup (like _persist_new_card) — the LIKE-search used before
    # could return a superstring row ("Dober dan" for "dan") and attach the new
    # card's media to it.
    guid = compute_guid(unit.text, body.language_code, unit.disambig_key or "")
    row_id = db.get_collocation_id_by_guid(guid)
    if row_id is None:  # pragma: no cover — defensive; add_collocation just inserted
        raise HTTPException(status_code=500, detail="Failed to retrieve created item")
    result = db.get_collocation_by_id(row_id)
    if result is None:  # pragma: no cover — defensive; id came from get_collocation_id_by_guid
        raise HTTPException(status_code=500, detail="Failed to retrieve created item")
    _, item, lang = result
    # Complete the card now (image + audio) so it renders in /review without a
    # sync — the user added it in TunaTale; it shouldn't depend on Anki.
    llm = getattr(request.app.state, "llm", None)
    await _generate_add_time_media(db, llm, row_id, unit, language_code=body.language_code)
    img = db.get_image_filename(row_id)
    image_url = f"/api/srs/media/{img}" if img else None
    aud = db.get_audio_filename(row_id)
    audio_url = f"/api/srs/media/{aud}" if aud else None
    return _item_to_dict(row_id, item, lang, image_url, audio_url)


async def _persist_new_card(
    db,
    unit: SyntacticUnit,
    language_code: str,
    *,
    synthesize: bool,
    audio_sentence: str = "",
    audio_word: str = "",
    llm=None,
    media_word: str | None = None,
) -> dict:
    """Add a NEW collocation and return its ``{id, was_created, item}`` dict.

    Shared persistence tail for the card-creating endpoints (``/items/base`` and
    ``/inflection-clozes``): insert (idempotent by guid), look the id back up,
    best-effort synthesize cloze audio when ``synthesize`` and the row is newly
    created, then serialize. ``audio_sentence`` is the *raw* sentence (never the
    pre-clozed ``source_sentence``) and ``audio_word`` the surface to voice.
    ``media_word`` is forwarded to ``_generate_add_time_media`` as the word to
    fetch image/audio for — ``None`` (the default) falls back to ``unit.text``,
    so the /inflection-clozes caller needs no change. For a
    newly-created *vocab* base card, fetch image + word audio inline so it's
    complete in /review without a sync (no-op for cloze / missing Pixabay key).
    """
    was_created = db.add_collocation(unit, language_code=language_code)
    guid = compute_guid(unit.text, language_code, unit.disambig_key or "")
    coll_id = db.get_collocation_id_by_guid(guid)
    if coll_id is None:  # pragma: no cover — defensive; add_collocation just inserted
        raise HTTPException(status_code=500, detail="Failed to create collocation")

    if synthesize and was_created:
        try:
            await synthesize_cloze_audios(db, coll_id, audio_sentence, audio_word, voice=get_tts_voice(language_code))
        except Exception:
            _logger.warning("Failed to synthesize cloze audio for %r", unit.text)

    if was_created:
        await _generate_add_time_media(db, llm, coll_id, unit, language_code=language_code, media_word=media_word)

    result = db.get_collocation_by_id(coll_id)
    if result is None:  # pragma: no cover — defensive; id came from get_collocation_id_by_guid
        raise HTTPException(status_code=500, detail="Failed to retrieve created collocation")
    _, item, _ = result
    img = db.get_image_filename(coll_id)
    image_url = f"/api/srs/media/{img}" if img else None
    aud = db.get_audio_filename(coll_id)
    audio_url = f"/api/srs/media/{aud}" if aud else None
    return {
        "id": coll_id,
        "was_created": was_created,
        "item": _item_to_dict(coll_id, item, language_code, image_url, audio_url),
    }


@router.post(
    "/items/base",
    status_code=200,
    response_model=CreateCardResponse,
    # item nests SrsItemResponse whose directions omit "left" when None
    # (srs.py::_direction_to_dict) — same exclude_unset trap as create_item.
    response_model_exclude_unset=True,
)
async def create_base_card(body: CreateBaseCardRequest, request: Request) -> dict:
    """Create a base card for an unknown clicked word (Phase 5, Part C / decision 8, C-a).

    Branches by word type (the word-learning state machine):
      - function word → production-only cloze (the *surface* blanked in the sentence)
      - content word  → vocab (recognition + production)
    Both created in NEW state. Idempotent by the base guid. Honors the
    add_collocation card-adding contract (no Anki ids; sync_create_new mints +
    links). No LLM auto-translate here — the caller passes the transcript gloss.
    """
    db = request.state.srs_db
    lang = body.language_code
    lemma = body.lemma.casefold()

    # Clozes-only verbs (e.g. biti) have no base card — only per-form conjugation
    # clozes via /inflection-clozes. Reject so a click can't mint a spurious base.
    if is_clozes_only_verb(lemma, lang):
        raise HTTPException(status_code=409, detail="Clozes-only verb has no base card")

    # POS-first function-word detection: read the active surface's UPOS from the
    # sentence (classla → AUX for biti forms etc.; LowercaseLemmatizer → "" so the
    # curated include-list is the sole signal). The surface is checked too — an
    # inflected function form (classla "sem" → lemma "biti") classifies via its
    # surface even when the dictionary lemma isn't itself a function word.
    # Offload the (classla) lemmatizer off the event loop — see get_lesson_transcript.
    lemmatizer = get_lemmatizer(lang)
    mv = model_version_for(lemmatizer)
    analyses = await anyio.to_thread.run_sync(analyze_sentence_cached, db, lemmatizer, body.sentence, lang, mv)
    analysis = next((ta for ta in analyses if ta.surface.casefold() == body.surface.casefold()), None)
    upos = analysis.upos if analysis else None
    gender = analysis.gender if analysis else ""
    # Check both lemma and surface with the surface's upos (a single-word click).
    upos_map = {lemma.casefold(): upos, body.surface.casefold(): upos} if upos else None
    is_func = is_function_word_for(lemma, {lemma, body.surface}, lang, upos_map)
    if is_func:
        # Blank the surface as it appeared, not the dictionary lemma (Phase 2b):
        # the cloze must reference the word present in the stored sentence.
        source_sentence = make_cloze_text(body.surface, body.sentence)
        card_type = "cloze"
    else:
        source_sentence = body.sentence
        card_type = "vocab"

    # Verb base cards: the transcript gloss is the *conjugated* in-context meaning
    # ("pokazem" → "I will show"). classla gives us the lemma + POS, but the
    # English base meaning is a translation only the LLM can produce — re-gloss to
    # the bare dictionary form ("show") to match the existing verb cards.
    translation = body.translation
    if upos == "VERB":
        llm_client = getattr(request.app.state, "llm", None)
        if llm_client is not None:
            gloss = await generate_word_gloss(llm_client, surface=body.surface, lemma=lemma, source_lang=lang, pos=upos)
            if gloss:
                translation = gloss

    # Stanza can return a truncated fragment as the lemma (`trøtt` → `trø`). When
    # the fragment fails the language's plausibility check, front the card with the
    # surface as it appeared — the headword stays a real word, never a skipped card.
    lemma_plausible = get_lemma_plausible(lang)
    headword = lemma
    if card_type == "vocab" and lemma_plausible is not None and not lemma_plausible(body.surface, lemma):
        headword = body.surface.casefold()

    unit = SyntacticUnit(
        text=format_vocab_headword(headword, upos, lang) if card_type == "vocab" else lemma,
        translation=translation,
        word_count=1,
        difficulty=1,
        source="user",
        lemma=headword,
        card_type=card_type,
        source_sentence=source_sentence,
        article=get_gender_article(lang, gender) if upos == "NOUN" else "",
    )
    return await _persist_new_card(
        db,
        unit,
        lang,
        synthesize=is_func,
        audio_sentence=body.sentence,
        audio_word=body.surface,
        llm=getattr(request.app.state, "llm", None),
        media_word=headword,
    )


@router.get("/items", status_code=200, response_model=ListItemsResponse, response_model_exclude_unset=True)
async def list_items(
    request: Request,
    search: str | None = None,
    state: str | None = None,
    sort: str = "text",
    order: str = "asc",
    limit: int = 50,
    offset: int = 0,
):
    db = request.state.srs_db
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
    image_map = db.get_image_filenames([rid for rid, _, _ in rows])
    return {
        "items": [
            _item_to_dict(
                rid,
                item,
                lang,
                image_url=f"/api/srs/media/{image_map[rid]}" if rid in image_map else None,
            )
            for rid, item, lang in rows
        ],
        "total": total,
    }


@router.patch("/items/{item_id}", status_code=200, response_model=SrsItemResponse, response_model_exclude_unset=True)
async def patch_item(item_id: int, body: UpdateItemRequest, request: Request):
    db = request.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    try:
        db.update_collocation_fields(item_id, text=body.text, translation=body.translation)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return _item_to_dict(row_id, item, lang)


@router.delete("/items/{item_id}", status_code=200, response_model=StatusResponse)
async def delete_item(item_id: int, request: Request):
    db = request.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete_collocation(item_id)
    return {"status": "deleted"}


@router.post("/items/bulk-delete", status_code=200, response_model=BulkDeleteResponse)
async def bulk_delete_items(body: BulkDeleteRequest, request: Request):
    db = request.state.srs_db
    deleted = db.delete_collocations(body.ids)
    return {"deleted": deleted}


@router.post(
    "/items/{item_id}/reset", status_code=200, response_model=SrsItemResponse, response_model_exclude_unset=True
)
async def reset_item(item_id: int, request: Request):
    db = request.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    db.reset_collocation(item_id)
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return _item_to_dict(row_id, item, lang)


@router.post(
    "/items/{item_id}/state",
    status_code=200,
    response_model=SrsItemResponse,
    response_model_exclude_unset=True,
)
async def set_item_state(item_id: int, body: SetStateRequest, request: Request):
    if body.state not in _VALID_USER_STATES:
        raise HTTPException(
            status_code=422, detail=f"Invalid state: {body.state!r}. Must be one of {sorted(_VALID_USER_STATES)}"
        )
    db = request.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if body.state == "learning":
        db.promote_to_learning(item_id)
    elif body.state == "known":
        from app.srs.fsrs import stability_for_interval
        from app.srs.queue_stats import resolve_fsrs_params, resolve_maximum_review_interval

        max_ivl, _ = resolve_maximum_review_interval(db)
        params, _ = resolve_fsrs_params(db)
        dr = params.desired_retention
        stability = stability_for_interval(max_ivl, dr)
        due_date = anki_today() + timedelta(days=max_ivl)
        due_at = due_at_rollover_utc(due_date)
        db.mark_known(item_id, due_at=due_at, stability=stability)
    else:
        db.set_state_by_id(item_id, _STATE_MAP[body.state])
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return _item_to_dict(row_id, item, lang)


@router.post(
    "/items/{item_id}/restore-known",
    status_code=200,
    response_model=SrsItemResponse,
    response_model_exclude_unset=True,
)
async def restore_known_item(item_id: int, request: Request):
    """Reverse a "Mark known" — restore the snapshotted pre-known schedule.

    Dedicated rather than overloading set_item_state: the "review"/"new" state
    mappings there are label-only / full-reset and would be confusing here.
    No-op (still 200) when the item has no known snapshot.
    """
    db = request.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    db.restore_known(item_id)
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return _item_to_dict(row_id, item, lang)


@router.post(
    "/items/{item_id}/untrack",
    status_code=200,
    response_model=UntrackItemResponse,
    response_model_exclude_unset=True,
)
async def untrack_item(item_id: int, request: Request):
    db = request.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    result = db.untrack_collocation(item_id)
    if result["action"] == "deleted":
        return {"action": "deleted"}
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return {"action": "suspended", "item": _item_to_dict(row_id, item, lang)}


@router.post(
    "/items/{item_id}/suspend",
    status_code=200,
    response_model=SrsItemResponse,
    response_model_exclude_unset=True,
)
async def suspend_item(item_id: int, body: SuspendRequest, request: Request):
    db = request.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    dir_enum: Direction | None = None
    if body.direction is not None:
        try:
            dir_enum = Direction(body.direction)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid direction: {body.direction!r}") from exc
    db.set_suspended(item_id, body.suspended, direction=dir_enum)
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return _item_to_dict(row_id, item, lang)


@router.post("/ignored-lemmas", status_code=200, response_model=StatusResponse)
async def add_ignored_lemma(body: IgnoreLemmaRequest, request: Request):
    db = request.state.srs_db
    db.add_ignored_lemma(body.language_code, body.lemma)
    return {"status": "ok"}


@router.delete("/ignored-lemmas", status_code=200, response_model=StatusResponse)
async def remove_ignored_lemma(lemma: str, language_code: str, request: Request):
    db = request.state.srs_db
    db.remove_ignored_lemma(language_code, lemma)
    return {"status": "ok"}


def _queue_item_to_dict(
    row_id: int,
    item: SRSItem,
    lang: str,
    direction: Direction,
    db,
    ambiguous_surfaces: set[str] | None = None,
) -> dict:
    img = db.get_image_filename(row_id)
    image_url = f"/api/srs/media/{img}" if img else None
    if item.syntactic_unit.card_type == "cloze":
        sent_aud = db.get_sentence_audio_filename(row_id)
        audio_url = f"/api/srs/media/{sent_aud}" if sent_aud else None
        word_aud = db.get_audio_filename(row_id)
        word_audio_url = f"/api/srs/media/{word_aud}" if word_aud else None
    else:
        aud = db.get_audio_filename(row_id)
        audio_url = f"/api/srs/media/{aud}" if aud else None
        word_audio_url = None
    base = _item_to_dict(row_id, item, lang, image_url, audio_url, ambiguous_surfaces)
    base["direction"] = direction.value
    base["word_audio_url"] = word_audio_url
    # `_item_to_dict` populates flat fields from recognition (or production for
    # cloze). For a queue item that's the OTHER direction, those values misrepresent
    # the actual card on screen: a production card due today + heavily-reviewed can
    # come back with recognition's untouched stats. Override every per-direction
    # field with the queued direction's authoritative value.
    ds = item.directions[direction]
    base["state"] = ds.state.value
    base["due_at"] = ds.due_at.isoformat()
    base["stability"] = ds.stability
    base["difficulty"] = ds.difficulty
    base["reps"] = ds.reps
    base["lapses"] = ds.lapses
    base["last_review"] = ds.last_review.isoformat() if ds.last_review else None
    return base


@router.post(
    "/inflection-clozes",
    status_code=200,
    response_model=CreateCardResponse,
    # Same nested SrsItemResponse "left" trap as /items/base — see above.
    response_model_exclude_unset=True,
)
async def create_inflection_cloze(body: InflectionClozeRequest, request: Request) -> dict:
    """Create one morphology cloze for an inflected surface (Phase 4a).

    Gated on the lemma's base production being in REVIEW or KNOWN.
    Idempotent by guid. Follows the add_collocation contract
    (card_type=cloze, no Anki ids).
    """
    db = request.state.srs_db
    language_code = body.language_code

    # 1. Eligibility gate — base word production must be REVIEW/KNOWN.
    #    Clozes-only verbs (e.g. biti) have no base card and are ungated.
    if not is_clozes_only_verb(body.lemma, language_code):
        base = db.get_collocation_by_lemma(body.lemma)
        if base is None:
            raise HTTPException(status_code=409, detail="Base word not yet learned")
        prod = base.directions.get(Direction.PRODUCTION)
        if prod is None or prod.state not in (SRSState.REVIEW, SRSState.KNOWN):
            raise HTTPException(status_code=409, detail="Base word not yet learned")

    # 2. Degenerate guard — surface == lemma reveals the answer
    if body.lemma.casefold() == body.surface.casefold():
        raise HTTPException(status_code=422, detail="Surface equals lemma — nothing to cloze")

    # 3. Resolve word gloss + sentence translation from the lesson, mirroring
    #    /listen. The grammar hint lives in its own `grammar` field — never the
    #    translation — so it can't leak into the displayed L1 gloss.
    word_translation = body.translation
    sentence_translation = ""
    if body.lesson_id:
        from app.models.lesson import extract_sentence_translations_from_translated

        lesson = request.state.content_store.get_lesson(body.lesson_id)
        if lesson is not None:
            token_glosses: dict[str, str] = lesson.generation_metadata.get("token_glosses", {})
            sentence_translations: dict[str, str] = dict(lesson.generation_metadata.get("sentence_translations", {}))
            for k, v in extract_sentence_translations_from_translated(lesson).items():
                sentence_translations.setdefault(k, v)
            sentence_translation = sentence_translations.get(body.sentence, "")
            if not sentence_translation:
                # The transcript passes a sentence reconstructed from surfaces,
                # which drops the lesson key's internal punctuation. Fall back to
                # a punctuation/case-insensitive match.
                match_index = {normalize_sentence_key(k): v for k, v in sentence_translations.items()}
                sentence_translation = match_index.get(normalize_sentence_key(body.sentence), "")
            if not word_translation:
                word_translation = token_glosses.get(body.surface.lower()) or token_glosses.get(body.lemma) or ""

    # 3b. Prefer an LLM gloss of the specific inflected form — the token gloss is
    #     the *base* meaning and biti forms have only the grammar hint, so neither
    #     conveys the conjugation ("boste" → "you will be"). classla supplies the
    #     lemma/feature; the LLM supplies the English. Fail-soft: keep the
    #     resolved fallback when the LLM is absent or errors.
    llm_client = getattr(request.app.state, "llm", None)
    if llm_client is not None:
        gloss = await generate_word_gloss(
            llm_client,
            surface=body.surface,
            lemma=body.lemma,
            source_lang=language_code,
            feature=body.feature,
            sentence=body.sentence,
        )
        if gloss:
            word_translation = gloss

    # 4. Build + create (mirrors /listen morphology-cloze block)
    disambig = f"morph:{body.feature.replace(':', '-')}"
    cloze_sent = make_morphology_cloze_text(body.surface, body.lemma, body.sentence)
    grammar_hint = format_morphology_hint(body.lemma, body.feature)
    unit = SyntacticUnit(
        text=body.surface,
        translation=word_translation,
        word_count=1,
        difficulty=1,
        source="llm",
        lemma=body.lemma,
        disambig_key=disambig,
        card_type="cloze",
        source_sentence=cloze_sent,
        source_sentence_translation=sentence_translation,
        grammar=grammar_hint,
    )
    # 5. Persist + synthesize + serialize (always a cloze).
    result = await _persist_new_card(
        db, unit, language_code, synthesize=True, audio_sentence=body.sentence, audio_word=body.surface
    )

    # 6. Self-healing backfill (mirrors /listen, srs.py:461). add_collocation is
    #    idempotent by guid and does NOT update an existing row, so a cloze first
    #    minted without lesson context (empty sentence_translation) would strand
    #    permanently — no Anki Back Extra <span class="st">. When we resolved a
    #    translation and re-hit an existing row that lacks one, stamp it dirty so
    #    the next sync rewrites Back Extra. (A freshly-created row already carries
    #    the translation from `unit`, so only the idempotent path needs this.)
    if sentence_translation and not result["was_created"]:
        guid = compute_guid(unit.text, language_code, unit.disambig_key or "")
        stored = db.get_collocation_by_guid(guid)
        if not stored.syntactic_unit.source_sentence_translation:
            db.set_sentence_translation_dirty(guid, sentence_translation)
    return result


@router.get(
    "/review-queue",
    status_code=200,
    response_model=ReviewQueueResponse,
    # Same nested `left` omission as the lesson queue — see above.
    response_model_exclude_unset=True,
)
async def get_review_queue(request: Request, response: Response, session_start: bool = False) -> dict:
    """Return the entire ordered review queue in one shot.

    Implements Anki's queue construction: combined new-card cap across directions,
    sibling burying, and newSpread ordering.

    `session_start=1` is the deck-open analog: it advances `learning_cutoff` to
    `now` so any learning card whose timer has elapsed since the last grade jumps
    into `ready_learning`. Frontend passes it on page mount (= deck open). Other
    callers (per-grade refetch, polling) leave it false to preserve the frozen
    cutoff between grades. Mirrors Anki's `update_learning_cutoff_and_count`
    being called at queue build time (rslib scheduler/queue/builder/mod.rs:222).
    """
    # Live state; never cache. Without this, a normal browser refresh can serve
    # /review-queue from heuristic cache — the JS still runs onMount and sends
    # session_start=1, but the browser short-circuits with the cached body and
    # the rebuild never reaches the backend. Only hard-refresh (Cmd+Shift+R)
    # bypasses the cache, which is a bad UX.
    response.headers["Cache-Control"] = "no-store"

    db = request.state.srs_db
    ordered = assemble_review_queue(db, session_start=session_start)

    # POS is a disambiguator: show it only where a surface spans >=2 word classes.
    # Computed once per language present in the queue, then passed per item.
    ambiguous_by_lang: dict[str, set[str]] = {}
    for _rid, _item, qlang, _dir in ordered:
        if qlang not in ambiguous_by_lang:
            ambiguous_by_lang[qlang] = db.get_ambiguous_surfaces(qlang)
    return {
        "queue": [
            _queue_item_to_dict(rid, it, qlang, qdir, db, ambiguous_by_lang[qlang]) for rid, it, qlang, qdir in ordered
        ]
    }
