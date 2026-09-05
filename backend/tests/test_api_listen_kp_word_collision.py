"""A single-word key phrase and its lemma row are ONE card, not two.

`get_listen_preview` builds its rows in two independent passes — one over the
lesson's lemmas, one over ``lesson.key_phrases`` — and nothing reconciled them.
They cannot normally collide, because a curriculum lesson's key phrases are
multi-word (``spor i snøen``) and a lemma row is a single word. **A review
session breaks that assumption**: its key phrases are not phrases at all, they
are individual vocabulary items drawn from the deck, so every single-word key
phrase that is also a tracked lemma in the dialogue produced two rows for the
same card (measured on the live `no` deck 2026-09-05: ``dessuten`` and
``derimot``, both `item_id` 1570 / 1526, once as ``kind: "word"`` and once as
``kind: "kp"``).

The visible half is a duplicated row. The half that loses data is the commit:
both rows are sent — `word_ratings` and `kp_ratings` are separate maps — and
`mark_lesson_listened` runs the word pass before the key-phrase pass, both
staging against one ``pending_listen_grades`` row whose
``ON CONFLICT … DO UPDATE`` (db_pending_grades.py) makes the later write win
silently. Worse, a *confirmed* word row is APPLIED while the key-phrase row is
then STAGED: one card both reviewed and re-asked, which is exactly the
double-question the pending bucket exists to remove.

**The key-phrase row survives** (user's call, 2026-09-05): it carries the
better label, and it is the row a review session is actually about.

Identity is the resolved collocation id, never the text — see
``test_multi_word_key_phrase_does_not_swallow_its_own_words``, which is the
control that fails if the dedupe is ever rewritten as a text/substring match.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/content/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"


def _setup_lesson(phrase_text: str, key_phrases: list[KeyPhraseInfo]):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Review session",
        language_code="sl",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(
                        text=phrase_text,
                        voice_id="female-1",
                        language_code="sl",
                        role="female-1",
                    ),
                ],
            )
        ],
        key_phrases=key_phrases,
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


def _seed_review_due(db, text: str, *, days_overdue: int = 1) -> None:
    """A tracked vocab card whose recognition is REVIEW and past due.

    due_at follows the day-level 04:00-UTC convention (rollover.py::
    due_at_rollover_utc); an instant-flavored seed (now - Nh) crosses the UTC
    date line past 20:00 local and misreads as "ahead".
    """
    from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

    unit = SyntacticUnit(
        text=text,
        translation=f"t-{text}",
        word_count=len(text.split()),
        difficulty=1,
        source="test",
    )
    db.add_collocation(unit, language_code="sl")
    item = db.get_collocation(text)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.last_review = datetime.now(UTC) - timedelta(days=5)
    rec.due_at = due_at_rollover_utc(anki_today() - timedelta(days=days_overdue))
    rec.reps = 5
    db.update_collocation(item)


async def _preview() -> list[dict]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()["candidates"]


async def _post_listen(payload: dict) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(LISTEN_URL, json=payload)
    assert resp.status_code == 200
    return resp.json()


def _rows(candidates: list[dict], text: str) -> list[dict]:
    return [c for c in candidates if c["text"] == text]


class TestPreviewDeduplication:
    async def test_single_word_key_phrase_renders_once_as_kp(self):
        """The whole bug, at the row the learner sees."""
        db = _setup_lesson("Ampak derimot je bilo drugace", [KeyPhraseInfo(phrase="derimot", translation="however")])
        _seed_review_due(db, "derimot")

        rows = _rows(await _preview(), "derimot")

        assert len(rows) == 1, f"expected one row for the shared card, got {[r['kind'] for r in rows]}"
        assert rows[0]["kind"] == "kp"

    async def test_the_surviving_row_is_the_one_the_commit_will_act_on(self):
        """Preview↔commit agreement, not just a tidier list.

        The dropped row must be the one the commit ignores too, or the panel
        stops describing what "Mark as listened" does — the 6a5c718 divergence
        class.
        """
        db = _setup_lesson("Ampak derimot je bilo drugace", [KeyPhraseInfo(phrase="derimot", translation="however")])
        _seed_review_due(db, "derimot")
        coll_id = db.get_collocation_id_by_guid(db.get_collocation("derimot").guid)

        # The word row is graded AND confirmed; the key-phrase row is left to its
        # default "good". Pre-fix the word pass applies an immediate review, and
        # the key-phrase pass then finds the card already reviewed today
        # (`_listen_grade_class` -> None) and drops it — so the row the preview
        # showed as a key phrase silently becomes a real review of a card the
        # learner graded under a different label, and nothing reaches the
        # pending bucket at all.
        await _post_listen(
            {
                "content_id": "lesson-1",
                "word_ratings": {"derimot": "again"},
                "confirmed_words": ["derimot"],
            }
        )

        pending = [p for p in db.get_pending_grades("lesson-1") if p["collocation_id"] == coll_id]
        assert len(pending) == 1
        assert pending[0]["rating"] == "good", "the key-phrase row's grade is the one that lands"
        assert db.get_collocation("derimot").directions[Direction.RECOGNITION].reps == 5, (
            "the dropped word row must not also apply a review"
        )

    async def test_multi_word_key_phrase_does_not_swallow_its_own_words(self):
        """THE CONTROL. Identity is the collocation id, not the text.

        `ta hensyn` and `hensyn` are two cards and two rows. A dedupe written as
        a text or substring match passes every other test in this file and fails
        this one.
        """
        db = _setup_lesson(
            "Moramo ta hensyn in hensyn",
            [KeyPhraseInfo(phrase="ta hensyn", translation="take into account")],
        )
        _seed_review_due(db, "ta hensyn")
        _seed_review_due(db, "hensyn")

        candidates = await _preview()

        assert [c["kind"] for c in _rows(candidates, "ta hensyn")] == ["kp"]
        assert [c["kind"] for c in _rows(candidates, "hensyn")] == ["word"]

    async def test_untracked_word_still_offers_a_create_row(self):
        """A key phrase with no card claims nothing.

        `get_collocation` returns None, so the key-phrase pass emits no row; the
        lemma must still be offered for creation. Guards the dedupe against
        suppressing a row for a card that does not exist.
        """
        _setup_lesson("Ampak derimot je bilo drugace", [KeyPhraseInfo(phrase="derimot", translation="however")])

        rows = _rows(await _preview(), "derimot")

        assert [r["kind"] for r in rows] == ["create"]
