"""NEW-state cards surface in the listen preview (and commit) — 2026-08 brief.

Before this change ``_listen_grade_class`` returned ``None`` for a NEW-state
direction, so a carded-but-never-introduced lemma was dropped from BOTH the
preview and the commit. The lesson stats line counted it as "new" while the
preview showed no row of any kind — the two numbers visibly disagreed.

The load-bearing part is the budget: releasing a staged grade on a NEW-state
card *introduces* it, consuming Anki's daily new-card allowance. A lesson with
40 NEW-state words must not introduce 40 cards in one listen. Introductions and
creations therefore share ONE budget, with cards created earlier today free
(they already hold a slot via ``count_new_created_today``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/content/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"
COMMIT_URL = "/api/srs/content/lesson-1/commit-pending"


def _setup_lesson(phrase_text: str, key_phrases=None):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(text=phrase_text, voice_id="female-1", language_code="sl", role="female-1"),
                ],
            )
        ],
        key_phrases=key_phrases or [],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


def _seed_new_state(db, text: str, *, created_days_ago: int = 0) -> None:
    """A tracked vocab card whose recognition direction is still NEW.

    ``add_collocation`` already leaves recognition in NEW, so the only work
    here is optionally backdating ``collocations.created_at`` out of today's
    Anki-day window — a card created today is FREE against the shared
    introduction budget, so "charged" fixtures must be backdated.
    """
    unit = SyntacticUnit(text=text, translation=f"t-{text}", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    if created_days_ago:
        stamp = (datetime.now(UTC) - timedelta(days=created_days_ago)).strftime("%Y-%m-%d %H:%M:%S")
        guid = db.get_collocation(text).guid
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET created_at = ? WHERE guid = ?", (stamp, guid))


def _set_new_cap(db, cap: int) -> None:
    db.set_anki_state_cache("daily_new_cap", str(cap))


async def _preview() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()


async def _post_listen(payload: dict) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(LISTEN_URL, json=payload)
    assert resp.status_code == 200
    return resp.json()


def _dir(state, *, due_at=None, last_review=None, reps=0):
    """A bare DirectionState for the classifier tests (`due_at` is NOT NULL)."""
    from app.models.srs_item import DirectionState

    return DirectionState(
        direction=Direction.RECOGNITION,
        due_at=due_at if due_at is not None else datetime.now(UTC),
        state=state,
        reps=reps,
        last_review=last_review,
    )


def _pending_texts(db) -> set[str]:
    """Texts of every collocation currently holding a staged listen grade."""
    with db._get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.text FROM pending_listen_grades p
            JOIN collocations c ON c.id = p.collocation_id
            """
        ).fetchall()
    return {r[0] for r in rows}


class TestGradeClass:
    """`_listen_grade_class` gains a "new" class; every other return is unchanged."""

    def _window(self):
        from app.api.srs import _listen_day_window

        return _listen_day_window()

    def _classify(self, rec):
        from app.api.srs import _listen_grade_class

        start, end, eod = self._window()
        return _listen_grade_class(rec, start, end, end_of_day_utc=eod)

    def test_new_state_classifies_as_new(self):
        assert self._classify(_dir(SRSState.NEW)) == "new"

    def test_learning_and_relearning_still_learning(self):
        assert self._classify(_dir(SRSState.LEARNING)) == "learning"
        assert self._classify(_dir(SRSState.RELEARNING)) == "learning"

    def test_none_direction_still_none(self):
        assert self._classify(None) is None

    def test_review_due_and_ahead_unchanged(self):
        from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

        due = _dir(SRSState.REVIEW, reps=3, due_at=due_at_rollover_utc(anki_today() - timedelta(days=1)))
        assert self._classify(due) == "due"

        ahead = _dir(SRSState.REVIEW, reps=3, due_at=due_at_rollover_utc(anki_today() + timedelta(days=30)))
        assert self._classify(ahead) == "ahead"

    def test_graded_today_still_none(self):
        from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

        rec = _dir(
            SRSState.REVIEW,
            reps=3,
            due_at=due_at_rollover_utc(anki_today() - timedelta(days=1)),
            last_review=datetime.now(UTC),
        )
        assert self._classify(rec) is None

    def test_release_review_kind_is_none_for_new_state(self):
        """An introduction is not a review-ahead, so kind stays None."""
        from app.api.srs import _release_review_kind

        assert _release_review_kind(_dir(SRSState.NEW)) is None


# `TestBudgetAllocator` lived here and unit-tested `_allocate_new_state_budget`
# (free-rows-first, charged rows overflow to the tail, remainder returned for
# creation). F-2 replaced that helper with `_allocate_intro_pool`, which ranks
# NEW-state rows and creation candidates in ONE pool and so has no "remainder"
# to return. Every behaviour those four tests pinned is re-pinned against the
# new helper in `test_api_listen_one_ranking_pool.py::TestAllocateIntroPool` —
# including the two rules that survived the merge unchanged ("created today is
# free" and key phrases never being frequency-ranked). Deleted rather than
# ported so there is one allocator oracle, not two that can disagree.


class TestCreatedInWindow:
    """`_created_in_window` — the 4 AM-rollover "created today" test."""

    def _window(self):
        from app.api.srs import _listen_day_window

        start, end, _ = _listen_day_window()
        return start, end

    def test_now_is_inside_the_window(self):
        from app.api.srs import _created_in_window

        start, end = self._window()
        assert _created_in_window(datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"), start, end) is True

    def test_old_stamp_is_outside_the_window(self):
        from app.api.srs import _created_in_window

        start, end = self._window()
        stamp = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        assert _created_in_window(stamp, start, end) is False

    def test_missing_stamp_is_not_today(self):
        from app.api.srs import _created_in_window

        start, end = self._window()
        assert _created_in_window(None, start, end) is False

    def test_unparseable_stamp_is_not_today(self):
        """A legacy/garbage created_at must not 500 the preview."""
        from app.api.srs import _created_in_window

        start, end = self._window()
        assert _created_in_window("not-a-timestamp", start, end) is False


class TestPreviewSurfacesNewState:
    async def test_new_state_card_appears_as_word_row(self):
        db = _setup_lesson("Banka")
        _set_new_cap(db, 5)
        _seed_new_state(db, "banka", created_days_ago=3)

        row = next(c for c in (await _preview())["candidates"] if c["text"] == "banka")
        assert row["kind"] == "word"
        assert row["grade_class"] == "new"
        assert row["item_id"] is not None
        assert row["due_at"] is None
        assert row["well_known"] is False

    async def test_new_state_row_is_offered_not_created(self):
        """It already has a card, so it must never be a "create" row."""
        db = _setup_lesson("Banka")
        _set_new_cap(db, 5)
        _seed_new_state(db, "banka", created_days_ago=3)

        cands = (await _preview())["candidates"]
        assert [c["grade_class"] for c in cands if c["text"] == "banka"] == ["new"]


class TestPreviewCommitParity:
    """The 6a5c718 bug class: preview and commit must offer the same set."""

    async def test_previewed_new_state_row_is_staged_by_listen(self):
        db = _setup_lesson("Banka")
        _set_new_cap(db, 5)
        _seed_new_state(db, "banka", created_days_ago=3)

        offered = {c["text"] for c in (await _preview())["candidates"] if c["grade_class"] == "new"}
        assert offered == {"banka"}

        await _post_listen({"content_id": "lesson-1"})
        assert _pending_texts(db) == {"banka"}

    async def test_over_budget_new_state_row_is_not_staged(self):
        """A row the preview pushed into the tail must not be staged."""
        db = _setup_lesson("Banka riba")
        _set_new_cap(db, 1)
        _seed_new_state(db, "banka", created_days_ago=3)
        _seed_new_state(db, "riba", created_days_ago=3)

        cands = (await _preview())["candidates"]
        live = {c["text"] for c in cands if c["grade_class"] == "new" and c["will_create"] is True}
        tail = {c["text"] for c in cands if c["grade_class"] == "new" and c["will_create"] is False}
        assert len(live) == 1
        assert len(tail) == 1

        await _post_listen({"content_id": "lesson-1"})
        assert _pending_texts(db) == live


class TestSharedIntroductionBudget:
    async def test_budget_caps_live_rows_and_rest_go_to_tail(self):
        db = _setup_lesson("Banka riba novo mesto")
        _set_new_cap(db, 2)
        for w in ("banka", "riba", "novo", "mesto"):
            _seed_new_state(db, w, created_days_ago=3)

        cands = [c for c in (await _preview())["candidates"] if c["grade_class"] == "new"]
        assert len(cands) == 4
        assert sum(1 for c in cands if c["will_create"] is True) == 2
        assert sum(1 for c in cands if c["will_create"] is False) == 2

        await _post_listen({"content_id": "lesson-1"})
        assert _pending_texts(db) == {c["text"] for c in cands if c["will_create"] is True}

    async def test_cards_created_today_are_free(self):
        """A NEW card created earlier today already holds a slot via
        count_new_created_today — charging it again double-counts it."""
        db = _setup_lesson("Banka")
        _set_new_cap(db, 1)
        _seed_new_state(db, "banka")  # created today

        row = next(c for c in (await _preview())["candidates"] if c["text"] == "banka")
        assert row["grade_class"] == "new"
        assert row["will_create"] is True

        await _post_listen({"content_id": "lesson-1"})
        assert _pending_texts(db) == {"banka"}

    async def test_a_commoner_creation_candidate_now_outranks_a_new_state_row(self):
        """⚠️ This test asserted the OPPOSITE until F-2 (2026-08-04).

        It used to be `test_new_state_takes_precedence_over_creation`, pinning
        "cards already in the deck get finished before more are added". The user
        retired that rule: NEW-state rows and creation candidates now compete in
        ONE pool ranked by corpus frequency, so a commoner untracked lemma takes
        the slot from a rarer carded-but-never-introduced one.

        The fixture had to change with it. The old one was "Banka riba", where
        banka (zipf 4.39) is commoner than riba (4.14) — under one pool banka
        wins on frequency, so the old assertions would still have passed while
        testing nothing. `delo` (5.85) beats `banka` and makes the inversion
        real.

        Full coverage of the one-pool semantics is in
        `test_api_listen_one_ranking_pool.py`; this stays here so a reader of
        the shared-budget file is not left with a retired rule.
        """
        db = _setup_lesson("Banka delo")
        _set_new_cap(db, 1)
        _seed_new_state(db, "banka", created_days_ago=3)  # carded, NEW, zipf 4.39
        # "delo" is untracked → a creation candidate, zipf 5.85, and it wins.

        cands = (await _preview())["candidates"]
        banka = next(c for c in cands if c["text"] == "banka")
        delo = next(c for c in cands if c["text"] == "delo")
        assert banka["grade_class"] == "new"
        assert banka["will_create"] is False, "the rarer NEW-state row lost the slot"
        assert delo["grade_class"] == "create"
        assert delo["will_create"] is True

        before = {r[0] for r in _all_texts(db)}
        await _post_listen({"content_id": "lesson-1"})
        assert {r[0] for r in _all_texts(db)} - before == {"delo"}, "the create landed, and only it"
        assert _pending_texts(db) == set(), "the NEW-state row was in the tail, so nothing was staged"


def _all_texts(db):
    with db._get_conn() as conn:
        return conn.execute("SELECT text FROM collocations").fetchall()


class TestReleaseIntroduces:
    async def test_releasing_a_new_state_grade_introduces_the_card(self):
        from app.srs.anki_mirror.rollover import anki_today

        db = _setup_lesson("Banka")
        _set_new_cap(db, 5)
        _seed_new_state(db, "banka", created_days_ago=3)

        today = anki_today()
        assert db.count_new_introduced_today(today) == 0

        await _post_listen({"content_id": "lesson-1"})
        assert _pending_texts(db) == {"banka"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(COMMIT_URL)
        assert resp.status_code == 200

        rec = db.get_collocation("banka").directions[Direction.RECOGNITION]
        assert rec.state is not SRSState.NEW, "release must leave NEW"
        assert rec.introduced_at is not None, "introduced_at must be stamped"
        assert db.count_new_introduced_today(today) == 1


class TestNoNewStateRegression:
    async def test_lesson_without_new_state_cards_is_unchanged(self):
        """Guard: the pre-existing learning/due/create flow still behaves."""
        from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

        db = _setup_lesson("Banka riba novo")
        _set_new_cap(db, 5)

        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        banka = db.get_collocation("banka")
        banka.directions[Direction.RECOGNITION].state = SRSState.LEARNING
        banka.directions[Direction.RECOGNITION].reps = 1
        db.update_collocation(banka)

        unit2 = SyntacticUnit(text="riba", translation="fish", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit2, language_code="sl")
        riba = db.get_collocation("riba")
        rec = riba.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.reps = 5
        rec.last_review = datetime.now(UTC) - timedelta(days=5)
        rec.due_at = due_at_rollover_utc(anki_today() - timedelta(days=1))
        db.update_collocation(riba)

        by_text = {c["text"]: c for c in (await _preview())["candidates"]}
        assert by_text["banka"]["grade_class"] == "learning"
        assert by_text["riba"]["grade_class"] == "due"
        assert by_text["novo"]["grade_class"] == "create"
        assert not any(c["grade_class"] == "new" for c in by_text.values())


class TestNewStateRatingPaths:
    """The rating branches of the NEW-state staging loop."""

    async def test_skip_rating_stages_nothing(self):
        """A skipped NEW-state row consumes its allocated slot but is not staged.

        Allocation is rating-independent (it has to be — the preview has no
        ratings), so the skip decision lands at staging time.
        """
        db = _setup_lesson("Banka")
        _set_new_cap(db, 5)
        _seed_new_state(db, "banka", created_days_ago=3)

        data = await _post_listen({"content_id": "lesson-1", "word_ratings": {"banka": "skip"}})

        assert _pending_texts(db) == set()
        assert data["staged"] == 0

    async def test_confirmed_new_state_word_is_applied_not_staged(self):
        """A grade the user performed by hand is applied immediately."""
        db = _setup_lesson("Banka")
        _set_new_cap(db, 5)
        _seed_new_state(db, "banka", created_days_ago=3)

        data = await _post_listen(
            {"content_id": "lesson-1", "word_ratings": {"banka": "good"}, "confirmed_words": ["banka"]}
        )

        assert _pending_texts(db) == set(), "a confirmed grade is applied, never staged"
        assert data["staged"] == 0
        rec = db.get_collocation("banka").directions[Direction.RECOGNITION]
        assert rec.state is not SRSState.NEW, "applying the grade introduces the card"


class TestNewStateKeyPhrase:
    """A NEW-state key phrase draws on the same shared budget as words."""

    async def test_new_state_key_phrase_is_offered_and_staged(self):
        from app.models.lesson import KeyPhraseInfo

        db = _setup_lesson(
            "Banka",
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )
        _set_new_cap(db, 5)
        _seed_new_state(db, "dober dan", created_days_ago=3)

        row = next(c for c in (await _preview())["candidates"] if c["text"] == "dober dan")
        assert row["kind"] == "kp"
        assert row["grade_class"] == "new"
        assert row["will_create"] is True
        assert row["due_at"] is None

        await _post_listen({"content_id": "lesson-1"})
        assert "dober dan" in _pending_texts(db)

    async def test_over_budget_new_state_key_phrase_is_not_staged(self):
        """Key phrases overflow into the same tail, not an unbudgeted path."""
        from app.models.lesson import KeyPhraseInfo

        db = _setup_lesson(
            "Banka",
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="lahko noc", translation="good night"),
            ],
        )
        _set_new_cap(db, 1)
        _seed_new_state(db, "dober dan", created_days_ago=3)
        _seed_new_state(db, "lahko noc", created_days_ago=3)

        cands = [c for c in (await _preview())["candidates"] if c["grade_class"] == "new"]
        live = {c["text"] for c in cands if c["will_create"] is True}
        assert len(live) == 1
        assert len([c for c in cands if c["will_create"] is False]) == 1

        await _post_listen({"content_id": "lesson-1"})
        assert _pending_texts(db) == live


class TestCreatedInWindowTimezoneAware:
    def test_timezone_aware_stamp_is_respected(self):
        """created_at is normally naive UTC, but an offset-carrying value must
        be compared in UTC rather than reinterpreted as naive."""
        from app.api.srs import _created_in_window, _listen_day_window

        start, end, _ = _listen_day_window()
        assert _created_in_window(datetime.now(UTC).isoformat(), start, end) is True
        assert _created_in_window((datetime.now(UTC) - timedelta(days=3)).isoformat(), start, end) is False
