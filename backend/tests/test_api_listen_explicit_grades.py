"""Explicit preview grades are applied, not staged.

The pending bucket exists so a listen doesn't silently grade cards the user
never looked at: everything a listen auto-rates "good" lands in
``pending_listen_grades`` and is released later via "Check your work".

But a grade the user actually *picked* in the preview is not a guess — it is a
review. Making the user re-review it is asking the same question twice. So:

    listed in confirmed_words/confirmed_kps → the user chose it → apply now
    not listed                              → auto-"good"      → stage pending
    "skip"                                  → neither

Confirmation travels in its OWN field rather than being inferred from presence
in word_ratings, because presence is already overloaded: a well-known row has
to appear in word_ratings for the backend to consider it at all, so "present"
cannot also mean "the user reviewed this". Ratings say what the grade is;
confirmed_* says who decided it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

LISTEN_URL = "/api/srs/listen"


def _setup_lesson(phrase_text: str, key_phrases: list[KeyPhraseInfo] | None = None):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=phrase_text, voice_id="female-1", language_code="sl", role="female-1")],
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


def _seed_review_due(db, text: str, *, days_overdue: int = 1) -> None:
    """A tracked vocab card whose recognition is REVIEW and past due.

    due_at uses the day-level 04:00-UTC convention (rollover.py::
    due_at_rollover_utc) — instant-flavored seeds (now - Nh) cross the UTC date
    line past 20:00 local and misread as "ahead".
    """
    from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

    unit = SyntacticUnit(text=text, translation=f"t-{text}", word_count=1, difficulty=1, source="test")
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.last_review = datetime.now(UTC) - timedelta(days=5)
    rec.due_at = due_at_rollover_utc(anki_today() - timedelta(days=days_overdue))
    rec.reps = 5
    db.update_collocation(item)


async def _listen(payload: dict) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(LISTEN_URL, json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _pending(db, text: str) -> dict | None:
    item = db.get_collocation(text)
    coll_id = db.get_collocation_id_by_guid(item.guid)
    return db.get_pending_grade(coll_id, "recognition")


def _reps(db, text: str) -> int:
    return db.get_collocation(text).directions[Direction.RECOGNITION].reps


class TestExplicitGradesApplyImmediately:
    async def test_explicit_non_default_grade_is_applied_not_staged(self):
        db = _setup_lesson("Banka riba")
        _seed_review_due(db, "banka")

        result = await _listen(
            {
                "lesson_id": "lesson-1",
                "word_ratings": {"banka": "hard"},
                "confirmed_words": ["banka"],
            }
        )

        assert _pending(db, "banka") is None, "an explicitly graded card must not need re-review"
        assert _reps(db, "banka") == 6, "the grade must actually have been applied"
        assert result["applied"] == 1

    async def test_confirmed_good_is_applied_even_though_it_is_the_default(self):
        """The rating is identical to the auto one; only the confirmation differs.

        An explicit "good" and an auto "good" carry the same value but mean
        different things: one is a review the user performed, the other is an
        assumption. A rule keyed on the rating value alone gets this wrong, and
        it is the whole point of the feature.
        """
        db = _setup_lesson("Banka riba")
        _seed_review_due(db, "banka")

        result = await _listen({"lesson_id": "lesson-1", "confirmed_words": ["banka"]})

        assert _pending(db, "banka") is None
        assert _reps(db, "banka") == 6
        assert result["applied"] == 1

    async def test_absent_entry_is_still_staged_for_review(self):
        """The auto-graded majority keeps its safety net."""
        db = _setup_lesson("Banka riba")
        _seed_review_due(db, "banka")

        result = await _listen({"lesson_id": "lesson-1"})

        row = _pending(db, "banka")
        assert row is not None, "an unreviewed auto-grade must still be staged"
        assert row["rating"] == "good"
        assert _reps(db, "banka") == 5, "staging must not grade"
        assert result["staged"] == 1
        assert result["applied"] == 0

    async def test_skip_neither_applies_nor_stages(self):
        db = _setup_lesson("Banka riba")
        _seed_review_due(db, "banka")

        result = await _listen(
            {
                "lesson_id": "lesson-1",
                "word_ratings": {"banka": "skip"},
                "confirmed_words": ["banka"],
            }
        )

        assert _pending(db, "banka") is None
        assert _reps(db, "banka") == 5
        assert result["applied"] == 0

    async def test_mixed_listen_splits_applied_from_staged(self):
        db = _setup_lesson("Banka riba mesto")
        for w in ("banka", "riba", "mesto"):
            _seed_review_due(db, w)

        result = await _listen(
            {
                "lesson_id": "lesson-1",
                "word_ratings": {"banka": "easy", "riba": "skip"},
                "confirmed_words": ["banka", "riba"],
            }
        )

        assert _pending(db, "banka") is None and _reps(db, "banka") == 6  # explicit → applied
        assert _pending(db, "riba") is None and _reps(db, "riba") == 5  # skip → untouched
        assert _pending(db, "mesto") is not None and _reps(db, "mesto") == 5  # auto → staged
        assert (result["applied"], result["staged"]) == (1, 1)

    async def test_explicit_key_phrase_grade_is_applied(self):
        db = _setup_lesson("Banka riba", key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")])
        _seed_review_due(db, "dober dan")

        result = await _listen(
            {
                "lesson_id": "lesson-1",
                "kp_ratings": {"dober dan": "hard"},
                "confirmed_kps": ["dober dan"],
            }
        )

        assert _pending(db, "dober dan") is None
        assert _reps(db, "dober dan") == 6
        assert result["applied"] == 1

    async def test_applied_grade_writes_a_revlog_row(self):
        """Applied means really applied — through schedule → revlog → dirty,
        the same path a released pending grade takes, so the sync push sees it.
        """
        db = _setup_lesson("Banka riba")
        _seed_review_due(db, "banka")
        item = db.get_collocation("banka")
        coll_id = db.get_collocation_id_by_guid(item.guid)
        before = db.get_tt_revlog_ids(coll_id, Direction.RECOGNITION)

        await _listen(
            {
                "lesson_id": "lesson-1",
                "word_ratings": {"banka": "hard"},
                "confirmed_words": ["banka"],
            }
        )

        after = db.get_tt_revlog_ids(coll_id, Direction.RECOGNITION)
        assert len(after - before) == 1

    async def test_two_explicit_grades_do_not_collide_in_the_revlog(self):
        """tt_revlog.id is a millisecond PK and append_revlog is INSERT OR
        IGNORE, so two grades in the same millisecond would silently drop one —
        the batch needs the monotonic grade clock, exactly like commit-pending.
        """
        db = _setup_lesson("Banka riba mesto")
        for w in ("banka", "riba", "mesto"):
            _seed_review_due(db, w)

        await _listen(
            {
                "lesson_id": "lesson-1",
                "word_ratings": {"banka": "hard", "riba": "good", "mesto": "easy"},
                "confirmed_words": ["banka", "riba", "mesto"],
            }
        )

        ids: list[int] = []
        for w in ("banka", "riba", "mesto"):
            item = db.get_collocation(w)
            coll_id = db.get_collocation_id_by_guid(item.guid)
            rows = db.get_tt_revlog_ids(coll_id, Direction.RECOGNITION)
            assert len(rows) == 1, f"{w} lost its revlog row to a PK collision"
            ids.extend(rows)
        assert len(set(ids)) == 3, "revlog ids collided across the batch"


class TestConfirmationIsNotInferredFromPresence:
    async def test_a_well_known_row_sent_as_good_but_unconfirmed_is_staged(self):
        """The case that rules out inferring confirmation from presence.

        A well-known row MUST appear in word_ratings or the backend won't
        consider it at all. If presence also meant "confirmed", pressing Grade
        All — which sets collapsed well-known rows to "good" without the user
        ever seeing them — would commit reviews for cards behind a disclosure
        triangle. Sent-but-unconfirmed must still stage.
        """
        from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

        db = _setup_lesson("Banka riba")
        _seed_review_due(db, "banka")
        # Push it far enough out to count as well-known.
        item = db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.due_at = due_at_rollover_utc(anki_today() + timedelta(days=400))
        db.update_collocation(item)

        result = await _listen({"lesson_id": "lesson-1", "word_ratings": {"banka": "good"}})

        assert _pending(db, "banka") is not None, "unconfirmed must stage, whatever the rating map says"
        assert _reps(db, "banka") == 5
        assert result["applied"] == 0
