"""Skipping a create row CONSUMES its creation slot — it does not free it.

Orchestrator-authored. ⚠️ DO NOT EDIT while implementing — this is the oracle,
not a test of your implementation. If an assertion looks wrong, STOP and report.

Ratified by the user 2026-07-31, reversing the promote-on-uncheck semantics this
file pinned earlier the same day (`d589559`). The model now:

- A skipped untracked lemma still occupies its rank position, so the budget slot
  is spent whether or not a card is created. Nothing is promoted into its place.
- Skip All therefore creates NOTHING, rather than quietly creating the next
  block of lemmas the user never saw.
- The unspent daily-new headroom is not wasted: the queue engine's
  `new_quota = cap - introduced_today` never subtracted created-today, so
  existing NEW cards fill the session normally. That fallback needs no code and
  is not asserted here — it is simply why "create nothing" is the right answer.

Consequence worth understanding before changing anything: because skipping never
promotes, the server's `will_create` flag is correct no matter what the user
checks, so the modal partitions on it STATICALLY. The client-side re-derivation
this file used to justify is gone. See
`frontend/src/routes/c/[curriculumId]/l/[lessonId]/ListenPreviewModal.creationTail.test.ts`.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/lesson/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"

# occurrences: banka=3, kava=2, hotel=1, center=1, mesto=1.
# First-appearance order: hotel, kava, banka, center, mesto.
# Stable sort on -occurrences therefore ranks:
#     banka, kava, hotel, center, mesto
# With a cap of 2: live = [banka, kava], tail = [hotel, center, mesto].
_SENTENCE = "hotel kava banka kava banka banka center mesto"
_EXPECTED_RANK = ["banka", "kava", "hotel", "center", "mesto"]


def _setup(language_code: str = "sl"):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code=language_code,
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[
                    Phrase(
                        text=_SENTENCE,
                        voice_id="female-1",
                        language_code=language_code,
                        role="female-1",
                    )
                ],
            )
        ],
        key_phrases=[],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


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


def _created(db, lemmas: list[str]) -> set[str]:
    return {lem for lem in lemmas if db.get_collocation_by_lemma(lem) is not None}


class TestSkipConsumesItsCreationSlot:
    async def test_preview_discloses_the_full_ranking_with_the_budget_flagged(self):
        """Unchanged by this reversal: the preview is still the full ranking,
        live block first. Only what a SKIP does to it has changed."""
        db = _setup()
        db.set_anki_state_cache("daily_new_cap", "2")

        preview = await _get_preview()
        creates = [c for c in preview["candidates"] if c["kind"] == "create"]

        assert [c["text"] for c in creates] == _EXPECTED_RANK
        assert [c["text"] for c in creates if c["will_create"]] == ["banka", "kava"]
        assert [c["text"] for c in creates if not c["will_create"]] == ["hotel", "center", "mesto"]

    async def test_skipping_one_live_row_creates_only_the_other_one(self):
        """The reversal, in one assertion: the freed slot is NOT handed to the
        next-ranked lemma. `hotel` led the tail and stays uncreated."""
        db = _setup()
        db.set_anki_state_cache("daily_new_cap", "2")

        listen = await _post_listen({"lesson_id": "lesson-1", "word_ratings": {"banka": "skip"}})

        assert listen["created"] == 1
        assert _created(db, _EXPECTED_RANK) == {"kava"}

    async def test_skip_all_creates_nothing(self):
        """The case that motivated the reversal. Skipping every live create is a
        refusal, not a request for two different cards."""
        db = _setup()
        db.set_anki_state_cache("daily_new_cap", "2")

        listen = await _post_listen({"lesson_id": "lesson-1", "word_ratings": {"banka": "skip", "kava": "skip"}})

        assert listen["created"] == 0
        assert _created(db, _EXPECTED_RANK) == set()

    async def test_skipping_a_tail_row_changes_nothing(self):
        """A tail row is outside the budget window, so naming it is inert — it
        neither creates anything nor displaces a live row. This is what lets the
        modal send NOTHING for tail rows."""
        db = _setup()
        db.set_anki_state_cache("daily_new_cap", "2")

        listen = await _post_listen({"lesson_id": "lesson-1", "word_ratings": {"mesto": "skip"}})

        assert listen["created"] == 2
        assert _created(db, _EXPECTED_RANK) == {"banka", "kava"}

    async def test_a_skipped_lemma_is_still_offered_on_the_next_listen(self):
        """Consuming the slot must not mean consuming the CANDIDATE. A skip is
        'not today', so the lemma comes back — otherwise skipping would silently
        blacklist a word (that is what the ignore list is for)."""
        db = _setup()
        db.set_anki_state_cache("daily_new_cap", "2")

        await _post_listen({"lesson_id": "lesson-1", "word_ratings": {"banka": "skip", "kava": "skip"}})

        preview = await _get_preview()
        creates = [c["text"] for c in preview["candidates"] if c["kind"] == "create"]
        assert "banka" in creates, "a skipped lemma must remain a candidate"
        assert "kava" in creates
