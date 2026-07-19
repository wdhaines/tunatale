"""SRS state and review endpoints."""

from __future__ import annotations

import datetime
import json
import logging
import re
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import NamedTuple

import anyio
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from app.api.models import (
    BulkDeleteRequest,
    CreateBaseCardRequest,
    CreateItemRequest,
    DrillRequest,
    IgnoreLemmaRequest,
    ImportListensRequest,
    InflectionClozeRequest,
    ListenRequest,
    SetStateRequest,
    SuspendRequest,
    TranslateRequest,
    UpdateItemRequest,
)
from app.audio.cloze_tts import synthesize_cloze_audios
from app.common.guid import compute_guid
from app.languages import get_tts_voice, known_language_codes
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
from app.srs.grade_undo import UndoNotAvailable, record_grade_snapshot, undo_last_grade
from app.srs.lemmatizer import analyze_sentence_cached, get_lemmatizer, lemmatize_surfaces_in_context, model_version_for
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
    live_review_budget,
    resolve_bury_new,
    resolve_col_crt,
    resolve_daily_new_cap,
    resolve_daily_review_cap,
    resolve_fsrs_params,
    resolve_new_cards_ignore_review_limit,
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
_MEDIA_DIR = Path(__file__).parent.parent.parent / "media"

# The lemmatizer is resolved per-request from the content's language_code, never a
# process-wide singleton — multi-language mode (settings.database_urls) serves both
# languages from one process, so a frozen import-time lemmatizer would analyze e.g.
# Norwegian transcripts with the Slovene model. See get_lemmatizer(language_code).

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
    db, llm, coll_id: int, unit: SyntacticUnit, *, language_code: str, used_image_urls: set[str] | None = None
) -> None:
    """Fetch image + word audio for a freshly-created vocab card, inline.

    So a card the user creates in TunaTale is complete in /review immediately —
    not blank until its first sync (the nasvidenje gap). Cloze cards are skipped
    (they get sentence audio via ``synthesize_cloze_audios``), and the underlying
    ``generate_vocab_media`` no-ops when no Pixabay key is configured. Best-effort:
    never raises, so a media hiccup can't fail card creation. ``sync_create_new``
    reuses whatever this stores rather than re-fetching.
    """
    if unit.card_type == "cloze":
        return
    from app.cards.media.vocab_media import generate_vocab_media
    from app.config import settings

    await generate_vocab_media(
        db,
        coll_id,
        unit.text,
        unit.translation,
        llm=llm,
        pixabay_key=settings.pixabay_api_key,
        language_code=language_code,
        source_sentence=unit.source_sentence or "",
        grammar=unit.grammar or "",
        used_image_urls=used_image_urls,
    )


@router.get("/due", status_code=200)
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


@router.get("/new", status_code=200)
async def get_new_collocations(request: Request, limit: int = 10, direction: str = "recognition"):
    db = request.state.srs_db
    try:
        dir_enum = Direction(direction)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid direction: {direction!r}") from exc
    triples = db.get_new_items(limit=limit, direction=dir_enum)
    return {"new": _triples_to_dicts(db, triples)}


@router.post("/items/{item_id}/direction/{direction}/feedback", status_code=200)
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
    )
    db.update_direction_by_id(item_id, dir_enum, updated.directions[dir_enum])
    row = build_revlog_row(
        item_id, dir_enum, prev_dir, updated.directions[dir_enum], rating, body.time_ms, now=now, col_crt=col_crt
    )
    db.append_revlog(row)
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


@router.post("/items/{item_id}/direction/{direction}/undo", status_code=200)
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


@router.get("/media/{filename}", status_code=200)
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


def _resolve_gloss_translation(
    lemma: str,
    token_glosses: dict[str, str],
    surfaces: set[str],
    first_surface: str,
    *,
    language_code: str,
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
    """
    for key in [lemma, first_surface.lower(), *sorted(s.lower() for s in surfaces)]:
        gloss = token_glosses.get(key)
        if gloss:
            return gloss
    _logger.warning(
        "No gloss for lemma %r (surfaces=%s) in %s lesson; card created with empty translation",
        lemma,
        sorted(surfaces),
        language_code,
    )
    return ""


def _listen_grade_class(
    rec: DirectionState | None,
    today_start: datetime.datetime,
    today_end: datetime.datetime,
    *,
    end_of_day_utc: str,
) -> str | None:
    """Classify a recognition direction for /listen grading.

    Returns one of ``"learning"``, ``"due"``, ``"ahead"``, or ``None`` (not
    eligible).  ``last_review`` gates ONLY the once-per-day window
    (already-graded-today returns ``None`` for both due and ahead); a missing
    or legacy non-datetime ``last_review`` means "not graded today" and falls
    through to the dueness check.  The REVIEW-state dueness boundary matches
    ``count_review_due_collocations`` (``due_at <= end_of_day_utc``; a NULL
    ``due_at`` is not counted as due there, so it classifies as ``"ahead"``).
    """
    if rec is None:
        return None
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


def _bump_grade_clock(last_ms: int) -> tuple[datetime.datetime, int]:
    """Monotonic per-grade timestamp for multi-grade listens.

    ``tt_revlog.id`` is a millisecond-epoch primary key and ``append_revlog``
    is INSERT OR IGNORE (sync-replay idempotency), so two grades landing in
    the same millisecond would silently drop the second row — losing FSRS
    replay history and under-counting ``count_reviews_completed_today``.
    Anki never faces this (one answer per user interaction); a listen grades
    a whole lesson in one request, so bump into the next free millisecond.
    """
    now = datetime.datetime.now(datetime.UTC)
    ms = int(now.timestamp() * 1000)
    if ms <= last_ms:
        ms = last_ms + 1
        now = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.UTC)
    return now, ms


def _rank_listen_candidates(key_phrases, lemmas, occurrences) -> list[tuple[str, object]]:
    """Rank untracked creation candidates for a staged listen (plan D2).

    Key phrases first, in lesson order — they're the lesson's pedagogical
    core. Then lemmas by in-lesson occurrence count descending; the stable
    sort keeps ties in first-appearance order (the order of ``lemmas``).
    Returns ``("kp", key_phrase)`` / ``("lemma", lemma)`` tuples.
    """
    ranked_lemmas = sorted(lemmas, key=lambda lem: -occurrences.get(lem, 0))
    return [("kp", kp) for kp in key_phrases] + [("lemma", lem) for lem in ranked_lemmas]


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
        for surface, lemma in zip(surfaces, phrase_lemmas, strict=True):
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


@router.post("/listen", status_code=200)
async def mark_lesson_listened(body: ListenRequest, request: Request):
    store = request.state.content_store
    lesson = store.get_lesson(body.lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    db = request.state.srs_db
    col_crt = resolve_col_crt(db)
    llm = getattr(request.app.state, "llm", None)
    # One shared set across this request so two new words don't pick the same image.
    used_image_urls: set[str] = set()
    # One session balancer for the whole request; each grade below feeds itself
    # back via _balancer_add so later grades in this lesson see earlier ones.
    balancer = build_live_load_balancer(db, now=datetime.datetime.now(datetime.UTC), col_crt=col_crt)

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

    # Lemma analysis shared verbatim with /lesson/{id}/review-queue — the
    # blocking (classla) pass runs on a worker thread so the event loop
    # doesn't stall; the await means no concurrent access to the maps.
    words = await anyio.to_thread.run_sync(_analyze_lesson_words, lesson, db)
    lemma_occurrences = words.occurrences
    lemma_to_sentence = words.first_sentence
    lemma_to_surfaces = words.surfaces
    lemma_to_first_surface = words.first_surface
    surface_to_upos = words.surface_upos

    # ── Today window (Anki-day rollover, matches count_new_introduced_today's
    # convention via the shared rollover helper — NOT local midnight; a card
    # graded in [midnight, 4 AM) is still "today" for Anki until rollover) ────
    today = anki_today()
    today_start, today_end = anki_day_bounds_utc_dt(today)
    # Naive local-date cutoff string. Correct ONLY because REVIEW-state due_at
    # is date-encoded at 04:00 UTC (rollover.py::due_at_rollover_utc) — the
    # lexicographic compare is a due-DATE <= today check in disguise. An
    # instant-flavored due_at (e.g. now-1h) whose UTC date exceeds the local
    # date would misclassify as "ahead" past 20:00 local (UTC-4).
    end_of_day_utc = datetime.datetime.combine(today, datetime.time.max).isoformat()

    created_count = 0
    graded_count = 0

    # ── Live review budget: due-today grades count against it, ahead/learning
    # grades are unlimited (brief Step B) ──────────────────────────────────
    review_budget = live_review_budget(db, today)
    last_grade_ms = 0  # revlog-id monotonicity across this listen's grades

    # ── Per-listen creation budget (plan D1) ────────────────────────────
    # One listen queues at most one Anki-day's worth of new cards, net of
    # today's introductions and of still-NEW cards created earlier today
    # (a same-day re-listen therefore creates ~0 more; refills next Anki
    # day). Deliberately does NOT subtract the whole-deck NEW backlog —
    # the queue engine's new-quota remains the real flow limiter.
    new_cap, _ = resolve_daily_new_cap(db)
    creation_budget = max(0, new_cap - db.count_new_introduced_today(today) - db.count_new_created_today(today))
    lemma_candidates: list[str] = []

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
            if grade_cls == "due" and review_budget <= 0:
                continue
            rating = _WORD_RATING_MAP.get(body.word_ratings.get(lemma, "good"), Rating.GOOD)
            now, last_grade_ms = _bump_grade_clock(last_grade_ms)
            prev_dir = existing.directions[Direction.RECOGNITION]
            updated = schedule(
                existing,
                rating,
                direction=Direction.RECOGNITION,
                params=resolve_fsrs_params(db)[0],
                now=now,
                col_crt=col_crt,
                load_balancer=balancer,
            )
            db.update_collocation(updated)
            listen_coll_id = db.get_collocation_id_by_guid(existing.guid)
            assert listen_coll_id is not None
            row = build_revlog_row(
                listen_coll_id,
                Direction.RECOGNITION,
                prev_dir,
                updated.directions[Direction.RECOGNITION],
                rating,
                0,
                now=now,
                col_crt=col_crt,
                review_kind=3 if grade_cls == "ahead" else None,
            )
            db.append_revlog(row)
            _balancer_add(balancer, card_id=prev_dir.anki_card_id, note_id=existing.anki_note_id, interval=row.interval)
            graded_count += 1
            if grade_cls == "due":
                review_budget -= 1

    # ── Key phrase auto-grade (existing cards only; creation deferred) ────
    for kp in lesson.key_phrases:
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
        if grade_cls == "due" and review_budget <= 0:
            continue
        now, last_grade_ms = _bump_grade_clock(last_grade_ms)
        prev_dir = existing.directions[Direction.RECOGNITION]
        updated = schedule(
            existing,
            Rating.GOOD,
            direction=Direction.RECOGNITION,
            params=resolve_fsrs_params(db)[0],
            now=now,
            col_crt=col_crt,
            load_balancer=balancer,
        )
        db.update_collocation(updated)
        kp_coll_id = db.get_collocation_id_by_guid(existing.guid)
        assert kp_coll_id is not None
        row = build_revlog_row(
            kp_coll_id,
            Direction.RECOGNITION,
            prev_dir,
            updated.directions[Direction.RECOGNITION],
            Rating.GOOD,
            0,
            now=now,
            col_crt=col_crt,
            review_kind=3 if grade_cls == "ahead" else None,
        )
        db.append_revlog(row)
        _balancer_add(balancer, card_id=prev_dir.anki_card_id, note_id=existing.anki_note_id, interval=row.interval)
        graded_count += 1
        if grade_cls == "due":
            review_budget -= 1

    # ── Staged creation over ranked candidates, truncated to budget (D2/D3) ──
    # No persisted cursor: each listen recomputes lesson-word-set minus tracked
    # cards and takes the top of the ranking; cards created by this listen are
    # "existing" for the next one.
    ranked = _rank_listen_candidates([], lemma_candidates, lemma_occurrences)
    for _kind, cand in ranked:
        if created_count >= creation_budget:
            break
        lemma = cand
        is_func = is_function_word_for(
            lemma, lemma_to_surfaces.get(lemma, set()), lesson.language_code, surface_to_upos
        )
        sent = lemma_to_sentence.get(lemma, "")
        # Cloze rows blank the surface as it appeared, not the dictionary lemma:
        # the lemmatizer may map an inflected surface to a different lemma (classla
        # "sem" → "biti") that isn't in the sentence. Store the cloze pre-built;
        # sync's idempotent make_cloze_text passes it through. (Phase 2b.)
        stored_sentence = make_cloze_text(lemma_to_first_surface.get(lemma, lemma), sent) if is_func else sent
        unit = SyntacticUnit(
            text=lemma,
            translation=_resolve_gloss_translation(
                lemma,
                token_glosses,
                lemma_to_surfaces.get(lemma, set()),
                lemma_to_first_surface.get(lemma, lemma),
                language_code=lesson.language_code,
            ),
            word_count=1,
            difficulty=1,
            source="llm",
            lemma=lemma,
            card_type="cloze" if is_func else "vocab",
            source_sentence=stored_sentence,
            source_sentence_translation=sentence_translations.get(sent, ""),
        )
        db.add_collocation(unit, language_code=lesson.language_code)
        if is_func:
            coll = db.get_collocation_by_lemma_with_id(lemma)
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
            new_id, _ = db.get_collocation_by_lemma_with_id(lemma)
            await _generate_add_time_media(
                db, llm, new_id, unit, language_code=lesson.language_code, used_image_urls=used_image_urls
            )
        created_count += 1
    remaining_candidates = len(ranked) - created_count

    # Server-side listened state (TT-only, never syncs): one row per listen.
    db.record_listen(body.lesson_id)

    registered = created_count + graded_count
    return {
        "status": "ok",
        "registered": registered,
        "created": created_count,
        "graded": graded_count,
        "remaining_candidates": remaining_candidates,
        "listen_count": db.count_listens(body.lesson_id),
    }


@router.get("/listens", status_code=200)
async def get_listens(request: Request):
    db = request.state.srs_db
    return {"lessons": db.get_listened_lessons()}


@router.post("/listens/import", status_code=200)
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


@router.get("/lesson/{lesson_id}/review-queue", status_code=200)
async def get_lesson_review_queue(lesson_id: str, request: Request, response: Response) -> dict:
    """Lesson-scoped "Check your work" queue (learning-modes slice 1, D6).

    Items share ``_queue_item_to_dict``'s shape with /review-queue; grading a
    served item goes through the normal per-item feedback endpoint (an Again
    on an auto-Good'ed card is an ordinary same-day lapse). Strictly
    read-only w.r.t. parity state: no learning-cutoff advance, no
    session_main_queue write, no unbury sweep, no queue-engine involvement —
    the frozen main-queue order must survive this endpoint unchanged (pinned
    by the parity-guard test). Off-main-queue grading already exists today
    (Read mode); the freeze reconciliation drops graded keys by design.

    Inclusion: learning/relearning cards; tracked NEW cards in D2 rank order
    (grading one introduces it, same as Read mode's tap-to-introduce); REVIEW
    cards touched today (the /listen auto-Good correction set — same
    local-midnight window as ``_listen_grade_class``) or already due.
    Everything else (known/suspended/buried/untracked, future-due untouched
    REVIEW) is excluded. Vocab serves recognition only — never fighting
    Layer 65's production gate — and cloze serves production only.
    """
    response.headers["Cache-Control"] = "no-store"
    store = request.state.content_store
    lesson = store.get_lesson(lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    db = request.state.srs_db

    words = await anyio.to_thread.run_sync(_analyze_lesson_words, lesson, db)

    # Build variant index once — mirrors the transcript's _build_variant_index
    # so the lesson queue resolves the same cards the transcript shows as tracked.
    variant_index = _build_variant_index(db, lesson.language_code)

    # Anki-day rollover window (NOT local midnight) — same convention as
    # mark_lesson_listened's _listen_grade_class window, via the shared helper.
    today = anki_today()
    today_start, today_end = anki_day_bounds_utc_dt(today)
    now = datetime.datetime.now(datetime.UTC)

    seen: set[int] = set()
    learning: list[tuple[datetime.datetime, int, SRSItem, Direction]] = []
    review: list[tuple[datetime.datetime, int, SRSItem, Direction]] = []
    new_kp: list[tuple[int, SRSItem, Direction]] = []
    new_lemma_order: list[str] = []
    new_by_lemma: dict[str, tuple[int, SRSItem, Direction]] = {}

    def _classify(rid: int, item: SRSItem) -> str | None:
        """Return the D6 bucket for a tracked card, or None if excluded."""
        direction = Direction.PRODUCTION if item.syntactic_unit.card_type == "cloze" else Direction.RECOGNITION
        ds = item.directions.get(direction)
        if ds is None:
            return None
        if ds.state in (SRSState.LEARNING, SRSState.RELEARNING):
            learning.append((ds.due_at, rid, item, direction))
            return "learning"
        if ds.state == SRSState.NEW:
            return "new"
        if ds.state == SRSState.REVIEW:
            lr = ds.last_review
            touched_today = lr is not None and today_start <= lr.astimezone(datetime.UTC) < today_end
            if touched_today or ds.due_at <= now:
                review.append((ds.due_at, rid, item, direction))
                return "review"
        return None

    for kp in lesson.key_phrases:
        item = db.get_collocation(kp.phrase)
        if item is None:
            continue
        rid = db.get_collocation_id_by_guid(item.guid)
        if rid is None or rid in seen:
            continue
        seen.add(rid)
        if _classify(rid, item) == "new":
            direction = Direction.PRODUCTION if item.syntactic_unit.card_type == "cloze" else Direction.RECOGNITION
            new_kp.append((rid, item, direction))

    for lemma in words.first_sentence:
        res = _resolve_card_for_lemma(db, lemma, words.surfaces.get(lemma, set()), variant_index)
        if res is None:
            continue
        rid, item = res
        if rid in seen:
            continue
        seen.add(rid)
        if _classify(rid, item) == "new":
            direction = Direction.PRODUCTION if item.syntactic_unit.card_type == "cloze" else Direction.RECOGNITION
            new_lemma_order.append(lemma)
            new_by_lemma[lemma] = (rid, item, direction)

    # Bucket order (1)(2)(3) per D6; within learning/review due_at then row id,
    # NEW re-uses the exact /listen creation ranking so "what you'd study next"
    # and "what a listen would create next" never disagree.
    learning.sort(key=lambda t: (t[0], t[1]))
    review.sort(key=lambda t: (t[0], t[1]))
    ordered: list[tuple[int, SRSItem, Direction]] = [(rid, item, d) for _, rid, item, d in learning]
    for kind, entry in _rank_listen_candidates(new_kp, new_lemma_order, words.occurrences):
        ordered.append(entry if kind == "kp" else new_by_lemma[entry])
    ordered.extend((rid, item, d) for _, rid, item, d in review)

    ambiguous = db.get_ambiguous_surfaces(lesson.language_code)
    return {
        "queue": [_queue_item_to_dict(rid, item, lesson.language_code, d, db, ambiguous) for rid, item, d in ordered]
    }


@router.get("/lesson/{lesson_id}/transcript", status_code=200)
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


@router.post("/translate", status_code=200)
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


@router.post("/translate-missing", status_code=200)
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


@router.post("/backfill-translations", status_code=200)
async def backfill_translations(request: Request):
    """One-time repair: fill empty translations from all stored lesson glosses."""
    store = request.state.content_store
    db = request.state.srs_db
    glosses = store.get_all_token_glosses()
    updated = db.backfill_translations(glosses)
    return {"updated": updated, "glosses_found": len(glosses)}


@router.get("/stats", status_code=200)
async def get_stats(request: Request):
    db = request.state.srs_db
    today = anki_today()
    return {"total": db.count_collocations(), "due_today": db.count_due_collocations(today)}


@router.get("/queue-stats", status_code=200)
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


@router.post("/items", status_code=201)
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
) -> dict:
    """Add a NEW collocation and return its ``{id, was_created, item}`` dict.

    Shared persistence tail for the card-creating endpoints (``/items/base`` and
    ``/inflection-clozes``): insert (idempotent by guid), look the id back up,
    best-effort synthesize cloze audio when ``synthesize`` and the row is newly
    created, then serialize. ``audio_sentence`` is the *raw* sentence (never the
    pre-clozed ``source_sentence``) and ``audio_word`` the surface to voice. For a
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
        await _generate_add_time_media(db, llm, coll_id, unit, language_code=language_code)

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


@router.post("/items/base", status_code=200)
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
    upos = next((ta.upos for ta in analyses if ta.surface.casefold() == body.surface.casefold()), None)
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

    unit = SyntacticUnit(
        text=lemma,
        translation=translation,
        word_count=1,
        difficulty=1,
        source="user",
        lemma=lemma,
        card_type=card_type,
        source_sentence=source_sentence,
    )
    return await _persist_new_card(
        db,
        unit,
        lang,
        synthesize=is_func,
        audio_sentence=body.sentence,
        audio_word=body.surface,
        llm=getattr(request.app.state, "llm", None),
    )


@router.get("/items", status_code=200)
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


@router.patch("/items/{item_id}", status_code=200)
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


@router.delete("/items/{item_id}", status_code=200)
async def delete_item(item_id: int, request: Request):
    db = request.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete_collocation(item_id)
    return {"status": "deleted"}


@router.post("/items/bulk-delete", status_code=200)
async def bulk_delete_items(body: BulkDeleteRequest, request: Request):
    db = request.state.srs_db
    deleted = db.delete_collocations(body.ids)
    return {"deleted": deleted}


@router.post("/items/{item_id}/reset", status_code=200)
async def reset_item(item_id: int, request: Request):
    db = request.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    db.reset_collocation(item_id)
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return _item_to_dict(row_id, item, lang)


@router.post("/items/{item_id}/state", status_code=200)
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


@router.post("/items/{item_id}/restore-known", status_code=200)
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


@router.post("/items/{item_id}/untrack", status_code=200)
async def untrack_item(item_id: int, request: Request):
    db = request.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    result = db.untrack_collocation(item_id)
    if result["action"] == "deleted":
        return {"action": "deleted"}
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return {"action": "suspended", "item": _item_to_dict(row_id, item, lang)}


@router.post("/items/{item_id}/suspend", status_code=200)
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


@router.post("/ignored-lemmas", status_code=200)
async def add_ignored_lemma(body: IgnoreLemmaRequest, request: Request):
    db = request.state.srs_db
    db.add_ignored_lemma(body.language_code, body.lemma)
    return {"status": "ok"}


@router.delete("/ignored-lemmas", status_code=200)
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


@router.post("/inflection-clozes", status_code=200)
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


@router.get("/review-queue", status_code=200)
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
