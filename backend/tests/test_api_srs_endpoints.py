"""Tests for SRS endpoints."""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import SRSState
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


class TestSRSEndpoints:
    """Tests for SRS due/stats/feedback/new endpoints."""

    async def test_srs_due_returns_200(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/due")

        assert response.status_code == 200
        data = response.json()
        assert "due" in data
        assert isinstance(data["due"], list)

    async def test_srs_stats_returns_200(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/stats")

        assert response.status_code == 200
        data = response.json()
        assert "total" in data

    async def test_srs_new_returns_200(self):
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        db.add_collocation(
            SyntacticUnit(text="dober dan", translation="good day", word_count=2, difficulty=1, source="llm")
        )
        db.add_collocation(
            SyntacticUnit(text="prosim kavo", translation="a coffee please", word_count=2, difficulty=1, source="llm")
        )
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/new")

        assert response.status_code == 200
        data = response.json()
        assert "new" in data
        assert len(data["new"]) == 2
        assert all("text" in item and "translation" in item for item in data["new"])

    async def test_srs_new_returns_empty_when_no_new_cards(self):
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/new")

        assert response.json()["new"] == []

    async def test_srs_new_respects_limit(self):
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase

        db = SRSDatabase(":memory:")
        for i in range(15):
            db.add_collocation(
                SyntacticUnit(text=f"phrase {i}", translation=f"trans {i}", word_count=2, difficulty=1, source="llm")
            )
        app.state.srs_db = db

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/srs/new")

        assert len(response.json()["new"]) == 10

    async def test_listen_skips_phrases_with_wrong_language_code(self):
        """NATURAL_SPEED phrases whose language_code != lesson language_code are skipped."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        # Lesson is Slovene but one NATURAL_SPEED phrase is English (narrator)
        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="At the café", voice_id="narrator", language_code="en", role="narrator"),
                    ],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-foreign", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-foreign"})

        assert response.status_code == 200
        # The English phrase should contribute 0 lemmas (registered = 0 lemmas + 0 key phrases)
        assert db.count_collocations() == 0

    async def test_listen_same_lemma_in_multiple_phrases_dedup_sentence(self):
        """When the same lemma appears in multiple NATURAL_SPEED phrases, only
        the first sentence is stored as source_sentence."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text="Kje je banka?", voice_id="female-1", language_code="sl", role="female-1"),
                        Phrase(text="Kje je center?", voice_id="female-1", language_code="sl", role="female-1"),
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

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        # kje and je appear in both phrases, but only first sentence stored
        kje = db.get_collocation_by_lemma("kje")
        assert kje is not None
        assert kje.syntactic_unit.source_sentence == "{{c1::Kje}} je banka?"

    async def test_listen_registers_collocations(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
                )
            ],
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="prosim kavo", translation="a coffee please"),
            ],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["registered"] == 2
        assert db.count_collocations() == 2
        assert db.get_collocation("dober dan") is not None
        assert db.get_collocation("prosim kavo") is not None

    async def test_listen_creates_collocations_with_source_llm_and_no_anki_link(self):
        """Layer 34 spec: /listen creates TT collocations with source='llm' and
        anki_note_id=NULL — guaranteeing the next sync_create_new picks them up
        and pushes them as proper Slovene Vocabulary notes (or links via guid if
        Anki already has them)."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )
        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert response.status_code == 200

        # Inspect the persisted collocation
        with db._get_conn() as conn:
            row = conn.execute("SELECT text, source, anki_note_id FROM collocations WHERE text='dober dan'").fetchone()
        assert row is not None
        assert row["source"] == "llm"
        assert row["anki_note_id"] is None

    async def test_listen_is_idempotent(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.KEY_PHRASES,
                    phrases=[Phrase(text="dober dan", voice_id="sl-SI-PetraNeural", language_code="sl")],
                )
            ],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        assert db.count_collocations() == 1

    async def test_listen_returns_404_for_missing_lesson(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        app.state.srs_db = SRSDatabase(":memory:")
        app.state.content_store = ContentStore(":memory:")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "nonexistent"})

        assert response.status_code == 404

    async def test_listen_registers_all_l2_words(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

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

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        # 2 cloze (kje, je) + 1 vocab (banka)
        assert data["registered"] == 3
        assert db.get_collocation_by_lemma("kje") is not None
        assert db.get_collocation_by_lemma("je") is not None
        banka = db.get_collocation_by_lemma("banka")
        assert banka is not None
        assert banka.syntactic_unit.card_type == "vocab"

    async def test_listen_surface_keyed_card_graded_not_duplicated(self, monkeypatch):
        """A token whose lemma has no card but whose surface matches an existing
        card (greeting 'dobrodošli' → lemma 'dobrodošel') grades the existing card
        instead of spawning a 'dobrodošel' duplicate."""
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase
        from app.srs.lemmatizer import TokenAnalysis
        from app.storage.store import ContentStore
        from tests._helpers.lemmatizer import StubLemmatizer

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="Dobrodošli", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )
        db = SRSDatabase(":memory:")
        db.add_collocation(
            SyntacticUnit(
                text="dobrodošli",
                translation="Welcome.",
                word_count=1,
                difficulty=1,
                source="llm",
                lemma="dobrodošli",
            ),
            language_code="sl",
        )
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        import app.api.srs as srs_mod

        stub = StubLemmatizer()
        stub.set_sentence("Dobrodošli", [TokenAnalysis(surface="Dobrodošli", lemma="dobrodošel")])
        # Inject the stub as the per-language engine (Slovene lesson → get_lemmatizer("sl")).
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        assert db.get_collocation_by_lemma("dobrodošel") is None
        assert db.get_collocation_by_lemma("dobrodošli") is not None

    async def test_listen_key_phrases_translation_preserved(self):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        assert item.syntactic_unit.translation == "good day"

    async def test_listen_lemma_auto_filled_on_single_word_collocation(self):
        """Single-word collocations now auto-fill lemma = casefolded text,
        so get_collocation_by_lemma finds the pre-existing row and no duplicate is created."""
        from app.models.syntactic_unit import SyntacticUnit
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[Phrase(text="banka", voice_id="female-1", language_code="sl", role="female-1")],
                )
            ],
            key_phrases=[],
        )

        db = SRSDatabase(":memory:")
        # Pre-insert with text="banka" — lemma is auto-filled by add_collocation
        db.add_collocation(SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="corpus"))
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert response.status_code == 200
        assert db.count_collocations() == 1
        # Lemma was auto-filled, so the pre-existing row is found
        assert db.get_collocation_by_lemma("banka") is not None

    async def test_listen_auto_added_cards_have_null_anki_note_id(self):
        """Auto-added key_phrase collocations have anki_note_id IS NULL and appear in list_items_without_anki_note."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="prosim kavo", translation="a coffee please"),
            ],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        items_without_anki = db.list_items_without_anki_note()
        assert len(items_without_anki) == 2
        for _guid, item, _coll_id in items_without_anki:
            assert item.anki_note_id is None

    async def test_listen_key_phrases_state_is_new_on_first_listen(self):
        """Key phrases get state=NEW on first listen — no FSRS grade (soft exposure only)."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        item = db.get_collocation("dober dan")
        assert item is not None
        assert item.state == SRSState.NEW
        assert item.reps == 0

    async def test_listen_key_phrases_do_not_duplicate_on_relisten(self):
        """Second listen with same key_phrases does not create duplicate collocations."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[
                KeyPhraseInfo(phrase="dober dan", translation="good day"),
                KeyPhraseInfo(phrase="prosim kavo", translation="a coffee please"),
            ],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            first = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
            second = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})

        assert first.status_code == 200
        assert second.status_code == 200
        assert db.count_collocations() == 2

    async def test_listen_empty_word_ratings_does_not_crash(self):
        """Explicitly verify that word_ratings={} (as sent by the frontend) works."""
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="sl",
            sections=[],
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/api/srs/listen",
                json={"lesson_id": "lesson-1", "word_ratings": {}},
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["registered"] == 1


class TestResolveGlossTranslation:
    """Gloss lookup for /listen card creation: lemma → surface → warn (no silent '')."""

    def test_lemma_hit(self):
        from app.api.srs import _resolve_gloss_translation

        got = _resolve_gloss_translation("banka", {"banka": "bank"}, {"banka"}, "banka", language_code="sl")
        assert got == "bank"

    def test_surface_fallback_when_lemma_missing(self):
        """Stanza lemmatizes 'snøen' → 'snø' but the LLM glossed the surface 'snøen';
        the card is keyed by lemma, so fall back to the surface form."""
        from app.api.srs import _resolve_gloss_translation

        got = _resolve_gloss_translation("snø", {"snøen": "the snow"}, {"snøen"}, "snøen", language_code="no")
        assert got == "the snow"

    def test_first_surface_preferred_over_other_surfaces(self):
        from app.api.srs import _resolve_gloss_translation

        got = _resolve_gloss_translation(
            "x",
            {"first": "A", "second": "B"},
            {"first", "second"},
            "first",
            language_code="no",
        )
        assert got == "A"

    def test_miss_logs_warning_and_returns_empty(self, caplog):
        """The 'går' bug: lemma 'går' and its only surface 'går' are absent from the
        gloss map (LLM keyed 'gå' + 'i går'). No silent '' — emit a warning."""
        from app.api.srs import _resolve_gloss_translation

        with caplog.at_level("WARNING"):
            got = _resolve_gloss_translation(
                "går", {"gå": "go / walk", "i går": "yesterday"}, {"går"}, "går", language_code="no"
            )
        assert got == ""
        assert any("går" in r.message and "empty translation" in r.message for r in caplog.records)
