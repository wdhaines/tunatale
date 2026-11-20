"""The Grade page's backend: a lesson's grade plan, and a Save that applies an explicit grade set (tunatale-5i1p.2).

Contract and oracle: ``.beads-tasks/briefs/design-grade-page-2026-11.md`` § Slice 2.
Replaces the "Mark as Listened" popup's ``GET .../listen-preview`` and
``POST /listen`` for the new page; those routes stay until the popup retires.

Explicit grades only. A row the page does not send is untouched: no revlog row,
no pending row, its direction row unchanged. That retires the popup's polarity,
where an absent row meant "Good" (``ListenPreviewModal.svelte::buildRatings``).

Every grade goes through ``_apply_grade_now``, the path the popup's
hand-confirmed grades take, with one shared load balancer and a monotonic grade
clock across the batch. A grade on a REVIEW card that is not due logs Anki's
review-ahead kind (3) and does not charge today's review limit (owner,
2026-11-13: "Match Anki").

What a Save adds (word suggestions, contrast cards) and the study mark (the
lesson's own card list, one listen row) reuse the listen's own helpers, fed by
the same ``_build_listen_plan`` read the preview makes, so a suggestion's
``will_create`` and what the Save creates cannot drift (the 6a5c718 class).

Sentence mining (tunatale-h0q3.66): the plan lists the lesson's lines
(``sentence_lines``), and a Save carries the learner's changes to them as
``sentence_edits``, applied through ``sentence_mining`` (the one write path;
design-sentence-mining-2026-11.md § 5).
"""

from __future__ import annotations

import dataclasses
import datetime
from typing import Annotated

import anyio
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request

from app.api import app_state
from app.api.models import DueCountsResponse, GradePlanResponse, GradeRequest, GradeResponse
from app.api.srs import (
    _add_contrast_cards,
    _analyze_lesson_words,
    _apply_grade_now,
    _build_listen_plan,
    _create_ranked_words,
    _drill_dict,
    _listen_day_window,
    _ListenPlan,
    _record_study_pass,
    _review_is_due,
    _schedule_listen_media,
)
from app.llm.translate import generate_word_gloss
from app.models.srs_item import Direction, DirectionState, SRSItem, SRSState
from app.srs import sentence_mining
from app.srs.affix_drill_card import affix_card_key
from app.srs.anki_mirror.queue_stats import (
    advance_learning_cutoff,
    build_live_load_balancer,
    resolve_col_crt,
    resolve_fsrs_params,
    resolve_learning_steps,
    resolve_relearning_steps,
)
from app.srs.anki_mirror.rollover import anki_today
from app.srs.drill_card import DrillCard, drill_card, is_drill_key
from app.srs.lesson_card_mint import lesson_card_rows
from app.srs.lesson_cards import lesson_cards
from app.srs.sentence_card import SENTENCE_KEY_PREFIX, SentenceCard, sentence_card
from app.srs.sentence_lines import sentence_lines

router = APIRouter(prefix="/api/srs", tags=["srs"])

# A row the plan could have offered but which can no longer be graded: the
# page's plan is stale, so it refetches (409). Anything else wrong with a row
# is a bad request (422).
_STALE = {"graded_today", "not_ripe", "new", "suspended", "buried", "known"}


@dataclasses.dataclass(frozen=True)
class _LessonCard:
    item_id: int
    kind: str
    item: SRSItem
    sentence: SentenceCard | None = None
    drill: DrillCard | None = None

    def text(self, in_review: int) -> str:
        if self.drill is not None:
            return self.drill.view(in_review).answer
        return self.sentence.text if self.sentence is not None else self.item.syntactic_unit.text

    def gloss(self, in_review: int) -> str:
        if self.drill is not None:
            return self.drill.view(in_review).english
        return self.sentence.english if self.sentence is not None else self.item.syntactic_unit.translation or ""


def _line_prefix(lesson, content_id: str) -> str:
    """The key prefix of the lesson's lines: its list's set, else its content id (a lesson with no list)."""
    cards = (lesson.generation_metadata.get("story") or {}).get("cards")
    return f"{SENTENCE_KEY_PREFIX}{cards['set'] if cards else content_id}:"


def _lesson_cards(db, lesson, plan: _ListenPlan, content_id: str) -> dict[int, _LessonCard]:
    """The lesson's cards by id: its words, its phrases (a phrase wins a shared card), its own sentence
    and drill cards, and its affix drill's card.

    Sentence cards are every row of every line under the lesson's key prefix
    (``sent:<set>:``, or ``sent:<content id>:`` with no list): the lines its card
    list names, blanks mined since (they join the line's sibling group), and lines
    mined that the list does not name (tunatale-h0q3.66). A drill
    card (5i1p.4) is its own sibling group, read at the step its answers into review ask for (5i1p.10).
    A function-word cloze is not a row: the popup never graded one.
    """
    cards: dict[int, _LessonCard] = {cid: _LessonCard(cid, "word", item) for cid, (_, item) in plan.word_cards.items()}
    cards.update({cid: _LessonCard(cid, "phrase", item) for cid, (_, item) in plan.phrase_cards.items()})
    story = lesson.generation_metadata.get("story") or {}
    course_list = story.get("cards")
    lines: dict[str, None] = {}
    for card in lesson_cards(course_list, story.get("drills", [])) if course_list else []:
        made = sentence_card(card.unit)
        if made is not None:
            lines.setdefault(made.line_key, None)
        elif drill_card(card.unit) is not None:
            lines.setdefault(card.unit.disambig_key, None)
    for line_key in db.list_sibling_groups(_line_prefix(lesson, content_id)):
        if not is_drill_key(line_key):
            lines.setdefault(line_key, None)
    # The pattern's affix drill card (5i1p.6), on every lesson that drills the pattern.
    affix_key = affix_card_key(lesson)
    if affix_key is not None:
        lines.setdefault(affix_key, None)
    for line_key in lines:
        for _guid, item, cid in db.list_sibling_rows(line_key):
            made = sentence_card(item.syntactic_unit)
            if made is not None:
                cards[cid] = _LessonCard(cid, "sentence", item, made)
            elif (drill := drill_card(item.syntactic_unit)) is not None:
                cards[cid] = _LessonCard(cid, "drill", item, drill=drill)
    return cards


@dataclasses.dataclass(frozen=True)
class _Window:
    """Today's Anki day, read once per request so every row is classified against the same instant."""

    today: datetime.date
    start: datetime.datetime
    end: datetime.datetime
    end_of_day_utc: str
    now: datetime.datetime

    @classmethod
    def read(cls) -> _Window:
        start, end, end_of_day_utc = _listen_day_window()
        return cls(anki_today(), start, end, end_of_day_utc, datetime.datetime.now(datetime.UTC))


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    return value.replace(tzinfo=datetime.UTC) if value.tzinfo is None else value.astimezone(datetime.UTC)


def _grade_class(rec: DirectionState, window: _Window) -> tuple[str | None, str | None]:
    """``(grade_class, None)`` when the page may grade *rec* now, else ``(None, reason)``.

    REVIEW: once per Anki day (graded today → refused), then due or ahead by
    the same boundary ``count_review_due_collocations`` uses. (Re)learning: only
    once its step is ripe. Every other state (new, suspended, buried, known) is
    never a row. A REVIEW or (re)learning row with no due date is not either:
    ``due_at`` is NOT NULL in a fresh schema, but migration 451 added it
    nullable and the live decks were built through it.
    """
    gradable = (SRSState.REVIEW, SRSState.LEARNING, SRSState.RELEARNING)
    if rec.state in gradable and rec.due_at is None:
        return None, "no_due_date"
    if rec.state in (SRSState.LEARNING, SRSState.RELEARNING):
        if _as_utc(rec.due_at) <= window.now:
            return "learning", None
        return None, "not_ripe"
    if rec.state == SRSState.REVIEW:
        lr = rec.last_review
        if isinstance(lr, datetime.datetime) and window.start <= _as_utc(lr) < window.end:
            return None, "graded_today"
        return ("due" if _review_is_due(rec, window.end_of_day_utc) else "ahead"), None
    return None, rec.state.value


async def _read_plan(request: Request, content_id: str):
    store = request.state.content_store
    lesson = store.get_readable_content(content_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found")
    db = request.state.srs_db
    # Anki releases yesterday's sibling-buries at rollover; TT does it here and on
    # the card-review path. Without it a card buried yesterday is missing from the
    # plan, and refused by a Save, until card reviews are next opened.
    db.unbury_if_needed(anki_today())
    words = await anyio.to_thread.run_sync(_analyze_lesson_words, lesson, db)
    plan = _build_listen_plan(db, store, lesson, content_id, words)
    return db, lesson, words, plan


@router.get("/content/{content_id}/grade-plan", status_code=200, response_model=GradePlanResponse)
async def get_grade_plan(
    content_id: str,
    request: Request,
    range_days: int | None = Query(None, ge=0),
) -> dict:
    """The lesson's gradable cards, one row per direction, soonest first; plus what a Save would add.

    ``range_days`` keeps REVIEW rows due within that many days of today (0 =
    due today); omitted, every gradable row. Ripe (re)learning rows always
    show. Rows past the range, and unripe steps, are counted in ``later``.
    Read-only.
    """
    db, lesson, _words, plan = await _read_plan(request, content_id)
    rows, later, waiting_new = _plan_rows(db, lesson, plan, _Window.read(), range_days, content_id)
    return {
        "studied": db.has_listen(content_id),
        "listen_count": db.count_listens(content_id),
        "range_days": range_days,
        "cards": rows,
        "later": later,
        "waiting_new": waiting_new,
        "suggestions": {
            "words": [
                {"key": c["lemma"], "text": c["text"], "gloss": c["translation"], "will_create": c["will_create"]}
                for c in plan.candidates
                if c["kind"] == "create"
            ],
            "contrast": plan.affix_cards,
            "sentences": [_line_dict(line) for line in sentence_lines(db, lesson, content_id)],
        },
    }


def _token_dict(token) -> dict:
    return {
        "start": token.start,
        "end": token.end,
        "part": token.part,
        "state": token.state,
        "origin": token.origin,
        "reviewed": token.reviewed,
        "gloss": token.gloss,
    }


def _line_dict(line) -> dict:
    return {
        "line_key": line.line_key,
        "speaker": line.speaker,
        "text": line.text,
        "english": line.english,
        "linked": line.linked,
        "tokens": [_token_dict(t) for t in line.tokens],
        "whole": _token_dict(line.whole),
    }


@router.get("/due-counts", status_code=200, response_model=DueCountsResponse)
async def get_due_counts(
    request: Request,
    content_id: Annotated[list[str] | None, Query()] = None,
    range_days: int | None = Query(None, ge=0),
) -> dict:
    """Per lesson, how many rows its Grade page shows for *range_days* (the lesson list's badge).

    Same reads and the same row rule as ``get_grade_plan``, so the badge and
    the page cannot disagree. An id with no readable lesson is left out.
    It writes what opening each Grade page would: the day's unbury, and the
    lemma cache of a lesson not analysed before. It reads the whole deck once
    per lesson: 12 lessons measured 0.6-0.8 s (tunatale-5i1p.11).
    """
    window = _Window.read()
    counts: dict[str, int] = {}
    for cid in content_id or []:
        try:
            db, lesson, _words, plan = await _read_plan(request, cid)
        except HTTPException:
            continue
        counts[cid] = len(_plan_rows(db, lesson, plan, window, range_days, cid)[0])
    return {"counts": counts}


def _plan_rows(db, lesson, plan: _ListenPlan, window: _Window, range_days: int | None, content_id: str):
    """The Grade page's rows for *range_days*, soonest first, with the ``later`` and ``waiting_new`` tallies."""
    horizon = (
        None
        if range_days is None
        else datetime.datetime.combine(
            window.today + datetime.timedelta(days=range_days), datetime.time.max
        ).isoformat()
    )
    rows: list[dict] = []
    later = {Direction.RECOGNITION.value: 0, Direction.PRODUCTION.value: 0}
    waiting_new = 0
    for card in _lesson_cards(db, lesson, plan, content_id).values():
        for direction, rec in card.item.directions.items():
            if rec.state == SRSState.NEW:
                waiting_new += 1
                continue
            grade_class, reason = _grade_class(rec, window)
            if grade_class is None:
                if reason == "not_ripe":
                    later[direction.value] += 1
                continue
            if grade_class == "ahead" and horizon is not None and not _review_is_due(rec, horizon):
                later[direction.value] += 1
                continue
            blank = card.sentence.blank if card.sentence is not None else None
            in_review = db.count_answers_into_review(card.item_id) if card.drill is not None else 0
            rows.append(
                {
                    "item_id": card.item_id,
                    "direction": direction.value,
                    "kind": card.kind,
                    "text": card.text(in_review),
                    "gloss": card.gloss(in_review),
                    "due_at": rec.due_at.isoformat(),
                    "grade_class": grade_class,
                    "line_key": card.sentence.line_key if card.sentence is not None else None,
                    "blank_start": blank[0] if blank else None,
                    "blank_end": blank[1] if blank else None,
                    "drill": _drill_dict(card.item.syntactic_unit, db, card.item_id) if card.drill else None,
                }
            )
    rows.sort(key=lambda r: (r["due_at"], r["item_id"], r["direction"]))
    return rows, later, waiting_new


def _refusals(db, cards: dict[int, _LessonCard], body: GradeRequest, window: _Window) -> tuple[int, list[dict]]:
    """Every row of *body* that cannot be applied, in request order, and the status to refuse with (0 = none)."""
    rows: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for grade in body.card_grades:
        key = (grade.item_id, grade.direction)
        reason = None
        if key in seen:
            reason = "duplicate"
        elif grade.item_id not in cards:
            reason = "unknown" if db.get_collocation_by_id(grade.item_id) is None else "not_in_lesson"
        else:
            rec = cards[grade.item_id].item.directions.get(Direction(grade.direction))
            reason = "no_direction" if rec is None else _grade_class(rec, window)[1]
        seen.add(key)
        if reason is not None:
            rows.append({"item_id": grade.item_id, "direction": grade.direction, "reason": reason})
    if not rows:
        return 0, rows
    return (409 if all(r["reason"] in _STALE for r in rows) else 422), rows


@router.post("/content/{content_id}/grade", status_code=200, response_model=GradeResponse)
async def grade_lesson(
    content_id: str, body: GradeRequest, request: Request, background_tasks: BackgroundTasks
) -> dict:
    """Save the Grade page: apply the grades sent, add the suggestions left on, and mark the lesson studied.

    Order: validate every grade (any refusal applies nothing: 422 for a row
    that was never this lesson's to grade, else 409, the plan was stale and the
    page refetches), then the grades, then the creations, then the study mark.
    Creations come before the course list's mint, as on the listen: the mint
    skips a word the deck already has, so the other order could make a twin.

    ``declined`` words are skipped (each keeps its slot: nothing is promoted
    into it) and written to the ignore list, so they stay off; ``restored``
    takes a word back off that list. A declined contrast card is skipped for
    this Save only. ``over_cap`` opts a suggestion past the day's budget.

    The first Save of a lesson is its study mark whatever ``studied`` says; a
    later one records another listen only when ``studied`` is true. The mark
    mints the lesson's own card list. Media work runs after the response.
    """
    db, lesson, words, plan = await _read_plan(request, content_id)
    window = _Window.read()
    cards = _lesson_cards(db, lesson, plan, content_id)
    status, refused = _refusals(db, cards, body, window)
    if status:
        raise HTTPException(status_code=status, detail={"message": "Some grades cannot be applied", "rows": refused})
    try:
        edits = sentence_mining.plan_line_edits(db, lesson, content_id, [e.model_dump() for e in body.sentence_edits])
    except sentence_mining.NeedsConfirm as exc:
        raise HTTPException(status_code=exc.status, detail={"message": str(exc), "needs_confirm": exc.keys}) from exc
    except sentence_mining.MiningError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    glosses = await _blank_glosses(request, lesson.language_code, edits.needs_gloss)

    applied = 0
    if body.card_grades:
        fsrs_params, _ = resolve_fsrs_params(db)
        learn_steps, _ = resolve_learning_steps(db)
        relearn_steps, _ = resolve_relearning_steps(db)
        col_crt = resolve_col_crt(db)
        balancer = build_live_load_balancer(db, now=window.now, col_crt=col_crt)
        last_ms = 0
        for grade in body.card_grades:
            dir_enum = Direction(grade.direction)
            now, last_ms = _apply_grade_now(
                db,
                grade.item_id,
                dir_enum,
                grade.rating,
                fsrs_params=fsrs_params,
                col_crt=col_crt,
                balancer=balancer,
                last_grade_ms=last_ms,
                learn_steps=learn_steps,
                relearn_steps=relearn_steps,
            )
            # Any staged grade for this card is now stale: releasing it later
            # would grade the same assessment twice. Not lesson-scoped.
            db.clear_pending_grade(grade.item_id, dir_enum.value)
            applied += 1
        # A batch is one grade event: the cutoff advances once, to the last grade.
        advance_learning_cutoff(db, now)

    declined = {kind: {d.key for d in body.declined if d.kind == kind} for kind in ("word", "contrast")}
    over_cap = {kind: {d.key for d in body.over_cap if d.kind == kind} for kind in ("word", "contrast")}
    made = _create_ranked_words(
        db, lesson, words, plan.ranked, plan.live_creates, opted_in=over_cap["word"], skipped=declined["word"]
    )
    contrast = _add_contrast_cards(
        db, lesson.language_code, plan.offered, skipped=declined["contrast"], over_cap=over_cap["contrast"]
    )
    made.clozes += contrast

    sentence_mining.write_marks(db, edits)
    recorded = body.studied or not db.has_listen(content_id)
    course, voiced, affix = (
        _record_study_pass(db, request.state.content_store, lesson, content_id) if recorded else ([], [], None)
    )
    made.vocab += [(cid, card.unit, card.unit.lemma) for cid, card in course if card.unit.card_type == "vocab"]
    mined = sentence_mining.apply_rows(
        db, lesson.language_code, lesson, edits, glosses, studied=db.has_listen(content_id)
    )
    if mined.made:
        # The line's takes, for the rows this Save added: the list's own and the blanks
        # mined on its lines (``attach_lesson_card_audio`` copies them to a mined blank).
        story = lesson.generation_metadata["story"]
        voiced = lesson_card_rows(db, story["cards"], story.get("drills", []))

    for lemma in declined["word"]:
        db.add_ignored_lemma(lesson.language_code, lemma)
    for restored in body.restored:
        db.remove_ignored_lemma(lesson.language_code, restored.key)

    _schedule_listen_media(
        background_tasks,
        request,
        db,
        lesson,
        vocab=made.vocab,
        clozes=made.clozes,
        reglosses=made.reglosses,
        course=voiced,
        affix=affix,
    )
    return {
        "status": "ok",
        "applied": applied,
        "created": made.created + len(contrast) + len(course) + (affix is not None) + len(mined.made),
        "recorded_study": recorded,
        "listen_count": db.count_listens(content_id),
        "not_made": mined.not_made,
    }


async def _blank_glosses(request: Request, language_code: str, needs) -> dict:
    """A gloss for each new blank: the line's own, else the model's; a blank with neither is left out.

    Fetched before anything is written (design § 4), so a failing model call writes nothing.
    """
    glosses: dict = {}
    llm = app_state.llm(request)
    for need in needs:
        gloss = need.own_gloss
        if not gloss and llm is not None:
            gloss = await generate_word_gloss(
                llm, surface=need.word, lemma=need.word, source_lang=language_code, sentence=need.sentence
            )
        if gloss:
            glosses[(need.line_key, need.span)] = gloss
    return glosses
