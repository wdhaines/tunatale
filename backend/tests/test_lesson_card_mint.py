"""Minting a lesson's card list into the deck (tunatale-h0q3.43).

The owner's decisions (2026-11-05/06, on the bead):

* Cards mint when the lesson is marked studied ("I listened"), not at import,
  and ALL of them mint at once as NEW: the deck's 10/day paces them. The listen
  preview's budget does not gate them.
* A card list added to a day already studied (a re-import, as Wolff 1 part 2
  needs) mints at import.
* Idempotent: the same list again mints nothing, and a word the deck already has
  is not minted a second time.

Card-adding contract (.claude/rules/anki-sync.md): rows go through
``add_collocation``, NEW, no Anki ids; same-day surfacing in /review-queue.
"""

from __future__ import annotations

import copy

from httpx import ASGITransport, AsyncClient

from app.audio.render_cost import natural_take
from app.common.guid import compute_guid
from app.main import app
from app.models.lesson import Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from app.srs.database import SRSDatabase
from app.srs.lesson_card_mint import attach_lesson_card_audio, day_was_studied, mint_lesson_cards
from app.srs.lesson_cards import lesson_cards
from app.storage.store import ContentStore
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401
from tests.test_lesson_card_list import _cards, _story
from tests.test_lesson_take import NB_A, NB_B, _instrument_paths


def _keys(db: SRSDatabase) -> list[str]:
    with db._get_conn() as conn:
        rows = conn.execute("SELECT text, disambig_key FROM collocations ORDER BY id").fetchall()
    return [r["disambig_key"] or r["text"] for r in rows]


def _expected_keys() -> list[str]:
    return [c.unit.disambig_key or c.unit.text for c in lesson_cards(_cards())]


class TestMint:
    def test_mints_every_card_new_in_list_order(self):
        db = SRSDatabase(":memory:")
        minted = mint_lesson_cards(db, "no", _cards())
        assert _keys(db) == _expected_keys()
        assert [card.unit.disambig_key or card.unit.text for _, card in minted] == _expected_keys()
        for cid, _card in minted:
            _, item, _lang = db.get_collocation_by_id(cid)
            assert item.syntactic_unit.source == "course"
            assert item.anki_note_id is None
            for state in item.directions.values():
                assert state.state == SRSState.NEW
                assert state.introduced_at is None

    def test_a_second_mint_adds_nothing(self):
        db = SRSDatabase(":memory:")
        mint_lesson_cards(db, "no", _cards())
        assert mint_lesson_cards(db, "no", copy.deepcopy(_cards())) == []
        assert _keys(db) == _expected_keys()

    def test_a_word_the_deck_has_is_not_minted_again(self):
        db = SRSDatabase(":memory:")
        db.add_collocation(
            SyntacticUnit(text="takk", translation="thanks", word_count=1, difficulty=1, source="llm", lemma="takk"),
            language_code="no",
        )
        minted = mint_lesson_cards(db, "no", _cards())
        assert "takk" not in [card.unit.text for _, card in minted if card.unit.card_type == "vocab"]
        assert _keys(db) == _expected_keys()  # the old takk row, then the sentences

    def test_a_word_the_deck_has_under_another_lemma_is_matched_by_its_text(self):
        db = SRSDatabase(":memory:")
        db.add_collocation(
            SyntacticUnit(text="takk", translation="thanks", word_count=1, difficulty=1, source="llm", lemma="takke"),
            language_code="no",
        )
        minted = mint_lesson_cards(db, "no", _cards())
        assert [c.unit.card_type for _, c in minted] == ["cloze"] * 5

    def test_a_sentence_card_carries_its_course_and_set(self):
        db = SRSDatabase(":memory:")
        minted = mint_lesson_cards(db, "no", _cards())
        cid, card = minted[3]  # line 2, whole
        guid = compute_guid(card.unit.text, "no", card.unit.disambig_key)
        assert db.get_collocation_id_by_guid(guid) == cid
        assert Direction.PRODUCTION in db.get_collocation_by_id(cid)[1].directions


def _lesson_with_takes(cache) -> Lesson:
    """The story's lines as a rendered lesson would say them: Kari = female-1 (NB_B), Per = male-1 (NB_A)."""
    lesson = Lesson(
        title="Hei",
        language_code="no",
        sections=[
            Section(
                SectionType.NATURAL_SPEED,
                [
                    Phrase("Hei!", NB_B, "no", role="female-1"),
                    Phrase("Hei, hvordan gar det?", NB_A, "no", role="male-1"),
                    Phrase("Bra, takk, og du?", NB_B, "no", role="female-1"),
                ],
            )
        ],
    )
    paths = _instrument_paths(lesson, cache)
    for (text, voice, enunciated), path in paths.items():
        if not enunciated:
            path.write_bytes(f"{text}|{voice}".encode())
    return lesson


class TestAudio:
    def test_each_sentence_card_gets_its_own_and_its_cues_take(self, tmp_path, language_no):
        db = SRSDatabase(":memory:")
        cache, media = tmp_path / "cache", tmp_path / "media"
        cache.mkdir()
        media.mkdir()
        lesson = _lesson_with_takes(cache)
        minted = mint_lesson_cards(db, "no", _cards())

        attach_lesson_card_audio(db, minted, lesson, language_no.tts_voice_map, cache_dir=cache, media_dir=media)

        by_key = {card.unit.disambig_key: cid for cid, card in minted if card.unit.disambig_key}
        line2 = by_key["sent:test-01:2"]
        answer = db.get_sentence_audio_filename(line2)
        cue = db.get_media_filename(line2, "audio_cue")
        assert (media / answer).read_bytes() == f"Hei, hvordan gar det?|{NB_A}".encode()
        assert (media / cue).read_bytes() == f"Hei!|{NB_B}".encode()
        opener = by_key["sent:test-01:1"]
        assert db.get_media_filename(opener, "audio_cue") is None
        assert (media / db.get_sentence_audio_filename(opener)).read_bytes() == f"Hei!|{NB_B}".encode()
        assert answer == f"take_{natural_take(lesson, 'Hei, hvordan gar det?', cache_dir=cache).name}"

    def test_a_word_is_left_to_the_listen_media(self, tmp_path, language_no):
        db = SRSDatabase(":memory:")
        cache, media = tmp_path / "cache", tmp_path / "media"
        cache.mkdir()
        media.mkdir()
        lesson = _lesson_with_takes(cache)
        minted = mint_lesson_cards(db, "no", _cards())
        attach_lesson_card_audio(db, minted, lesson, language_no.tts_voice_map, cache_dir=cache, media_dir=media)
        word_cid = minted[0][0]
        assert db.get_sentence_audio_filename(word_cid) is None


class TestDayWasStudied:
    def _store(self) -> ContentStore:
        store = ContentStore(":memory:")
        lesson = Lesson(title="t", language_code="no", sections=[])
        store.save_lesson("old-1", "cur-1", 2, lesson)
        store.save_lesson("other-1", "cur-1", 3, lesson)
        store.save_lesson("else-1", "cur-2", 2, lesson)
        return store

    def test_a_listen_on_any_lesson_of_that_day_counts(self):
        db = SRSDatabase(":memory:")
        store = self._store()
        db.record_listen("old-1")
        assert day_was_studied(db, store, "cur-1", 2) is True

    def test_a_listen_on_another_day_or_curriculum_does_not(self):
        db = SRSDatabase(":memory:")
        store = self._store()
        db.record_listen("other-1")
        db.record_listen("else-1")
        db.record_listen("gone-lesson")  # a listen whose lesson row no longer exists
        assert day_was_studied(db, store, "cur-1", 2) is False


# --------------------------------------------------------------------------
# Wiring: /listen and /api/story/import
# --------------------------------------------------------------------------


def _install(story: dict | None) -> SRSDatabase:
    """A no-op lesson for the generic listen mint (no L2 lines), carrying *story* as its source."""
    lesson = Lesson(
        title="Hei",
        language_code="no",
        sections=[Section(SectionType.NATURAL_SPEED, [])],
        key_phrases=[],
        generation_metadata={"story": story} if story is not None else {},
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


async def _post(path: str, body: dict):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(path, json=body)


class TestListenMints:
    async def test_listen_mints_the_lessons_card_list(self):
        db = _install(_story())
        resp = await _post("/api/srs/listen", {"content_id": "lesson-1"})
        assert resp.status_code == 200
        assert _keys(db) == _expected_keys()
        assert resp.json()["created"] == len(_expected_keys())

    async def test_a_second_listen_mints_nothing_more(self):
        db = _install(_story())
        await _post("/api/srs/listen", {"content_id": "lesson-1"})
        resp = await _post("/api/srs/listen", {"content_id": "lesson-1"})
        assert resp.json()["created"] == 0
        assert _keys(db) == _expected_keys()

    async def test_a_lesson_without_a_card_list_mints_none(self):
        story = _story()
        del story["cards"]
        db = _install(story)
        await _post("/api/srs/listen", {"content_id": "lesson-1"})
        assert _keys(db) == []

    async def test_a_minted_card_surfaces_today_without_a_sync(self):
        _install(_story())
        await _post("/api/srs/listen", {"content_id": "lesson-1"})
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            queue = (await client.get("/api/srs/review-queue")).json()["queue"]
        assert any(q["text"] == "takk" and q["state"] == "new" for q in queue)


class TestImportMints:
    """A card list added to a day already studied mints at import (Wolff 1 part 2's case).

    Slovene, unlike the rest of this module: the import route runs the
    lemmatizer, and this is the language whose import path the existing route
    tests (test_api_lesson_authoring.py) prove in CI's lean install.
    """

    def _setup(self):
        from app.generation.pipeline import LessonPipeline
        from app.languages import get_language
        from app.llm.activity import ActivityLog
        from app.models.curriculum import Curriculum, CurriculumDay

        store = ContentStore(":memory:")
        day = CurriculumDay(day=2, title="d", focus="f", learning_objective="o", collocations=[])
        store.save_curriculum("c1", Curriculum(id="c1", topic="t", language_code="sl", cefr_level="A1", days=[day]))
        store.save_lesson("old-1", "c1", 2, Lesson(title="t", language_code="sl", sections=[]))
        db = SRSDatabase(":memory:")
        app.state.content_store = store
        app.state.language = get_language("sl")
        app.state.srs_db = db
        app.state.pipeline = LessonPipeline(
            story_generator=None,
            renderer=None,
            audio_dir=None,
            content_stores={"sl": store},
            languages={"sl": get_language("sl")},
            srs_dbs={},
            activity_log=ActivityLog(maxlen=100),
            llm_client=None,
        )
        return db

    @staticmethod
    def _file() -> dict:
        story = _story()
        story["dialogue_glosses"] = [{"word": "hei", "translation": "hi"}]
        return {"curriculum_id": "c1", "day": 2, "story": story}

    async def test_a_studied_day_mints_its_new_card_list_at_import(self):
        db = self._setup()
        db.record_listen("old-1")
        resp = await _post("/api/story/import", self._file())
        assert resp.status_code == 201
        assert _keys(db) == _expected_keys()

    async def test_a_re_import_mints_nothing_twice(self):
        db = self._setup()
        db.record_listen("old-1")
        await _post("/api/story/import", self._file())
        await _post("/api/story/import", self._file())
        assert _keys(db) == _expected_keys()

    async def test_a_day_not_yet_studied_waits_for_its_listen(self):
        db = self._setup()
        resp = await _post("/api/story/import", self._file())
        assert resp.status_code == 201
        assert _keys(db) == []
