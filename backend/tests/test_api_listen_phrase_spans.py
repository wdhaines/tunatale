"""A listen grades the PHRASE card a word sits inside, not the word's own card.

bd tunatale-ceuc, slice 2. The live case: Norwegian ``gang`` is one card,
"hall", and ``med en gang`` ("at once") is another. The reader has always shown
``med en gang`` as one span carrying the phrase card. A listen did not look at
spans at all: it counted lemmas across the lesson, so every ``med en gang``
graded the "hall" card, and the phrase card — the one the line is actually
about — was never graded by a listen unless the lesson named it a key phrase.
Measured on the live decks 2026-10-06: 0 of 75 Norwegian key phrases have a
card, while 5 of 11 lessons use ``gang`` only inside a phrase card.

The rule, decided with the user:
- An occurrence inside a matched phrase card is not an occurrence of the single
  word. A word the lesson uses ONLY that way is neither graded nor offered for
  creation; a word it also uses on its own is graded as before.
- The phrase card is graded instead, and when phrases nest it is the OUTERMOST
  one — which is what the reader's span already is (``match_spans`` is greedy
  longest-first).

Every assertion is made twice, on the preview and on the commit: a row the
preview does not show must not be staged, and the reverse (the 6a5c718 class).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/content/lesson-1/listen-preview"
LISTEN_URL = "/api/srs/listen"


def _setup_lesson(lines: list[str], key_phrases: list[KeyPhraseInfo] | None = None):
    from app.srs.database import SRSDatabase
    from app.storage.store import ContentStore

    lesson = Lesson(
        title="Day 1",
        language_code="no",
        sections=[
            Section(
                section_type=SectionType.NATURAL_SPEED,
                phrases=[Phrase(text=t, voice_id="female-1", language_code="no", role="female-1") for t in lines],
            )
        ],
        key_phrases=key_phrases or [],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    # No creations unless a test asks: the cards under test are all tracked.
    db.set_anki_state_cache("daily_new_cap", "0")
    db.set_anki_state_cache("daily_review_cap", "50")
    return db


def _seed_review_due(db, text: str, translation: str) -> int:
    """A tracked card whose recognition is REVIEW and past due; returns its id.

    due_at follows the 04:00-UTC day convention (rollover.py::
    due_at_rollover_utc) — an instant-flavoured seed misreads near midnight.
    """
    unit = SyntacticUnit(
        text=text, translation=translation, word_count=len(text.split()), difficulty=1, source="test", lemma=text
    )
    db.add_collocation(unit, language_code="no")
    item = db.get_collocation(text)
    rec = item.directions[Direction.RECOGNITION]
    rec.state = SRSState.REVIEW
    rec.last_review = datetime.now(UTC) - timedelta(days=5)
    rec.due_at = due_at_rollover_utc(anki_today() - timedelta(days=1))
    rec.reps = 5
    db.update_collocation(item)
    coll_id = db.get_collocation_id_by_guid(item.guid)
    assert coll_id is not None
    return coll_id


async def _preview() -> list[dict]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(PREVIEW_URL)
    assert resp.status_code == 200
    return resp.json()["candidates"]


async def _listen(payload: dict | None = None) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(LISTEN_URL, json={"content_id": "lesson-1", **(payload or {})})
    assert resp.status_code == 200
    return resp.json()


def _staged_ids(db) -> set[int]:
    return {p["collocation_id"] for p in db.get_pending_grades("lesson-1")}


def _preview_ids(candidates: list[dict]) -> set[int]:
    return {c["item_id"] for c in candidates if c["item_id"] is not None}


class TestWordInsideAPhraseCard:
    async def test_premise_the_reader_shows_the_phrase_as_one_span(self):
        """If THIS fails the seed is wrong — fix the setup, not the rule."""
        db = _setup_lesson(["Kom med en gang"])
        _seed_review_due(db, "gang", "hall")
        phrase_id = _seed_review_due(db, "med en gang", "at once")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/content/lesson-1/transcript")
        words = resp.json()["dialogue_lines"][0]["words"]
        assert [w["collocation_span_id"] for w in words] == [None, phrase_id, phrase_id, phrase_id]

    async def test_a_word_used_only_inside_a_phrase_is_not_staged_and_the_phrase_is(self):
        db = _setup_lesson(["Kom med en gang"])
        gang_id = _seed_review_due(db, "gang", "hall")
        phrase_id = _seed_review_due(db, "med en gang", "at once")

        await _listen()

        staged = _staged_ids(db)
        assert phrase_id in staged, "the phrase card the line is about was not graded"
        assert gang_id not in staged, "the listen graded the single word's card for a use inside a phrase"

    async def test_the_preview_shows_the_phrase_row_and_not_the_word_row(self):
        db = _setup_lesson(["Kom med en gang"])
        gang_id = _seed_review_due(db, "gang", "hall")
        phrase_id = _seed_review_due(db, "med en gang", "at once")

        candidates = await _preview()

        ids = _preview_ids(candidates)
        assert phrase_id in ids
        assert gang_id not in ids
        row = next(c for c in candidates if c["item_id"] == phrase_id)
        assert (row["kind"], row["text"], row["translation"]) == ("kp", "med en gang", "at once")

    async def test_a_word_also_used_on_its_own_is_still_staged(self):
        """THE CONTROL: the exclusion is per occurrence, not per word."""
        db = _setup_lesson(["Kom med en gang", "En lang gang"])
        gang_id = _seed_review_due(db, "gang", "hall")
        phrase_id = _seed_review_due(db, "med en gang", "at once")

        candidates = await _preview()
        await _listen()

        assert {gang_id, phrase_id} <= _preview_ids(candidates)
        assert {gang_id, phrase_id} <= _staged_ids(db)

    async def test_without_a_phrase_card_the_word_is_staged_as_before(self):
        """Regression guard: no phrase card, no span, nothing changes."""
        db = _setup_lesson(["Kom med en gang"])
        gang_id = _seed_review_due(db, "gang", "hall")

        candidates = await _preview()
        await _listen()

        assert gang_id in _preview_ids(candidates)
        assert gang_id in _staged_ids(db)

    async def test_preview_and_commit_name_the_same_cards(self):
        db = _setup_lesson(["Kom med en gang", "En lang gang", "Jeg kom i går"])
        _seed_review_due(db, "gang", "hall")
        _seed_review_due(db, "med en gang", "at once")
        _seed_review_due(db, "i går", "yesterday")
        _seed_review_due(db, "kom", "came")

        candidates = await _preview()
        await _listen()

        assert _preview_ids(candidates) == _staged_ids(db)


class TestOutermostPhrase:
    async def test_the_longest_phrase_is_graded_and_the_one_inside_it_is_not(self):
        db = _setup_lesson(["Kom med en gang"])
        inner_id = _seed_review_due(db, "en gang", "once")
        outer_id = _seed_review_due(db, "med en gang", "at once")

        candidates = await _preview()
        await _listen()

        assert outer_id in _preview_ids(candidates)
        assert inner_id not in _preview_ids(candidates)
        assert _staged_ids(db) == {outer_id}

    async def test_the_inner_phrase_is_graded_where_it_stands_alone(self):
        db = _setup_lesson(["Kom med en gang", "Bare en gang"])
        inner_id = _seed_review_due(db, "en gang", "once")
        outer_id = _seed_review_due(db, "med en gang", "at once")

        await _listen()

        assert _staged_ids(db) == {inner_id, outer_id}


class TestPhraseRowsRideTheKeyPhraseRails:
    @pytest.mark.parametrize("named", ["med en gang", "Med en gang"])
    async def test_a_phrase_that_is_also_a_key_phrase_is_one_row(self, named):
        """Exact text: the key phrase finds the card itself, and the matched
        card must not be listed again. Different case: the key phrase finds no
        card (the lookup is exact), and the matched card is the one row."""
        db = _setup_lesson(["Kom med en gang"], [KeyPhraseInfo(phrase=named, translation="right away")])
        phrase_id = _seed_review_due(db, "med en gang", "at once")

        candidates = await _preview()
        await _listen()

        assert [c["item_id"] for c in candidates if c["item_id"] == phrase_id] == [phrase_id]
        assert len([p for p in db.get_pending_grades("lesson-1") if p["collocation_id"] == phrase_id]) == 1

    async def test_the_phrase_row_takes_a_rating_by_its_text(self):
        """The client keys key-phrase ratings by the row's text; a matched
        phrase row must answer to the text the preview gave it."""
        db = _setup_lesson(["Kom med en gang"])
        phrase_id = _seed_review_due(db, "med en gang", "at once")

        text = next(c["text"] for c in await _preview() if c["item_id"] == phrase_id)
        await _listen({"kp_ratings": {text: "again"}})

        pending = [p for p in db.get_pending_grades("lesson-1") if p["collocation_id"] == phrase_id]
        assert [p["rating"] for p in pending] == ["again"]

    async def test_a_skipped_phrase_row_stages_nothing_and_does_not_free_the_word(self):
        """Skipping the phrase is "not today" for the phrase. It must not hand
        the occurrence back to the single word."""
        db = _setup_lesson(["Kom med en gang"])
        gang_id = _seed_review_due(db, "gang", "hall")
        _seed_review_due(db, "med en gang", "at once")

        await _listen({"kp_ratings": {"med en gang": "skip"}})

        assert gang_id not in _staged_ids(db)
        assert _staged_ids(db) == set()


class TestCreationInsideAPhraseCard:
    async def test_an_untracked_word_used_only_inside_a_phrase_is_not_offered_for_creation(self):
        db = _setup_lesson(["Kom med en gang"])
        _seed_review_due(db, "med en gang", "at once")
        db.set_anki_state_cache("daily_new_cap", "10")

        candidates = await _preview()
        data = await _listen()

        creates = {c["text"] for c in candidates if c["kind"] == "create"}
        assert creates == {"kom"}, "only the word outside the phrase is a creation candidate"
        assert data["created"] == 1
        assert db.get_collocation("gang") is None
        assert db.get_collocation("kom") is not None


class TestANewPhraseCardIsNotIntroducedByAListen:
    """A listen reviews a matched phrase; it does not introduce one.

    On the key-phrase rails a NEW row LEADS the daily introduction budget, ahead
    of every frequency-ranked word. That is right for the few phrases a lesson
    is built around and wrong for any deck phrase a line happens to contain.
    """

    # "til slutt", NOT "i dag": the latter is in the plugin's multiword_traps
    # list, which already suppresses `dag` after `i` — the word test below
    # passed against the pre-change code for that reason alone.
    def _seed_new_phrase(self, db, text: str) -> int:
        unit = SyntacticUnit(text=text, translation="finally", word_count=2, difficulty=1, source="anki")
        db.add_collocation(unit, language_code="no")
        item = db.get_collocation(text)
        assert item.directions[Direction.RECOGNITION].state == SRSState.NEW
        # Backdated out of today's window: a card created today already holds a
        # budget slot and is introduced for free, which would make every budget
        # assertion below true whatever the code did.
        stamp = (datetime.now(UTC) - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET created_at = ? WHERE guid = ?", (stamp, item.guid))
            conn.commit()
        return db.get_collocation_id_by_guid(item.guid)

    async def test_a_new_matched_phrase_has_no_row_and_takes_no_budget(self):
        db = _setup_lesson(["Jeg kom til slutt"])
        phrase_id = self._seed_new_phrase(db, "til slutt")
        db.set_anki_state_cache("daily_new_cap", "1")

        candidates = await _preview()
        data = await _listen()

        assert phrase_id not in _preview_ids(candidates)
        assert phrase_id not in _staged_ids(db)
        # The one slot goes to a word, not to the phrase.
        assert data["created"] == 1
        assert db.get_collocation("til slutt").directions[Direction.RECOGNITION].state == SRSState.NEW

    async def test_its_words_are_still_not_single_word_occurrences(self):
        db = _setup_lesson(["Jeg kom til slutt"])
        self._seed_new_phrase(db, "til slutt")
        slutt_id = _seed_review_due(db, "slutt", "end")

        candidates = await _preview()
        await _listen()

        assert slutt_id not in _preview_ids(candidates)
        assert slutt_id not in _staged_ids(db)

    async def test_a_lessons_own_new_key_phrase_still_leads_the_budget(self):
        """THE CONTROL: the exclusion is for matched phrases only."""
        db = _setup_lesson(["Jeg kom til slutt"], [KeyPhraseInfo(phrase="til slutt", translation="finally")])
        phrase_id = self._seed_new_phrase(db, "til slutt")
        db.set_anki_state_cache("daily_new_cap", "1")

        candidates = await _preview()
        data = await _listen()

        row = next(c for c in candidates if c["item_id"] == phrase_id)
        assert row["kind"] == "kp"
        assert phrase_id in _staged_ids(db)
        assert data["created"] == 0, "the key phrase took the one slot"
