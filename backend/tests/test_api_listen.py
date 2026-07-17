"""Tests for listen endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, time
from unittest.mock import AsyncMock

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


class TestListenClozeIntegration:
    """Phase F: /listen as recognition-exposure event (Layer 1 redesign)."""

    async def _setup_lesson(
        self,
        phrase_text: str = "Kje je banka?",
        language_code: str = "sl",
    ):
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
                            text=phrase_text,
                            voice_id="female-1",
                            language_code=language_code,
                            role="female-1",
                        ),
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

    async def test_listen_creates_cloze_card_when_enabled(self):
        """Created cloze rows have state='new', reps=0, introduced_at IS NULL."""

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.card_type == "cloze"
        prod = item_kje.directions[Direction.PRODUCTION]
        assert prod.state == SRSState.NEW
        assert prod.reps == 0
        assert prod.introduced_at is None

        item_je = db.get_collocation_by_lemma("je")
        assert item_je is not None
        assert item_je.syntactic_unit.card_type == "cloze"
        assert item_je.syntactic_unit.source_sentence == "Kje {{c1::je}} banka?"

    async def test_listen_creates_no_cloze_for_biti_surface(self, monkeypatch):
        """A biti surface (lemma "biti") is clozes-only — no base cloze created by /listen.
        biti's per-person conjugation clozes are created by click, not auto.
        Regression: Phase 2b surface-blanking test now reflects the special-case."""
        import app.api.srs as srs_mod

        def fake_lemmatize(surfaces, text, lemmatizer, language_code, db=None, model_version=""):
            return ["biti" if s.lower() == "sem" else s.lower() for s in surfaces]

        monkeypatch.setattr(srs_mod, "lemmatize_surfaces_in_context", fake_lemmatize)

        db = await self._setup_lesson(phrase_text="Sem doma.")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # No base cloze should be created for biti
        item = db.get_collocation_by_lemma("biti")
        assert item is None

        # Other function words (e.g. "kje" if present) still get base clozes fine.
        # In this lesson ("Sem doma."), there are no other function words — just verify
        # no biti row was created regardless of surface.
        with db._get_conn() as conn:
            rows = conn.execute("SELECT * FROM collocations WHERE lemma = 'biti'").fetchall()
        assert len(rows) == 0

    async def test_listen_skips_biti_via_pos_aux(self, monkeypatch):
        """A biti surface (ste → classla AUX) is a clozes-only verb — no base cloze
        created by /listen. Without an analyzer 'ste' would fall through to
        standalone vocab, but even with POS detection biti is skipped."""
        import app.api.srs as srs_mod
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_analysis("ste", "biti", upos="AUX")
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)
        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", AsyncMock())

        db = await self._setup_lesson(phrase_text="Zdravo kje ste")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # No base cloze for biti
        item = db.get_collocation_by_lemma("biti")
        assert item is None

        # Other function words (kje) still get base clozes
        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.card_type == "cloze"

    async def test_listen_creates_no_base_cloze_for_biti(self, monkeypatch):
        """A lesson with biti surfaces creates NO base cloze — only click-triggered
        conjugation clozes. Regression for the biti clozes-only special case."""
        import app.api.srs as srs_mod
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_lemma("ste", "biti")
        stub.set_lemma("sem", "biti")
        stub.set_analysis("ste", "biti", upos="AUX")
        stub.set_analysis("sem", "biti", upos="AUX")
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)
        audio_mock = AsyncMock()
        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", audio_mock)

        db = await self._setup_lesson(phrase_text="Sem doma, kje ste")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # biti should have NO base cloze (card_type='cloze', lemma='biti', empty disambig)
        with db._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM collocations WHERE lemma = 'biti' AND card_type = 'cloze' AND (disambig_key IS NULL OR disambig_key = '')"
            ).fetchall()
        assert len(rows) == 0, f"Expected no base cloze for biti, got {len(rows)}"

        # Other function words in the lesson (kje) should still get their base clozes
        with db._get_conn() as conn:
            kje_row = conn.execute("SELECT * FROM collocations WHERE lemma = 'kje'").fetchone()
        assert kje_row is not None

    async def test_listen_cloze_audio_uses_surface_not_lemma(self, monkeypatch):
        """The answer-word audio for a plain cloze is synthesized from the surface
        that was blanked, not the dictionary lemma. Uses a non-biti function word
        (biti is clozes-only and no longer creates a base cloze)."""
        import app.api.srs as srs_mod

        # Use a mock lemmatizer that diverges lemma from surface for a non-biti
        # function word: "kje" maps to lemma "kje" (same), so we fake a different
        # scenario: surface "Kje" (capitalized) maps to "kje"
        audio_mock = AsyncMock()
        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", audio_mock)

        await self._setup_lesson(phrase_text="Kje je banka?")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert resp.status_code == 200

        # Cloze for kje — audio uses the surface that appears in the sentence
        words = [call.args[3] for call in audio_mock.await_args_list]
        assert "Kje" in words

    async def test_listen_existing_cloze_surface_keyed(self, monkeypatch):
        """A cloze card keyed by surface (not lemma) is found via the surface
        fallback and does not crash on re-query — the id is carried from the
        initial lookup rather than re-queried by lemma."""
        import app.api.srs as srs_mod
        from app.models.syntactic_unit import SyntacticUnit

        def fake_lemmatize(surfaces, text, lemmatizer, language_code, db=None, model_version=""):
            return ["pozdrav" if s.lower() == "zdravo" else s.lower() for s in surfaces]

        monkeypatch.setattr(srs_mod, "lemmatize_surfaces_in_context", fake_lemmatize)
        audio_mock = AsyncMock()
        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", audio_mock)

        db = await self._setup_lesson(phrase_text="Zdravo svet")

        # Pre-create a cloze card keyed by the surface form, not the lemma
        pre_unit = SyntacticUnit(
            text="zdravo",
            translation="hello",
            word_count=1,
            difficulty=1,
            source="test",
            lemma="zdravo",
            card_type="cloze",
            source_sentence="Zdravo svet",
        )
        db.add_collocation(pre_unit, language_code="sl")
        pre_id, _ = db.get_collocation_by_lemma_with_id("zdravo")
        assert pre_id is not None

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # No new card was created for the dictionary lemma (surface-keyed one was reused)
        assert db.get_collocation_by_lemma("pozdrav") is None

        # Audio was synthesized for the pre-existing card, not a new one
        assert audio_mock.await_args is not None
        call_ids = [call.args[1] for call in audio_mock.await_args_list]
        assert pre_id in call_ids

    async def test_listen_creates_vocab_for_unknown_content_word(self):
        """Unknown content-word lemma → vocab row, state='new', both directions present."""

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation_by_lemma("banka")
        assert item is not None
        assert item.syntactic_unit.card_type == "vocab"
        assert Direction.RECOGNITION in item.directions
        assert Direction.PRODUCTION in item.directions
        assert item.directions[Direction.RECOGNITION].state == SRSState.NEW
        assert item.directions[Direction.RECOGNITION].reps == 0

    async def test_listen_grades_recognition_when_learning(self):
        """Pre-existing vocab with recognition state=LEARNING → grade recognition, production unchanged."""

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        # Force banka's recognition to LEARNING
        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.LEARNING
        rec.reps = 1
        db.update_collocation(banka)

        prod_before = banka.directions.get(Direction.PRODUCTION)
        prod_reps_before = prod_before.reps if prod_before else 0

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        rec = banka.directions[Direction.RECOGNITION]
        assert rec.reps == 2  # graded
        assert rec.state != SRSState.NEW

        prod = banka.directions.get(Direction.PRODUCTION)
        if prod:
            assert prod.reps == prod_reps_before

    async def test_listen_grades_recognition_when_review_first_time_today(self):
        """Pre-existing vocab with rec state=REVIEW, last_review 2 days ago → graded."""
        from datetime import timedelta

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(days=2)
        rec.reps = 5
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 6

    async def test_listen_skips_recognition_when_review_already_today(self):
        """Pre-existing vocab with rec state=REVIEW, last_review today → no grade."""

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC)
        rec.reps = 5
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 5  # unchanged

    async def test_listen_creates_no_morphology_clozes_after_phase_4b(self):
        """Phase 4b: /listen on a lesson with inflected A1 surfaces creates zero morphology clozes."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(
                            text="Grem v Ljubljano. Sem v hotelu.",
                            voice_id="female-1",
                            language_code="sl",
                            role="female-1",
                        ),
                    ],
                )
            ],
            key_phrases=[],
            generation_metadata={
                "token_glosses": {},
                "sentence_translations": {
                    "Grem v Ljubljano. Sem v hotelu.": "I'm going to Ljubljana. I'm at the hotel."
                },
                "morphology_focus": [
                    {"lemma": "ljubljana", "surface": "Ljubljano", "feature": "noun:acc:sg", "gloss": "Ljubljana"},
                ],
            },
        )
        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("phase-4b-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "phase-4b-1"})
        assert response.status_code == 200

        with db._get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS cnt FROM collocations WHERE card_type = 'cloze' AND disambig_key LIKE 'morph:%'",
            ).fetchall()
        assert rows[0]["cnt"] == 0, "Phase 4b: /listen should create zero morphology clozes"

    async def test_listen_never_grades_production(self):
        """Pre-existing vocab with production state=LEARNING → /listen does not touch production."""

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        if Direction.PRODUCTION in banka.directions:
            prod = banka.directions[Direction.PRODUCTION]
            prod.state = SRSState.LEARNING
            prod.reps = 3
            db.update_collocation(banka)  # saves RECOGNITION, not production

            # Directly update production in DB
            db.update_direction(banka.guid, Direction.PRODUCTION, prod)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        prod = banka.directions.get(Direction.PRODUCTION)
        if prod:
            assert prod.reps == 3  # unchanged
            assert prod.state == SRSState.LEARNING  # unchanged

    async def test_listen_never_grades_cloze(self):
        """Pre-existing cloze row → /listen never grades it."""

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        kje = db.get_collocation_by_lemma("kje")
        assert kje is not None
        assert kje.syntactic_unit.card_type == "cloze"
        prod = kje.directions[Direction.PRODUCTION]
        prod.state = SRSState.LEARNING
        prod.reps = 1
        db.update_direction(kje.guid, Direction.PRODUCTION, prod)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        kje = db.get_collocation_by_lemma("kje")
        prod = kje.directions[Direction.PRODUCTION]
        assert prod.reps == 1  # unchanged
        assert prod.state == SRSState.LEARNING  # unchanged

    async def test_listen_skips_existing_new_state(self):
        """Pre-existing vocab, recognition state=NEW → no grade."""

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        # Already state=NEW after creation
        rec_reps_before = banka.directions[Direction.RECOGNITION].reps

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == rec_reps_before

    async def test_listen_skips_cloze_and_non_slovene_content_still_created(self):
        """Non-Slovene lesson: content words created as vocab (no en function-word list)."""

        db = await self._setup_lesson(
            phrase_text="Where is the bank?",
            language_code="en",
        )
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # English words are all "content" (no function-word list for en) → created as vocab
        for lemma in ("where", "is", "the", "bank"):
            item = db.get_collocation_by_lemma(lemma)
            assert item is not None, f"{lemma} should be created as vocab"
            assert item.syntactic_unit.card_type == "vocab"
        # Cloze card type should not appear
        assert not any(
            db.get_collocation_by_lemma(lemma).syntactic_unit.card_type == "cloze"
            for lemma in ("where", "is", "the", "bank")
        )

    async def test_listen_cloze_returns_card_type_and_source_sentence_via_api(self):
        """Cloze items expose card_type and source_sentence via the items API."""
        await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

            response = await client.get("/api/srs/items", params={"limit": 50})
        assert response.status_code == 200
        items = {i["text"]: i for i in response.json()["items"]}

        kje = items.get("kje")
        assert kje is not None
        assert kje["card_type"] == "cloze"
        assert kje["source_sentence"] == "{{c1::Kje}} je banka?"

        banka = items.get("banka")
        assert banka is not None
        assert banka["card_type"] == "vocab"

    async def test_listen_cloze_response_state_reflects_production_direction(self):
        """`_item_to_dict` for cloze items reads state from PRODUCTION."""

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

            item = db.get_collocation_by_lemma("kje")
            assert item is not None
            item.directions[Direction.PRODUCTION].state = SRSState.LEARNING
            item.directions[Direction.PRODUCTION].reps = 2
            db.update_direction(item.guid, Direction.PRODUCTION, item.directions[Direction.PRODUCTION])

            response = await client.get("/api/srs/items", params={"limit": 50})
        assert response.status_code == 200
        items = {i["text"]: i for i in response.json()["items"]}
        kje = items["kje"]
        assert kje["state"] == "learning"
        assert kje["reps"] == 2

    async def test_listen_with_flag_on_still_registers_key_phrases(self):
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        assert lesson is not None
        lesson.key_phrases = [
            KeyPhraseInfo(phrase="dober dan", translation="good day"),
            KeyPhraseInfo(phrase="hvala lepa", translation="thank you very much"),
        ]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        kp1 = db.get_collocation("dober dan")
        assert kp1 is not None
        kp2 = db.get_collocation("hvala lepa")
        assert kp2 is not None

        assert db.get_collocation_by_lemma("kje") is not None
        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        assert banka.syntactic_unit.card_type == "vocab"

    # ── Key-phrase auto-grade tests ──────────────────────────────────────

    async def test_listen_grades_key_phrase_when_recognition_learning(self):
        """Pre-existing KP with rec state=LEARNING → reps incremented, production untouched."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.LEARNING
        rec.reps = 1
        db.update_collocation(item)

        prod_before = item.directions.get(Direction.PRODUCTION)
        prod_reps_before = prod_before.reps if prod_before else 0

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        rec = item.directions[Direction.RECOGNITION]
        assert rec.reps == 2  # graded
        assert rec.state != SRSState.NEW

        prod = item.directions.get(Direction.PRODUCTION)
        if prod:
            assert prod.reps == prod_reps_before

    async def test_listen_grades_key_phrase_when_review_first_time_today(self):
        """Pre-existing KP with rec state=REVIEW, last_review 2 days ago → graded (reps+1)."""
        from datetime import timedelta

        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(days=2)
        rec.reps = 5
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.directions[Direction.RECOGNITION].reps == 6

    async def test_listen_skips_key_phrase_when_review_already_today(self):
        """Pre-existing KP with rec state=REVIEW, last_review today → no grade."""

        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC)
        rec.reps = 5
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.directions[Direction.RECOGNITION].reps == 5  # unchanged

    async def test_listen_skips_key_phrase_when_recognition_new(self):
        """Pre-existing KP with rec state=NEW → no grade."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec_reps_before = item.directions[Direction.RECOGNITION].reps

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.directions[Direction.RECOGNITION].reps == rec_reps_before  # unchanged

    async def test_listen_skips_key_phrase_when_recognition_known(self):
        """Pre-existing KP with rec state=KNOWN → no grade."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.KNOWN
        rec.reps = 3
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.directions[Direction.RECOGNITION].reps == 3  # unchanged

    async def test_listen_creates_cloze_with_sentence_translation_from_metadata(self):
        """New cloze uses sentence_translations from lesson.generation_metadata."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.generation_metadata = {"sentence_translations": {"Kje je banka?": "Where is the bank?"}}
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.source_sentence_translation == "Where is the bank?"

    async def test_listen_creates_cloze_with_sentence_translation_from_translated_section(self):
        """When metadata is missing, /listen falls back to deriving from TRANSLATED section."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        # No sentence_translations in metadata; emulate pre-Layer-N stored lesson
        lesson.generation_metadata = {}
        lesson.sections.append(
            Section(
                section_type=SectionType.TRANSLATED,
                phrases=[
                    Phrase(text="Kje je banka?", voice_id="v", language_code="sl"),
                    Phrase(text="Where is the bank?", voice_id="v", language_code="en"),
                ],
            )
        )
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.source_sentence_translation == "Where is the bank?"

    async def test_listen_backfills_existing_cloze_sentence_translation(self):
        """Existing cloze with empty sentence_translation gets populated and marked dirty."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje is not None
        assert item_kje.syntactic_unit.source_sentence_translation == ""

        # Simulate "card already in Anki" so sync_push will see it: stamp anki_note_id.
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET anki_note_id = 9999 WHERE guid = ?", (item_kje.guid,))
            conn.commit()

        # Re-load lesson with TRANSLATED section so /listen has source data
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.generation_metadata = {"sentence_translations": {"Kje je banka?": "Where is the bank?"}}
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item_kje = db.get_collocation_by_lemma("kje")
        assert item_kje.syntactic_unit.source_sentence_translation == "Where is the bank?"

        dirty = db.get_dirty_fields(item_kje.guid)
        assert "sentence_translation" in dirty.split(",")

    async def test_listen_skips_key_phrase_when_cloze(self):
        """Key phrase existing as cloze (defensive) → skip, no crash."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        # Manually change card_type to cloze
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET card_type = 'cloze' WHERE text = 'dober dan'")
            conn.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation("dober dan")
        assert item.syntactic_unit.card_type == "cloze"

    async def test_listen_skips_key_phrase_without_recognition_direction(self):
        """Key phrase existing without RECOGNITION direction (defensive) → skip, no crash."""
        from app.storage.store import ContentStore

        db = await self._setup_lesson()
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.key_phrases = [KeyPhraseInfo(phrase="dober dan", translation="good day")]
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        # Delete recognition direction
        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE collocation_id = (SELECT id FROM collocations WHERE text = 'dober dan') AND direction = 'recognition'"
            )
            conn.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

    async def test_listen_skips_existing_cloze_with_populated_sentence_translation(self):
        """Existing cloze already has sentence_translation → no re-backfill, dirty_fields unchanged."""
        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        # Manually set sentence_translation to simulate already-backfilled state
        with db._get_conn() as conn:
            conn.execute("UPDATE collocations SET sentence_translation = 'already set' WHERE text = 'kje'")
            conn.commit()
        # Clear dirty fields to verify they aren't re-marked
        item = db.get_collocation_by_lemma("kje")
        db.set_dirty_fields(item.guid, "")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        item = db.get_collocation_by_lemma("kje")
        assert item.syntactic_unit.source_sentence_translation == "already set"
        dirty = db.get_dirty_fields(item.guid)
        assert dirty == ""  # not re-marked

    # ── Edge cases for _listen_grade_eligible (lines 228, 233, 236, 238) ──

    async def test_listen_skips_known_state(self):
        """Pre-existing vocab, recognition state=KNOWN → skip (no grade)."""

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.KNOWN
        rec.reps = 3
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 3

    async def test_listen_grades_review_with_no_last_review(self):
        """REVIEW state with last_review=None → eligible for grade (line 233)."""

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = None
        rec.reps = 5
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 6

    async def test_listen_grades_review_with_suspended_state(self):
        """SUSPENDED state → skip (not eligible for grade), covers fallthrough at line 238."""

        db = await self._setup_lesson()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.SUSPENDED
        rec.reps = 3
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        banka = db.get_collocation_by_lemma("banka")
        assert banka.directions[Direction.RECOGNITION].reps == 3

    async def test_listen_skips_vocab_without_recognition_direction(self):
        """Line 314 via /listen: vocab card without RECOGNITION direction → skip (no crash)."""

        db = await self._setup_lesson(phrase_text="testword")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE collocation_id = (SELECT id FROM collocations WHERE lemma = 'testword') AND direction = 'recognition'",
            )
            conn.commit()

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

    async def test_listen_existing_cloze_audio_uses_raw_sentence(self, monkeypatch):
        """Existing cloze audio backfill reads the raw sentence, not the pre-clozed
        source_sentence (which contains {{c1::…}} markup under Phase-2b)."""
        from datetime import date

        import app.api.srs as srs_mod
        from app.models.srs_item import DirectionState
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        # Seed a cloze row with pre-clozed source_sentence (simulating Phase-2b
        # storage) and no sentence audio.
        cloze_unit = SyntacticUnit(
            text="je",
            translation="is",
            word_count=1,
            difficulty=1,
            source="llm",
            lemma="je",
            card_type="cloze",
            source_sentence="Kje {{c1::je}} banka?",
        )
        cloze_dir = {
            Direction.PRODUCTION: DirectionState(
                Direction.PRODUCTION,
                date.today(),
                state=SRSState.NEW,
            )
        }
        db.upsert_by_guid(cloze_unit, "sl", cloze_dir)

        audio_mock = AsyncMock()
        monkeypatch.setattr(srs_mod, "synthesize_cloze_audios", audio_mock)

        store = ContentStore(":memory:")
        app.state.content_store = store
        app.state.language = None
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1"),
                    ],
                )
            ],
            key_phrases=[],
        )
        store.save_lesson("lesson-a", "curriculum-1", 1, lesson)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-a"})
        assert response.status_code == 200

        assert len(audio_mock.await_args_list) > 0
        for call in audio_mock.await_args_list:
            sent = call.args[2]
            assert isinstance(sent, str), f"sentence arg is not str: {sent!r}"
            assert "{{c1" not in sent, f"sentence arg contains cloze markup: {sent!r}"
            assert sent == "Kje je banka?"


class TestListenGradeEligible:
    """Direct unit tests for _listen_grade_eligible edge cases."""

    def test_rec_is_none_returns_false(self):

        from app.api.srs import _listen_grade_eligible

        assert _listen_grade_eligible(None, datetime.now(UTC), datetime.now(UTC)) is False

    def test_legacy_date_last_review_returns_true(self):
        from datetime import date, timedelta

        from app.api.srs import _listen_grade_eligible
        from app.models.srs_item import DirectionState

        rec = DirectionState(
            direction=Direction.RECOGNITION,
            due_at=datetime.combine(date.today(), time(4, 0), tzinfo=UTC),
            state=SRSState.REVIEW,
            reps=5,
            last_review=date(2026, 5, 1),  # legacy date, not datetime
        )
        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        assert _listen_grade_eligible(rec, today_start, today_end) is True

    def test_non_cloze_without_recognition_skipped(self):
        """Line 314: existing item is vocab but has no RECOGNITION direction → skip (no crash)."""

        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        unit = SyntacticUnit(
            text="testword",
            translation="",
            word_count=1,
            difficulty=1,
            source="test",
            lemma="testword",
            card_type="vocab",
        )
        db.add_collocation(unit, language_code="sl")
        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE collocation_id = (SELECT id FROM collocations WHERE lemma = 'testword') AND direction = 'recognition'"
            )
            conn.commit()

        item = db.get_collocation_by_lemma("testword")
        assert item is not None
        assert Direction.RECOGNITION not in item.directions
        assert item.syntactic_unit.card_type == "vocab"

        from datetime import timedelta

        from app.api.srs import _listen_grade_eligible

        today_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        rec = item.directions.get(Direction.RECOGNITION)
        assert _listen_grade_eligible(rec, today_start, today_end) is False


class TestListenWindowUsesAnkiRollover:
    """Regression (docs/master-cleanup-list-2026-07.md item 1): mark_lesson_listened's
    grade-eligibility window must anchor on Anki's 4 AM local rollover, not local
    midnight. A card graded late the previous evening is still within the SAME
    active Anki day when `now` sits in [midnight, 4 AM) local — Anki's rollover
    hasn't happened yet — so it must still block a same-Anki-day regrade.

    The old local-midnight window (`date.today()` + `combine(time(0))`) would
    wrongly treat that late-evening grade as "not today" once local midnight
    passed, re-eligible-izing a card Anki still considers freshly graded.
    """

    def test_late_evening_grade_still_blocks_regrade_before_rollover(self):
        from datetime import timedelta

        from app.api.srs import _listen_grade_eligible
        from app.models.srs_item import DirectionState
        from app.srs.anki_mirror.rollover import anki_day_bounds_utc_dt, anki_today

        # "now" = 02:00 on day D — inside [midnight, 4 AM), before rollover.
        # UTC stands in for "local" here (matching rollover.py's own test idiom
        # of treating an aware tzinfo as the local zone under test).
        now = datetime(2026, 5, 8, 2, 0, tzinfo=UTC)
        # last_review = 23:00 the PRIOR evening — same active Anki day as `now`
        # (Anki day D-1 spans [4 AM D-1, 4 AM D), and 23:00 D-1 falls inside it).
        last_review = datetime(2026, 5, 7, 23, 0, tzinfo=UTC)

        today = anki_today(now)
        today_start, today_end = anki_day_bounds_utc_dt(today, now)

        rec = DirectionState(
            direction=Direction.RECOGNITION,
            state=SRSState.REVIEW,
            due_at=today_start,
            reps=5,
            last_review=last_review,
        )
        assert _listen_grade_eligible(rec, today_start, today_end) is False, (
            "a card graded late the prior evening is still 'today' by Anki's "
            "4 AM rollover and must not be regraded before rollover"
        )

        # Sanity check: the OLD local-midnight window would have excluded
        # last_review (23:00 the calendar day before `now`'s calendar day) and
        # wrongly marked the card eligible — confirms this scenario actually
        # distinguishes the two conventions, so a revert would flip the
        # assertion above.
        old_today_start = datetime(2026, 5, 8, 0, 0, tzinfo=UTC)
        old_today_end = old_today_start + timedelta(days=1)
        assert _listen_grade_eligible(rec, old_today_start, old_today_end) is True
