"""Stage-2 guardrails: POST /api/srs/listen STAGES provisional grades, never applies them.

Orchestrator-authored per the ``bp-listen-pending-bucket-2026-07`` brief. These
assertions ARE the staging contract — they may not be weakened, deleted, or
shadowed by a more permissive alternative test. If one of them looks wrong,
stop and report rather than editing it.

The contract under test:

* every eligible tracked recognition card gets exactly ONE
  ``pending_listen_grades`` row (upserted on re-listen), carrying the rating
  and the ``_listen_grade_class`` value at stage time;
* NOTHING is applied — no ``tt_revlog`` row, no FSRS state change, no
  ``dirty_fsrs`` flip, no ``last_review`` stamp. A listen is invisible to sync;
* there is NO listen-time review-budget cap any more: a due-today card stages
  even when the day's review budget is exhausted (the budget is charged later,
  when the pending grade is APPLIED — Stage 3);
* card CREATION is unchanged; a newly created card has no grade to stage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

LESSON_ID = "lesson-1"


def _lesson(phrases: list[str], key_phrases=None, language_code: str = "sl") -> Lesson:
    return Lesson(
        title="Day 1",
        language_code=language_code,
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text=t, voice_id="female-1", language_code=language_code, role="female-1") for t in phrases
                ],
            )
        ],
        key_phrases=key_phrases or [],
    )


def _setup(lesson: Lesson):
    """App state with an in-memory DB + store, creation budget off by default."""
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson(LESSON_ID, "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    # Staging tests seed their own tracked cards; keep creation out of the way
    # unless a test explicitly re-opens the budget.
    db.set_anki_state_cache("daily_new_cap", "0")
    return db


def _seed(db, text: str, *, klass: str = "due", reps: int = 5):
    """Seed a tracked vocab card whose recognition direction classifies as *klass*.

    ``due``      — REVIEW, last graded 2 days ago, due at today's rollover.
    ``ahead``    — REVIEW, last graded 10 days ago, due 5 days out.
    ``learning`` — LEARNING state (always eligible, never budget-capped).
    ``graded``   — REVIEW, already graded today (``_listen_grade_class`` → None).
    ``new``      — untouched NEW state (``_listen_grade_class`` → None).
    """
    from app.models.syntactic_unit import SyntacticUnit
    from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

    unit = SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    assert item is not None
    rec = item.directions[Direction.RECOGNITION]
    if klass == "due":
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(days=2)
        # Explicit 04:00-UTC rollover seed — add_collocation's default due_at is a
        # date.today() site that flips "due" to "ahead" in the [00:00, 04:00) UTC
        # window and would silently retarget the test.
        rec.due_at = due_at_rollover_utc(anki_today())
        rec.reps = reps
    elif klass == "ahead":
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(days=10)
        rec.due_at = datetime.now(UTC) + timedelta(days=5)
        rec.reps = reps
    elif klass == "learning":
        rec.state = SRSState.LEARNING
        rec.reps = 1
    elif klass == "graded":
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC)
        rec.due_at = due_at_rollover_utc(anki_today())
        rec.reps = reps
    else:
        assert klass == "new"
    db.update_collocation(item)
    return db.get_collocation(text)


def _cid(db, item) -> int:
    cid = db.get_collocation_id_by_guid(item.guid)
    assert cid is not None
    return cid


def _snapshot(db, text: str) -> dict:
    """The FSRS/sync-visible facts a listen must leave completely untouched."""
    rec = db.get_collocation(text).directions[Direction.RECOGNITION]
    return {
        "state": rec.state,
        "reps": rec.reps,
        "due_at": rec.due_at,
        "last_review": rec.last_review,
        "stability": rec.stability,
        "dirty_fsrs": rec.dirty_fsrs,
    }


def _revlog_count(db) -> int:
    with db._get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM tt_revlog").fetchone()[0]


async def _listen(**body) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/srs/listen", json={"lesson_id": LESSON_ID, **body})
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestListenStagesInsteadOfGrading:
    async def test_due_card_stages_one_pending_row_and_applies_nothing(self):
        db = _setup(_lesson(["banka"]))
        item = _seed(db, "banka", klass="due")
        before = _snapshot(db, "banka")

        data = await _listen()

        rows = db.get_pending_grades(LESSON_ID)
        assert len(rows) == 1
        assert rows[0]["collocation_id"] == _cid(db, item)
        assert rows[0]["direction"] == Direction.RECOGNITION.value
        assert rows[0]["rating"] == "good"
        assert rows[0]["grade_class"] == "due"
        assert rows[0]["lesson_id"] == LESSON_ID
        assert data["staged"] == 1

        # Nothing applied: no revlog row, no FSRS movement, nothing for sync.
        assert _revlog_count(db) == 0
        assert _snapshot(db, "banka") == before

    async def test_ahead_card_stages_with_grade_class_ahead(self):
        db = _setup(_lesson(["banka"]))
        _seed(db, "banka", klass="ahead")
        before = _snapshot(db, "banka")

        data = await _listen()

        rows = db.get_pending_grades(LESSON_ID)
        assert len(rows) == 1
        assert rows[0]["grade_class"] == "ahead"
        assert data["staged"] == 1
        assert _revlog_count(db) == 0
        assert _snapshot(db, "banka") == before

    async def test_learning_card_stages_with_grade_class_learning(self):
        db = _setup(_lesson(["banka"]))
        _seed(db, "banka", klass="learning")
        before = _snapshot(db, "banka")

        data = await _listen()

        rows = db.get_pending_grades(LESSON_ID)
        assert len(rows) == 1
        assert rows[0]["grade_class"] == "learning"
        assert data["staged"] == 1
        assert _revlog_count(db) == 0
        assert _snapshot(db, "banka") == before

    async def test_due_card_stages_even_when_review_budget_is_exhausted(self):
        """The listen-time budget cap is GONE — staging charges nothing.

        Under the old model a due-today card was skipped once
        ``live_review_budget`` hit 0. The budget is now charged when the pending
        grade is APPLIED, so staging must ignore it entirely.
        """
        db = _setup(_lesson(["banka"]))
        _seed(db, "banka", klass="due")
        db.set_anki_state_cache("daily_review_cap", "0")

        data = await _listen()

        assert data["staged"] == 1
        assert len(db.get_pending_grades(LESSON_ID)) == 1
        assert _revlog_count(db) == 0

    async def test_already_graded_today_stages_nothing(self):
        db = _setup(_lesson(["banka"]))
        _seed(db, "banka", klass="graded")

        data = await _listen()

        assert db.get_pending_grades(LESSON_ID) == []
        assert data["staged"] == 0
        assert _revlog_count(db) == 0

    async def test_new_card_stages_nothing(self):
        db = _setup(_lesson(["banka"]))
        _seed(db, "banka", klass="new")

        data = await _listen()

        assert db.get_pending_grades(LESSON_ID) == []
        assert data["staged"] == 0

    async def test_multiple_eligible_cards_stage_one_row_each(self):
        db = _setup(_lesson(["banka center"]))
        banka = _seed(db, "banka", klass="due")
        center = _seed(db, "center", klass="ahead")

        data = await _listen()

        rows = db.get_pending_grades(LESSON_ID)
        assert {r["collocation_id"] for r in rows} == {_cid(db, banka), _cid(db, center)}
        assert len(rows) == 2
        assert data["staged"] == 2
        assert _revlog_count(db) == 0


class TestListenRestagesRatherThanDuplicating:
    async def test_same_day_relisten_upserts_a_single_row(self):
        db = _setup(_lesson(["banka"]))
        _seed(db, "banka", klass="due")
        before = _snapshot(db, "banka")

        first = await _listen()
        second = await _listen()

        rows = db.get_pending_grades(LESSON_ID)
        assert len(rows) == 1, "a re-listen must UPSERT the pending row, never append a second"
        assert first["staged"] == 1
        assert second["staged"] == 1
        # Still nothing applied on the second pass either.
        assert _revlog_count(db) == 0
        assert _snapshot(db, "banka") == before

    async def test_relisten_replaces_the_provisional_rating(self):
        db = _setup(_lesson(["banka"]))
        _seed(db, "banka", klass="due")

        await _listen()
        await _listen(word_ratings={"banka": "again"})

        rows = db.get_pending_grades(LESSON_ID)
        assert len(rows) == 1
        assert rows[0]["rating"] == "again"


class TestListenRatings:
    async def test_word_rating_again_is_staged(self):
        db = _setup(_lesson(["banka"]))
        _seed(db, "banka", klass="due")

        data = await _listen(word_ratings={"banka": "again"})

        rows = db.get_pending_grades(LESSON_ID)
        assert len(rows) == 1
        assert rows[0]["rating"] == "again"
        assert data["staged"] == 1
        # An "again" is still only provisional — no lapse until it is applied.
        assert _revlog_count(db) == 0
        assert db.get_collocation("banka").directions[Direction.RECOGNITION].state == SRSState.REVIEW

    async def test_word_rating_skip_stages_nothing(self):
        """ "skip" must be handled BEFORE the rating map — not defaulted to Good."""
        db = _setup(_lesson(["banka"]))
        _seed(db, "banka", klass="due")

        data = await _listen(word_ratings={"banka": "skip"})

        assert db.get_pending_grades(LESSON_ID) == []
        assert data["staged"] == 0
        assert _revlog_count(db) == 0

    async def test_word_rating_skip_does_not_suppress_other_cards(self):
        db = _setup(_lesson(["banka center"]))
        _seed(db, "banka", klass="due")
        center = _seed(db, "center", klass="due")

        data = await _listen(word_ratings={"banka": "skip"})

        rows = db.get_pending_grades(LESSON_ID)
        assert len(rows) == 1
        assert rows[0]["collocation_id"] == _cid(db, center)
        assert data["staged"] == 1

    async def test_key_phrase_stages_good_by_default(self):
        db = _setup(_lesson(["banka"], key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")]))
        kp = _seed(db, "dober dan", klass="due")
        before = _snapshot(db, "dober dan")

        data = await _listen()

        rows = db.get_pending_grades(LESSON_ID)
        assert len(rows) == 1
        assert rows[0]["collocation_id"] == _cid(db, kp)
        assert rows[0]["rating"] == "good"
        assert rows[0]["grade_class"] == "due"
        assert data["staged"] == 1
        assert _revlog_count(db) == 0
        assert _snapshot(db, "dober dan") == before

    async def test_kp_rating_skip_stages_nothing(self):
        db = _setup(_lesson(["banka"], key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")]))
        _seed(db, "dober dan", klass="due")

        data = await _listen(kp_ratings={"dober dan": "skip"})

        assert db.get_pending_grades(LESSON_ID) == []
        assert data["staged"] == 0
        assert _revlog_count(db) == 0

    async def test_kp_rating_again_is_staged(self):
        db = _setup(_lesson(["banka"], key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")]))
        _seed(db, "dober dan", klass="due")

        await _listen(kp_ratings={"dober dan": "again"})

        rows = db.get_pending_grades(LESSON_ID)
        assert len(rows) == 1
        assert rows[0]["rating"] == "again"

    async def test_kp_already_graded_today_stages_nothing(self):
        db = _setup(_lesson(["banka"], key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")]))
        _seed(db, "dober dan", klass="graded")

        data = await _listen()

        assert db.get_pending_grades(LESSON_ID) == []
        assert data["staged"] == 0


class TestListenResponseShape:
    async def test_response_reports_staged_and_no_longer_reports_grades(self):
        db = _setup(_lesson(["banka center"]))
        _seed(db, "banka", klass="due")
        _seed(db, "center", klass="ahead")

        data = await _listen()

        assert set(data) == {"status", "staged", "created", "remaining_candidates", "listen_count"}
        assert data["status"] == "ok"
        assert data["staged"] == 2
        assert data["created"] == 0
        assert data["listen_count"] == 1
        assert db.count_pending_grades(LESSON_ID) == 2


class TestListenCreationIsUnchanged:
    async def test_creation_still_happens_and_stages_nothing_for_new_cards(self):
        """Creation is out of scope for staging — a new card has no grade to hold."""
        db = _setup(_lesson(["banka banka banka", "center center"]))
        db.set_anki_state_cache("daily_new_cap", "2")

        data = await _listen()

        assert data["created"] == 2
        assert db.get_collocation_by_lemma("banka") is not None
        assert db.get_collocation_by_lemma("center") is not None
        assert data["staged"] == 0
        assert db.get_pending_grades(LESSON_ID) == []
        assert _revlog_count(db) == 0

    async def test_listen_is_still_recorded(self):
        db = _setup(_lesson(["banka"]))
        _seed(db, "banka", klass="due")

        await _listen()
        data = await _listen()

        assert data["listen_count"] == 2
        assert db.count_listens(LESSON_ID) == 2
