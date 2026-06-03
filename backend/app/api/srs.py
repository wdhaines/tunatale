"""SRS state and review endpoints."""

from __future__ import annotations

import datetime
import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse

from app.api.models import (
    BulkDeleteRequest,
    CreateBaseCardRequest,
    CreateItemRequest,
    DrillRequest,
    InflectionClozeRequest,
    ListenRequest,
    SetStateRequest,
    SuspendRequest,
    TranslateRequest,
    UpdateItemRequest,
)
from app.audio.cloze_tts import synthesize_cloze_audios
from app.common.guid import compute_guid
from app.llm.translate import translate_term
from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.feedback import rating_from_input
from app.srs.fsrs import Rating, build_revlog_row, schedule
from app.srs.function_words import (
    format_morphology_hint,
    is_function_word,
    make_cloze_text,
    make_morphology_cloze_text,
)
from app.srs.lemmatizer import get_lemmatizer, lemmatize_surfaces_in_context
from app.srs.queue_stats import (
    advance_learning_cutoff,
    build_live_load_balancer,
    clear_session_main_queue,
    get_session_main_queue,
    resolve_bury_new,
    resolve_bury_review,
    resolve_col_crt,
    resolve_daily_new_cap,
    resolve_daily_review_cap,
    resolve_fsrs_params,
    resolve_learning_cutoff,
    resolve_new_spread,
    set_session_main_queue,
)
from app.srs.tokenizer import tokenize
from app.srs.transcript import extract_transcript

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

_lemmatizer = get_lemmatizer()

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


@router.get("/due", status_code=200)
async def get_due_collocations(request: Request, direction: str = "recognition"):
    db = request.app.state.srs_db
    today = datetime.date.today()
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
    db = request.app.state.srs_db
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

    db = request.app.state.srs_db
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
    row = build_revlog_row(item_id, dir_enum, prev_dir, updated.directions[dir_enum], rating, body.time_ms, now=now)
    db.append_revlog(row)
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


@router.get("/media/{filename}", status_code=200)
async def serve_media(filename: str, request: Request):
    media_dir = _MEDIA_DIR
    file_path = (media_dir / filename).resolve()
    if not str(file_path).startswith(str(media_dir.resolve())):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(file_path)


def _listen_grade_eligible(
    rec: DirectionState | None, today_start: datetime.datetime, today_end: datetime.datetime
) -> bool:
    """True iff the recognition direction should accept a /listen Good grade."""
    if rec is None:
        return False
    if rec.state in (SRSState.LEARNING, SRSState.RELEARNING):
        return True
    if rec.state == SRSState.REVIEW:
        if rec.last_review is None:
            return True
        lr = rec.last_review
        if not isinstance(lr, datetime.datetime):
            return True
        return not (today_start <= lr.astimezone(datetime.UTC) < today_end)
    return False


@router.post("/listen", status_code=200)
async def mark_lesson_listened(body: ListenRequest, request: Request):
    store = request.app.state.content_store
    lesson = store.get_lesson(body.lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    db = request.app.state.srs_db
    col_crt = resolve_col_crt(db)
    # One session balancer for the whole request; each grade below feeds itself
    # back via _balancer_add so later grades in this lesson see earlier ones.
    balancer = build_live_load_balancer(db, now=datetime.datetime.now(datetime.UTC), col_crt=col_crt)

    # ── Word-level tracking from NATURAL_SPEED section ──────────────────
    from app.models.lesson import SectionType, extract_sentence_translations_from_translated

    token_glosses: dict[str, str] = lesson.generation_metadata.get("token_glosses", {})
    sentence_translations: dict[str, str] = lesson.generation_metadata.get("sentence_translations", {})
    # Backfill path: pre-Layer-N lessons have no `sentence_translations` in
    # metadata. Recover from the TRANSLATED section so old lessons can still
    # populate cloze cards' Back Extra. First-occurrence wins on the merge.
    derived_st = extract_sentence_translations_from_translated(lesson)
    for k, v in derived_st.items():
        sentence_translations.setdefault(k, v)

    natural_speed = next(
        (s for s in lesson.sections if s.section_type == SectionType.NATURAL_SPEED),
        None,
    )

    unique_lemmas: set[str] = set()
    lemma_to_sentence: dict[str, str] = {}
    lemma_to_surfaces: dict[str, set[str]] = {}
    # The surface as it first appeared, paired with lemma_to_sentence — used to
    # blank the *surface* (not the dictionary lemma) in plain function-word clozes.
    lemma_to_first_surface: dict[str, str] = {}
    # Surface (casefolded) → classla UPOS, for POS-first function-word detection.
    # Empty/"" under LowercaseLemmatizer, so the curated include-list is the only
    # signal there (legacy behavior); classla supplies AUX/ADP/PRON/... and catches
    # the whole biti paradigm (ste/smo/so) without enumerating surfaces.
    surface_to_upos: dict[str, str] = {}
    if natural_speed is not None:
        for phrase in natural_speed.phrases:
            if phrase.language_code != lesson.language_code:
                continue
            surfaces = tokenize(phrase.text)
            phrase_lemmas = lemmatize_surfaces_in_context(surfaces, phrase.text, _lemmatizer, lesson.language_code)
            for ta in _lemmatizer.analyze_sentence(phrase.text, lesson.language_code):
                surface_to_upos.setdefault(ta.surface.casefold(), ta.upos)
            for surface, lemma in zip(surfaces, phrase_lemmas, strict=True):
                unique_lemmas.add(lemma)
                if lemma not in lemma_to_sentence:
                    lemma_to_sentence[lemma] = phrase.text
                    lemma_to_first_surface[lemma] = surface
                lemma_to_surfaces.setdefault(lemma, set()).add(surface)

    # ── Today window (mirrors count_new_introduced_today convention) ────
    local_tz = datetime.datetime.now().astimezone().tzinfo
    today = datetime.date.today()
    today_start = datetime.datetime.combine(today, datetime.time(0), tzinfo=local_tz).astimezone(datetime.UTC)
    today_end = today_start + datetime.timedelta(days=1)

    created_count = 0
    graded_count = 0

    for lemma in unique_lemmas:
        # Cloze cards are always on, for every language (no feature flag, no
        # language gate — see ~/.claude/plans/word-learning-state-machine.md
        # Phase 1). Whether a cloze is actually created is capability-driven:
        # `is_func` is only true where a function-word config exists for the
        # language, so non-Slovene content words still fall through to vocab.
        # POS-first: each surface carries its UPOS (when an analyzer is present).
        is_func = is_function_word(lemma, lesson.language_code) or any(
            is_function_word(s, lesson.language_code, upos=surface_to_upos.get(s.casefold()))
            for s in lemma_to_surfaces.get(lemma, set())
        )

        existing = db.get_collocation_by_lemma(lemma)
        if existing is None:
            # A card may be keyed by its surface form (e.g. greeting "dobrodošli",
            # whose dictionary lemma "dobrodošel" has no card) — grade it rather
            # than spawning a duplicate.
            for s in lemma_to_surfaces.get(lemma, set()):
                if s.lower() != lemma:
                    existing = db.get_collocation_by_lemma(s.lower())
                    if existing is not None:
                        break

        if existing is None:
            # ── Create new row (cloze for function words, vocab for content words) ──
            sent = lemma_to_sentence.get(lemma, "")
            # Cloze rows blank the surface as it appeared, not the dictionary lemma:
            # the lemmatizer may map an inflected surface to a different lemma (classla
            # "sem" → "biti") that isn't in the sentence. Store the cloze pre-built;
            # sync's idempotent make_cloze_text passes it through. (Phase 2b.)
            stored_sentence = make_cloze_text(lemma_to_first_surface.get(lemma, lemma), sent) if is_func else sent
            unit = SyntacticUnit(
                text=lemma,
                translation=token_glosses.get(lemma, ""),
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
                    await synthesize_cloze_audios(db, new_id, sent, lemma_to_first_surface.get(lemma, lemma))
                except Exception:
                    _logger.warning("Failed to synthesize cloze audio for %r", lemma)
            created_count += 1
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
                coll_with_id = db.get_collocation_by_lemma_with_id(lemma)
                existing_id, _ = coll_with_id
                sent = lemma_to_sentence.get(lemma, "")
                if sent and not db.get_sentence_audio_filename(existing_id):
                    try:
                        await synthesize_cloze_audios(db, existing_id, sent, lemma_to_first_surface.get(lemma, lemma))
                    except Exception:
                        _logger.warning("Failed to synthesize cloze audio for %r", lemma)
                continue

            rec = existing.directions.get(Direction.RECOGNITION)
            if rec is None:
                continue

            if _listen_grade_eligible(rec, today_start, today_end):
                rating = _WORD_RATING_MAP.get(body.word_ratings.get(lemma, "good"), Rating.GOOD)
                now = datetime.datetime.now(datetime.UTC)
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
                # `existing` came from db.get_collocation(); guid is valid → row always exists.
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
                )
                db.append_revlog(row)
                _balancer_add(
                    balancer, card_id=prev_dir.anki_card_id, note_id=existing.anki_note_id, interval=row.interval
                )
                graded_count += 1

    # ── Key phrase registration + auto-grade ────────────────────────────
    for kp in lesson.key_phrases:
        existing = db.get_collocation(kp.phrase)
        if existing is None:
            unit = SyntacticUnit(
                text=kp.phrase,
                translation=kp.translation,
                word_count=min(8, max(1, len(kp.phrase.split()))),
                difficulty=1,
                source="llm",
            )
            db.add_collocation(unit, language_code=lesson.language_code)
            created_count += 1
        else:
            if existing.syntactic_unit.card_type == "cloze":
                continue
            rec = existing.directions.get(Direction.RECOGNITION)
            if rec is None:
                continue
            if _listen_grade_eligible(rec, today_start, today_end):
                now = datetime.datetime.now(datetime.UTC)
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
                # `existing` came from db.get_collocation(); guid is valid → row always exists.
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
                )
                db.append_revlog(row)
                _balancer_add(
                    balancer, card_id=prev_dir.anki_card_id, note_id=existing.anki_note_id, interval=row.interval
                )
                graded_count += 1

    registered = created_count + graded_count
    return {"status": "ok", "registered": registered}


@router.get("/lesson/{lesson_id}/transcript", status_code=200)
async def get_lesson_transcript(lesson_id: str, request: Request):
    from datetime import date

    store = request.app.state.content_store
    lesson = store.get_lesson(lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")

    db = request.app.state.srs_db
    today = date.today()
    transcript = extract_transcript(lesson, db, _lemmatizer, today=today)

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
                        "card_type": w.card_type,
                        "active_state": w.active_state,
                        "active_direction": w.active_direction,
                        "is_due": w.is_due,
                        "progress": w.progress,
                        "inflectable": w.inflectable,
                        "inflection_feature": w.inflection_feature,
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


_VALID_LANGUAGE_CODES = frozenset({"sl", "en"})


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
    db = request.app.state.srs_db
    llm = request.app.state.llm
    language = request.app.state.language

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
    store = request.app.state.content_store
    db = request.app.state.srs_db
    glosses = store.get_all_token_glosses()
    updated = db.backfill_translations(glosses)
    return {"updated": updated, "glosses_found": len(glosses)}


@router.get("/stats", status_code=200)
async def get_stats(request: Request):
    db = request.app.state.srs_db
    today = datetime.date.today()
    return {"total": db.count_collocations(), "due_today": db.count_due_collocations(today)}


@router.get("/queue-stats", status_code=200)
async def get_queue_stats(request: Request, response: Response):
    # Live state; never cache. Without this, a normal browser refresh can be
    # served from heuristic disk cache and the badges go stale.
    response.headers["Cache-Control"] = "no-store"
    db = request.app.state.srs_db
    today = datetime.date.today()
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
    review_remaining = max(0, min(review_due_raw, review_cap - reviews_today))
    # New-sibling bury (Anki's bury_new): a new card whose sibling is gathered
    # into today's queue (review-due-today / learning / graded-today) is buried
    # out of the new pool. `_compute_live_main` already applies this to the
    # served queue; the bury-aware count keeps the badge consistent with it.
    # Falls back to the raw count when bury_new is off (no regression).
    bury_new, _ = resolve_bury_new(db)
    new_available = db.count_new_available_collocations(today) if bury_new else db.count_new_available()
    return {
        "new": min(remaining_quota, new_available),
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
    db = request.app.state.srs_db
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
    rows, _ = db.list_collocations(search=body.text, limit=1)
    if not rows:
        raise HTTPException(status_code=500, detail="Failed to retrieve created item")
    row_id, item, lang = rows[0]
    return _item_to_dict(row_id, item, lang)


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
    db = request.app.state.srs_db
    lang = body.language_code
    lemma = body.lemma.casefold()

    # POS-first function-word detection: read the active surface's UPOS from the
    # sentence (classla → AUX for biti forms etc.; LowercaseLemmatizer → "" so the
    # curated include-list is the sole signal). The surface is checked too — an
    # inflected function form (classla "sem" → lemma "biti") classifies via its
    # surface even when the dictionary lemma isn't itself a function word.
    analyses = _lemmatizer.analyze_sentence(body.sentence, lang)
    upos = next((ta.upos for ta in analyses if ta.surface.casefold() == body.surface.casefold()), None)
    is_func = is_function_word(lemma, lang, upos=upos) or is_function_word(body.surface, lang, upos=upos)
    if is_func:
        # Blank the surface as it appeared, not the dictionary lemma (Phase 2b):
        # the cloze must reference the word present in the stored sentence.
        source_sentence = make_cloze_text(body.surface, body.sentence)
        card_type = "cloze"
    else:
        source_sentence = body.sentence
        card_type = "vocab"

    unit = SyntacticUnit(
        text=lemma,
        translation=body.translation,
        word_count=1,
        difficulty=1,
        source="user",
        lemma=lemma,
        card_type=card_type,
        source_sentence=source_sentence,
    )
    was_created = db.add_collocation(unit, language_code=lang)

    guid = compute_guid(lemma, lang, "")
    coll_id = db.get_collocation_id_by_guid(guid)
    if coll_id is None:  # pragma: no cover — defensive; add_collocation just inserted
        raise HTTPException(status_code=500, detail="Failed to create base card")

    if is_func and was_created:
        try:
            await synthesize_cloze_audios(db, coll_id, body.sentence, body.surface)
        except Exception:
            _logger.warning("Failed to synthesize base-cloze audio for %r", lemma)

    result = db.get_collocation_by_id(coll_id)
    if result is None:  # pragma: no cover — defensive; id came from get_collocation_id_by_guid
        raise HTTPException(status_code=500, detail="Failed to retrieve base card")
    _, item, _ = result
    return {"id": coll_id, "was_created": was_created, "item": _item_to_dict(coll_id, item, lang)}


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
    db = request.app.state.srs_db
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
    return {"items": [_item_to_dict(rid, item, lang) for rid, item, lang in rows], "total": total}


@router.patch("/items/{item_id}", status_code=200)
async def patch_item(item_id: int, body: UpdateItemRequest, request: Request):
    db = request.app.state.srs_db
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
    db = request.app.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete_collocation(item_id)
    return {"status": "deleted"}


@router.post("/items/bulk-delete", status_code=200)
async def bulk_delete_items(body: BulkDeleteRequest, request: Request):
    db = request.app.state.srs_db
    deleted = db.delete_collocations(body.ids)
    return {"deleted": deleted}


@router.post("/items/{item_id}/reset", status_code=200)
async def reset_item(item_id: int, request: Request):
    db = request.app.state.srs_db
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
    db = request.app.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if body.state == "learning":
        db.promote_to_learning(item_id)
    else:
        db.set_state_by_id(item_id, _STATE_MAP[body.state])
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return _item_to_dict(row_id, item, lang)


@router.post("/items/{item_id}/untrack", status_code=200)
async def untrack_item(item_id: int, request: Request):
    db = request.app.state.srs_db
    if db.get_collocation_by_id(item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    result = db.untrack_collocation(item_id)
    if result["action"] == "deleted":
        return {"action": "deleted"}
    row_id, item, lang = db.get_collocation_by_id(item_id)
    return {"action": "suspended", "item": _item_to_dict(row_id, item, lang)}


@router.post("/items/{item_id}/suspend", status_code=200)
async def suspend_item(item_id: int, body: SuspendRequest, request: Request):
    db = request.app.state.srs_db
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


_FNV_OFFSET_BASIS_64 = 0xCBF29CE484222325
_FNV_PRIME_64 = 0x100000001B3


def _fnv1a_64_i64(*args: int) -> int:
    """Compute Anki's `fnvhash(args...)` over the i64 little-endian bytes.

    Mirrors rslib/src/storage/sqlite.rs:add_fnvhash_function — FNV-1a 64-bit
    hash, fed each i64 argument as 8 little-endian bytes via `write_i64`.
    Returned as a Python int in the signed-i64 range so direct comparison
    matches SQLite's `ORDER BY fnvhash(...)` ordering.
    """
    h = _FNV_OFFSET_BASIS_64
    for a in args:
        for byte in (a & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little"):
            h ^= byte
            h = (h * _FNV_PRIME_64) & 0xFFFFFFFFFFFFFFFF
    return h - (1 << 64) if h >= (1 << 63) else h


def _merge_by_retrievability_ascending(
    rec: list[tuple[int, SRSItem, str]],
    prod: list[tuple[int, SRSItem, str]],
    today: datetime.date,
    col_crt: int | None = None,
) -> list[tuple[int, SRSItem, str, Direction]]:
    """Sort the combined due pool by retrievability ascending.

    Mirrors Anki's SortOrder::RetrievabilityAscending: every card with
    due_date <= today competes in one flat pool, ordered by R alone. An overdue
    but well-remembered card sits behind a today-due card the user is about to
    forget. Tie-break matches Anki exactly: `fnvhash(anki_card_id, anki_card_mod)`
    appended after the primary sort (rslib/src/storage/card/mod.rs:897). When
    either field is missing, fall back to anki_card_id then row_id so the order
    stays deterministic but no longer claims Anki parity.
    """
    from app.srs.fsrs import compute_retrievability
    from app.srs.queue_stats import resolve_fsrs_params

    params, _ = resolve_fsrs_params()
    dr = params.desired_retention
    decay = params.decay

    combined: list[tuple[int, SRSItem, str, Direction]] = [
        (row_id, item, lang, Direction.RECOGNITION) for row_id, item, lang in rec
    ]
    combined.extend((row_id, item, lang, Direction.PRODUCTION) for row_id, item, lang in prod)

    def _key(t: tuple[int, SRSItem, str, Direction]) -> tuple:
        row_id, item, _, direction = t
        dstate = item.directions[direction]
        r = compute_retrievability(dstate, today, desired_retention=dr, decay=-decay, col_crt=col_crt)
        if dstate.anki_card_id is not None and dstate.anki_card_mod is not None:
            return (r, 0, _fnv1a_64_i64(dstate.anki_card_id, dstate.anki_card_mod), 0)
        # Fallback for rows that haven't been synced from Anki yet.
        return (r, 1, dstate.anki_card_id or 0, row_id)

    combined.sort(key=_key)
    return combined


def _merge_directions(
    rec: list[tuple[int, SRSItem, str]],
    prod: list[tuple[int, SRSItem, str]],
) -> list[tuple[int, SRSItem, str, Direction]]:
    """Merge new-card directions in Anki's gather order.

    Mirrors Anki's `add_new_card` (rslib `queue/builder/gathering.rs:63-169`),
    which fetches cards under `NewCardSorting::HighestPosition` =
    ``"due DESC, ord ASC"`` (storage/card/mod.rs:923) and proactively buries
    the LATER sibling per note. By interleaving both directions in that gather
    order BEFORE sibling-bury runs, the higher-anki_due sibling wins. The
    downstream Template re-sort (applied to the survivors in `get_review_queue`)
    then ranks ord=0 (recognition) ahead of ord=1 (production).

    Sort key (LOWER sorts first):
      1. ``(0,)`` for ``anki_due IS NULL`` else ``(1, -anki_due)`` — NULLS FIRST, DESC
      2. ord ASC (Direction.RECOGNITION = 0, Direction.PRODUCTION = 1)
      3. anki_card_id ASC NULLS LAST (deterministic tiebreak)
      4. row_id ASC (final tiebreak)

    Together with the post-bury Template sort in `get_review_queue`, this
    reproduces the gather → bury → Template-sort pipeline exactly.

    Phase 3 note (Layer 65): the production NEW pool is gated upstream in
    `get_new_items` — a production card is withheld until its recognition
    sibling has graduated past the learning arc. So for a paired both-NEW note
    no production card reaches this merge; recognition wins. The "higher-anki_due
    sibling wins" behavior only applies once recognition is REVIEW (production
    introducible) or among recognition cards / cloze cards.
    """
    combined: list[tuple[int, SRSItem, str, Direction]] = []
    for row_id, item, lang in rec:
        combined.append((row_id, item, lang, Direction.RECOGNITION))
    for row_id, item, lang in prod:
        combined.append((row_id, item, lang, Direction.PRODUCTION))

    def _gather_key(
        t: tuple[int, SRSItem, str, Direction],
    ) -> tuple[int, int, int, int, int]:
        row_id, item, _lang, direction = t
        ds = item.directions[direction]
        ord_value = 0 if direction == Direction.RECOGNITION else 1
        # Layer 33: distinguish fresh /listen-added rows from stale/phantom
        # directions when anki_due is NULL. A fresh add has no anki_note_id at
        # the COLLOCATION level (never pushed to Anki); it should sit at the top
        # of the new bucket (NULLS FIRST). A phantom direction belongs to a
        # collocation that IS linked to Anki but whose own anki_due never got
        # populated — typically a cross-note homonym link that sync_pull can't
        # reach via the parent collocation. Sinking phantoms to the bottom keeps
        # them out of the queue head while preserving the listen-first benefit.
        primary = ((0, 0) if item.anki_note_id is None else (2, 0)) if ds.anki_due is None else (1, -ds.anki_due)
        return (*primary, ord_value, ds.anki_card_id or (1 << 62), row_id)

    combined.sort(key=_gather_key)
    return combined


def _spread_mix(
    reviews: list[tuple[int, SRSItem, str, Direction]],
    news: list[tuple[int, SRSItem, str, Direction]],
) -> list[tuple[int, SRSItem, str, Direction]]:
    """Interleave news into reviews matching Anki's Intersperser exactly.

    Port of rslib/src/scheduler/queue/builder/intersperser.rs. Uses the
    continuous ratio (one_len + 1) / (two_len + 1) so the first item comes from
    the longer iter when populations are imbalanced, and items are distributed
    evenly between the start and end. For 10 reviews + 2 news the first new
    appears at position 3, not position 5 like a floor-ratio approach.
    """
    if not news:
        return list(reviews)
    if not reviews:
        return list(news)
    one_len = len(reviews)
    two_len = len(news)
    ratio = (one_len + 1) / (two_len + 1)
    one_idx = 0
    two_idx = 0
    result: list[tuple[int, SRSItem, str, Direction]] = []
    while one_idx < one_len or two_idx < two_len:
        if one_idx < one_len and two_idx < two_len:
            relative_idx2 = (two_idx + 1) * ratio
            if relative_idx2 < (one_idx + 1):
                result.append(news[two_idx])
                two_idx += 1
            else:
                result.append(reviews[one_idx])
                one_idx += 1
        elif one_idx < one_len:
            result.append(reviews[one_idx])
            one_idx += 1
        else:
            result.append(news[two_idx])
            two_idx += 1
    return result


def _queue_item_to_dict(row_id: int, item: SRSItem, lang: str, direction: Direction, db) -> dict:
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
    base = _item_to_dict(row_id, item, lang, image_url, audio_url)
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


def _compute_live_main(db) -> list[tuple[int, SRSItem, str, Direction]]:
    """Build the post-spread `live_main` order from current DB state.

    Layer 29: exposed as a module-level function so `sync_pull` can eagerly
    rebuild the freeze immediately on sync completion, instead of waiting for
    the next `/review-queue` request. Anki rebuilds its queue at session
    open / sync; mirroring the rebuild moment keeps the first-new-card position
    aligned across apps right after sync.

    Mirrors the body of `get_review_queue` up through the spread step. Does NOT
    apply the cache reconciliation, the learning cards, or the collapse hack —
    those live in the route handler where the response is shaped.
    """
    today = datetime.date.today()

    db.unbury_if_needed(today)

    cap, _ = resolve_daily_new_cap(db)
    spread, _ = resolve_new_spread(db)
    bury_new, _ = resolve_bury_new(db)
    bury_review, _ = resolve_bury_review(db)
    col_crt = resolve_col_crt(db)

    introduced_today = db.count_new_introduced_today(today)
    new_quota = max(0, cap - introduced_today)
    buried = db.list_collocations_reviewed_today(today)

    due_rec = db.get_due_items(today, Direction.RECOGNITION)
    due_prod = db.get_due_items(today, Direction.PRODUCTION)
    due = _merge_by_retrievability_ascending(due_rec, due_prod, today, col_crt=col_crt)
    if bury_review:
        due = [t for t in due if t[0] not in buried]

    # Layer 32: fetch the FULL per-direction new pool, not a quota-based overfetch.
    # The bug was that a small per-direction limit truncates one direction before
    # the other, breaking cross-direction sibling-bury. For a paired note whose
    # prod sits outside the limit but whose rec slips in (because new_rec has
    # fewer total cards), the merge sees rec without prod → no bury → rec
    # survives → Template sort puts it ahead. Fetching unbounded per direction
    # makes the bury step see both siblings whenever both are state=new.
    # `count_new_available` is the total across both directions; using it as the
    # per-direction cap is a strict upper bound.
    _NEW_OVERFETCH = max(db.count_new_available(), new_quota + 50)
    new_rec = db.get_new_items(direction=Direction.RECOGNITION, limit=_NEW_OVERFETCH)
    new_prod = db.get_new_items(direction=Direction.PRODUCTION, limit=_NEW_OVERFETCH)
    new_combined = _merge_directions(new_rec, new_prod)
    if bury_new:
        new_combined = [t for t in new_combined if t[0] not in buried]

    learning_rec = db.get_learning_items(direction=Direction.RECOGNITION)
    learning_prod = db.get_learning_items(direction=Direction.PRODUCTION)
    learning_collocation_ids = {row_id for row_id, _, _ in learning_rec}
    learning_collocation_ids.update(row_id for row_id, _, _ in learning_prod)

    nonlearning_due = [t for t in due if t[1].directions[t[3]].state not in (SRSState.LEARNING, SRSState.RELEARNING)]
    nonlearning_new = [t for t in new_combined if t[0] not in learning_collocation_ids]

    seen_collocation_ids: set[int] = set(learning_collocation_ids)

    def _bury(cards, when):
        survivors = []
        for t in cards:
            if t[0] in seen_collocation_ids and when:
                continue
            seen_collocation_ids.add(t[0])
            survivors.append(t)
        return survivors

    nonlearning_due = _bury(nonlearning_due, bury_review)
    nonlearning_new = _bury(nonlearning_new, bury_new)
    nonlearning_new.sort(key=lambda t: 0 if t[3] == Direction.RECOGNITION else 1)
    nonlearning_new = nonlearning_new[:new_quota]

    if spread == 1:
        return nonlearning_due + nonlearning_new
    if spread == 2:
        return nonlearning_new + nonlearning_due
    return _spread_mix(nonlearning_due, nonlearning_new)


def build_and_freeze_main_queue(db) -> None:
    """Compute live_main and write it to session_main_queue cache.

    Called by sync_pull post-ingest so the freeze moment is at sync completion,
    matching when Anki rebuilds its own queue. Without this, TT freezes on the
    first /review-queue request after sync — which can be much later, with a
    different pool state, causing drift on the very-first-new-card position.
    """
    today = datetime.date.today()
    live_main = _compute_live_main(db)
    set_session_main_queue(db, today, [(t[0], t[3].value) for t in live_main])


@router.post("/inflection-clozes", status_code=200)
async def create_inflection_cloze(body: InflectionClozeRequest, request: Request) -> dict:
    """Create one morphology cloze for an inflected surface (Phase 4a).

    Gated on the lemma's base production being in REVIEW or KNOWN.
    Idempotent by guid. Follows the add_collocation contract
    (card_type=cloze, no Anki ids).
    """
    db = request.app.state.srs_db
    language_code = body.language_code

    # 1. Eligibility gate — base word production must be REVIEW/KNOWN
    base = db.get_collocation_by_lemma(body.lemma)
    if base is None:
        raise HTTPException(status_code=409, detail="Base word not yet learned")
    prod = base.directions.get(Direction.PRODUCTION)
    if prod is None or prod.state not in (SRSState.REVIEW, SRSState.KNOWN):
        raise HTTPException(status_code=409, detail="Base word not yet learned")

    # 2. Degenerate guard — surface == lemma reveals the answer
    if body.lemma.casefold() == body.surface.casefold():
        raise HTTPException(status_code=422, detail="Surface equals lemma — nothing to cloze")

    # 3. Build + create (mirrors /listen morphology-cloze block)
    disambig = f"morph:{body.feature.replace(':', '-')}"
    cloze_sent = make_morphology_cloze_text(body.surface, body.lemma, body.feature, body.sentence)
    grammar_hint = format_morphology_hint(body.lemma, body.feature)
    unit = SyntacticUnit(
        text=body.surface,
        translation="",
        word_count=1,
        difficulty=1,
        source="llm",
        lemma=body.lemma,
        disambig_key=disambig,
        card_type="cloze",
        source_sentence=cloze_sent,
        grammar=grammar_hint,
    )
    was_created = db.add_collocation(unit, language_code=language_code)

    # 4. Look up collocation id (needed for both new and existing)
    guid = compute_guid(body.surface, language_code, disambig)
    coll_id = db.get_collocation_id_by_guid(guid)
    if coll_id is None:  # pragma: no cover — defensive; add_collocation just inserted
        raise HTTPException(status_code=500, detail="Failed to create collocation")

    # 5. Synthesize audio if newly created (best-effort)
    if was_created:
        try:
            await synthesize_cloze_audios(db, coll_id, body.sentence, body.surface)
        except Exception:
            _logger.warning("Failed to synthesize inflection-cloze audio for %r", body.surface)

    # 6. Return serialized item
    result = db.get_collocation_by_id(coll_id)
    if result is None:  # pragma: no cover — defensive; id came from get_collocation_id_by_guid
        raise HTTPException(status_code=500, detail="Failed to retrieve created collocation")
    _, srs_item, _ = result
    return {
        "id": coll_id,
        "was_created": was_created,
        "item": _item_to_dict(coll_id, srs_item, language_code),
    }


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

    db = request.app.state.srs_db
    today = datetime.date.today()
    now = datetime.datetime.now(datetime.UTC)

    if session_start:
        advance_learning_cutoff(db, now)
        # Anki parity: deck-open also rebuilds the frozen main queue, not just
        # the learning cutoff. The frontend fires session_start=1 exactly when
        # the user navigates to /review (fresh mount / refresh / new tab) —
        # that's TT's deck-open analog. Without rebuilding here, TT's queue
        # stays frozen at the last sync_pull moment while Anki rebuilds on
        # every reopen, and the two apps' intersperser positions drift
        # irreversibly until next sync.
        clear_session_main_queue(db)
        build_and_freeze_main_queue(db)

    # Build live_main via the shared helper (also called by sync_pull eager
    # rebuild). The unbury sweep runs inside _compute_live_main.
    live_main = _compute_live_main(db)

    # Learning cards live alongside main — gather them separately so they can
    # surface as queue=1 (ready) at the head and queue=1-future (pending) at
    # the tail. Anki's queue dispatcher dispatches intraday-learning first
    # (queue/mod.rs:149-157).
    learning_rec = db.get_learning_items(direction=Direction.RECOGNITION)
    learning_prod = db.get_learning_items(direction=Direction.PRODUCTION)
    learning_cards: list[tuple[int, SRSItem, str, Direction]] = [
        (row_id, item, lang, Direction.RECOGNITION) for row_id, item, lang in learning_rec
    ]
    learning_cards.extend((row_id, item, lang, Direction.PRODUCTION) for row_id, item, lang in learning_prod)

    # Sort learning cards by TT's `due_at` (authoritative after a fresh grade,
    # before sync has refreshed Anki's `anki_due`), then anki_due, then
    # anki_card_id ASC, then row id. Anki's queue=1 sort is `(reps==0, due)`
    # only (rslib scheduler/queue/learning.rs cmp_by_reps_then_due); the
    # underlying SQL has no ORDER BY, so SQLite's stable scan order — effectively
    # cards.id ASC — is the de-facto final tiebreak. We mirror that with
    # anki_card_id; stability is intentionally NOT in the key because two cards
    # lapsed in the same review session share `due_at`/`anki_due` to the second,
    # and Anki ignores stability for ordering.
    _SENTINEL_FUTURE = datetime.datetime.max.replace(tzinfo=datetime.UTC)
    learning_cards.sort(
        key=lambda t: (
            t[1].directions[t[3]].due_at is None,
            t[1].directions[t[3]].due_at or _SENTINEL_FUTURE,
            t[1].directions[t[3]].anki_due is None,
            t[1].directions[t[3]].anki_due or 0,
            t[1].directions[t[3]].anki_card_id is None,
            t[1].directions[t[3]].anki_card_id or 0,
            t[0],
        ),
    )

    # Split learning into ready (past-due / null due_at) vs pending (future).
    # Anki parity: compare due_at against a frozen `cutoff` (Anki's
    # `current_learning_cutoff`), not live `now`. The cutoff is initialized to
    # `now` on first call and only advances on grade events / sync ingest, so a
    # learning card whose timer expires *between* grades stays pending until the
    # next grade — matching Anki's "card on screen is sticky" behavior.
    cutoff = resolve_learning_cutoff(db, fallback=now)
    ready_learning: list[tuple[int, SRSItem, str, Direction]] = []
    pending_learning: list[tuple[int, SRSItem, str, Direction]] = []
    for t in learning_cards:
        ds = t[1].directions[t[3]]
        if ds.due_at is None or ds.due_at <= cutoff:
            ready_learning.append(t)
        else:
            pending_learning.append(t)

    # `live_main` was computed above by `_compute_live_main` (spread already applied).

    # Anki parity: freeze the main queue per day. Anki builds `main` once at
    # deck-open and pops the head as cards are graded — it does NOT re-run the
    # intersperser on every grade. Without this freeze, TT recomputes the order
    # on every poll and always serves the lowest-R review next, diverging from
    # Anki whenever the intersperser would have placed a new card mid-sequence
    # (e.g. with 109 reviews + 30 new, Anki's intersperser puts the first new
    # card at position 3 — TT must surface it at that position too, not just
    # whenever counts shift).
    cached_order = get_session_main_queue(db, today)
    key_to_tuple = {(t[0], t[3].value): t for t in live_main}
    if cached_order is None:
        ordered_main = live_main
        set_session_main_queue(db, today, [(t[0], t[3].value) for t in live_main])
    else:
        seen_keys: set[tuple[int, str]] = set()
        ordered_main = []
        for cid, dir_str in cached_order:
            key = (cid, dir_str)
            if key in seen_keys or key not in key_to_tuple:
                continue
            seen_keys.add(key)
            ordered_main.append(key_to_tuple[key])
        # Anki parity for mid-day latecomers: only NEW-state cards may be
        # tail-appended (mid-day imports via /listen — a TT-only UX allowance).
        # REVIEW-state cards joining live_main without being in the cache are
        # state transitions (learning→review graduation, formerly buried→active);
        # Anki drops these from today's queue entirely
        # (rslib scheduler/queue/learning.rs:60-77 — maybe_requeue_learning_card
        # returns None for non-intraday-learning cards). The legitimate path for
        # review-state changes is cache invalidation on sync / deck-config change,
        # which rebuilds the frozen order from current state on the next call.
        for t in live_main:
            if (t[0], t[3].value) not in seen_keys:
                dstate = t[1].directions[t[3]]
                if dstate.state == SRSState.NEW:
                    ordered_main.append(t)

    # Anki parity: counts.all_zero() auto-bump. (Layer 36 trigger 4)
    # `CardQueues::counts()` in rslib/scheduler/queue/mod.rs:187-196 advances the
    # cutoff whenever the visible counts are all zero — so a pending learning
    # card whose timer ripens between grades surfaces on the next fetch without
    # the user having to grade. We mirror that here: if ready_learning AND
    # ordered_main are both empty, and any pending learning card's due_at is
    # past `now`, advance cutoff to `now` and re-split. Preserves the
    # "card on screen is sticky" invariant: when main has items, the freeze
    # stays in place (test_review_queue_auto_bump_skipped_when_main_has_items).
    if not ready_learning and not ordered_main and pending_learning:
        any_ripe = any(
            t[1].directions[t[3]].due_at is not None and t[1].directions[t[3]].due_at <= now for t in pending_learning
        )
        if any_ripe:
            advance_learning_cutoff(db, now)
            cutoff = now
            ready_learning = []
            new_pending = []
            for t in learning_cards:
                ds = t[1].directions[t[3]]
                if ds.due_at is None or ds.due_at <= cutoff:
                    ready_learning.append(t)
                else:
                    new_pending.append(t)
            pending_learning = new_pending

    # Anki parity "collapse" (rslib/.../queue/learning.rs:94-113): when main
    # is empty and the head of pending_learning was just graded
    # (last_review == cutoff), shift it past the next-soonest pending card so
    # the user doesn't see the same card immediately after grading. Anki does
    # this in `requeue_learning_entry` by bumping the entry's `due` to
    # `next.due + 1s`; we swap positions for the same effect since we rebuild
    # the queue from disk each request.
    if not ordered_main and len(pending_learning) >= 2:
        head_t = pending_learning[0]
        next_t = pending_learning[1]
        head_ds = head_t[1].directions[head_t[3]]
        next_ds = next_t[1].directions[next_t[3]]
        cutoff_ahead = cutoff + datetime.timedelta(seconds=1200)
        if (
            head_ds.last_review == cutoff
            and head_ds.due_at is not None
            and head_ds.due_at <= cutoff_ahead
            and next_ds.due_at is not None
            and next_ds.due_at >= head_ds.due_at
            and next_ds.due_at + datetime.timedelta(seconds=1) < cutoff_ahead
        ):
            pending_learning[0], pending_learning[1] = pending_learning[1], pending_learning[0]

    # 5. Ready learning first (Anki queue=1 priority), then reviews/new,
    #    then pending learning (cards waiting on their step timer).
    ordered = ready_learning + ordered_main + pending_learning

    return {"queue": [_queue_item_to_dict(*t, db) for t in ordered]}
