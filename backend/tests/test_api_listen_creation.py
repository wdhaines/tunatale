"""Tests for listen creation endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401


class TestRankListenCandidates:
    """_rank_listen_candidates (plan D2): untracked key phrases first (lesson
    order), then untracked lemmas by in-lesson occurrence count descending,
    ties broken by first appearance (the order of the input list)."""

    def test_key_phrases_before_lemmas(self):
        from app.api.srs import _rank_listen_candidates

        ranked = _rank_listen_candidates(["kp1", "kp2"], ["a", "b"], {"a": 5, "b": 1})
        assert ranked == [("kp", "kp1"), ("kp", "kp2"), ("lemma", "a"), ("lemma", "b")]

    def test_lemmas_sorted_by_occurrence_desc(self):
        from app.api.srs import _rank_listen_candidates

        ranked = _rank_listen_candidates([], ["a", "b", "c"], {"a": 1, "b": 3, "c": 2})
        assert ranked == [("lemma", "b"), ("lemma", "c"), ("lemma", "a")]

    def test_ties_keep_first_appearance_order(self):
        from app.api.srs import _rank_listen_candidates

        ranked = _rank_listen_candidates([], ["x", "y", "z"], {"x": 2, "y": 2, "z": 2})
        assert ranked == [("lemma", "x"), ("lemma", "y"), ("lemma", "z")]

    def test_empty_inputs(self):
        from app.api.srs import _rank_listen_candidates

        assert _rank_listen_candidates([], [], {}) == []


class TestListenStagedCreation:
    """Staged, budget-capped creation in POST /api/srs/listen (plan Step 3).

    Budget = max(0, daily_new_cap − introduced_today − created_today_still_new);
    candidates ranked by _rank_listen_candidates. Grading and backfills of
    existing cards stay unconditional (plan D5)."""

    def _lesson(self, phrases, key_phrases=None, language_code="sl"):
        return Lesson(
            title="Day 1",
            language_code=language_code,
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text=t, voice_id="female-1", language_code=language_code, role="female-1")
                        for t in phrases
                    ],
                )
            ],
            key_phrases=key_phrases or [],
        )

    def _setup(self, lesson):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store
        return db

    async def _listen(self, **ratings):
        # `ratings` carries word_ratings / kp_ratings through. Needed since F-5:
        # known and learning rows are deferred unless explicitly rated, so a
        # test whose subject happens to be a learning card must opt it in.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1", **ratings})
        assert resp.status_code == 200
        return resp.json()

    async def test_budget_caps_creation_to_highest_frequency_lemmas(self):
        # Ranking is corpus frequency (wordfreq zipf), not in-lesson occurrence
        # count — updated 2026-08-03 with bp-frequency-ranking-2026-07. The cap
        # behaviour under test is unchanged; only which 2 of the 3 lemmas it
        # admits moved.
        #   occurrences: banka 3, center 2, hotel 1
        #   zipf(sl):    center 5.21, hotel 4.98, banka 4.39
        # So the budget of 2 now takes center + hotel and leaves banka.
        db = self._setup(self._lesson(["banka center hotel", "banka center", "banka"]))
        db.set_anki_state_cache("daily_new_cap", "2")

        data = await self._listen()

        assert set(data) == {
            "status",
            "staged",
            "applied",
            "created",
            "remaining_candidates",
            "listen_count",
        }
        assert data["status"] == "ok"
        assert data["created"] == 2
        assert data["staged"] == 0
        assert data["remaining_candidates"] == 1
        assert data["listen_count"] == 1
        assert db.get_collocation_by_lemma("center") is not None
        assert db.get_collocation_by_lemma("hotel") is not None
        assert db.get_collocation_by_lemma("banka") is None

    async def test_listen_no_longer_creates_key_phrase_cards(self):
        """Untracked key phrases are skipped; only lemmas are created."""
        db = self._setup(
            self._lesson(
                ["banka banka banka"],
                key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
            )
        )
        db.set_anki_state_cache("daily_new_cap", "10")

        data = await self._listen()

        assert db.get_collocation("dober dan") is None
        assert data["created"] == 1
        assert db.get_collocation_by_lemma("banka") is not None

    async def test_remaining_candidates_counts_only_untracked_lemmas(self):
        """remaining_candidates excludes untracked key phrases."""
        db = self._setup(
            self._lesson(
                ["banka banka banka"],
                key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
            )
        )
        db.set_anki_state_cache("daily_new_cap", "10")

        data = await self._listen()

        assert data["remaining_candidates"] == 0

    async def test_already_tracked_key_phrase_still_staged_when_budget_zero(self):
        """An ALREADY-tracked key-phrase card is still staged by a listen (no grade)."""
        from app.models.syntactic_unit import SyntacticUnit

        db = self._setup(
            self._lesson(
                [],
                key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
            )
        )
        unit = SyntacticUnit(
            text="dober dan",
            translation="good day",
            word_count=2,
            difficulty=1,
            source="test",
        )
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("dober dan")
        assert item is not None
        rec = item.directions.get(Direction.RECOGNITION)
        assert rec is not None
        rec.state = SRSState.LEARNING
        rec.reps = 1
        db.update_collocation(item)

        db.set_anki_state_cache("daily_new_cap", "0")

        # Opted in explicitly (F-5): the subject here is that a zero CREATION
        # budget does not suppress staging of an already-tracked card, not the
        # default rating of a learning row.
        data = await self._listen(kp_ratings={"dober dan": "good"})

        assert data["created"] == 0
        assert data["staged"] == 1
        item = db.get_collocation("dober dan")
        rec = item.directions[Direction.RECOGNITION]
        assert rec.reps == 1  # staging does not grade

    async def test_same_day_relisten_creates_zero(self):
        db = self._setup(self._lesson(["banka center hotel kava mesto"]))
        db.set_anki_state_cache("daily_new_cap", "2")

        first = await self._listen()
        assert first["created"] == 2
        assert first["listen_count"] == 1

        second = await self._listen()
        assert second["created"] == 0
        assert second["listen_count"] == 2
        assert second["remaining_candidates"] == 3
        assert db.count_collocations() == 2

    async def test_introduced_today_netting_shrinks_budget(self):
        from app.models.syntactic_unit import SyntacticUnit

        db = self._setup(self._lesson(["banka center hotel kava mesto"]))
        db.set_anki_state_cache("daily_new_cap", "3")
        # One collocation introduced today (left NEW, introduced_at stamped):
        # charges the budget via introduced_today, not created_today_still_new.
        db.add_collocation(
            SyntacticUnit(text="stara", translation="old", word_count=1, difficulty=1, source="llm"),
            language_code="sl",
        )
        now_iso = datetime.now(UTC).isoformat()
        with db._get_conn() as conn:
            conn.execute(
                "UPDATE collocation_directions SET state='learning', introduced_at=?"
                " WHERE collocation_id=(SELECT id FROM collocations WHERE text='stara')",
                (now_iso,),
            )
            conn.commit()

        data = await self._listen()

        assert data["created"] == 2

    async def test_fully_acquired_lesson_creates_and_remains_zero(self):
        db = self._setup(self._lesson(["banka center"]))
        db.set_anki_state_cache("daily_new_cap", "10")

        first = await self._listen()
        assert first["created"] == 2

        second = await self._listen()
        assert second["created"] == 0
        assert second["remaining_candidates"] == 0
        assert second["listen_count"] == 2

    async def test_backfills_still_run_at_budget_zero(self):
        from app.storage.store import ContentStore

        db = self._setup(self._lesson(["Kje je banka?"]))
        db.set_anki_state_cache("daily_new_cap", "10")
        await self._listen()
        kje = db.get_collocation_by_lemma("kje")
        assert kje is not None
        assert kje.syntactic_unit.source_sentence_translation == ""

        db.set_anki_state_cache("daily_new_cap", "0")
        store: ContentStore = app.state.content_store
        lesson = store.get_lesson("lesson-1")
        lesson.generation_metadata = {"sentence_translations": {"Kje je banka?": "Where is the bank?"}}
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)

        data = await self._listen()

        assert data["created"] == 0
        kje = db.get_collocation_by_lemma("kje")
        assert kje.syntactic_unit.source_sentence_translation == "Where is the bank?"

    async def test_every_listen_appends_source_listen_row(self):
        db = self._setup(self._lesson(["banka"]))

        await self._listen()
        await self._listen()

        assert db.count_listens("lesson-1") == 2
        with db._get_conn() as conn:
            rows = conn.execute("SELECT source FROM lesson_listens WHERE lesson_id='lesson-1'").fetchall()
        assert [r["source"] for r in rows] == ["listen", "listen"]

    async def test_404_records_no_listen_row(self):
        db = self._setup(self._lesson(["banka"]))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": "no-such-lesson"})

        assert resp.status_code == 404
        assert db.count_listens("no-such-lesson") == 0

    async def test_single_word_key_phrase_shared_with_lemma_not_double_created(self):
        db = self._setup(self._lesson(["banka"], key_phrases=[KeyPhraseInfo(phrase="banka", translation="bank")]))
        db.set_anki_state_cache("daily_new_cap", "10")

        data = await self._listen()

        assert data["created"] == 1
        assert db.count_collocations() == 1

    async def test_duplicate_key_phrase_not_double_created(self):
        db = self._setup(
            self._lesson(
                ["banka"],
                key_phrases=[
                    KeyPhraseInfo(phrase="dober dan", translation="good day"),
                    KeyPhraseInfo(phrase="dober dan", translation="good day"),
                ],
            )
        )
        db.set_anki_state_cache("daily_new_cap", "10")

        data = await self._listen()

        assert data["created"] == 1
        assert db.count_collocations() == 1

    async def test_norwegian_verb_lemma_gets_infinitive_marker(self, monkeypatch):
        """A Norwegian VERB creation candidate fronts as "å " + lemma."""
        import app.api.srs as srs_mod
        from app.srs.lemmatizer import TokenAnalysis
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_sentence(
            "Jeg lyver",
            [
                TokenAnalysis(surface="Jeg", lemma="jeg", upos="PRON"),
                TokenAnalysis(surface="lyver", lemma="lyve", upos="VERB"),
            ],
        )
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)

        db = self._setup(self._lesson(["Jeg lyver"], language_code="no"))
        db.set_anki_state_cache("daily_new_cap", "10")

        data = await self._listen()

        assert data["created"] == 2
        coll = db.get_collocation_by_lemma("lyve")
        assert coll is not None
        assert coll.syntactic_unit.text == "å lyve"
        assert coll.syntactic_unit.card_type == "vocab"

    async def test_norwegian_verb_media_fetch_uses_bare_lemma(self, monkeypatch):
        """The Forvo/Pixabay fetch in the listen path stays keyed on the bare lemma."""
        import app.api.srs as srs_mod
        from app.cards.media import vocab_media
        from app.cards.media.pipeline import MediaResult
        from app.config import settings
        from app.srs.lemmatizer import TokenAnalysis
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_sentence(
            "Jeg lyver",
            [
                TokenAnalysis(surface="Jeg", lemma="jeg", upos="PRON"),
                TokenAnalysis(surface="lyver", lemma="lyve", upos="VERB"),
            ],
        )
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)
        monkeypatch.setattr(settings, "pixabay_api_key", "test-key")
        fetched: list[str] = []

        async def _query(_word, _english, **_kw):
            return "a clear depiction"

        async def _fetch(word, _english, **_kw):
            fetched.append(word)
            return MediaResult(audio_bytes=b"A", audio_source="forvo", image_bytes=b"I", image_ext="jpg")

        monkeypatch.setattr(vocab_media, "generate_image_query", _query)
        monkeypatch.setattr(vocab_media, "fetch_card_media", _fetch)

        db = self._setup(self._lesson(["Jeg lyver"], language_code="no"))
        db.set_anki_state_cache("daily_new_cap", "10")

        data = await self._listen()

        assert data["created"] == 2
        assert fetched == ["lyve"]
        coll = db.get_collocation_by_lemma("lyve")
        assert coll is not None
        assert coll.syntactic_unit.text == "å lyve"

    async def test_norwegian_non_verb_lemma_stays_bare(self, monkeypatch):
        """A Norwegian NOUN creation candidate keeps its bare lemma front."""
        import app.api.srs as srs_mod
        from app.srs.lemmatizer import TokenAnalysis
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_sentence(
            "Kaffe",
            [TokenAnalysis(surface="Kaffe", lemma="kaffe", upos="NOUN")],
        )
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)

        db = self._setup(self._lesson(["Kaffe"], language_code="no"))
        db.set_anki_state_cache("daily_new_cap", "10")

        data = await self._listen()

        assert data["created"] == 1
        coll = db.get_collocation_by_lemma("kaffe")
        assert coll is not None
        assert coll.syntactic_unit.text == "kaffe"


class TestListenReviewCap:
    """Staging: /listen stages all eligible cards — no listen-time review budget cap."""

    def _lesson(self, phrases, key_phrases=None, language_code="sl"):
        return Lesson(
            title="Day 1",
            language_code=language_code,
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text=t, voice_id="female-1", language_code=language_code, role="female-1")
                        for t in phrases
                    ],
                )
            ],
            key_phrases=key_phrases or [],
        )

    def _setup(self, lesson):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        db = SRSDatabase(":memory:")
        store = ContentStore(":memory:")
        store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
        app.state.srs_db = db
        app.state.content_store = store
        return db

    async def _listen(self, **ratings):
        # `ratings` carries word_ratings / kp_ratings through. Needed since F-5:
        # known and learning rows are deferred unless explicitly rated, so a
        # test whose subject happens to be a learning card must opt it in.
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1", **ratings})
        assert resp.status_code == 200
        return resp.json()

    async def test_stages_all_eligible_cards_no_budget_cap(self):
        """With daily_review_cap=2 and 4 due-today tracked review cards, all 4 are staged."""
        from datetime import timedelta

        db = self._setup(self._lesson(["banka center hotel kava"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        for text in ("banka", "center", "hotel", "kava"):
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            item = db.get_collocation(text)
            rec = item.directions[Direction.RECOGNITION]
            rec.state = SRSState.REVIEW
            rec.last_review = datetime.now(UTC) - timedelta(days=10)
            rec.due_at = datetime.now(UTC) - timedelta(days=1)
            rec.reps = 5
            db.update_collocation(item)

        db.set_anki_state_cache("daily_review_cap", "2")

        data = await self._listen()

        assert data["staged"] == 4
        for text in ("banka", "center", "hotel", "kava"):
            item = db.get_collocation(text)
            coll_id = db.get_collocation_id_by_guid(item.guid)
            with db._get_conn() as conn:
                rows = conn.execute(
                    "SELECT COUNT(*) FROM tt_revlog WHERE collocation_id = ?",
                    (coll_id,),
                ).fetchall()
            assert rows[0][0] == 0  # no revlog rows
            rec = item.directions[Direction.RECOGNITION]
            assert rec.state == SRSState.REVIEW
            assert rec.reps == 5  # FSRS untouched

    async def test_ahead_cards_stage_with_grade_class_ahead(self):
        """Future-due review cards are staged with grade_class='ahead'."""
        from datetime import timedelta

        db = self._setup(self._lesson(["banka center"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        for text in ("banka", "center"):
            from app.models.syntactic_unit import SyntacticUnit

            unit = SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            item = db.get_collocation(text)
            rec = item.directions[Direction.RECOGNITION]
            rec.state = SRSState.REVIEW
            rec.last_review = datetime.now(UTC) - timedelta(days=10)
            rec.due_at = datetime.now(UTC) + timedelta(days=5)
            rec.reps = 5
            db.update_collocation(item)

        db.set_anki_state_cache("daily_review_cap", "0")

        data = await self._listen()

        assert data["staged"] == 2
        for text in ("banka", "center"):
            item = db.get_collocation(text)
            coll_id = db.get_collocation_id_by_guid(item.guid)
            pg = db.get_pending_grade(coll_id, Direction.RECOGNITION.value)
            assert pg is not None
            assert pg["grade_class"] == "ahead"

    async def test_learning_card_stages_when_budget_zero(self):
        """LEARNING-state cards are staged even when budget is 0.

        The subject is the BUDGET: a learning card is not an introduction, so a
        zero new-card and zero review cap must not suppress it. Since F-5 the
        row is opted in explicitly — the default is now skip, which would make
        this test pass for the wrong reason.
        """
        db = self._setup(self._lesson(["banka"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.LEARNING
        rec.reps = 1
        db.update_collocation(item)

        db.set_anki_state_cache("daily_review_cap", "0")

        data = await self._listen(word_ratings={"banka": "good"})

        assert data["staged"] == 1
        item = db.get_collocation("banka")
        assert item.directions[Direction.RECOGNITION].reps == 1  # staging does not grade

    async def test_same_day_relisten_upserts_provisional_grade(self):
        """Same-day re-listen upserts the pending grade (still staged, not applied)."""
        from datetime import timedelta

        db = self._setup(self._lesson(["banka"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(days=10)
        rec.reps = 5
        db.update_collocation(item)

        db.set_anki_state_cache("daily_review_cap", "100")

        first = await self._listen()
        assert first["staged"] == 1

        second = await self._listen()
        assert second["staged"] == 1  # upsert, still staged

        rows = db.get_pending_grades("lesson-1")
        assert len(rows) == 1  # upserted, not duplicated

    async def _queue_stats(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/queue-stats")
        assert resp.status_code == 200
        return resp.json()

    async def test_ahead_card_staging_leaves_fsrs_and_badges_unchanged(self):
        """Staging an ahead card does not move due_at or charge counters."""
        from datetime import timedelta

        from app.srs.anki_mirror.rollover import anki_today

        db = self._setup(self._lesson(["banka"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        from app.models.syntactic_unit import SyntacticUnit

        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(days=10)
        rec.due_at = datetime.now(UTC) + timedelta(days=5)
        rec.stability = 9.0
        rec.reps = 5
        db.update_collocation(item)
        due_before = rec.due_at

        db.set_anki_state_cache("daily_review_cap", "0")
        badges_before = await self._queue_stats()

        data = await self._listen()

        assert data["staged"] == 1
        rec = db.get_collocation("banka").directions[Direction.RECOGNITION]
        assert rec.due_at == due_before  # FSRS untouched
        assert db.count_reviews_completed_today(anki_today()) == 0
        badges_after = await self._queue_stats()
        for key in ("new", "learning", "review"):
            assert badges_after[key] == badges_before[key]

    async def test_staging_charges_nothing_and_hides_nothing(self):
        """Staging completes no reviews, and no longer empties the review pool.

        Two separate facts, and the distinction is still the whole model — but
        F-14 (2026-08-05) changed the second one. FSRS is untouched
        (``count_reviews_completed_today`` stays 0, no budget *spent*), and the
        staged cards now REMAIN in the due pool and the badge, because a
        provisional grade is not an applied one. This used to assert 0 on both
        counts: staging emptied the pool, and TT's review count sat below Anki's
        by the pending count (Layer 81). That divergence is retired — Anki counts
        all 4 and so does TT.

        The badge still reads 2 rather than 4, and that is the review CAP doing
        its ordinary job (``daily_review_cap`` is 2 here), not the staging.
        """
        from datetime import timedelta

        from app.srs.anki_mirror.rollover import anki_today

        db = self._setup(self._lesson(["banka center hotel kava mesto"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        from app.models.syntactic_unit import SyntacticUnit

        for text, due_delta in (
            ("banka", -1),
            ("center", -1),
            ("hotel", -1),
            ("kava", -1),
            ("mesto", +5),
        ):
            unit = SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            item = db.get_collocation(text)
            rec = item.directions[Direction.RECOGNITION]
            rec.state = SRSState.REVIEW
            rec.last_review = datetime.now(UTC) - timedelta(days=10)
            rec.due_at = datetime.now(UTC) + timedelta(days=due_delta)
            rec.reps = 5
            db.update_collocation(item)

        db.set_anki_state_cache("daily_review_cap", "2")

        previous_budget = (await self._queue_stats())["review"]
        assert previous_budget == 2

        data = await self._listen()
        assert data["staged"] == 5  # all eligible (4 due + 1 ahead)

        graded_due = db.count_reviews_completed_today(anki_today())
        assert graded_due == 0  # nothing applied — no FSRS movement, no budget spent
        # ...and all 4 due cards stay in the review pool (F-14).
        assert db.count_review_due_collocations(anki_today()) == 4
        assert (await self._queue_stats())["review"] == 2, "capped at daily_review_cap, not emptied by staging"

        # Releasing one changes nothing — it was never withheld to begin with.
        released = db.get_collocation_id_by_guid(db.get_collocation("banka").guid)
        db.clear_pending_grade(released, Direction.RECOGNITION.value)
        assert db.count_review_due_collocations(anki_today()) == 4

    async def test_all_staged_cards_surface_in_lesson_review_queue(self):
        """All staged cards (no cap) remain due in FSRS and appear in Check your work."""
        from datetime import timedelta

        db = self._setup(self._lesson(["banka center hotel"]))
        db.set_anki_state_cache("daily_new_cap", "0")

        from app.models.syntactic_unit import SyntacticUnit

        for text in ("banka", "center", "hotel"):
            unit = SyntacticUnit(text=text, translation=text, word_count=1, difficulty=1, source="test")
            db.add_collocation(unit, language_code="sl")
            item = db.get_collocation(text)
            rec = item.directions[Direction.RECOGNITION]
            rec.state = SRSState.REVIEW
            rec.last_review = datetime.now(UTC) - timedelta(days=10)
            rec.due_at = datetime.now(UTC) - timedelta(days=1)
            rec.reps = 5
            db.update_collocation(item)

        db.set_anki_state_cache("daily_review_cap", "1")

        data = await self._listen()
        assert data["staged"] == 3

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/lesson/lesson-1/review-queue")
        assert resp.status_code == 200
        texts = {q["text"] for q in resp.json()["queue"]}
        assert {"banka", "center", "hotel"} <= texts


class TestLemmaPlausibilityBulkPath:
    """End-to-end wiring of the lemma-plausibility guard on the BULK creation
    path (POST /api/srs/listen, source='llm') — the path that produced the
    reported `trø` bug.

    Stanza lemmatizes `trøtt` (adjective, "tired") to `trø` (an unrelated verb,
    "to tread"). The gloss is right for the surface; the HEADWORD would teach
    the wrong word. On rejection the headword falls back to the surface as it
    appeared — never a skipped card.
    """

    def _setup(self, phrase_text):
        from app.srs.database import SRSDatabase
        from app.storage.store import ContentStore

        lesson = Lesson(
            title="Day 1",
            language_code="no",
            sections=[
                Section(
                    section_type=SectionType.NATURAL_SPEED,
                    phrases=[
                        Phrase(text=phrase_text, voice_id="female-1", language_code="no", role="female-1"),
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

    async def _listen(self):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/api/srs/listen", json={"lesson_id": "lesson-1"})
        assert resp.status_code == 200
        return resp.json()

    async def test_creation_falls_back_to_the_surface(self, monkeypatch):
        import app.api.srs as srs_mod
        from app.srs.lemmatizer import TokenAnalysis
        from tests._helpers.lemmatizer import StubLemmatizer

        stub = StubLemmatizer()
        stub.set_sentence(
            "Trøtt",
            [TokenAnalysis(surface="Trøtt", lemma="trø", upos="ADJ", case="", number="", person="", gender="")],
        )
        monkeypatch.setattr(srs_mod, "get_lemmatizer", lambda code: stub)

        db = self._setup("Trøtt")

        data = await self._listen()

        assert data["created"] == 1
        coll = db.get_collocation_by_lemma("trøtt")
        assert coll is not None
        assert coll.syntactic_unit.text == "trøtt"
        assert coll.syntactic_unit.lemma == "trøtt"
        assert coll.syntactic_unit.source == "llm"
        assert db.get_collocation_by_lemma("trø") is None
