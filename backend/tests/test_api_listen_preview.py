"""Guard tests for the listen-preview endpoint (Stage 4, pending-bucket model).

Contract: `GET /api/srs/lesson/{lesson_id}/listen-preview` returns a read-only
classification of what a listen would stage, ordered creations-first then
tracked by mastery ascending. No side effects (the lemma-cache write is
exempt). No budget concept — staging is budget-free.

These tests define the API contract. BP: implement to green. Do NOT edit these
tests — `git diff` on this file must be empty at delivery. Add separate tests
for any extra coverage you need.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.lesson import KeyPhraseInfo, Lesson, Phrase, Section, SectionType
from app.models.srs_item import Direction, SRSState
from app.models.syntactic_unit import SyntacticUnit
from tests._helpers.api_app_state import _clean_app_state  # noqa: F401

PREVIEW_URL = "/api/srs/lesson/lesson-1/listen-preview"
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
        key_phrases=key_phrases or [],
    )
    db = SRSDatabase(":memory:")
    store = ContentStore(":memory:")
    store.save_lesson("lesson-1", "curriculum-1", 1, lesson)
    app.state.srs_db = db
    app.state.content_store = store
    return db


def _seed_review_due(db, text: str, *, days_overdue: int = 1) -> None:
    """Create a tracked vocab card whose recognition is REVIEW and past due.

    Seeds due_at via the day-level 04:00-UTC convention (rollover.py::
    due_at_rollover_utc) — instant-flavored seeds (now - Nh) cross the UTC
    date line past 20:00 local and misread as "ahead".
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


def _rec_reps(db, text: str) -> int:
    return db.get_collocation(text).directions[Direction.RECOGNITION].reps


async def _post_listen(payload: dict) -> dict:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(LISTEN_URL, json=payload)
    assert resp.status_code == 200
    return resp.json()


class TestSkipRating:
    """A3: word_ratings["skip"] — must be handled BEFORE the rating-map lookup
    (the current `.get(..., Rating.GOOD)` silently converts it to GOOD)."""

    async def test_a3_skip_rating_does_not_stage(self):
        """A3: a due word with rating "skip" produces no pending row and does
        not apply a grade (no new tt_revlog rows, no FSRS mutation)."""
        db = _setup_lesson("Banka riba")
        _seed_review_due(db, "banka")
        due_before = db.get_collocation("banka").directions[Direction.RECOGNITION].due_at

        await _post_listen({"lesson_id": "lesson-1", "word_ratings": {"banka": "skip"}})

        item = db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        assert rec.reps == 5, "skip must not grade"
        assert rec.due_at == due_before

        coll_id = db.get_collocation_id_by_guid(item.guid)
        assert db.get_pending_grade(coll_id, "recognition") is None, "skip must not stage a pending row"

    async def test_a3_skip_untracked_lemma_not_created(self):
        """A3: "skip" on an untracked lemma suppresses card creation for it —
        without suppressing creation of the lesson's other new words."""
        db = _setup_lesson("Banka riba")

        await _post_listen({"lesson_id": "lesson-1", "word_ratings": {"banka": "skip"}})

        assert db.get_collocation_by_lemma("banka") is None, "skip must suppress creation"
        assert db.get_collocation_by_lemma("riba") is not None, "skip must not leak to other words"


class TestKpRatings:
    """A3: kp_ratings — key-phrase staging."""

    async def test_a3_kp_ratings_hard_staged(self):
        """A3: kp_ratings={phrase: "hard"} stages a pending row with rating
        'hard' and does NOT apply a grade."""
        db = _setup_lesson("Banka riba", key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")])
        _seed_review_due(db, "dober dan")

        await _post_listen({"lesson_id": "lesson-1", "kp_ratings": {"dober dan": "hard"}})

        item = db.get_collocation("dober dan")
        rec = item.directions[Direction.RECOGNITION]
        assert rec.reps == 5, "kp staging must not grade"

        coll_id = db.get_collocation_id_by_guid(item.guid)
        pending = db.get_pending_grade(coll_id, "recognition")
        assert pending is not None, "kp_ratings 'hard' must stage a pending row"
        assert pending["rating"] == "hard"

    async def test_a3_kp_ratings_skip_not_staged(self):
        """A3: kp_ratings={phrase: "skip"} leaves the kp card ungraded and
        does not produce a pending row."""
        db = _setup_lesson("Banka riba", key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")])
        _seed_review_due(db, "dober dan")

        await _post_listen({"lesson_id": "lesson-1", "kp_ratings": {"dober dan": "skip"}})

        assert _rec_reps(db, "dober dan") == 5, "kp skip must not grade"
        item = db.get_collocation("dober dan")
        coll_id = db.get_collocation_id_by_guid(item.guid)
        assert db.get_pending_grade(coll_id, "recognition") is None, "kp skip must not stage"


class TestListenPreview:
    """Stage 4: GET /lesson/{id}/listen-preview — read-only, ordered, no budget."""

    async def test_preview_shape_and_ordering(self):
        """200; creations first, then tracked candidates with non-decreasing
        progress; required keys present; grade_class correct."""
        db = _setup_lesson("Banka riba novo")
        # banka: learning; riba: overdue review; novo: untracked → creation.
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        banka = db.get_collocation("banka")
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.LEARNING
        rec.reps = 1
        db.update_collocation(banka)
        _seed_review_due(db, "riba")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        assert resp.status_code == 200, "preview endpoint missing"
        data = resp.json()

        cands = data["candidates"]
        assert cands, "no candidates returned"
        for c in cands:
            assert set(c) >= {"kind", "text", "grade_class", "rating", "translation"}

        by_text = {c["text"]: c for c in cands}
        assert by_text["novo"]["grade_class"] == "create"
        assert by_text["banka"]["grade_class"] == "learning"
        assert by_text["riba"]["grade_class"] == "due"

        # Ordering: every creation before every tracked candidate; tracked
        # tail sorted by progress ascending (least-known first).
        kinds = [c["grade_class"] for c in cands]
        first_tracked = next(i for i, k in enumerate(kinds) if k != "create")
        assert all(k == "create" for k in kinds[:first_tracked])
        tracked_progress = [c["progress"] for c in cands[first_tracked:]]
        assert tracked_progress == sorted(tracked_progress)

    async def test_preview_is_pure(self):
        """Preview performs NO SRS mutations — no revlog rows, no card
        creation, no direction-state changes, no pending writes. (The lemma
        cache is exempt: `_analyze_lesson_words` legitimately warms it —
        deliberately not asserted here.)"""
        db = _setup_lesson("Banka riba")
        _seed_review_due(db, "banka")

        def _snapshot():
            with db._get_conn() as conn:
                revlog = conn.execute("SELECT count(*) FROM tt_revlog").fetchone()[0]
                colls = conn.execute("SELECT count(*) FROM collocations").fetchone()[0]
                dirs = conn.execute(
                    "SELECT collocation_id, direction, state, reps, due_at FROM collocation_directions ORDER BY 1, 2"
                ).fetchall()
                pending = conn.execute("SELECT count(*) FROM pending_listen_grades").fetchone()[0]
            return revlog, colls, dirs, pending

        before = _snapshot()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        assert resp.status_code == 200, "preview endpoint missing"
        assert _snapshot() == before, "preview mutated SRS state"

    async def test_preview_endpoint_exists(self):
        """Preview returns 200 (not 404/405) — basic contract existence."""
        _setup_lesson("Banka riba")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        assert resp.status_code == 200

    async def test_preview_ahead_class_for_future_due(self):
        """A card in REVIEW state with due_at in the future classifies as
        'ahead' (would be staged but is not yet due)."""
        from app.srs.anki_mirror.rollover import anki_today, due_at_rollover_utc

        db = _setup_lesson("Banka riba")
        unit = SyntacticUnit(text="banka", translation="bank", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        banka = db.get_collocation("banka")
        rec = banka.directions[Direction.RECOGNITION]
        rec.state = SRSState.REVIEW
        rec.last_review = datetime.now(UTC) - timedelta(days=5)
        rec.due_at = due_at_rollover_utc(anki_today() + timedelta(days=2))
        rec.reps = 5
        db.update_collocation(banka)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        cands = resp.json()["candidates"]
        by_text = {c["text"]: c for c in cands}
        assert by_text["banka"]["grade_class"] == "ahead"

    async def test_preview_creates_first_for_untracked(self):
        """Untracked lemmas in the lesson appear as kind='create' entries,
        even when there are tracked cards too."""
        db = _setup_lesson("Banka riba novo mesto")
        _seed_review_due(db, "banka")
        _seed_review_due(db, "riba")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        cands = resp.json()["candidates"]
        creates = [c for c in cands if c["kind"] == "create"]
        assert len(creates) >= 2, "should have at least 2 creation candidates"
        assert {c["text"] for c in creates} >= {"novo", "mesto"}

    async def test_preview_default_rating_is_good(self):
        """Every candidate's default rating is 'good' when no override is
        specified."""
        db = _setup_lesson("Banka riba novo")
        _seed_review_due(db, "banka")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        assert resp.status_code == 200
        for c in resp.json()["candidates"]:
            assert c["rating"] == "good", f"candidate {c['text']} should default to 'good' rating"

    async def test_preview_excludes_already_graded_today(self):
        """A card already graded today (_listen_grade_class returns None) must
        not appear as a candidate."""
        db = _setup_lesson("Banka riba")
        _seed_review_due(db, "banka")
        # Grade banka today so _listen_grade_class returns None
        item = db.get_collocation("banka")
        rec = item.directions[Direction.RECOGNITION]
        rec.last_review = datetime.now(UTC)
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        cands = resp.json()["candidates"]
        assert all(c["text"] != "banka" for c in cands), "already-graded card should be excluded"

    async def test_preview_staged_cards_also_appear(self):
        """Cards that already have a pending grade still appear in preview
        (they would be re-staged / upserted by a re-listen)."""
        db = _setup_lesson("Banka riba")
        _seed_review_due(db, "banka")
        # Manually stage a pending grade
        item = db.get_collocation("banka")
        coll_id = db.get_collocation_id_by_guid(item.guid)
        db.stage_pending_grade("lesson-1", coll_id, "recognition", "hard", "due")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        cands = resp.json()["candidates"]
        by_text = {c["text"]: c for c in cands}
        assert "banka" in by_text, "pending-staged card should still appear in preview"

    async def test_preview_lesson_not_found(self):
        """Preview of a nonexistent lesson returns 404."""
        _setup_lesson("Banka riba")  # sets up content_store with "lesson-1"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/srs/lesson/nonexistent/listen-preview")
        assert resp.status_code == 404

    async def test_preview_includes_key_phrases(self):
        """Tracked key phrases appear as kind='kp' candidates with the
        correct grade_class and translation."""
        db = _setup_lesson("Banka riba", key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")])
        _seed_review_due(db, "dober dan")

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        cands = resp.json()["candidates"]
        by_text = {c["text"]: c for c in cands}
        assert "dober dan" in by_text
        kp_cand = by_text["dober dan"]
        assert kp_cand["kind"] == "kp"
        assert kp_cand["grade_class"] == "due"
        assert kp_cand["rating"] == "good"
        assert kp_cand["translation"] == "t-dober dan"  # seeded card's translation

    async def test_preview_skips_untracked_key_phrases(self):
        """Key phrases not in the DB are silently skipped (creation deferred)."""
        _setup_lesson("Banka riba", key_phrases=[KeyPhraseInfo(phrase="nasvidenje", translation="goodbye")])

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        cands = resp.json()["candidates"]
        assert all(c["text"] != "nasvidenje" for c in cands), "untracked kp should not appear"

    async def test_preview_excludes_cloze_cards(self):
        """Existing cloze cards (card_type='cloze') are excluded from the
        preview candidates — only vocab cards are staged."""
        db = _setup_lesson("Kje je banka?")
        # Create a cloze card manually
        from app.srs.function_words import make_cloze_text

        sent = "Kje je banka?"
        cloze_text = make_cloze_text("kje", sent)
        unit = SyntacticUnit(
            text="kje",
            translation="where",
            word_count=1,
            difficulty=1,
            source="test",
            card_type="cloze",
            source_sentence=cloze_text,
        )
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("kje")
        rec = item.directions[Direction.PRODUCTION]
        rec.state = SRSState.REVIEW
        rec.reps = 3
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        cands = resp.json()["candidates"]
        assert all(c["text"] != "kje" for c in cands), "cloze card should be excluded from preview"

    async def test_preview_excludes_cloze_key_phrases(self):
        """Key phrases with card_type='cloze' are excluded from preview."""
        db = _setup_lesson(
            "Banka riba",
            key_phrases=[KeyPhraseInfo(phrase="kje je", translation="where is")],
        )
        from app.srs.function_words import make_cloze_text

        unit = SyntacticUnit(
            text="kje je",
            translation="where is",
            word_count=2,
            difficulty=1,
            source="test",
            card_type="cloze",
            source_sentence=make_cloze_text("kje je", "Kje je banka?"),
        )
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("kje je")
        prod = item.directions[Direction.PRODUCTION]
        prod.state = SRSState.REVIEW
        prod.reps = 3
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        cands = resp.json()["candidates"]
        assert all(c["text"] != "kje je" for c in cands), "cloze KP should be excluded"

    async def test_preview_kp_already_graded_today_excluded(self):
        """Key phrases already graded today are excluded (grade_class=None)."""
        db = _setup_lesson(
            "Banka riba",
            key_phrases=[KeyPhraseInfo(phrase="dober dan", translation="good day")],
        )
        _seed_review_due(db, "dober dan")
        item = db.get_collocation("dober dan")
        rec = item.directions[Direction.RECOGNITION]
        rec.last_review = datetime.now(UTC)
        db.update_collocation(item)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        cands = resp.json()["candidates"]
        assert all(c["text"] != "dober dan" for c in cands), "already-graded KP should be excluded"

    async def test_preview_kp_no_recognition_direction(self):
        """Key phrases with no recognition direction are excluded."""
        db = _setup_lesson(
            "Banka riba",
            key_phrases=[KeyPhraseInfo(phrase="hvala", translation="thank you")],
        )
        unit = SyntacticUnit(text="hvala", translation="thank you", word_count=1, difficulty=1, source="test")
        db.add_collocation(unit, language_code="sl")
        item = db.get_collocation("hvala")
        coll_id = db.get_collocation_id_by_guid(item.guid)
        # Delete the recognition direction from DB so only production remains
        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE collocation_id = ? AND direction = 'recognition'",
                (coll_id,),
            )
            db._commit(conn)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        cands = resp.json()["candidates"]
        assert all(c["text"] != "hvala" for c in cands), "KP without recognition should be excluded"

    async def test_preview_skips_clozes_only_verb(self, monkeypatch):
        """Clozes-only verbs (e.g. biti) are excluded from creation candidates."""
        import app.api.srs as srs_mod

        def fake_is_func(lemma, surfaces, language_code, surface_upos=None):
            return lemma == "biti"

        def fake_is_clozes_only(lemma, language_code):
            return lemma == "biti"

        monkeypatch.setattr(srs_mod, "is_function_word_for", fake_is_func)
        monkeypatch.setattr(srs_mod, "is_clozes_only_verb", fake_is_clozes_only)

        # Use a lesson where the lemmatizer would produce "biti" as a lemma.
        # We monkeypatch _analyze_lesson_words to return "biti" directly.
        def fake_analyze(lesson, db):
            return srs_mod._LessonWords(
                occurrences={"biti": 1},
                first_sentence={"biti": "Sem doma."},
                surfaces={"biti": {"sem"}},
                first_surface={"biti": "sem"},
                surface_upos={"sem": "AUX"},
            )

        monkeypatch.setattr(srs_mod, "_analyze_lesson_words", fake_analyze)

        _setup_lesson("Sem doma.")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        cands = resp.json()["candidates"]
        assert all(c["text"] != "biti" for c in cands), "clozes-only verb should be excluded"

    async def test_preview_word_no_recognition_direction(self):
        """Tracked vocab cards with no recognition direction are excluded."""
        db = _setup_lesson("Banka riba")
        _seed_review_due(db, "banka")
        item = db.get_collocation("banka")
        coll_id = db.get_collocation_id_by_guid(item.guid)
        with db._get_conn() as conn:
            conn.execute(
                "DELETE FROM collocation_directions WHERE collocation_id = ? AND direction = 'recognition'",
                (coll_id,),
            )
            db._commit(conn)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get(PREVIEW_URL)
        cands = resp.json()["candidates"]
        assert all(c["text"] != "banka" for c in cands), "word without recognition should be excluded"
