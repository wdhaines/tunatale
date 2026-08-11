"""F-5: a listen must not auto-stage a LEARNING-state card.

User decision, 2026-08-04 (a same-day revision of a first answer that would
have dropped the rows entirely): *"maybe learning should be treated like known
words? I think they should be skipped by default but I'd prefer that they are
visible and auto-gradable opt-in like known is."*

So a learning/relearning row is **visible in the preview, rated skip by
default, stageable only by an explicit per-row grade** — exactly the mechanism
well-known rows already use. The observation behind it: `hage` was introduced at
10:09 and due at 10:20, and a listen rated it "good" in between. The learning
step exists to test recall at a specific interval and a listen is not that test.

The two populations are carried by ONE field, ``deferred_reason``, rather than a
second boolean beside ``well_known``. The polarity here is inverted from every
other row — absent from ``word_ratings`` means *skip* for these and *good* for
everything else — and the frontend's ``buildRatings`` already calls that its
sharpest edge. A second ad-hoc copy of an inverted rule is how the third one
gets written wrong.

⚠️ Both loops. The key-phrase loop carries its own copy of the well-known guard,
so it needs the learning case too — `9af858e` is the recorded instance of key
phrases being left out of exactly this kind of change and preview and commit
diverging as a result.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/lesson/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"


def _setup(phrases: list[str], language_code: str = "sl", key_phrases: list[str] | None = None):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    kp_list = [KeyPhraseInfo(phrase=kp, translation=f"t-{kp}") for kp in (key_phrases or [])]
    lesson = Lesson(
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
        key_phrases=kp_list,
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


def _seed(db, text: str, state: SRSState, *, days_until_due: int, stability: float = 10.0) -> None:
    unit = SyntacticUnit(text=text, translation=f"t-{text}", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = state
    rec.stability = stability
    rec.reps = 5
    rec.due_at = due_at_rollover_utc(anki_today() + timedelta(days=days_until_due))
    rec.last_review = datetime.now(UTC) - timedelta(days=5)
    db.update_collocation(item)


def _seed_learning(db, text: str) -> None:
    _seed(db, text, SRSState.LEARNING, days_until_due=0)


def _seed_relearning(db, text: str) -> None:
    _seed(db, text, SRSState.RELEARNING, days_until_due=0)


def _seed_due(db, text: str) -> None:
    _seed(db, text, SRSState.REVIEW, days_until_due=0)


def _seed_well_known(db, text: str) -> None:
    _seed(db, text, SRSState.REVIEW, days_until_due=400)


async def _get_preview() -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()


async def _post_listen(payload: dict) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(LISTEN_URL, json=payload)
    assert resp.status_code == 200
    return resp.json()


def _row(preview: dict, text: str) -> dict:
    matches = [c for c in preview["candidates"] if c["text"] == text]
    assert len(matches) == 1, f"expected exactly one row for {text!r}, got {len(matches)}"
    return matches[0]


class TestDeferredReasonOnThePreview:
    """The preview labels WHY a row is deferred, so the modal can group by it."""

    async def test_learning_row_is_deferred_as_learning(self):
        db = _setup(["alpha beta"])
        _seed_learning(db, "alpha")
        _seed_due(db, "beta")

        preview = await _get_preview()
        assert _row(preview, "alpha")["deferred_reason"] == "learning"
        # The row is still VISIBLE and still classified as learning — this is
        # the opt-in answer, not the rejected "return None and drop it" one.
        assert _row(preview, "alpha")["grade_class"] == "learning"

    async def test_relearning_row_is_deferred_too(self):
        # A lapsed card is mid-reacquisition for the same reason a new one is
        # mid-acquisition. `_listen_grade_class` folds both into "learning", so
        # this passes for free — and pins that folding, which is the part a
        # future refactor could quietly split.
        db = _setup(["alpha"])
        _seed_relearning(db, "alpha")

        preview = await _get_preview()
        assert _row(preview, "alpha")["deferred_reason"] == "learning"

    async def test_well_known_row_is_deferred_as_known(self):
        db = _setup(["alpha"])
        _seed_well_known(db, "alpha")

        preview = await _get_preview()
        assert _row(preview, "alpha")["deferred_reason"] == "known"

    async def test_well_known_stays_derivable_from_the_reason(self):
        # `well_known` is kept for compatibility but must be DERIVED, never
        # maintained in parallel — two fields that can disagree is the failure
        # this single-field shape exists to prevent.
        db = _setup(["alpha beta gamma"])
        _seed_well_known(db, "alpha")
        _seed_learning(db, "beta")
        _seed_due(db, "gamma")

        preview = await _get_preview()
        for c in preview["candidates"]:
            assert c["well_known"] == (c["deferred_reason"] == "known"), c["text"]

    async def test_ordinary_and_create_rows_are_not_deferred(self):
        db = _setup(["alpha banka"])
        _seed_due(db, "alpha")

        preview = await _get_preview()
        assert _row(preview, "alpha")["deferred_reason"] is None
        # banka is untracked → a create row. A create is the most-wanted row in
        # the list; it must never land in a collapsed group.
        create = _row(preview, "banka")
        assert create["kind"] == "create"
        assert create["deferred_reason"] is None


class TestLearningCommitParity:
    """The commit side must agree with the preview: skip by default, stage on
    an explicit rating. Asserted against the STAGED COUNT, not against another
    surface — F-5's own lesson is that the badge and the served queue agreed
    with each other while both were wrong."""

    async def test_learning_word_skipped_by_default(self):
        db = _setup(["alpha beta"])
        _seed_learning(db, "alpha")
        _seed_due(db, "beta")

        result = await _post_listen({"lesson_id": "lesson-1", "word_ratings": {}})
        # beta (ordinary due) stages; alpha (learning) does not.
        assert result["staged"] == 1

    async def test_learning_word_staged_with_an_explicit_rating(self):
        db = _setup(["alpha beta"])
        _seed_learning(db, "alpha")
        _seed_due(db, "beta")

        result = await _post_listen({"lesson_id": "lesson-1", "word_ratings": {"alpha": "good"}})
        assert result["staged"] == 2

    async def test_an_explicit_skip_is_still_a_skip(self):
        # The opt-in reads word_ratings for MEMBERSHIP, so a literal "skip"
        # must not be mistaken for an opt-in and staged as one.
        db = _setup(["alpha beta"])
        _seed_learning(db, "alpha")
        _seed_due(db, "beta")

        result = await _post_listen({"lesson_id": "lesson-1", "word_ratings": {"alpha": "skip"}})
        assert result["staged"] == 1


class TestLearningKeyPhraseCommitParity:
    """The key-phrase loop carries its own copy of the guard (9af858e)."""

    async def test_learning_kp_skipped_by_default(self):
        db = _setup(["alpha"], key_phrases=["zdravo"])
        _seed_due(db, "alpha")
        _seed_learning(db, "zdravo")

        result = await _post_listen({"lesson_id": "lesson-1", "word_ratings": {}, "kp_ratings": {}})
        # Only alpha stages. A learning key phrase is deferred exactly like a
        # learning word — if this reads 2, the guard went into the word loop
        # only, which is the divergence the preview cannot see.
        assert result["staged"] == 1

    async def test_learning_kp_staged_with_an_explicit_rating(self):
        db = _setup(["alpha"], key_phrases=["zdravo"])
        _seed_due(db, "alpha")
        _seed_learning(db, "zdravo")

        result = await _post_listen({"lesson_id": "lesson-1", "word_ratings": {}, "kp_ratings": {"zdravo": "good"}})
        assert result["staged"] == 2


class TestWellKnownBehaviourIsUnchanged:
    """Regression guards. The known path is being re-expressed through
    ``deferred_reason``, not redesigned — these must stay green throughout."""

    async def test_well_known_still_skipped_by_default(self):
        db = _setup(["alpha beta"])
        _seed_well_known(db, "alpha")
        _seed_due(db, "beta")

        result = await _post_listen({"lesson_id": "lesson-1", "word_ratings": {}})
        assert result["staged"] == 1

    async def test_well_known_still_staged_with_an_explicit_rating(self):
        db = _setup(["alpha beta"])
        _seed_well_known(db, "alpha")
        _seed_due(db, "beta")

        result = await _post_listen({"lesson_id": "lesson-1", "word_ratings": {"alpha": "good"}})
        assert result["staged"] == 2
