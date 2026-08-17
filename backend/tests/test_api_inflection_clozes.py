"""Tests for POST /api/srs/inflection-clozes (Phase 4a)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.models import CreateCardResponse
from app.common.guid import compute_guid
from app.main import app
from app.models.srs_item import Direction, DirectionState, SRSState
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.srs_item_shape import SRS_ITEM_KEYS


class TestInflectionClozes:
    """Phase 4a: on-demand morphology cloze creation."""

    @pytest.fixture(autouse=True)
    def _mock_audio(self, monkeypatch: pytest.MonkeyPatch):
        import app.api.srs as srs_mod

        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", AsyncMock())

    @staticmethod
    def _seed_base_learned(db):
        """Seed a base vocab collocation with production in REVIEW."""
        unit = SyntacticUnit(text="ljubljana", translation="ljubljana", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("ljubljana")
        today = date.today()
        db.update_direction(
            item.guid,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                due_at=datetime.combine(today, time(4, 0), tzinfo=UTC),
                stability=5.0,
                difficulty=4.0,
                reps=5,
                state=SRSState.REVIEW,
            ),
        )

    async def test_eligible_base_creates_cloze(self, api_app_state):
        """Base production in REVIEW → cloze created with correct shape."""
        self._seed_base_learned(api_app_state)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["id"] > 0
        assert "item" in data

        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze is not None
        assert cloze.syntactic_unit.card_type == "cloze"
        assert cloze.syntactic_unit.disambig_key == "morph:noun-acc-sg"
        expected_sentence = "Grem v Ljubljan{{c1::o}}."
        assert cloze.syntactic_unit.source_sentence == expected_sentence
        assert cloze.syntactic_unit.grammar == "ljubljana, accusative singular"
        assert cloze.anki_note_id is None
        assert cloze.directions[Direction.PRODUCTION].state == SRSState.NEW

    async def test_inflection_cloze_response_keys_match_model(self, api_app_state):
        """Oracle for the response_model flip (openapi ledger batch 7)."""
        self._seed_base_learned(api_app_state)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert set(data.keys()) == {"id", "was_created", "item"}
        assert set(data["item"].keys()) == SRS_ITEM_KEYS
        assert set(CreateCardResponse.model_fields) == {"id", "was_created", "item"}

        # Element-level key set for the NESTED direction — see the twin
        # assertion in test_api_base_cards.py. A cloze is production-only and
        # NEW, so `_direction_to_dict` omits `left`; a plain `response_model=`
        # would re-add `"left": null`. This is what pins
        # `response_model_exclude_unset=True` on this route.
        prod = data["item"]["directions"]["production"]
        assert set(prod.keys()) == {
            "state",
            "due_at",
            "stability",
            "difficulty",
            "reps",
            "lapses",
            "last_review",
            "last_review_time_ms",
            "anki_card_id",
        }, "a NEW cloze's direction must not carry a `left` key"

    async def test_base_absent_returns_409(self, api_app_state):
        """No base collocation → 409, no row created."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 409
        detail = resp.json()["detail"].lower()
        assert "base word not yet learned" in detail or "not yet learned" in detail

        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        assert api_app_state.get_collocation_by_guid(guid) is None

    async def test_base_not_learned_returns_409(self, api_app_state):
        """Base production still NEW → 409, no row created."""
        # Seed base with production in NEW (default)
        unit = SyntacticUnit(text="ljubljana", translation="ljubljana", word_count=1, difficulty=1, source="test")
        api_app_state.add_collocation(unit, language_code="sl")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 409
        detail = resp.json()["detail"].lower()
        assert "base word not yet learned" in detail or "not yet learned" in detail

        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        assert api_app_state.get_collocation_by_guid(guid) is None

    async def test_base_with_no_production_card_says_so(self, api_app_state):
        """A missing production card and an unlearned one are different facts.

        Before the just-in-time mint (tunatale-qf6.2) they were the same 409 for a
        good reason — 2990 of 3009 Norwegian words had no production direction at
        all and never would, so the distinction was academic. Now the promotion
        phase fills them in at 10 per sync, so a word can be *waiting for its
        production card to be minted* rather than waiting for the learner, and a
        single message makes a feature that is working look like one that is not
        (`feedback_silent_state_hides_failures`).
        """
        # Recognition-only, which is the shape the Anki seed import actually
        # produces — not a direction deleted after the fact.
        unit = SyntacticUnit(text="ljubljana", translation="ljubljana", word_count=1, difficulty=1, source="test")
        api_app_state.upsert_by_guid(
            unit,
            "sl",
            {
                Direction.RECOGNITION: DirectionState(
                    direction=Direction.RECOGNITION,
                    due_at=datetime.now(UTC),
                    state=SRSState.REVIEW,
                    reps=9,
                )
            },
            anki_note_id=4242,
        )

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 409
        detail = resp.json()["detail"].lower()
        assert "no production card" in detail, f"indistinguishable from not-yet-learned: {detail!r}"

        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        assert api_app_state.get_collocation_by_guid(guid) is None

    @staticmethod
    def _seed_covered_by_cloze(db, *, cloze_state: SRSState):
        """A word whose production card is a base cloze on a separate note."""
        from app.common.guid import compute_guid

        unit = SyntacticUnit(
            text="ljubljana",
            translation="ljubljana",
            word_count=1,
            difficulty=1,
            source="test",
            disambig_key="noun",
        )
        base_id = db.upsert_by_guid(
            unit,
            "sl",
            {
                Direction.RECOGNITION: DirectionState(
                    direction=Direction.RECOGNITION,
                    due_at=datetime.now(UTC),
                    state=SRSState.REVIEW,
                    reps=9,
                )
            },
            anki_note_id=8800,
        )
        cloze_unit = SyntacticUnit(
            text="ljubljana",
            translation="ljubljana",
            word_count=1,
            difficulty=1,
            source="test",
            lemma="ljubljana",
            card_type="cloze",
            source_sentence="Grem v {{c1::Ljubljano}}.",
        )
        db.add_collocation(cloze_unit, language_code="sl")
        cloze_guid = compute_guid(cloze_unit.text, "sl", "")
        db.update_direction(
            cloze_guid,
            Direction.PRODUCTION,
            DirectionState(
                direction=Direction.PRODUCTION,
                due_at=datetime.now(UTC),
                state=cloze_state,
                stability=30.0,
                reps=6,
            ),
        )
        db.set_base_collocation_id(db.get_collocation_id_by_guid(cloze_guid), base_id)

    async def test_a_word_covered_by_a_cloze_is_not_told_to_wait_for_a_mint(self, api_app_state):
        """The "yet" has to be true.

        "No production card yet" promises a pending mint. For a cloze-covered
        word that mint will NEVER come — the promotion phase excludes it by
        design, because the word already has a production card. Saying "yet"
        there is a specific false statement, and it was introduced by the fix
        that split this message in two (qf6.4).
        """
        self._seed_covered_by_cloze(api_app_state, cloze_state=SRSState.NEW)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 409
        detail = resp.json()["detail"].lower()
        assert "no production card" not in detail, f"promised a mint that will never come: {detail!r}"
        assert "not yet learned" in detail

    async def test_a_word_whose_covering_cloze_is_learned_can_be_inflected(self, api_app_state):
        """The affordance must actually open for these words.

        Their production card is a cloze, and a cloze in REVIEW means the learner
        can produce the word — which is the whole thing the gate is asking.
        """
        self._seed_covered_by_cloze(api_app_state, cloze_state=SRSState.REVIEW)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 200, resp.json()

    async def test_degenerate_surface_equals_lemma_returns_422(self, api_app_state):
        """surface==lemma → 422."""
        self._seed_base_learned(api_app_state)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "ljubljana",
                    "lemma": "ljubljana",
                    "feature": "noun:nom:sg",
                    "sentence": "Ljubljana je lepa.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 422
        detail = resp.json()["detail"].lower()
        assert "surface equals lemma" in detail or "nothing to cloze" in detail

    async def test_idempotent(self, api_app_state):
        """POST twice → exactly one row."""
        self._seed_base_learned(api_app_state)

        body = {
            "surface": "Ljubljano",
            "lemma": "ljubljana",
            "feature": "noun:acc:sg",
            "sentence": "Grem v Ljubljano.",
            "language_code": "sl",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp1 = await client.post("/api/srs/inflection-clozes", json=body)
            resp2 = await client.post("/api/srs/inflection-clozes", json=body)

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        with api_app_state._get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM collocations WHERE guid = ?", (guid,)).fetchone()[0]
        assert count == 1

    async def test_sync_create_new_round_trip(self, api_app_state):
        """After creation, sync_create_new links an Anki Cloze note."""
        from app.plugins.anki_sync.sync import AnkiSync, OfflineWriter
        from tests._helpers.anki_sync_create_new import (
            FakeReader,
            _make_dual_collection_conn,
        )

        self._seed_base_learned(api_app_state)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )
        assert resp.status_code == 200

        # Pre-link the base vocab so sync_create_new only processes the cloze
        base_item = api_app_state.get_collocation("ljubljana")
        api_app_state.set_anki_ids(base_item.guid, 99999, {Direction.RECOGNITION: 999991, Direction.PRODUCTION: 999992})

        anki_conn = _make_dual_collection_conn()
        writer = OfflineWriter(anki_conn)
        await AnkiSync(db=api_app_state, _reader=FakeReader(), _writer=writer).sync_create_new(
            deck_name="0. Slovene", model_name="Slovene Vocabulary"
        )

        notes = anki_conn.execute("SELECT n.id, n.mid, n.flds, n.tags, n.guid FROM notes n").fetchall()
        assert len(notes) == 1
        note = notes[0]
        assert note["mid"] == 1000002  # Cloze notetype
        flds = note["flds"].split("\x1f")
        expected_cloze = "Grem v Ljubljan{{c1::o}}."
        assert flds[0] == expected_cloze
        assert "ljubljana, accusative singular" in flds[1]
        assert 'class="grammar"' in flds[1]

        cards = anki_conn.execute("SELECT * FROM cards WHERE nid = ?", (note["id"],)).fetchall()
        assert len(cards) == 1

        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.anki_note_id == note["id"]

    async def test_audio_synthesis_failure_does_not_crash_endpoint(self, api_app_state, monkeypatch):
        """synthesize_cloze_audios raising does not prevent 200 response."""
        import app.api.srs as srs_mod

        audio_mock = AsyncMock(side_effect=RuntimeError("TTS failed"))
        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", audio_mock)

        self._seed_base_learned(api_app_state)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["was_created"] is True

    async def test_surfaces_same_day_in_review_queue(self, api_app_state):
        """NEW cloze appears in /review-queue without sync."""
        self._seed_base_learned(api_app_state)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )
        assert resp.status_code == 200

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            queue_resp = await client.get("/api/srs/review-queue")
        assert queue_resp.status_code == 200
        queue = queue_resp.json()["queue"]

        matching = [q for q in queue if q["text"] == "Ljubljano"]
        assert len(matching) >= 1
        item = matching[0]
        assert item["card_type"] == "cloze"
        assert item["state"] == "new"

    async def test_biti_with_no_base_succeeds(self, api_app_state):
        """biti is a clozes-only verb — no base required for inflection cloze."""
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "ste",
                    "lemma": "biti",
                    "feature": "verb:2pl",
                    "sentence": "Zdravo kje ste",
                    "language_code": "sl",
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["was_created"] is True

        guid = compute_guid("ste", "sl", "morph:verb-2pl")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze is not None
        assert cloze.syntactic_unit.card_type == "cloze"
        assert cloze.syntactic_unit.disambig_key == "morph:verb-2pl"
        assert cloze.syntactic_unit.grammar == "biti, 2nd person plural"
        assert cloze.directions[Direction.PRODUCTION].state == SRSState.NEW

    async def test_llm_glosses_the_inflected_form(self, api_app_state):
        """biti cloze → LLM gloss of the specific form becomes the translation."""
        from app.main import app

        mock_llm = AsyncMock()
        mock_llm.complete.return_value = "you will be"
        app.state.llm = mock_llm

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "boste",
                    "lemma": "biti",
                    "feature": "verb:2pl",
                    "sentence": "Kje boste ostali",
                    "language_code": "sl",
                },
            )
        assert resp.status_code == 200
        guid = compute_guid("boste", "sl", "morph:verb-2pl")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.syntactic_unit.translation == "you will be"
        # Grammar hint stays in its own field.
        assert cloze.syntactic_unit.grammar == "biti, 2nd person plural"

    async def test_llm_empty_gloss_keeps_fallback(self, api_app_state):
        """LLM failure/empty → keep the body/token-gloss fallback (fail-soft)."""
        from app.main import app

        mock_llm = AsyncMock()
        mock_llm.complete.return_value = ""
        app.state.llm = mock_llm

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "boste",
                    "lemma": "biti",
                    "feature": "verb:2pl",
                    "sentence": "Kje boste ostali",
                    "language_code": "sl",
                    "translation": "fallback gloss",
                },
            )
        assert resp.status_code == 200
        guid = compute_guid("boste", "sl", "morph:verb-2pl")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.syntactic_unit.translation == "fallback gloss"

    async def test_resolves_gloss_and_sentence_translation_from_lesson(self, api_app_state):
        """lesson_id → word gloss (token_glosses) + sentence_translation (metadata)."""
        from app.main import app
        from app.models.lesson import Lesson

        self._seed_base_learned(api_app_state)
        store = app.state.content_store
        lesson = Lesson(title="Day 1", language_code="sl", sections=[], key_phrases=[])
        lesson.generation_metadata = {
            "token_glosses": {"ljubljano": "Ljubljana"},
            "sentence_translations": {"Grem v Ljubljano.": "I'm going to Ljubljana."},
        }
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                    "lesson_id": "lesson-1",
                },
            )
        assert resp.status_code == 200

        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.syntactic_unit.translation == "Ljubljana"
        assert cloze.syntactic_unit.source_sentence_translation == "I'm going to Ljubljana."
        # The grammar hint stays in its own field — never the translation.
        assert cloze.syntactic_unit.grammar == "ljubljana, accusative singular"

    async def test_sentence_translation_matches_despite_punctuation(self, api_app_state):
        """The transcript passes a punctuation-stripped sentence; lookup still matches."""
        from app.main import app
        from app.models.lesson import Lesson

        self._seed_base_learned(api_app_state)
        store = app.state.content_store
        lesson = Lesson(title="Day 1", language_code="sl", sections=[], key_phrases=[])
        lesson.generation_metadata = {
            "sentence_translations": {"Grem v Ljubljano.": "I'm going to Ljubljana."},
        }
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    # No trailing period — as reconstructed from transcript surfaces.
                    "sentence": "Grem v Ljubljano",
                    "language_code": "sl",
                    "lesson_id": "lesson-1",
                },
            )
        assert resp.status_code == 200
        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.syntactic_unit.source_sentence_translation == "I'm going to Ljubljana."

    async def test_body_translation_used_as_word_gloss(self, api_app_state):
        """An explicit translation in the body becomes the word gloss."""
        from app.main import app

        self._seed_base_learned(api_app_state)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                    "translation": "Ljubljana (city)",
                },
            )
        assert resp.status_code == 200
        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.syntactic_unit.translation == "Ljubljana (city)"

    async def test_body_translation_wins_over_lesson_gloss(self, api_app_state):
        """Explicit body.translation is kept even when the lesson has a gloss."""
        from app.main import app
        from app.models.lesson import Lesson

        self._seed_base_learned(api_app_state)
        store = app.state.content_store
        lesson = Lesson(title="Day 1", language_code="sl", sections=[], key_phrases=[])
        lesson.generation_metadata = {"token_glosses": {"ljubljano": "auto-gloss"}}
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                    "lesson_id": "lesson-1",
                    "translation": "explicit",
                },
            )
        assert resp.status_code == 200
        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.syntactic_unit.translation == "explicit"

    async def test_gloss_falls_back_to_lemma_key(self, api_app_state):
        """When token_glosses lacks the surface, the lemma key is used."""
        from app.main import app
        from app.models.lesson import Lesson

        self._seed_base_learned(api_app_state)
        store = app.state.content_store
        lesson = Lesson(title="Day 1", language_code="sl", sections=[], key_phrases=[])
        lesson.generation_metadata = {"token_glosses": {"ljubljana": "the city"}}
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                    "lesson_id": "lesson-1",
                },
            )
        assert resp.status_code == 200
        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.syntactic_unit.translation == "the city"

    async def test_lesson_missing_leaves_translation_empty(self, api_app_state):
        """lesson_id pointing at no stored lesson → no gloss/sentence resolution."""
        from app.main import app

        self._seed_base_learned(api_app_state)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                    "lesson_id": "does-not-exist",
                },
            )
        assert resp.status_code == 200
        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.syntactic_unit.translation == ""
        assert cloze.syntactic_unit.source_sentence_translation == ""

    async def test_sentence_translation_derived_from_translated_section(self, api_app_state):
        """Sentence translation falls back to the TRANSLATED section when metadata lacks it."""
        from app.main import app
        from app.models.lesson import Lesson, Phrase, Section, SectionType

        self._seed_base_learned(api_app_state)
        store = app.state.content_store
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.TRANSLATED,
                    phrases=[
                        Phrase(text="Grem v Ljubljano.", voice_id="v", language_code="sl"),
                        Phrase(text="I'm going to Ljubljana.", voice_id="v", language_code="en"),
                    ],
                )
            ],
            key_phrases=[],
        )
        lesson.generation_metadata = {}
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                    "lesson_id": "lesson-1",
                },
            )
        assert resp.status_code == 200
        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.syntactic_unit.source_sentence_translation == "I'm going to Ljubljana."

    async def test_no_lesson_id_leaves_translation_empty(self, api_app_state):
        """Backward compat: no lesson_id/translation → empty word gloss, hint in grammar only."""
        from app.main import app

        self._seed_base_learned(api_app_state)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )
        assert resp.status_code == 200
        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.syntactic_unit.translation == ""
        assert cloze.syntactic_unit.source_sentence_translation == ""
        assert cloze.syntactic_unit.grammar == "ljubljana, accusative singular"

    async def test_biti_with_no_base_idempotent(self, api_app_state):
        """POST biti inflection cloze twice → exactly one row."""
        body = {
            "surface": "ste",
            "lemma": "biti",
            "feature": "verb:2pl",
            "sentence": "Zdravo kje ste",
            "language_code": "sl",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp1 = await client.post("/api/srs/inflection-clozes", json=body)
            resp2 = await client.post("/api/srs/inflection-clozes", json=body)

        assert resp1.status_code == 200
        assert resp2.status_code == 200

        guid = compute_guid("ste", "sl", "morph:verb-2pl")
        with api_app_state._get_conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM collocations WHERE guid = ?", (guid,)).fetchone()[0]
        assert count == 1

    async def test_backfills_sentence_translation_on_existing_empty_row(self, api_app_state):
        """Self-healing: a cloze first minted without lesson context (empty
        sentence_translation) gets backfilled when re-clicked from the lesson.

        Mirrors the /listen path's backfill (srs.py:461). Without it, an
        inflection cloze created before its lesson resolved — or via a UI surface
        that omitted lesson_id — strands permanently with no Anki Back Extra
        sentence-translation <span class="st"> (the nasvidenje/Glavnem-trgu bug).
        """
        from app.main import app
        from app.models.lesson import Lesson

        self._seed_base_learned(api_app_state)
        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. First mint WITHOUT lesson_id → empty sentence_translation.
            first = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                },
            )
            assert first.status_code == 200
            assert first.json()["was_created"] is True
            assert api_app_state.get_collocation_by_guid(guid).syntactic_unit.source_sentence_translation == ""

            # 2. The lesson now carries the translation; re-click WITH lesson_id.
            store = app.state.content_store
            lesson = Lesson(title="Day 1", language_code="sl", sections=[], key_phrases=[])
            lesson.generation_metadata = {
                "sentence_translations": {"Grem v Ljubljano.": "I'm going to Ljubljana."},
            }
            store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

            second = await client.post(
                "/api/srs/inflection-clozes",
                json={
                    "surface": "Ljubljano",
                    "lemma": "ljubljana",
                    "feature": "noun:acc:sg",
                    "sentence": "Grem v Ljubljano.",
                    "language_code": "sl",
                    "lesson_id": "lesson-1",
                },
            )
        assert second.status_code == 200
        assert second.json()["was_created"] is False

        # The existing row is backfilled and marked dirty so sync rewrites Back Extra.
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.syntactic_unit.source_sentence_translation == "I'm going to Ljubljana."
        with api_app_state._get_conn() as conn:
            dirty = conn.execute("SELECT dirty_fields FROM collocations WHERE guid = ?", (guid,)).fetchone()[0]
        assert "sentence_translation" in (dirty or "")

    async def test_idempotent_recall_does_not_redirty_populated_translation(self, api_app_state):
        """Re-clicking a cloze that already has its sentence_translation is a no-op.

        The backfill must fire only on an *empty* existing row — not re-dirty an
        already-populated one on every re-click (which would churn sync).
        """
        from app.main import app
        from app.models.lesson import Lesson

        self._seed_base_learned(api_app_state)
        store = app.state.content_store
        lesson = Lesson(title="Day 1", language_code="sl", sections=[], key_phrases=[])
        lesson.generation_metadata = {
            "sentence_translations": {"Grem v Ljubljano.": "I'm going to Ljubljana."},
        }
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        body = {
            "surface": "Ljubljano",
            "lemma": "ljubljana",
            "feature": "noun:acc:sg",
            "sentence": "Grem v Ljubljano.",
            "language_code": "sl",
            "lesson_id": "lesson-1",
        }

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/api/srs/inflection-clozes", json=body)
            second = await client.post("/api/srs/inflection-clozes", json=body)

        assert first.json()["was_created"] is True
        assert second.json()["was_created"] is False
        guid = compute_guid("Ljubljano", "sl", "morph:noun-acc-sg")
        cloze = api_app_state.get_collocation_by_guid(guid)
        assert cloze.syntactic_unit.source_sentence_translation == "I'm going to Ljubljana."
        # First mint stamped it dirty; the no-op re-call must not append a duplicate.
        with api_app_state._get_conn() as conn:
            dirty = conn.execute("SELECT dirty_fields FROM collocations WHERE guid = ?", (guid,)).fetchone()[0]
        assert (dirty or "").split(",").count("sentence_translation") <= 1
